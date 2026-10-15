"""
Google Ads Offline Conversion Imports via Data Manager API (Events).

OCI bridge is a spam filter: CRM funnel is unchanged, but ONLY two events
are uploaded to Google Ads:

  • Qualified_Lead  — target / qualified / in-work stages
  • Closed_Deal     — closed / paid / successful (with value + UZS)

Spam, raw/new leads, no-answer, refuse, archive → NEVER uploaded.

Transport: POST https://datamanager.googleapis.com/v1/events:ingest
(replaces deprecated customers:uploadClickConversions / ConversionUploadService).

Credentials (see Website/.env.example):

  Preferred auth:
    GOOGLE_ADS_AUTH_MODE=service_account
    GOOGLE_ADS_SERVICE_ACCOUNT_FILE=google_key.json
    google_key.json in app/ (Service Account JSON)

  Legacy user-OAuth (optional fallback):
    GOOGLE_ADS_CLIENT_ID / GOOGLE_ADS_CLIENT_SECRET / GOOGLE_ADS_REFRESH_TOKEN

  Always required:
    GOOGLE_ADS_CUSTOMER_ID          — 10 digits, no dashes
    GOOGLE_ADS_CONVERSION_ACTION_* / GOOGLE_ADS_CONV_*
    GOOGLE_ADS_ENABLED

  Recommended:
    GOOGLE_ADS_DEVELOPER_TOKEN      — still used for Ads diagnostics (listAccessible)
    GOOGLE_CLOUD_PROJECT            — x-goog-user-project (defaults to SA project_id)

  Optional:
    GOOGLE_ADS_LOGIN_CUSTOMER_ID   — MCC → Destination.loginAccount when it manages the client
    GOOGLE_ADS_EVENT_SOURCE        — Data Manager EventSource (default WEB)

Service Account JWT scopes: adwords + datamanager (DM API requires datamanager).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import httpx

log = logging.getLogger("leads.google_ads")

TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
DATAMANAGER_SCOPE = "https://www.googleapis.com/auth/datamanager"
# SA must request both: adwords (legacy Ads calls) + datamanager (events:ingest)
ADS_SCOPES = (ADS_SCOPE, DATAMANAGER_SCOPE)
DATAMANAGER_INGEST_URL = "https://datamanager.googleapis.com/v1/events:ingest"
# Hint / docs default only — never forced into headers without validation
DEFAULT_LOGIN_CUSTOMER_ID = "9961022203"
_MODULE_DIR = Path(__file__).resolve().parent
_SITE_DIR = _MODULE_DIR.parent if _MODULE_DIR.name == "app" else _MODULE_DIR


def _ads_api_version() -> str:
    """
    Read Ads API version from env (diagnostics / listAccessibleCustomers).

    Default v22 — older versions (v16–v21) return HTML 404 from Google's edge.
    """
    return (_env_ads("GOOGLE_ADS_API_VERSION") or "v22") or "v22"


# Back-compat alias — prefer _ads_api_version() in new code
ADS_API_VERSION = "v22"


def build_datamanager_ingest_url() -> str:
    """Canonical Data Manager Events ingest URL."""
    return DATAMANAGER_INGEST_URL


def build_upload_click_conversions_url(customer_id: str, api_version: str = "") -> str:
    """Deprecated Ads ConversionUploadService URL (kept for diagnostics only)."""
    cid = _digits_id(customer_id)
    ver = (api_version or _ads_api_version()).strip().lstrip("/")
    if not ver.startswith("v"):
        ver = "v" + ver
    return (
        f"https://googleads.googleapis.com/{ver}/"
        f"customers/{cid}:uploadClickConversions"
    )


def build_ads_request_headers(
    *,
    access_token: str,
    developer_token: str,
    login_customer_id: str = "",
) -> dict[str, str]:
    """
    Headers for Google Ads REST (exact names required by the API).

    Always:
      Authorization: Bearer <access_token>
      developer-token: <token>
      Content-Type / Accept: application/json

    Conditionally:
      login-customer-id: <MCC digits> — only when non-empty after digit-normalization.
      Do not send a wrong MCC: that yields USER_PERMISSION_DENIED even if the SA
      has direct access to the client account.
    """
    headers = {
        "Authorization": f"Bearer {access_token.strip()}",
        "developer-token": _clean_env_value(developer_token),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    login = _digits_id(login_customer_id or "")
    if login:
        headers["login-customer-id"] = login
    return headers


def build_datamanager_request_headers(
    *,
    access_token: str,
    login_customer_id: str = "",
    gcp_project: str = "",
) -> dict[str, str]:
    """
    Headers for Data Manager API events:ingest.

    - Authorization: Bearer <token>
    - Content-Type / Accept: application/json
    - x-goog-user-project: billing/quota project (recommended)
    - login-account: accountTypes/GOOGLE_ADS/accounts/{MCC} when using a manager
    Developer-token is NOT used by Data Manager API.
    """
    headers = {
        "Authorization": f"Bearer {access_token.strip()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    # Only set when explicitly configured — a wrong project yields USER_PROJECT_DENIED.
    # Without the header Google bills the SA's home project (must enable Data Manager API).
    project = (gcp_project or _gcp_project_id()).strip()
    if project:
        headers["x-goog-user-project"] = project
    login = _digits_id(login_customer_id or "")
    if login:
        headers["login-account"] = f"accountTypes/GOOGLE_ADS/accounts/{login}"
    return headers


def _gcp_project_id() -> str:
    """Optional GCP project for x-goog-user-project (explicit env only)."""
    return _env_ads("GOOGLE_CLOUD_PROJECT") or _env_ads("GCP_PROJECT") or ""


def _conversion_action_numeric_id(conversion_action: str) -> str:
    """
    Data Manager productDestinationId is the numeric conversion action id only.
    Accepts 'customers/123/conversionActions/456' or bare '456'.
    """
    raw = str(conversion_action or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        return raw
    parts = raw.split("/")
    if len(parts) >= 4 and parts[-2] == "conversionActions" and parts[-1].isdigit():
        return parts[-1]
    digits = re.sub(r"\D", "", raw)
    return digits


def _event_source() -> str:
    """EventSource enum for offline click conversions (default WEB)."""
    raw = (_env_ads("GOOGLE_ADS_EVENT_SOURCE") or "WEB").strip().upper()
    return raw or "WEB"


def _format_event_timestamp(conversion_date_time: str = "", ms: Optional[int] = None) -> str:
    """
    Data Manager eventTimestamp: RFC 3339 (e.g. 2026-10-13T10:00:00Z).

    Accepts legacy Ads format 'yyyy-mm-dd hh:mm:ss+00:00' or epoch ms.
    """
    raw = str(conversion_date_time or "").strip()
    if raw:
        # Already RFC 3339-ish
        if "T" in raw:
            return raw.replace(" ", "")
        # Ads format: '2026-10-13 10:00:00+00:00' → '2026-10-13T10:00:00+00:00'
        m = re.match(
            r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})([+-]\d{2}:\d{2}|Z)?$",
            raw,
        )
        if m:
            tz = m.group(3) or "Z"
            if tz == "+00:00":
                tz = "Z"
            return f"{m.group(1)}T{m.group(2)}{tz}"
        try:
            # Fallback: fromisoformat with space
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    if ms and ms > 0:
        dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_datamanager_ingest_payload(
    *,
    customer_id: str,
    conversion_action: str,
    gclid: str = "",
    gbraid: str = "",
    wbraid: str = "",
    conversion_date_time: str = "",
    conversion_value: float = 0.0,
    currency_code: str = "UZS",
    order_id: str = "",
    login_customer_id: str = "",
) -> dict[str, Any]:
    """Build IngestEventsRequest body for Google Ads offline conversions."""
    cid = _digits_id(customer_id)
    action_id = _conversion_action_numeric_id(conversion_action)
    destination: dict[str, Any] = {
        "operatingAccount": {
            "accountType": "GOOGLE_ADS",
            "accountId": cid,
        },
        "productDestinationId": action_id,
    }
    login = _digits_id(login_customer_id or "")
    if login and login != cid:
        destination["loginAccount"] = {
            "accountType": "GOOGLE_ADS",
            "accountId": login,
        }

    ad_ids: dict[str, str] = {}
    if gclid:
        ad_ids["gclid"] = gclid
    elif gbraid:
        ad_ids["gbraid"] = gbraid
    elif wbraid:
        ad_ids["wbraid"] = wbraid

    event: dict[str, Any] = {
        "adIdentifiers": ad_ids,
        "eventTimestamp": _format_event_timestamp(conversion_date_time),
        "eventSource": _event_source(),
        "conversionValue": float(conversion_value),
        "currency": (currency_code or _currency() or "UZS").upper(),
    }
    if order_id:
        event["transactionId"] = str(order_id)[:100]

    return {
        "destinations": [destination],
        "events": [event],
        # Required by IngestEventsRequest even when userData is absent
        "encoding": "HEX",
    }


def _ads_response_meta(resp: httpx.Response) -> dict[str, Any]:
    """Collect Google Ads response diagnostics (request id, useful headers)."""
    h = resp.headers
    # Google returns the id as request-id and/or x-goog-request-id depending on path
    request_id = (
        h.get("x-goog-request-id")
        or h.get("request-id")
        or h.get("X-Goog-Request-Id")
        or ""
    )
    interesting = {
        k: v
        for k, v in h.items()
        if k.lower().startswith("x-")
        or k.lower()
        in {
            "request-id",
            "content-type",
            "date",
            "server",
            "grpc-status",
            "grpc-message",
        }
    }
    return {
        "status_code": resp.status_code,
        "request_id": request_id,
        "response_headers": interesting,
    }


def _extract_google_ads_error(body: Any, status_code: int, text: str) -> str:
    """Pull authenticationError / authorizationError message from Ads JSON."""
    if not isinstance(body, dict):
        if status_code == 404 and "<html" in (text or "").lower():
            return (
                f"HTTP 404 HTML (API version likely sunset). "
                f"Set GOOGLE_ADS_API_VERSION=v22. Body starts: {(text or '')[:120]}"
            )
        return f"HTTP {status_code}: {(text or '')[:500]}"
    err = body.get("error") or {}
    details = err.get("details") or []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        for item in detail.get("errors") or []:
            if not isinstance(item, dict):
                continue
            code = item.get("errorCode") or {}
            msg = item.get("message") or ""
            if code:
                return f"HTTP {status_code}: {code} — {msg}"
    # partialFailureError shape (HTTP 200)
    partial = body.get("partialFailureError")
    if isinstance(partial, dict):
        pmsg = partial.get("message") or ""
        for detail in partial.get("details") or []:
            if not isinstance(detail, dict):
                continue
            for item in detail.get("errors") or []:
                if not isinstance(item, dict):
                    continue
                code = item.get("errorCode") or {}
                msg = item.get("message") or pmsg
                if code:
                    return f"partialFailure: {code} — {msg}"
        if pmsg:
            return f"partialFailure: {pmsg[:500]}"
    msg = err.get("message") or (text or "")[:500]
    return f"HTTP {status_code}: {msg}"


def _log_ads_api_failure(
    *,
    operation: str,
    url: str,
    req_headers: Mapping[str, str],
    resp: httpx.Response,
    body: Any,
    error: str,
) -> dict[str, Any]:
    """Log full Ads API failure context (masked secrets + x-goog-request-id)."""
    meta = _ads_response_meta(resp)
    safe_req = {
        "Authorization": "Bearer ***",
        "developer-token": _mask_secret(
            req_headers.get("developer-token", ""), keep_start=4, keep_end=4
        ),
        "login-customer-id": req_headers.get("login-customer-id") or "<none>",
        "Content-Type": req_headers.get("Content-Type"),
        "Accept": req_headers.get("Accept"),
    }
    body_preview = ""
    try:
        body_preview = json.dumps(body, ensure_ascii=False)[:1200]
    except Exception:  # noqa: BLE001
        body_preview = (resp.text or "")[:1200]
    log.error(
        "%s failed: %s | url=%s | status=%s | x-goog-request-id=%s | "
        "request_headers=%s | response_headers=%s | body=%s",
        operation,
        error,
        url,
        meta["status_code"],
        meta["request_id"] or "<none>",
        safe_req,
        meta["response_headers"],
        body_preview,
    )
    return {
        **meta,
        "error": error,
        "request_headers_safe": safe_req,
        "body": body,
    }


async def list_accessible_customer_ids(
    access_token: str,
    developer_token: str,
    *,
    login_customer_id: str = "",
) -> list[str]:
    """GET customers:listAccessibleCustomers — which Ads accounts this token can use."""
    ver = _ads_api_version()
    url = f"https://googleads.googleapis.com/{ver}/customers:listAccessibleCustomers"
    # listAccessibleCustomers must not force a login-customer-id
    headers = build_ads_request_headers(
        access_token=access_token,
        developer_token=developer_token,
        login_customer_id=login_customer_id or "",
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code >= 400:
        meta = _ads_response_meta(resp)
        log.warning(
            "listAccessibleCustomers failed HTTP %s x-goog-request-id=%s: %s",
            resp.status_code,
            meta["request_id"] or "<none>",
            (resp.text or "")[:400],
        )
        return []
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for name in data.get("resourceNames") or []:
        # customers/1234567890
        parts = str(name).split("/")
        if len(parts) == 2 and parts[1].isdigit():
            out.append(parts[1])
    return out


async def mcc_manages_customer(
    *,
    access_token: str,
    developer_token: str,
    login_customer_id: str,
    customer_id: str,
) -> bool:
    """
    True if customer_id appears under MCC via customer_client (any level).
    Used to avoid sending a wrong login-customer-id (USER_PERMISSION_DENIED).
    """
    mcc = _digits_id(login_customer_id)
    cid = _digits_id(customer_id)
    if not mcc or not cid:
        return False
    if mcc == cid:
        return True
    ver = _ads_api_version()
    url = f"https://googleads.googleapis.com/{ver}/customers/{mcc}/googleAds:search"
    headers = build_ads_request_headers(
        access_token=access_token,
        developer_token=developer_token,
        login_customer_id=mcc,
    )
    # Escape is unnecessary for pure digits; keep query simple
    query = (
        "SELECT customer_client.client_customer "
        "FROM customer_client "
        f"WHERE customer_client.client_customer = 'customers/{cid}'"
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json={"query": query})
        if resp.status_code >= 400:
            meta = _ads_response_meta(resp)
            log.warning(
                "mcc_manages_customer search failed MCC=%s client=%s "
                "status=%s x-goog-request-id=%s body=%s",
                mcc,
                cid,
                resp.status_code,
                meta["request_id"] or "<none>",
                (resp.text or "")[:300],
            )
            return False
        data = resp.json()
        results = data.get("results") or []
        return bool(results)
    except Exception as exc:  # noqa: BLE001
        log.warning("mcc_manages_customer exception: %s", exc)
        return False


async def resolve_login_customer_id_for_upload(
    *,
    access_token: str,
    developer_token: str,
    customer_id: str,
    configured_login: str,
    accessible: Optional[list[str]] = None,
) -> str:
    """
    Decide which login-customer-id (if any) to send on uploadClickConversions.

    - If MCC manages the client → send MCC.
    - If SA has direct access to the client and MCC does not manage it → omit
      (sending the wrong MCC causes USER_PERMISSION_DENIED).
    - If env LOGIN empty and direct access → omit.
    """
    cid = _digits_id(customer_id)
    login = _digits_id(configured_login or "")
    if accessible is None:
        accessible = await list_accessible_customer_ids(access_token, developer_token)
    direct = cid in (accessible or [])

    if login:
        if login == cid:
            log.info(
                "login-customer-id=%s equals customer_id — omitting header (self)",
                login,
            )
            return ""
        manages = await mcc_manages_customer(
            access_token=access_token,
            developer_token=developer_token,
            login_customer_id=login,
            customer_id=cid,
        )
        if manages:
            log.info(
                "Using login-customer-id=%s (MCC manages customer_id=%s)",
                login,
                cid,
            )
            return login
        if direct:
            log.warning(
                "GOOGLE_ADS_LOGIN_CUSTOMER_ID=%s does NOT manage customer_id=%s "
                "(customer_client empty). SA has direct access — omitting "
                "login-customer-id to avoid USER_PERMISSION_DENIED. "
                "Fix Ads hierarchy or clear GOOGLE_ADS_LOGIN_CUSTOMER_ID.",
                login,
                cid,
            )
            return ""
        log.warning(
            "GOOGLE_ADS_LOGIN_CUSTOMER_ID=%s may not manage %s and client is not "
            "in listAccessibleCustomers=%s — still sending login header",
            login,
            cid,
            accessible,
        )
        return login

    if direct:
        log.info(
            "No GOOGLE_ADS_LOGIN_CUSTOMER_ID; SA has direct access to %s — "
            "upload without login-customer-id",
            cid,
        )
        return ""

    # Last resort hint: default MCC only if it actually manages the client
    if await mcc_manages_customer(
        access_token=access_token,
        developer_token=developer_token,
        login_customer_id=DEFAULT_LOGIN_CUSTOMER_ID,
        customer_id=cid,
    ):
        log.info(
            "Using default MCC login-customer-id=%s (manages %s)",
            DEFAULT_LOGIN_CUSTOMER_ID,
            cid,
        )
        return DEFAULT_LOGIN_CUSTOMER_ID

    log.warning(
        "No valid login-customer-id for customer_id=%s accessible=%s",
        cid,
        accessible,
    )
    return ""

# CRM leadQualityStatus → Offline Conversion bucket (3 Google Ads actions).
# Display names in Google Ads UI MUST match exactly:
#   "Spam/Non targeted" | "Qualified Lead" | "Closed-won"
#
# Firebase RTDB keys (incoming_leads.leadQualityStatus):
#   spam, non_targeted, disqualified, targeted, qualified, warm,
#   negotiation, checkout, closed_won, retention  (+ aliases below)
#
# OCI allows ONLY these two buckets (spam/raw never leave the CRM → Ads bridge)
OCI_ALLOWED_BUCKETS = frozenset({"qualified", "closed_won"})

# Blocked / ignored for Google Ads (CRM may still use these statuses)
SPAM_STATUSES = frozenset(
    {
        "spam",
        "non_targeted",
        "disqualified",
        "nontargeted",
        "not_targeted",
        "нецелевой",
        "брак",
        "refuse",
        "refusal",
        "rejected",
        "reject",
        "отказ",
        "отказной",
        "no_answer",
        "noanswer",
        "недозвон",
        "archive",
        "archived",
        "архив",
    }
)
# Raw / primary inquiry — never OCI
RAW_LEAD_STATUSES = frozenset(
    {
        "cold",
        "new",
        "new_lead",
        "raw",
        "raw_lead",
        "unprocessed",
        "inbox",
        "новая",
        "новая_заявка",
        "неразобранное",
        "неразобранный",
        "primary",
        "incoming",
    }
)
QUALIFIED_STATUSES = frozenset(
    {
        "targeted",
        "qualified",
        "qualified_lead",
        "qual",
        "warm",
        "negotiation",
        "checkout",
        "in_progress",
        "in_work",
        "manager",
        "квалифицирован",
        "квалифицированный",
        "целевой",
        "целевой_лид",
        "в_работе",
        "в_работе_у_менеджера",
    }
)
CLOSED_WON_STATUSES = frozenset(
    {
        "closed_won",
        "closedwon",
        "closed_deal",
        "won",
        "successful",
        "success",
        "sale",
        "paid",
        "retention",
        "оплачено",
        "успешно_реализовано",
        "закрыто",
        "закрыта",
        "продажа",
    }
)

# Fallbacks when env actions are empty (account 4054797246)
# Spam action kept for legacy env only — bridge NEVER uploads it.
_DEFAULT_ACTION_SPAM = "customers/4054797246/conversionActions/7815460963"
_DEFAULT_ACTION_QUALIFIED = "customers/4054797246/conversionActions/7815437037"
_DEFAULT_ACTION_CLOSED_WON = "customers/4054797246/conversionActions/7815489823"

# Names used in audit logs / GTM mapping (Google Ads conversion actions)
CONVERSION_ACTION_DISPLAY_NAMES = {
    "qualified": "Qualified_Lead",
    "closed_won": "Closed_Deal",
}

# Alias → canonical CRM status key (before bucket mapping)
_STATUS_ALIASES = {
    "qualified_lead": "qualified",
    "qual": "qualified",
    "целевой": "qualified",
    "целевой_лид": "qualified",
    "квалифицирован": "qualified",
    "в_работе": "qualified",
    "в_работе_у_менеджера": "qualified",
    "in_progress": "qualified",
    "in_work": "qualified",
    "manager": "qualified",
    "nontargeted": "non_targeted",
    "not_targeted": "non_targeted",
    "брак": "spam",
    "refuse": "spam",
    "refusal": "spam",
    "rejected": "spam",
    "reject": "spam",
    "отказ": "spam",
    "отказной": "spam",
    "no_answer": "spam",
    "noanswer": "spam",
    "недозвон": "spam",
    "archive": "spam",
    "archived": "spam",
    "архив": "spam",
    "closedwon": "closed_won",
    "closed_deal": "closed_won",
    "won": "closed_won",
    "successful": "closed_won",
    "success": "closed_won",
    "sale": "closed_won",
    "paid": "closed_won",
    "нецелевой": "non_targeted",
    "квалифицированный": "qualified",
    "оплачено": "closed_won",
    "успешно_реализовано": "closed_won",
    "закрыто": "closed_won",
    "закрыта": "closed_won",
    "продажа": "closed_won",
    "new_lead": "new",
    "raw_lead": "raw",
    "новая_заявка": "new",
    "неразобранное": "unprocessed",
    "неразобранный": "unprocessed",
}
# Substrings → blocked spam/refuse labels
_SPAM_KEYWORDS = (
    "spam",
    "отказ",
    "otkaz",
    "refus",
    "reject",
    "ошиб",
    "error",
    "invalid",
    "fake",
    "мусор",
    "брак",
    "недозвон",
    "архив",
    "archive",
)

_token_cache: dict[str, Any] = {"access_token": None, "expires_at": 0.0}
_oauth_startup_logged = False
_creds_fingerprint: Optional[str] = None


def reload_ads_dotenv(*, override: bool = True) -> list[str]:
    """
    Reload .env into os.environ before every credential read / token request.

    override=True ensures an updated GOOGLE_ADS_CLIENT_SECRET on disk replaces
    stale values left in the process environment (Passenger workers, CLI).
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        log.warning("python-dotenv not installed — reading only process env")
        return []

    loaded: list[str] = []
    # Later paths override earlier ones: app/.env wins on Passenger
    candidates = (
        _SITE_DIR / ".env",
        _MODULE_DIR / ".env",
        Path.cwd() / ".env",
        Path.cwd() / "app" / ".env",
    )
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        load_dotenv(path, override=override)
        loaded.append(str(resolved))
    if loaded:
        log.debug("Ads dotenv reloaded (override=%s): %s", override, loaded)
    return loaded


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _clean_env_value(value: Any) -> str:
    """Strip whitespace and surrounding quotes from .env values."""
    raw = str(value or "").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("\"", "'"):
        raw = raw[1:-1].strip()
    # Drop UTF-8 BOM if pasted into .env
    if raw.startswith("\ufeff"):
        raw = raw.lstrip("\ufeff").strip()
    return raw


def _env_ads(name: str, default: str = "") -> str:
    """Read a GOOGLE_ADS_* (or any) env var with quote/BOM cleanup."""
    return _clean_env_value(os.getenv(name, default))


def clear_ads_token_cache(reason: str = "") -> None:
    """Drop cached access_token so the next call re-reads .env credentials."""
    _token_cache["access_token"] = None
    _token_cache["expires_at"] = 0.0
    if reason:
        log.info("Google Ads access_token cache cleared: %s", reason)


def resolve_service_account_file() -> Optional[Path]:
    """
    Locate google_key.json / service-account JSON.

    Order:
      1) GOOGLE_ADS_SERVICE_ACCOUNT_FILE
      2) GOOGLE_APPLICATION_CREDENTIALS
      3) app/google_key.json, cwd/google_key.json, site/google_key.json
    """
    reload_ads_dotenv(override=True)
    explicit = _env_ads("GOOGLE_ADS_SERVICE_ACCOUNT_FILE") or _env_ads(
        "GOOGLE_APPLICATION_CREDENTIALS"
    )
    candidates: list[Path] = []
    if explicit:
        p = Path(explicit)
        if not p.is_absolute():
            candidates.extend(
                (
                    _MODULE_DIR / p,
                    _SITE_DIR / p,
                    Path.cwd() / p,
                )
            )
        candidates.append(p)
    candidates.extend(
        (
            _MODULE_DIR / "google_key.json",
            _SITE_DIR / "google_key.json",
            Path.cwd() / "google_key.json",
            Path.cwd() / "app" / "google_key.json",
        )
    )
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def _service_account_email(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(data.get("client_email") or "")
    except Exception:  # noqa: BLE001
        return ""


def auth_mode() -> str:
    """
    service_account (default when google_key.json exists) | oauth

    Set GOOGLE_ADS_AUTH_MODE=oauth to force legacy Web-client refresh tokens.
    """
    reload_ads_dotenv(override=True)
    forced = _env_ads("GOOGLE_ADS_AUTH_MODE").lower()
    if forced in ("service_account", "sa", "json", "key"):
        return "service_account"
    if forced in ("oauth", "refresh", "user"):
        return "oauth"
    # Auto: prefer SA when key file is present
    if resolve_service_account_file() is not None:
        return "service_account"
    return "oauth"


def _env_first(*names: str) -> str:
    for name in names:
        val = _env_ads(name)
        if val:
            return val
    return ""


_PLACEHOLDER_SECRETS = frozenset(
    {"", "test", "changeme", "xxx", "your_token", "placeholder", "TODO", "todo"}
)


def _is_real_secret(value: str) -> bool:
    v = _clean_env_value(value)
    return bool(v) and v.lower() not in _PLACEHOLDER_SECRETS and len(v) >= 8


def _mask_secret(value: str, *, keep_start: int = 6, keep_end: int = 4) -> str:
    """Mask a secret for logs: abcdef…wxyz (len=N)."""
    raw = _clean_env_value(value)
    if not raw:
        return "<empty>"
    n = len(raw)
    if n <= keep_start + keep_end:
        return f"<set len={n}>"
    return f"{raw[:keep_start]}…{raw[-keep_end:]} (len={n})"


def _digits_id(value: str) -> str:
    return re.sub(r"\D", "", _clean_env_value(value))


def _customer_id() -> str:
    return _digits_id(_env_ads("GOOGLE_ADS_CUSTOMER_ID"))


def _login_customer_id() -> str:
    """
    Configured MCC id from GOOGLE_ADS_LOGIN_CUSTOMER_ID (digits only).

    Empty when unset — upload resolves whether a header is needed via
    resolve_login_customer_id_for_upload (wrong MCC → USER_PERMISSION_DENIED).
    """
    raw = _env_ads("GOOGLE_ADS_LOGIN_CUSTOMER_ID")
    if not raw or not str(raw).strip():
        return ""
    return _digits_id(raw)


def _developer_token() -> str:
    return _env_ads("GOOGLE_ADS_DEVELOPER_TOKEN")


def get_ads_oauth_credentials() -> dict[str, Any]:
    """
    Resolve Ads auth config (service account preferred, OAuth legacy optional).

    Always reloads .env (override=True). Access tokens are cached briefly;
    cache invalidates when the credential fingerprint changes.
    """
    global _creds_fingerprint

    env_files = reload_ads_dotenv(override=True)
    mode = auth_mode()
    sa_path = resolve_service_account_file()
    sa_email = _service_account_email(sa_path) if sa_path else ""

    client_id = _env_ads("GOOGLE_ADS_CLIENT_ID")
    client_secret = _env_ads("GOOGLE_ADS_CLIENT_SECRET")
    primary = _env_ads("GOOGLE_ADS_REFRESH_TOKEN")
    legacy = _env_ads("REFRESH_TOKEN")
    allow_legacy = _env_bool("GOOGLE_ADS_ALLOW_LEGACY_REFRESH_TOKEN", True)

    refresh = ""
    refresh_source = "missing"
    if primary:
        refresh = primary
        refresh_source = "GOOGLE_ADS_REFRESH_TOKEN"
        if legacy and legacy != primary:
            log.warning(
                "Both GOOGLE_ADS_REFRESH_TOKEN and REFRESH_TOKEN are set and DIFFER "
                "(lens %s vs %s). Using GOOGLE_ADS_REFRESH_TOKEN only; legacy ignored.",
                len(primary),
                len(legacy),
            )
    elif legacy and allow_legacy:
        refresh = legacy
        refresh_source = "REFRESH_TOKEN(legacy_fallback)"
        log.warning(
            "GOOGLE_ADS_REFRESH_TOKEN is empty — falling back to legacy REFRESH_TOKEN. "
            "Set GOOGLE_ADS_REFRESH_TOKEN explicitly to avoid conflicts."
        )
    elif legacy and not allow_legacy:
        refresh_source = "REFRESH_TOKEN(blocked_by_GOOGLE_ADS_ALLOW_LEGACY_REFRESH_TOKEN=false)"

    if mode == "service_account" and sa_path is not None:
        try:
            mtime = str(sa_path.stat().st_mtime_ns)
        except OSError:
            mtime = "0"
        fingerprint = f"sa|{sa_path}|{mtime}|{sa_email}"
    else:
        fingerprint = f"oauth|{client_id}|{client_secret}|{refresh}|{refresh_source}"

    if _creds_fingerprint is not None and fingerprint != _creds_fingerprint:
        clear_ads_token_cache("Ads credentials changed on disk/env")
    _creds_fingerprint = fingerprint

    return {
        "auth_mode": mode,
        "service_account_file": str(sa_path) if sa_path else "",
        "service_account_email": sa_email,
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh,
        "refresh_token_env": refresh_source,
        "developer_token": _developer_token(),
        "customer_id": _customer_id(),
        "login_customer_id": _login_customer_id(),
        "api_version": _ads_api_version(),
        "enabled": _env_bool("GOOGLE_ADS_ENABLED", False),
        "env_files": env_files,
        "client_secret_fingerprint": _mask_secret(
            client_secret, keep_start=6, keep_end=4
        ),
        "impersonate_user": _env_ads("GOOGLE_ADS_IMPERSONATE_USER"),
    }


def log_oauth_credentials_startup(*, force: bool = False) -> dict[str, str]:
    """Log masked auth config once at process start (SA or OAuth)."""
    global _oauth_startup_logged
    creds = get_ads_oauth_credentials()
    summary = {
        "auth_mode": str(creds.get("auth_mode")),
        "service_account_file": str(creds.get("service_account_file") or ""),
        "service_account_email": str(creds.get("service_account_email") or ""),
        "client_id": _mask_secret(creds["client_id"], keep_start=10, keep_end=6),
        "client_secret": _mask_secret(creds["client_secret"], keep_start=6, keep_end=4),
        "refresh_token": _mask_secret(creds["refresh_token"], keep_start=8, keep_end=6),
        "refresh_token_env": str(creds["refresh_token_env"]),
        "developer_token": _mask_secret(creds["developer_token"], keep_start=4, keep_end=4),
        "customer_id": creds["customer_id"] or "<empty>",
        "login_customer_id": creds["login_customer_id"] or "<empty>",
        "api_version": str(creds["api_version"]),
    }
    if force or not _oauth_startup_logged:
        _oauth_startup_logged = True
        if creds.get("auth_mode") == "service_account":
            log.info(
                "Google Ads auth=service_account file=%s email=%s "
                "DEVELOPER_TOKEN=%s CUSTOMER_ID=%s LOGIN_CUSTOMER_ID=%s "
                "API=%s ENABLED=%s",
                summary["service_account_file"] or "<missing>",
                summary["service_account_email"] or "<missing>",
                summary["developer_token"],
                summary["customer_id"],
                summary["login_customer_id"],
                summary["api_version"],
                creds["enabled"],
            )
        else:
            log.info(
                "Google Ads auth=oauth "
                "CLIENT_ID=%s CLIENT_SECRET=%s REFRESH_TOKEN=%s (from %s) "
                "DEVELOPER_TOKEN=%s CUSTOMER_ID=%s LOGIN_CUSTOMER_ID=%s "
                "API=%s ENABLED=%s",
                summary["client_id"],
                summary["client_secret"],
                summary["refresh_token"],
                summary["refresh_token_env"],
                summary["developer_token"],
                summary["customer_id"],
                summary["login_customer_id"],
                summary["api_version"],
                creds["enabled"],
            )
    return summary


def _normalize_status_key(status: str) -> str:
    key = (
        str(status or "")
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
        .replace("/", "_")
    )
    return _STATUS_ALIASES.get(key, key)


def _is_spam_status(key: str, raw: str) -> bool:
    if key in SPAM_STATUSES:
        return True
    blob = f"{key} {raw}".lower()
    return any(token in blob for token in _SPAM_KEYWORDS)


def _is_raw_lead_status(key: str, raw: str = "") -> bool:
    if key in RAW_LEAD_STATUSES:
        return True
    blob = f"{key} {raw}".lower()
    return any(
        token in blob
        for token in (
            "новая заяв",
            "неразобран",
            "raw_lead",
            "new_lead",
            "unprocessed",
        )
    )


def is_oci_status_allowed(status: str) -> tuple[bool, str, str]:
    """
    Guard: return (allowed, reason, normalized_key).
    Only qualified / closed_won buckets may proceed to Google Ads.
    """
    raw = str(status or "").strip()
    key = _normalize_status_key(raw)
    if _is_raw_lead_status(key, raw):
        return False, "raw_or_new_lead", key
    if _is_spam_status(key, raw):
        return False, "spam_or_blocked", key
    if key in CLOSED_WON_STATUSES:
        return True, "closed_won", key
    if key in QUALIFIED_STATUSES:
        return True, "qualified", key
    return False, "status_not_allowed", key


def _default_qualified_value() -> float:
    """Default conversion value for Qualified Lead when CRM has no amount."""
    for name in ("GOOGLE_ADS_DEFAULT_QUALIFIED_VALUE", "GOOGLE_ADS_QUALIFIED_VALUE"):
        raw = _env_ads(name)
        if raw:
            try:
                return float(raw)
            except ValueError:
                continue
    return 1.0


def ads_config_status() -> dict[str, Any]:
    """
    Diagnostic view of Ads OCI readiness (no secret values).
    Used by /health and early-return error messages.
    """
    creds = get_ads_oauth_credentials()
    enabled = bool(creds["enabled"])
    mode = str(creds.get("auth_mode") or "oauth")
    # Spam conversion action is optional — OCI bridge never uploads spam/raw.
    action_checks = {
        "GOOGLE_ADS_CONVERSION_ACTION_QUALIFIED": bool(
            _env_first(
                "GOOGLE_ADS_CONVERSION_ACTION_QUALIFIED", "GOOGLE_ADS_CONV_QUALIFIED"
            )
        ),
        "GOOGLE_ADS_CONVERSION_ACTION_CLOSED_WON": bool(
            _env_first(
                "GOOGLE_ADS_CONVERSION_ACTION_CLOSED_WON", "GOOGLE_ADS_CONV_SALE"
            )
        ),
    }
    if mode == "service_account":
        checks = {
            "GOOGLE_ADS_ENABLED": enabled,
            "GOOGLE_ADS_DEVELOPER_TOKEN": _is_real_secret(creds["developer_token"]),
            "GOOGLE_ADS_SERVICE_ACCOUNT_FILE": bool(creds.get("service_account_file")),
            "GOOGLE_ADS_CUSTOMER_ID": bool(creds["customer_id"]),
            **action_checks,
        }
    else:
        checks = {
            "GOOGLE_ADS_ENABLED": enabled,
            "GOOGLE_ADS_DEVELOPER_TOKEN": _is_real_secret(creds["developer_token"]),
            "GOOGLE_ADS_CLIENT_ID": _is_real_secret(creds["client_id"]),
            "GOOGLE_ADS_CLIENT_SECRET": _is_real_secret(creds["client_secret"]),
            "GOOGLE_ADS_REFRESH_TOKEN": _is_real_secret(creds["refresh_token"]),
            "GOOGLE_ADS_CUSTOMER_ID": bool(creds["customer_id"]),
            **action_checks,
        }
    missing = [k for k, ok in checks.items() if not ok]
    if not enabled:
        reason = "GOOGLE_ADS_ENABLED=false"
    elif missing:
        reason = "missing_or_placeholder:" + ",".join(missing)
    else:
        reason = "ok"
    return {
        "configured": (not missing) and enabled,
        "enabled": enabled,
        "auth_mode": mode,
        "reason": reason,
        "missing": missing,
        "customer_id": creds["customer_id"] or None,
        "login_customer_id": creds["login_customer_id"] or None,
        "service_account_file": creds.get("service_account_file") or None,
        "service_account_email": creds.get("service_account_email") or None,
        "refresh_token_env": creds["refresh_token_env"],
        "api_version": creds["api_version"],
        "client_id_masked": _mask_secret(creds["client_id"], keep_start=10, keep_end=6),
        "refresh_token_masked": _mask_secret(
            creds["refresh_token"], keep_start=8, keep_end=6
        ),
    }


def ads_configured() -> bool:
    return bool(ads_config_status().get("configured"))


def _currency() -> str:
    return str(os.getenv("GOOGLE_ADS_CURRENCY_CODE") or "UZS").strip().upper() or "UZS"


def _default_sale_value() -> float:
    """Default Closed-won value when deal amount missing / ≤ 0 (default 1.0)."""
    try:
        raw = os.getenv("GOOGLE_ADS_DEFAULT_SALE_VALUE")
        if raw is None or str(raw).strip() == "":
            return 1.0
        return float(raw)
    except ValueError:
        return 1.0


def resolve_conversion_for_status(
    status: str,
    *,
    conversion_value: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """
    Map CRM lead quality status → Google Ads conversion (STRICT filter).

    Allowed buckets only:
      qualified  → Qualified_Lead  (…/7815437037, default value 1.0)
      closed_won → Closed_Deal     (…/7815489823, deal amount + UZS)

    Spam / raw / refuse / archive / unknown → None (do not upload).
    """
    raw = str(status or "").strip()
    allowed, reason, key = is_oci_status_allowed(raw)
    currency = _currency()

    if not allowed:
        return None

    # Closed_Deal first (won / paid must not fall into qualified)
    if key in CLOSED_WON_STATUSES or reason == "closed_won":
        action = (
            _env_first(
                "GOOGLE_ADS_CONVERSION_ACTION_CLOSED_WON",
                "GOOGLE_ADS_CONV_SALE",
            )
            or _DEFAULT_ACTION_CLOSED_WON
        )
        value = conversion_value if conversion_value is not None and conversion_value > 0 else None
        if value is None:
            value = _default_sale_value()
        if value is None or value <= 0:
            value = 1.0
        return {
            "action": action,
            "value": float(value),
            "currency": currency or "UZS",
            "bucket": "closed_won",
            "conversion_name": "Closed_Deal",
            "status_key": key,
        }

    if key in QUALIFIED_STATUSES or reason == "qualified":
        action = (
            _env_first(
                "GOOGLE_ADS_CONVERSION_ACTION_QUALIFIED",
                "GOOGLE_ADS_CONV_QUALIFIED",
            )
            or _DEFAULT_ACTION_QUALIFIED
        )
        if conversion_value is not None and conversion_value > 0:
            value = float(conversion_value)
        else:
            value = _default_qualified_value()
            if value <= 0:
                value = 1.0
        return {
            "action": action,
            "value": float(value),
            "currency": currency or "UZS",
            "bucket": "qualified",
            "conversion_name": "Qualified_Lead",
            "status_key": key,
        }

    return None


# gclid embedded in CRM activity / comment text, e.g. "gclid: CjwKCAjw_…"
_GCLID_IN_TEXT_RE = re.compile(
    r"(?i)\bgclid\s*[:=]\s*([A-Za-z0-9_\-]{10,150})"
)
_GBRAID_IN_TEXT_RE = re.compile(
    r"(?i)\bgbraid\s*[:=]\s*([A-Za-z0-9_\-~]{10,150})"
)
_WBRAID_IN_TEXT_RE = re.compile(
    r"(?i)\bwbraid\s*[:=]\s*([A-Za-z0-9_\-~]{10,150})"
)


def _iter_text_blobs(lead: Mapping[str, Any]) -> list[str]:
    """Collect free-text fields where gclid may be pasted (activity, comments, …)."""
    blobs: list[str] = []

    def _walk_list_or_map(node: Any) -> None:
        if isinstance(node, Mapping):
            items = node.values()
        elif isinstance(node, list):
            items = node
        else:
            return
        for entry in items:
            if isinstance(entry, Mapping):
                for field in ("text", "comment", "message", "body", "note", "description"):
                    val = entry.get(field)
                    if val:
                        blobs.append(str(val))
            elif entry:
                blobs.append(str(entry))

    for key in ("activity", "comments", "statusHistory", "notes", "timeline"):
        _walk_list_or_map(lead.get(key))

    # Flat string fields sometimes hold attribution dump
    for key in ("comment", "notes", "description", "sourceDetails"):
        val = lead.get(key)
        if val:
            blobs.append(str(val))
    return blobs


def _click_id_from_text(lead: Mapping[str, Any], key: str) -> str:
    """Parse gclid/gbraid/wbraid from activity/comment text when top-level field is empty."""
    pattern = {
        "gclid": _GCLID_IN_TEXT_RE,
        "gbraid": _GBRAID_IN_TEXT_RE,
        "wbraid": _WBRAID_IN_TEXT_RE,
    }.get(key)
    if not pattern:
        return ""
    for blob in _iter_text_blobs(lead):
        m = pattern.search(blob)
        if m:
            found = m.group(1).strip()
            if found:
                log.info(
                    "Extracted %s from lead text (activity/comments): %s…",
                    key,
                    found[:16],
                )
                return found[:150]
    return ""


def _click_id_from_lead(lead: Mapping[str, Any], key: str) -> str:
    attr = lead.get("marketingAttribution") or {}
    wl = lead.get("websiteLead") or {}
    for src in (lead, attr, wl):
        if not isinstance(src, Mapping):
            continue
        raw = str(src.get(key) or "").strip()
        if raw:
            return raw[:150]
    # Fallback: parse from activity / comments (CRM often stores gclid only in text)
    return _click_id_from_text(lead, key)


def extract_gclid(lead: Mapping[str, Any]) -> str:
    return _click_id_from_lead(lead, "gclid")


def extract_click_identifiers(lead: Mapping[str, Any]) -> dict[str, str]:
    """Prefer gclid; fall back to gbraid / wbraid (iOS / Privacy Sandbox)."""
    gclid = _click_id_from_lead(lead, "gclid")
    gbraid = _click_id_from_lead(lead, "gbraid")
    wbraid = _click_id_from_lead(lead, "wbraid")
    out: dict[str, str] = {}
    if gclid:
        out["gclid"] = gclid
    elif gbraid:
        out["gbraid"] = gbraid
    elif wbraid:
        out["wbraid"] = wbraid
    return out


def hydrate_lead_click_ids(lead: Mapping[str, Any]) -> dict[str, Any]:
    """
    Return a shallow copy of lead with gclid/gbraid/wbraid filled from nested
    sources / activity text when top-level fields are empty.
    """
    doc = dict(lead)
    ids = extract_click_identifiers(doc)
    for k, v in ids.items():
        if v and not str(doc.get(k) or "").strip():
            doc[k] = v
        # Also mirror into marketingAttribution for downstream readers
        attr = doc.get("marketingAttribution")
        if not isinstance(attr, dict):
            attr = {}
            doc["marketingAttribution"] = attr
        if v and not str(attr.get(k) or "").strip():
            attr[k] = v
    return doc


def extract_conversion_value(lead: Mapping[str, Any]) -> Optional[float]:
    """
    Pull deal / offer amount from incoming_leads (and nested CRM shapes).

    Checked top-level and nested: commercial, websiteLead, deal, qualification.
    """
    flat_keys = (
        "offerAmount",
        "budget",
        "conversionValue",
        "dealAmount",
        "amount",
        "amountUsd",
        "amountUSD",
        "amountUzs",
        "amountUZS",
        "saleAmount",
        "contractAmount",
        "revenue",
        "price",
    )

    def _from_mapping(src: Any) -> Optional[float]:
        if not isinstance(src, Mapping):
            return None
        for key in flat_keys:
            try:
                val = float(src.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if val > 0:
                return val
        return None

    for src in (
        lead,
        lead.get("commercial"),
        lead.get("websiteLead"),
        lead.get("deal"),
        lead.get("qualification"),
        lead.get("marketingAttribution"),
    ):
        found = _from_mapping(src)
        if found is not None:
            return found
    return None


def _format_conversion_date_time(ms: Optional[int] = None) -> str:
    """Google Ads expects 'yyyy-mm-dd hh:mm:ss+|-hh:mm' (timezone offset)."""
    if ms and ms > 0:
        dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    # UTC offset form required by uploadClickConversions
    return dt.strftime("%Y-%m-%d %H:%M:%S+00:00")


def _parse_oauth_error_body(resp: httpx.Response) -> dict[str, Any]:
    """Extract Google OAuth error JSON (error / error_description) from a response."""
    raw_text = (resp.text or "")[:4000]
    err_json: dict[str, Any] = {}
    try:
        parsed = resp.json()
        if isinstance(parsed, dict):
            err_json = parsed
    except Exception:  # noqa: BLE001
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                err_json = parsed
        except Exception:  # noqa: BLE001
            err_json = {"raw": raw_text}
    return {
        "status_code": resp.status_code,
        "error": err_json.get("error"),
        "error_description": err_json.get("error_description"),
        "error_uri": err_json.get("error_uri"),
        "body_json": err_json,
        "body_text": raw_text,
    }


def _log_oauth_http_failure(
    resp: httpx.Response,
    *,
    client_id_masked: str,
    client_secret_masked: str,
    refresh_masked: str,
    refresh_source: str,
) -> dict[str, Any]:
    info = _parse_oauth_error_body(resp)
    oauth_error = str(info.get("error") or "")
    oauth_desc = str(info.get("error_description") or "")
    clear_ads_token_cache("oauth HTTP %s" % info["status_code"])

    log.error(
        "Google OAuth token refresh FAILED HTTP %s | error=%r | error_description=%r | "
        "error_uri=%r | body_json=%s | CLIENT_ID=%s CLIENT_SECRET=%s "
        "REFRESH_TOKEN=%s (from %s)",
        info["status_code"],
        oauth_error,
        oauth_desc,
        info.get("error_uri"),
        json.dumps(info.get("body_json") or {}, ensure_ascii=False)[:2000],
        client_id_masked,
        client_secret_masked,
        refresh_masked,
        refresh_source,
    )
    hints = {
        "invalid_grant": (
            "Refresh token revoked/expired, wrong client, or Playground token not "
            "exchanged for offline access. Re-issue GOOGLE_ADS_REFRESH_TOKEN for THIS "
            "GOOGLE_ADS_CLIENT_ID (Web client) with access_type=offline & prompt=consent."
        ),
        "unauthorized_client": (
            "This CLIENT_ID cannot use grant_type=refresh_token (Desktop vs Web mismatch, "
            "or secret does not belong to the client). Align Playground client with .env."
        ),
        "invalid_client": (
            "CLIENT_ID or CLIENT_SECRET rejected (401/invalid_client). Check pair from "
            "Google Cloud Console → APIs & Services → Credentials."
        ),
        "invalid_request": "Malformed token request — grant_type/fields incomplete.",
    }
    if info["status_code"] in (400, 401):
        log.error(
            "Google OAuth %s hint: %s",
            info["status_code"],
            hints.get(oauth_error, "See error + error_description JSON above."),
        )
    return info


def _refresh_service_account_token_sync(creds: Mapping[str, Any]) -> str:
    """
    JWT → access_token via google-auth (NOT Web OAuth client_id/secret).

    Still talks to Google's token endpoint with grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer,
    which does not use GOOGLE_ADS_CLIENT_ID and avoids invalid_client from a deleted Web client.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "google-auth is required for service account auth. "
            "pip install google-auth"
        ) from exc

    path = str(creds.get("service_account_file") or "")
    if not path or not Path(path).is_file():
        raise RuntimeError(
            "Service account JSON not found. Place google_key.json in app/ "
            "or set GOOGLE_ADS_SERVICE_ACCOUNT_FILE"
        )

    subject = str(creds.get("impersonate_user") or "").strip() or None
    # adwords (Ads diagnostics) + datamanager (events:ingest) — both required
    credentials = service_account.Credentials.from_service_account_file(
        path,
        scopes=list(ADS_SCOPES),
        subject=subject,
    )
    sa_email = creds.get("service_account_email") or _service_account_email(Path(path))
    log.info(
        "Google Ads SA token request (JWT) file=%s email=%s impersonate=%s "
        "scopes=%s login_customer_id=%s customer_id=%s",
        path,
        sa_email,
        subject or "<none>",
        list(ADS_SCOPES),
        creds.get("login_customer_id") or "<none>",
        creds.get("customer_id") or "<none>",
    )
    credentials.refresh(Request())
    token = str(credentials.token or "")
    if not token:
        raise RuntimeError("Service account refresh returned empty access_token")
    # google-auth expiry is datetime; cache ~55 min if missing
    expires_at = time.time() + 3300
    if getattr(credentials, "expiry", None) is not None:
        try:
            expires_at = credentials.expiry.timestamp()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    _token_cache["access_token"] = token
    _token_cache["expires_at"] = float(expires_at)

    # Verify token scope via tokeninfo (best-effort; never block on failure)
    token_scope = ""
    try:
        with httpx.Client(timeout=10.0) as probe:
            ti = probe.get(TOKENINFO_URL, params={"access_token": token})
        if ti.status_code < 400:
            token_scope = str((ti.json() or {}).get("scope") or "")
    except Exception as ti_exc:  # noqa: BLE001
        log.warning("tokeninfo probe failed: %s", ti_exc)
    got_scopes = set(token_scope.split()) if token_scope else set()
    missing = [s for s in ADS_SCOPES if token_scope and s not in got_scopes]
    if missing:
        log.error(
            "SA access_token missing required scope(s) %s; got scope=%s",
            missing,
            token_scope,
        )
    else:
        log.info(
            "Google Ads SA token OK token=%s expires_at=%s scope=%s",
            _mask_secret(token, keep_start=6, keep_end=4),
            expires_at,
            token_scope or " ".join(ADS_SCOPES),
        )
    return token


async def _get_access_token_oauth_user(client: httpx.AsyncClient, creds: dict[str, Any]) -> str:
    """Legacy Web-client refresh_token exchange (may hit invalid_client)."""
    masked = log_oauth_credentials_startup()
    client_id = creds["client_id"]
    client_secret = creds["client_secret"]
    refresh_token = creds["refresh_token"]
    secret_mask = _mask_secret(client_secret, keep_start=6, keep_end=4)

    if not client_id or not client_secret or not refresh_token:
        raise RuntimeError(
            "OAuth credentials incomplete: "
            f"client_id={bool(client_id)} client_secret={bool(client_secret)} "
            f"refresh_token={bool(refresh_token)} source={creds['refresh_token_env']}"
        )

    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    log.info(
        "Google OAuth token request → %s grant_type=refresh_token "
        "CLIENT_ID=%s CLIENT_SECRET=%s (first6…last4) REFRESH_TOKEN=%s "
        "source=%s env_files=%s",
        TOKEN_URL,
        masked["client_id"],
        secret_mask,
        masked["refresh_token"],
        creds["refresh_token_env"],
        creds.get("env_files"),
    )

    try:
        resp = await client.post(
            TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20.0,
        )
    except httpx.RequestError as exc:
        log.exception(
            "Google OAuth token request NETWORK error to %s: %s | CLIENT_ID=%s",
            TOKEN_URL,
            exc,
            masked["client_id"],
        )
        raise

    if resp.status_code >= 400:
        info = _log_oauth_http_failure(
            resp,
            client_id_masked=masked["client_id"],
            client_secret_masked=secret_mask,
            refresh_masked=masked["refresh_token"],
            refresh_source=str(creds["refresh_token_env"]),
        )
        detail = str(info.get("error") or f"HTTP {resp.status_code}")
        if info.get("error_description"):
            detail = f"{detail}: {info['error_description']}"
        raise httpx.HTTPStatusError(
            f"Google OAuth token error: {detail}",
            request=resp.request,
            response=resp,
        )

    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.error(
            "Google OAuth token response is not JSON: HTTP %s body=%s",
            resp.status_code,
            (resp.text or "")[:1000],
        )
        raise RuntimeError(f"OAuth token response not JSON: {exc}") from exc

    token = str(body.get("access_token") or "")
    if not token:
        raise RuntimeError("OAuth token response missing access_token")
    expires_in = int(body.get("expires_in") or 3600)
    _token_cache["access_token"] = token
    _token_cache["expires_at"] = time.time() + expires_in
    log.info(
        "Google OAuth token refresh OK expires_in=%ss token=%s",
        expires_in,
        _mask_secret(token, keep_start=6, keep_end=4),
    )
    return token


async def _get_access_token(client: httpx.AsyncClient) -> str:
    """
    Obtain Google Ads API access token.

    Default: Service Account JWT (google_key.json) — no Web OAuth client.
    Fallback: GOOGLE_ADS_AUTH_MODE=oauth → refresh_token grant.
    """
    import asyncio

    creds = get_ads_oauth_credentials()
    log_oauth_credentials_startup()

    now = time.time()
    cached = _token_cache.get("access_token")
    if cached and float(_token_cache.get("expires_at") or 0) > now + 60:
        return str(cached)

    mode = str(creds.get("auth_mode") or "oauth")
    if mode == "service_account":
        try:
            return await asyncio.to_thread(_refresh_service_account_token_sync, creds)
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "Google Ads service account token failed: %s | file=%s email=%s",
                exc,
                creds.get("service_account_file"),
                creds.get("service_account_email"),
            )
            raise

    return await _get_access_token_oauth_user(client, creds)


async def upload_click_conversion(
    *,
    gclid: str = "",
    gbraid: str = "",
    wbraid: str = "",
    conversion_action: str,
    conversion_date_time: str,
    conversion_value: float = 0.0,
    currency_code: str = "UZS",
    order_id: str = "",
) -> dict[str, Any]:
    """
    Upload an offline click conversion via Data Manager API events:ingest.

    Returns {ok, partial_failure?, raw?, error?, google_request_id?, transport}.
    Keeps the historical function name used by oci_sync / CRM hooks.
    """
    result: dict[str, Any] = {
        "ok": False,
        "error": None,
        "transport": "datamanager.events:ingest",
    }
    if not ads_configured():
        status = ads_config_status()
        result["error"] = (
            "Google Ads not configured or GOOGLE_ADS_ENABLED=false"
            f" ({status.get('reason')})"
        )
        result["config"] = status
        return result
    click_id = (gclid or gbraid or wbraid or "").strip()
    if not click_id:
        result["error"] = "gclid/gbraid/wbraid is empty"
        return result
    if not conversion_action:
        result["error"] = "conversion_action is empty"
        return result

    oauth = get_ads_oauth_credentials()
    customer_id = _digits_id(oauth["customer_id"])
    configured_login = _digits_id(oauth.get("login_customer_id") or "") or _login_customer_id()
    developer_token = _clean_env_value(oauth["developer_token"])
    if not customer_id:
        result["error"] = "GOOGLE_ADS_CUSTOMER_ID is empty"
        return result

    action = str(conversion_action or "").strip()
    action_id = _conversion_action_numeric_id(action)
    if not action_id:
        result["error"] = f"Invalid conversion_action (no numeric id): {action}"
        return result

    # Align resource name customer with operating account when present
    if action.startswith("customers/") and f"customers/{customer_id}/" not in action:
        parts = action.split("/")
        if len(parts) >= 4 and parts[0] == "customers":
            parts[1] = customer_id
            action = "/".join(parts)

    url = build_datamanager_ingest_url()

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            try:
                token = await _get_access_token(client)
            except httpx.HTTPStatusError as oauth_exc:
                err_info = _parse_oauth_error_body(oauth_exc.response)
                result["error"] = (
                    f"OAuth HTTP {err_info.get('status_code')}: "
                    f"{err_info.get('error')}: {err_info.get('error_description')}"
                )
                result["oauth_error"] = err_info
                return result
            except Exception as oauth_exc:  # noqa: BLE001
                result["error"] = f"OAuth exception: {oauth_exc}"
                log.exception("OAuth before Data Manager ingest failed: %s", oauth_exc)
                return result

            login_cid = ""
            if developer_token:
                accessible: list[str] = []
                try:
                    accessible = await list_accessible_customer_ids(
                        token, developer_token
                    )
                except Exception as list_exc:  # noqa: BLE001
                    log.warning("listAccessibleCustomers probe failed: %s", list_exc)
                try:
                    login_cid = await resolve_login_customer_id_for_upload(
                        access_token=token,
                        developer_token=developer_token,
                        customer_id=customer_id,
                        configured_login=configured_login,
                        accessible=accessible,
                    )
                except Exception as login_exc:  # noqa: BLE001
                    log.warning("resolve login for DM ingest failed: %s", login_exc)
                    login_cid = ""
            elif configured_login and configured_login != customer_id:
                login_cid = configured_login

            payload = build_datamanager_ingest_payload(
                customer_id=customer_id,
                conversion_action=action,
                gclid=gclid,
                gbraid=gbraid,
                wbraid=wbraid,
                conversion_date_time=conversion_date_time,
                conversion_value=float(conversion_value),
                currency_code=currency_code or _currency(),
                order_id=order_id,
                login_customer_id=login_cid,
            )
            headers = build_datamanager_request_headers(
                access_token=token,
                login_customer_id=login_cid,
            )

            if not str(headers.get("Authorization") or "").startswith("Bearer "):
                result["error"] = "Internal: Authorization Bearer header missing"
                return result

            log.info(
                "Data Manager events:ingest → %s "
                "headers=[Authorization=Bearer ***, x-goog-user-project=%s, "
                "login-account=%s] scopes=%s operatingAccount=%s "
                "productDestinationId=%s eventTimestamp=%s click=%s…",
                url,
                headers.get("x-goog-user-project") or "<none>",
                headers.get("login-account") or "<none>",
                list(ADS_SCOPES),
                customer_id,
                action_id,
                (payload.get("events") or [{}])[0].get("eventTimestamp"),
                click_id[:12],
            )

            resp = await client.post(url, headers=headers, json=payload)
            text = resp.text
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001
                body = {"raw": text[:1000]}

            # Data Manager returns request_id in the body (not only headers)
            body_request_id = ""
            if isinstance(body, dict):
                body_request_id = str(body.get("requestId") or body.get("request_id") or "")

            result["request"] = {
                "url": url,
                "customer_id": customer_id,
                "login_customer_id": login_cid or None,
                "conversion_action_id": action_id,
                "transport": "datamanager.events:ingest",
                "scopes": list(ADS_SCOPES),
                "payload_preview": {
                    "destinations": payload.get("destinations"),
                    "event": {
                        k: v
                        for k, v in ((payload.get("events") or [{}])[0]).items()
                        if k != "userData"
                    },
                },
            }

            if resp.status_code >= 400:
                result["error"] = _extract_google_ads_error(body, resp.status_code, text)
                diag = _log_ads_api_failure(
                    operation="datamanager.events:ingest",
                    url=url,
                    req_headers=headers,
                    resp=resp,
                    body=body,
                    error=result["error"] or "unknown",
                )
                result["raw"] = body
                result["google_request_id"] = body_request_id or diag.get("request_id")
                result["response_headers"] = diag.get("response_headers")

                # Retry once without MCC login if permission denied
                err_l = (result["error"] or "").upper()
                if login_cid and (
                    "PERMISSION_DENIED" in err_l or "USER_PERMISSION_DENIED" in err_l
                ):
                    log.warning(
                        "Retrying events:ingest without loginAccount after "
                        "PERMISSION_DENIED request_id=%s",
                        result.get("google_request_id") or "<none>",
                    )
                    payload2 = build_datamanager_ingest_payload(
                        customer_id=customer_id,
                        conversion_action=action,
                        gclid=gclid,
                        gbraid=gbraid,
                        wbraid=wbraid,
                        conversion_date_time=conversion_date_time,
                        conversion_value=float(conversion_value),
                        currency_code=currency_code or _currency(),
                        order_id=order_id,
                        login_customer_id="",
                    )
                    headers2 = build_datamanager_request_headers(
                        access_token=token,
                        login_customer_id="",
                    )
                    resp2 = await client.post(url, headers=headers2, json=payload2)
                    try:
                        body2 = resp2.json()
                    except Exception:  # noqa: BLE001
                        body2 = {"raw": (resp2.text or "")[:1000]}
                    rid2 = ""
                    if isinstance(body2, dict):
                        rid2 = str(body2.get("requestId") or body2.get("request_id") or "")
                    result["retried_without_login_customer_id"] = True
                    if resp2.status_code < 400:
                        warnings = []
                        if isinstance(body2, dict):
                            warnings = body2.get("fieldWarnings") or body2.get(
                                "field_warnings"
                            ) or []
                        if warnings:
                            result["ok"] = True
                            result["partial_failure"] = True
                            result["error"] = f"field_warnings: {str(warnings)[:500]}"
                            log.warning(
                                "events:ingest OK with field_warnings request_id=%s: %s",
                                rid2 or "<none>",
                                result["error"],
                            )
                        else:
                            result["ok"] = True
                            result["error"] = None
                            log.info(
                                "events:ingest OK after retry without login "
                                "request_id=%s",
                                rid2 or "<none>",
                            )
                        result["raw"] = body2
                        result["google_request_id"] = rid2 or _ads_response_meta(
                            resp2
                        ).get("request_id")
                        result["request"]["login_customer_id"] = None
                        return result
                    result["error"] = _extract_google_ads_error(
                        body2, resp2.status_code, resp2.text or ""
                    )
                    diag2 = _log_ads_api_failure(
                        operation="datamanager.events:ingest(retry-no-mcc)",
                        url=url,
                        req_headers=headers2,
                        resp=resp2,
                        body=body2,
                        error=result["error"] or "unknown",
                    )
                    result["raw"] = body2
                    result["google_request_id"] = rid2 or diag2.get("request_id")
                    result["response_headers"] = diag2.get("response_headers")
                return result

            # Success path — DM has no partialFailureError; fieldWarnings ≈ soft fail
            warnings = []
            if isinstance(body, dict):
                warnings = body.get("fieldWarnings") or body.get("field_warnings") or []
            meta = _ads_response_meta(resp)
            result["google_request_id"] = body_request_id or meta.get("request_id")
            result["raw"] = body
            if warnings:
                result["ok"] = True
                result["partial_failure"] = True
                result["error"] = f"field_warnings: {str(warnings)[:500]}"
                log.warning(
                    "events:ingest accepted with field_warnings request_id=%s: %s",
                    result.get("google_request_id") or "<none>",
                    result["error"],
                )
                return result

            result["ok"] = True
            log.info(
                "Data Manager conversion ingested click=%s… action=%s value=%s "
                "request_id=%s",
                click_id[:12],
                action_id,
                conversion_value,
                result.get("google_request_id") or "<none>",
            )
            return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        log.exception("upload_click_conversion (Data Manager) exception: %s", exc)
        return result


def _truthy_flag(val: Any) -> bool:
    return val is True or str(val).strip().lower() in {"1", "true", "yes", "y"}


def _bucket_already_sent(lead: Mapping[str, Any], bucket: str, action: str = "") -> bool:
    """
    Dedup: each conversion type (qualified / closed_won) once per deal.
    Checks ociSentByBucket, ociSentByStatus[bucket], and last success marker.
    """
    bucket = str(bucket or "").strip()
    if not bucket:
        return False

    sent_bucket = lead.get("ociSentByBucket") or {}
    if isinstance(sent_bucket, Mapping) and _truthy_flag(sent_bucket.get(bucket)):
        return True

    # Prefer bucket key; also accept legacy flags under same name
    sent_by = lead.get("ociSentByStatus") or {}
    if isinstance(sent_by, Mapping) and _truthy_flag(sent_by.get(bucket)):
        return True

    prev = lead.get("googleAdsConversion") or {}
    if (
        isinstance(prev, Mapping)
        and prev.get("ok")
        and not prev.get("skipped")
        and str(prev.get("bucket") or "") == bucket
    ):
        if not action or str(prev.get("action") or "") == action:
            return True
    return False


async def send_offline_conversion_for_lead(
    lead: Mapping[str, Any],
    *,
    status: str,
    lead_id: str = "",
    conversion_time_ms: Optional[int] = None,
) -> dict[str, Any]:
    """
    High-level OCI bridge: STRICT status filter → action/value → upload.

    Only Qualified_Lead / Closed_Deal leave for Google Ads. Spam/raw/refuse
    are ignored. Safe to call always — never raises into CRM.
    """
    deal_id = str(lead_id or lead.get("id") or "")
    status_name = str(status or "").strip()
    out: dict[str, Any] = {
        "ok": False,
        "skipped": False,
        "lead_id": deal_id,
        "status": status_name,
        "gclid": None,
        "bucket": None,
        "error": None,
        "display_name": None,
        "conversion_name": None,
    }

    # --- Guard: block spam / raw / non-allowed before any Ads network call ---
    allowed, deny_reason, norm_key = is_oci_status_allowed(status_name)
    if not allowed:
        out["skipped"] = True
        out["error"] = f"oci_ignored:{deny_reason}"
        log.info(
            "[OCI Ignored] Deal ID %s: status '%s' is not allowed for Google Ads.",
            deal_id or "<unknown>",
            status_name or norm_key or "<empty>",
        )
        return out

    cfg = ads_config_status()
    if not cfg.get("configured"):
        out["skipped"] = True
        out["error"] = "ads_disabled_or_unconfigured"
        out["config"] = cfg
        log.warning(
            "OCI skip lead=%s status=%s — %s missing=%s",
            deal_id,
            status_name,
            cfg.get("reason"),
            cfg.get("missing"),
        )
        return out

    # Fill gclid from activity/comments when top-level field is empty
    lead = hydrate_lead_click_ids(lead)
    click_ids = extract_click_identifiers(lead)
    gclid = click_ids.get("gclid", "")
    out["gclid"] = (gclid[:12] + "…") if gclid else None
    out["click_id_type"] = next(iter(click_ids), None)
    if not click_ids:
        out["skipped"] = True
        out["error"] = "no_gclid"
        log.info(
            "OCI skip lead=%s status=%s — no gclid/gbraid/wbraid on lead "
            "(checked fields + activity text)",
            deal_id,
            status_name,
        )
        return out

    value = extract_conversion_value(lead)
    mapping = resolve_conversion_for_status(status_name, conversion_value=value)
    if not mapping or mapping.get("bucket") not in OCI_ALLOWED_BUCKETS:
        out["skipped"] = True
        out["error"] = "status_not_mapped_or_action_missing"
        log.info(
            "[OCI Ignored] Deal ID %s: status '%s' is not allowed for Google Ads.",
            deal_id or "<unknown>",
            status_name or "<empty>",
        )
        return out

    bucket = str(mapping["bucket"])
    conversion_name = str(
        mapping.get("conversion_name")
        or CONVERSION_ACTION_DISPLAY_NAMES.get(bucket)
        or bucket
    )
    out["bucket"] = bucket
    out["display_name"] = conversion_name
    out["conversion_name"] = conversion_name
    status_key = str(mapping.get("status_key") or norm_key or status_name)

    # Dedup per conversion TYPE (Qualified_Lead / Closed_Deal) — once each
    if _bucket_already_sent(lead, bucket, action=str(mapping.get("action") or "")):
        out["skipped"] = True
        out["ok"] = True
        out["error"] = "already_uploaded"
        log.info(
            "[OCI Ignored] Deal ID %s: status '%s' conversion '%s' already sent "
            "(dedupe by bucket=%s).",
            deal_id or "<unknown>",
            status_name,
            conversion_name,
            bucket,
        )
        return out

    log.info(
        "OCI upload lead=%s status=%s bucket=%s conversion=%s action=%s value=%s %s click=%s…",
        deal_id,
        status_name,
        bucket,
        conversion_name,
        mapping["action"].split("/")[-1] if "/" in mapping["action"] else mapping["action"],
        mapping["value"],
        mapping.get("currency") or "UZS",
        (gclid or next(iter(click_ids.values()), ""))[:12],
    )

    # Unique per deal+bucket so Qualified_Lead and Closed_Deal stay distinct
    base_id = deal_id or "lead"
    order_id = f"{base_id}-{bucket}"[:100]
    try:
        uploaded = await upload_click_conversion(
            gclid=click_ids.get("gclid", ""),
            gbraid=click_ids.get("gbraid", ""),
            wbraid=click_ids.get("wbraid", ""),
            conversion_action=mapping["action"],
            conversion_date_time=_format_conversion_date_time(conversion_time_ms),
            conversion_value=float(mapping["value"]),
            currency_code=str(mapping.get("currency") or "UZS"),
            order_id=order_id,
        )
    except Exception as exc:  # noqa: BLE001 — never break CRM status flow
        out["error"] = str(exc)
        log.exception("send_offline_conversion_for_lead unexpected error: %s", exc)
        return out

    out["ok"] = bool(uploaded.get("ok"))
    out["error"] = uploaded.get("error")
    out["partial_failure"] = bool(uploaded.get("partial_failure"))
    out["google_request_id"] = uploaded.get("google_request_id")
    out["transport"] = uploaded.get("transport") or "datamanager.events:ingest"
    out["upload"] = {
        "action": mapping["action"],
        "value": mapping["value"],
        "currency": mapping.get("currency") or "UZS",
        "display_name": conversion_name,
        "conversion_name": conversion_name,
        "transport": out["transport"],
        "request_id": out.get("google_request_id"),
        "status_key": status_key,
        "bucket": bucket,
    }
    if uploaded.get("raw") is not None:
        out["raw"] = uploaded.get("raw")

    if out["ok"]:
        log.info(
            "[OCI Sent] Deal ID %s: Conversion '%s' successfully sent to Google Ads.",
            deal_id or "<unknown>",
            conversion_name,
        )
    else:
        log.warning(
            "[OCI Failed] Deal ID %s: Conversion '%s' err=%s",
            deal_id or "<unknown>",
            conversion_name,
            out.get("error"),
        )
    return out
