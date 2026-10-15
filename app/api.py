"""
CRM lead intake + Round-Robin distribution for Tikkurila website.

Firebase RTDB layout:
  /incoming_leads/{leadId}           — общий банк заявок (статус «Новый лид»)
  /deals/{leadId}                    — сделка в B2B-канбане (колонка «Новый лид» / cold)
  /partner_leads/{partnerId}/{id}    — копия в разделе назначенного посредника
  /lead_distribution/partners/{id}   — очередь посредников (фермеры/дилеры)
  /lead_distribution/cursor          — индекс Round-Robin
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

import httpx

log = logging.getLogger("leads.crm")

LEAD_STATUS = "new_lead"
LEAD_STATUS_LABEL = "Новый лид"
# B2B funnel id so the card is visible in the first kanban column
LEAD_STAGE = "cold"

# Professional lead quality statuses (Google Ads / Lead Quality Tracking)
# Exactly 3 working CRM / OCI statuses (UI + Firebase write keys)
LEAD_QUALITY_STATUSES: dict[str, str] = {
    "spam": "Спам / Нецелевой",
    "qualified": "Квалифицированный лид",
    "closed_won": "Успешная сделка / Продажа",
}
DEFAULT_LEAD_QUALITY = "qualified"
DEFAULT_LEAD_QUALITY_LABEL = LEAD_QUALITY_STATUSES[DEFAULT_LEAD_QUALITY]

# Legacy Firebase keys → one of the 3 working statuses
LEAD_QUALITY_LEGACY_MAP: dict[str, str] = {
    "spam": "spam",
    "non_targeted": "spam",
    "nontargeted": "spam",
    "not_targeted": "spam",
    "disqualified": "spam",
    "targeted": "qualified",
    "qualified": "qualified",
    "qualified_lead": "qualified",
    "qual": "qualified",
    "cold": "qualified",
    "warm": "qualified",
    "negotiation": "qualified",
    "checkout": "qualified",
    "closed_won": "closed_won",
    "closedwon": "closed_won",
    "won": "closed_won",
    "successful": "closed_won",
    "success": "closed_won",
    "sale": "closed_won",
    "paid": "closed_won",
    "retention": "closed_won",
    "new_lead": "qualified",
    "new": "qualified",
}

_ATTR_MAX_LEN = 200
_GCLID_MAX_LEN = 150
# gclid: letters/digits + common separators; braids may include more chars
_GCLID_RE_SAFE = re.compile(r"^[A-Za-z0-9._\-~]{1,150}$")
_BRAID_RE_SAFE = re.compile(r"^[A-Za-z0-9._\-~/+=]{1,150}$")

_PLACEHOLDERS = {
    "",
    "your_crm_token",
    "ваш_токен_crm",
    "CHANGEME",
    "CRM_API_TOKEN",
}

# Fallback queue if Firebase /lead_distribution/partners is empty
# Note: Firebase keys cannot contain '#', so id uses user_25 while login keeps user#25
_DEFAULT_PARTNERS: list[dict[str, Any]] = [
    {
        "id": "user_25",
        "login": "user#25",
        "name": "Хабибуллаева Карина",
        "role": "farmer",
        "active": True,
        "order": 0,
    },
    {
        "id": "user_26",
        "login": "user#26",
        "name": "Шаусманова Дилноза",
        "role": "farmer",
        "active": True,
        "order": 1,
    },
]


# ---------------------------------------------------------------------------
# Settings / Firebase URL helpers
# ---------------------------------------------------------------------------

def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _crm_token() -> str:
    token = _env("CRM_API_TOKEN")
    return "" if token in _PLACEHOLDERS else token


def _db_root() -> str:
    """
    Resolve Firebase DB root from CRM_DB_URL or CRM_API_URL.
    Examples:
      https://crm-tikkurila-default-rtdb.firebaseio.com
      https://crm-tikkurila-default-rtdb.firebaseio.com/deals
    """
    raw = _env("CRM_DB_URL") or _env("CRM_API_URL")
    if not raw:
        return ""
    base = raw.rstrip("/")
    if base.endswith(".json"):
        base = base[: -len(".json")]
    for suffix in ("/deals", "/incoming_leads", "/partner_leads", "/lead_distribution"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base.rstrip("/")


def crm_configured() -> bool:
    return bool(_db_root())


def _redact_url(url: str) -> str:
    """Hide auth token in logs."""
    if "auth=" not in url:
        return url
    head, _, _rest = url.partition("auth=")
    return head + "auth=***"


def _auth_url(path: str) -> str:
    """Build `https://…firebaseio.com/{path}.json?auth=…`."""
    root = _db_root()
    path = path.strip("/")
    url = f"{root}/{path}.json" if path else f"{root}/.json"
    token = _crm_token()
    if token:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}auth={token}"
    return url


def _headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        # Avoid stale RTDB snapshots in proxies / long-lived workers
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    }
    token = _crm_token()
    if token:
        # Database secret works via ?auth=; Bearer is for gateways / ID tokens.
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _strip_nulls(value: Any) -> Any:
    """Firebase REST rejects / mishandles explicit nulls in nested objects — drop them."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if item is None:
                continue
            cleaned = _strip_nulls(item)
            if cleaned is None:
                continue
            out[key] = cleaned
        return out
    if isinstance(value, list):
        return [_strip_nulls(item) for item in value if item is not None]
    return value


def _log_crm(msg: str, *args: Any, level: int = logging.INFO) -> None:
    """Log + print so cPanel / Passenger always shows the line."""
    text = msg % args if args else msg
    log.log(level, text)
    try:
        print(f"[leads.crm] {text}", flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Lead template (CRM deal shape)
# ---------------------------------------------------------------------------

def _infer_products(source: Optional[str], paint_type: Optional[str]) -> list[str]:
    blob = f"{source or ''} {paint_type or ''}".lower()
    products: list[str] = []
    if "sika" in blob:
        products.append("sika")
    if "decoral" in blob or "dekoral" in blob:
        products.append("decoral")
    if "tikkurila" in blob or not products:
        products.append("tikkurila")
    seen: set[str] = set()
    out: list[str] = []
    for p in products:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _clip_attr(value: Any, max_len: int = _ATTR_MAX_LEN) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:max_len]


def normalize_gclid(value: Any) -> str:
    """Validate Google Click ID for Offline Conversion Import."""
    raw = _clip_attr(value, _GCLID_MAX_LEN)
    if not raw:
        return ""
    if not _GCLID_RE_SAFE.match(raw):
        log.warning("Invalid gclid rejected: %r", raw[:40])
        return ""
    return raw


def extract_marketing_attribution(lead: Mapping[str, Any]) -> dict[str, str]:
    """
    Accept and sanitize gclid / UTM / source from webhook or Telegram bot payload.
    Also reads nested marketingAttribution if top-level fields are empty.
    Unknown empty values are omitted from the returned dict.
    """
    nested = lead.get("marketingAttribution") or lead.get("marketing_attribution") or {}
    if not isinstance(nested, Mapping):
        nested = {}

    def _pick(*keys: str) -> Any:
        for key in keys:
            val = lead.get(key)
            if val is not None and str(val).strip():
                return val
            val = nested.get(key)
            if val is not None and str(val).strip():
                return val
        return None

    source = _clip_attr(_pick("source", "utm_source") or "website")
    raw_gclid = _pick("gclid", "gclid_field")
    gclid = normalize_gclid(raw_gclid)
    if raw_gclid and not gclid:
        # Keep raw click id for CRM even if charset is unusual (OCI may still use it)
        gclid = _clip_attr(raw_gclid, _GCLID_MAX_LEN)
        log.warning("gclid kept after soft validation: %r", gclid[:40])

    gbraid = _clip_attr(_pick("gbraid"), _GCLID_MAX_LEN)
    wbraid = _clip_attr(_pick("wbraid"), _GCLID_MAX_LEN)

    attrs: dict[str, str] = {
        "source": source or "website",
        "utm_source": _clip_attr(_pick("utm_source")),
        "utm_medium": _clip_attr(_pick("utm_medium")),
        "utm_campaign": _clip_attr(_pick("utm_campaign")),
        "utm_content": _clip_attr(_pick("utm_content")),
        "utm_term": _clip_attr(_pick("utm_term")),
        "landing_page": _clip_attr(
            _pick("landing_page", "landingPage"), 500
        ),
        "referrer": _clip_attr(_pick("referrer"), 500),
    }
    if gclid:
        attrs["gclid"] = gclid
    if gbraid and (_BRAID_RE_SAFE.match(gbraid) or len(gbraid) >= 8):
        attrs["gbraid"] = gbraid
    if wbraid and (_BRAID_RE_SAFE.match(wbraid) or len(wbraid) >= 8):
        attrs["wbraid"] = wbraid

    # Drop empty strings except source
    return {k: v for k, v in attrs.items() if v or k == "source"}


def normalize_lead_quality_status(status: Any) -> tuple[str, str]:
    """
    Canonicalize to spam | qualified | closed_won (labels for CRM UI / Firebase).
    Legacy intermediate keys are soft-mapped and never rejected.
    """
    key = str(status or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "целевой": "targeted",
        "нецелевой": "non_targeted",
        "квалифицированный": "qualified",
        "некондиционный": "disqualified",
        "спам": "spam",
        "ошибочный": "spam",
        "отказ": "spam",
        "отказной": "spam",
        "refusal": "spam",
        "rejected": "spam",
        "reject": "spam",
        "холодный": "cold",
        "тёплый": "warm",
        "теплый": "warm",
        "переговоры": "negotiation",
        "оплата": "checkout",
        "оплачено": "closed_won",
        "повторный": "retention",
        "new_lead": "qualified",
        "new": "qualified",
    }
    key = aliases.get(key, key)
    key = LEAD_QUALITY_LEGACY_MAP.get(key, key)
    if key not in LEAD_QUALITY_STATUSES:
        return DEFAULT_LEAD_QUALITY, DEFAULT_LEAD_QUALITY_LABEL
    return key, LEAD_QUALITY_STATUSES[key]


def build_status_history_entry(
    *,
    from_status: str,
    to_status: str,
    actor: str,
    actor_login: str = "",
    comment: str = "",
    source: str = "api",
) -> dict[str, Any]:
    from_key, from_label = normalize_lead_quality_status(from_status)
    to_key, to_label = normalize_lead_quality_status(to_status)
    now_ms = _now_ms()
    entry: dict[str, Any] = {
        "id": f"sh_{uuid.uuid4().hex[:10]}",
        "from": from_key,
        "to": to_key,
        "fromLabel": from_label,
        "toLabel": to_label,
        "actor": actor or "Система",
        "at": now_ms,
        "source": source,
    }
    if actor_login:
        entry["actorLogin"] = actor_login
    if comment:
        entry["comment"] = _clip_attr(comment, 500)
    return entry


def build_lead_template(
    lead: Mapping[str, Any],
    *,
    lead_id: Optional[str] = None,
    responsible: str = "",
    author: str = "",
    partner: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Standard CRM lead/deal payload with column «Новый лид»."""
    name = str(lead.get("name") or "").strip() or "Без имени"
    phone = str(lead.get("phone") or "").strip()
    area = lead.get("area")
    paint_type = lead.get("paint_type")
    volume = lead.get("volume")
    lead_kind = lead.get("lead_kind") or "retail"
    attribution = extract_marketing_attribution(lead)
    source = attribution.get("source") or "website"
    quality_status, quality_label = normalize_lead_quality_status(
        lead.get("leadQualityStatus") or lead.get("lead_quality_status") or DEFAULT_LEAD_QUALITY
    )

    default_author = _env("CRM_DEFAULT_AUTHOR", "Сайт Tikkurila")
    author = author or default_author

    partner_name = ""
    partner_id = ""
    partner_login = ""
    if partner:
        partner_name = str(partner.get("name") or "").strip()
        partner_id = str(partner.get("id") or partner.get("login") or "").strip()
        partner_login = str(partner.get("login") or partner_id).strip()

    responsible = (
        responsible
        or partner_name
        or _env("CRM_DEFAULT_RESPONSIBLE", "Лид с сайта")
    )

    deal_id = lead_id or f"web_{uuid.uuid4().hex[:16]}"
    now_ms = _now_ms()
    now_iso = _now_iso()

    need_parts = ["Заявка с сайта"]
    if paint_type:
        need_parts.append(str(paint_type))
    if volume is not None:
        need_parts.append(f"≈ {volume} л")

    object_parts: list[str] = []
    if area is not None:
        object_parts.append(f"Площадь: {area} м²")
    if source:
        object_parts.append(f"Источник: {source}")
    if attribution.get("utm_campaign"):
        object_parts.append(f"UTM campaign: {attribution['utm_campaign']}")
    if attribution.get("utm_source"):
        object_parts.append(f"UTM source: {attribution['utm_source']}")
    if attribution.get("gclid"):
        object_parts.append(f"gclid: {attribution['gclid']}")
    if attribution.get("gbraid"):
        object_parts.append(f"gbraid: {attribution['gbraid']}")
    if attribution.get("wbraid"):
        object_parts.append(f"wbraid: {attribution['wbraid']}")
    if lead.get("city"):
        object_parts.append(f"Город: {lead['city']}")
    if lead.get("inn"):
        object_parts.append(f"ИНН: {lead['inn']}")

    title_prefix = "Дилерство" if lead_kind == "dealer" else "Сайт"
    comment_lines = [
        "Автоматический лид с сайта.",
        f"Банк: {LEAD_STATUS_LABEL}",
        f"Качество: {quality_label}",
        f"Имя: {name}",
        f"Телефон: {phone}",
    ]
    if partner_name:
        comment_lines.append(f"Назначен посредник (RR): {partner_name}")
    if lead.get("inn"):
        comment_lines.append(f"ИНН: {lead['inn']}")
    if lead.get("city"):
        comment_lines.append(f"Город: {lead['city']}")
    if area is not None:
        comment_lines.append(f"Площадь: {area} м²")
    if paint_type:
        comment_lines.append(f"Тип/продукция: {paint_type}")
    if volume is not None:
        comment_lines.append(f"Объём: ≈ {volume} л")
    if source:
        comment_lines.append(f"Источник: {source}")
    for utm_key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"):
        if attribution.get(utm_key):
            comment_lines.append(f"{utm_key}: {attribution[utm_key]}")
    if attribution.get("gclid"):
        comment_lines.append(f"gclid: {attribution['gclid']}")
    if attribution.get("gbraid"):
        comment_lines.append(f"gbraid: {attribution['gbraid']}")
    if attribution.get("wbraid"):
        comment_lines.append(f"wbraid: {attribution['wbraid']}")
    if attribution.get("landing_page"):
        comment_lines.append(f"landing: {attribution['landing_page']}")

    status_history = [
        build_status_history_entry(
            from_status="",
            to_status=quality_status,
            actor=author,
            comment="Создание лида (webhook)",
            source="webhook",
        )
    ]
    # Empty "from" looks odd — mark as system create
    status_history[0]["from"] = ""
    status_history[0]["fromLabel"] = "—"

    payload = {
        "id": deal_id,
        "title": f"{title_prefix} — {name}",
        "responsible": responsible,
        "author": author,
        "budget": 0,
        "offerAmount": 0,
        "clientType": "website_lead" if lead_kind != "dealer" else "dealer_application",
        "coExecutors": [],
        "stage": LEAD_STAGE,
        # bankStatus = lifecycle in Lead Bank; leadStatus/leadQualityStatus = quality funnel
        "leadStatus": quality_status,
        "leadStatusLabel": quality_label,
        "leadQualityStatus": quality_status,
        "leadQualityStatusLabel": quality_label,
        "column": LEAD_STATUS_LABEL,
        "pool": "incoming_leads",
        "bankStatus": LEAD_STATUS,
        "bankStatusLabel": LEAD_STATUS_LABEL,
        "dates": {
            "created": now_iso,
            "cold": now_iso,
            "new_lead": now_iso,
            quality_status: now_iso,
        },
        "qualification": {
            "need": "; ".join(need_parts),
            "object": "; ".join(object_parts),
            "task": "Связаться с клиентом по заявке с сайта",
            "deadline": "",
            "lpr": name,
            "budgetBasis": "",
            "contact": f"{name}, {phone}".strip(", "),
            "inn": str(lead.get("inn") or ""),
            "conditions": "",
        },
        "negotiation": {
            "commercialBrief": False,
            "technicalBrief": False,
        },
        "commercial": {},
        "contract": {},
        "implementation": {},
        "activity": [
            {
                "id": f"act_{uuid.uuid4().hex[:10]}",
                "type": "comment",
                "text": "\n".join(comment_lines),
                "author": author,
                "createdAt": now_ms,
            },
            {
                "id": f"act_{uuid.uuid4().hex[:10]}",
                "type": "lead_status_change",
                "text": f"Статус качества: {quality_label}",
                "author": author,
                "createdAt": now_ms,
                "fromStatus": "",
                "toStatus": quality_status,
            },
        ],
        "statusHistory": status_history,
        "products": _infer_products(
            str(source) if source else None,
            str(paint_type) if paint_type else None,
        ),
        "createdAt": now_ms,
        "updatedAt": now_ms,
        "sourceChannel": "website",
        "gclid": attribution.get("gclid") or "",
        "gbraid": attribution.get("gbraid") or "",
        "wbraid": attribution.get("wbraid") or "",
        "marketingAttribution": attribution,
        "websiteLead": {
            "name": name,
            "phone": phone,
            "source": source,
            "lead_kind": lead_kind,
            **{k: v for k, v in attribution.items() if k != "source"},
        },
        "distribution": {
            "method": "round_robin",
            "assignedAt": now_ms,
        },
    }

    # Optional fields — only when present (no nulls for Firebase REST)
    if partner_id:
        payload["responsible_partner"] = partner_id
        payload["responsiblePartnerId"] = partner_id
        payload["distribution"]["partnerId"] = partner_id
    if partner_login:
        payload["responsiblePartnerLogin"] = partner_login
    if partner_name:
        payload["responsiblePartnerName"] = partner_name
        payload["distribution"]["partnerName"] = partner_name
    if area is not None:
        payload["websiteLead"]["area"] = area
    if paint_type:
        payload["websiteLead"]["paint_type"] = paint_type
    if volume is not None:
        payload["websiteLead"]["volume"] = volume
    if lead.get("inn"):
        payload["websiteLead"]["inn"] = lead.get("inn")
    if lead.get("city"):
        payload["websiteLead"]["city"] = lead.get("city")
    if not payload.get("gclid"):
        payload.pop("gclid", None)
    if not payload.get("gbraid"):
        payload.pop("gbraid", None)
    if not payload.get("wbraid"):
        payload.pop("wbraid", None)

    log.info(
        "build_lead_template id=%s source=%s gclid=%s gbraid=%s utm_campaign=%s quality=%s",
        deal_id,
        source,
        "yes" if attribution.get("gclid") else "no",
        "yes" if attribution.get("gbraid") else "no",
        attribution.get("utm_campaign") or "-",
        quality_status,
    )

    return _strip_nulls(payload)


def build_crm_deal_payload(
    lead: Mapping[str, Any],
    *,
    responsible: str,
    author: str,
) -> dict[str, Any]:
    return build_lead_template(lead, responsible=responsible, author=author)


# ---------------------------------------------------------------------------
# HTTP primitives (verbose logging)
# ---------------------------------------------------------------------------

async def _fb_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    payload: Any = None,
) -> Any:
    url = _auth_url(path)
    safe_url = _redact_url(url)
    headers = _headers()
    body = _strip_nulls(payload) if payload is not None else None

    _log_crm(
        "Firebase %s path=%s url=%s token=%s payload_keys=%s",
        method,
        path,
        safe_url,
        "yes" if _crm_token() else "NO",
        list(body.keys()) if isinstance(body, dict) else type(body).__name__,
    )
    if isinstance(body, dict):
        preview = {
            "id": body.get("id"),
            "title": body.get("title"),
            "leadStatus": body.get("leadStatus"),
            "bankStatus": body.get("bankStatus"),
            "responsible_partner": body.get("responsible_partner"),
            "responsiblePartnerName": body.get("responsiblePartnerName"),
        }
        _log_crm("Firebase %s preview=%s", method, json.dumps(preview, ensure_ascii=False))

    try:
        resp = await client.request(method, url, json=body, headers=headers)
    except httpx.RequestError as exc:
        _log_crm(
            "Firebase %s NETWORK ERROR path=%s err=%s",
            method,
            path,
            exc,
            level=logging.ERROR,
        )
        raise

    text = resp.text or ""
    _log_crm(
        "Firebase %s response path=%s status=%s body=%s",
        method,
        path,
        resp.status_code,
        text[:500],
        level=logging.INFO if resp.status_code < 400 else logging.ERROR,
    )

    if resp.status_code >= 400:
        hint = ""
        low = text.lower()
        if "permission_denied" in low:
            hint = (
                " | HINT: deploy database.rules.json with incoming_leads / "
                "partner_leads / lead_distribution, or set CRM_API_TOKEN (database secret)"
            )
        elif "404" in str(resp.status_code):
            hint = " | HINT: check CRM_DB_URL / project id"
        raise RuntimeError(
            f"{method} {path} -> HTTP {resp.status_code}: {text[:400]}{hint}"
        )

    if not resp.content or text in ("", "null"):
        return None
    try:
        return resp.json()
    except json.JSONDecodeError:
        return text


async def _fb_get(client: httpx.AsyncClient, path: str) -> Any:
    return await _fb_request(client, "GET", path)


async def _fb_put(client: httpx.AsyncClient, path: str, payload: Any) -> Any:
    return await _fb_request(client, "PUT", path, payload)


async def _fb_patch(client: httpx.AsyncClient, path: str, payload: Mapping[str, Any]) -> Any:
    return await _fb_request(client, "PATCH", path, dict(payload))


# ---------------------------------------------------------------------------
# Partners queue + Round-Robin
# ---------------------------------------------------------------------------

def _parse_partners_from_env() -> list[dict[str, Any]]:
    """
    CRM_LEAD_PARTNERS=user#25:Хабибуллаева Карина,user#26:Шаусманова Дилноза
    """
    raw = _env("CRM_LEAD_PARTNERS")
    if not raw:
        return []
    partners: list[dict[str, Any]] = []
    for idx, chunk in enumerate(raw.split(",")):
        part = chunk.strip()
        if not part:
            continue
        if ":" in part:
            login, name = part.split(":", 1)
        else:
            login, name = part, part
        login = login.strip()
        name = name.strip() or login
        if not login:
            continue
        partners.append(
            {
                "id": _fb_safe_key(login),
                "login": login,
                "name": name,
                "role": "farmer",
                "active": True,
                "order": idx,
            }
        )
    return partners


def _fb_safe_key(value: str) -> str:
    """Firebase keys cannot contain . # $ [ ] /"""
    safe = (value or "").strip()
    for ch in (".", "#", "$", "[", "]", "/"):
        safe = safe.replace(ch, "_")
    return safe or "partner"


def _normalize_partner_list(raw: Any) -> list[dict[str, Any]]:
    partners: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        items = []
        for key, item in raw.items():
            if isinstance(item, dict):
                merged = dict(item)
                merged.setdefault("id", item.get("id") or key)
                items.append(merged)
    elif isinstance(raw, list):
        items = raw
    else:
        return partners

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        if item.get("active") is False:
            continue
        login = str(item.get("login") or item.get("id") or "").strip()
        pid = _fb_safe_key(str(item.get("id") or login))
        name = str(item.get("name") or login or pid).strip()
        if not pid:
            continue
        partners.append(
            {
                "id": pid,
                "login": login or pid,
                "name": name,
                "role": str(item.get("role") or "farmer"),
                "active": True,
                "order": int(item.get("order") if item.get("order") is not None else idx),
            }
        )
    partners.sort(key=lambda p: (p.get("order", 0), p.get("name") or ""))
    return partners


def _local_partners_fallback() -> list[dict[str, Any]]:
    seed_src = _parse_partners_from_env() or list(_DEFAULT_PARTNERS)
    return _normalize_partner_list(seed_src)


async def ensure_lead_partners(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """
    Load active partners from /lead_distribution/partners.
    On permission/network errors — use env/defaults so bank writes can still proceed.
    """
    try:
        raw = await _fb_get(client, "lead_distribution/partners")
        partners = _normalize_partner_list(raw)
        if partners:
            _log_crm("Partners loaded from Firebase: %s", [p["id"] for p in partners])
            return partners
    except Exception as exc:
        _log_crm(
            "Cannot read lead_distribution/partners (%s) — using local fallback",
            exc,
            level=logging.WARNING,
        )
        return _local_partners_fallback()

    seed = _local_partners_fallback()
    payload = {p["id"]: p for p in seed}
    try:
        await _fb_put(client, "lead_distribution/partners", payload)
        _log_crm("Seeded lead_distribution/partners with %s partners", len(seed))
    except Exception as exc:
        _log_crm(
            "Cannot seed lead_distribution/partners (%s) — continuing with local list",
            exc,
            level=logging.WARNING,
        )
    return seed


async def pick_next_partner_round_robin(
    client: httpx.AsyncClient,
    partners: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], int]:
    """
    Advance /lead_distribution/cursor and return the next partner.
    If cursor write fails, still rotate in-memory for this request.
    """
    if not partners:
        raise RuntimeError("No active partners for Round-Robin distribution")

    cursor = 0
    try:
        cursor_raw = await _fb_get(client, "lead_distribution/cursor")
        cursor = int(cursor_raw or 0)
    except Exception as exc:
        _log_crm("Cannot read RR cursor (%s) — start from 0", exc, level=logging.WARNING)
        cursor = 0

    n = len(partners)
    index = cursor % n
    partner = dict(partners[index])
    new_cursor = (cursor + 1) % n

    try:
        await _fb_put(client, "lead_distribution/cursor", new_cursor)
        await _fb_patch(
            client,
            "lead_distribution",
            {
                "lastPartnerId": partner.get("id"),
                "lastPartnerName": partner.get("name"),
                "lastAssignedAt": _now_ms(),
            },
        )
    except Exception as exc:
        _log_crm(
            "Cannot persist RR cursor (%s) — partner for this lead is still %s",
            exc,
            partner.get("id"),
            level=logging.WARNING,
        )

    _log_crm(
        "Round-Robin: cursor %s -> partner %s (%s), next_cursor=%s",
        cursor,
        partner.get("id"),
        partner.get("name"),
        new_cursor,
    )
    return partner, new_cursor


# ---------------------------------------------------------------------------
# Bank + partner section persistence
# ---------------------------------------------------------------------------

async def save_lead_to_bank(
    client: httpx.AsyncClient,
    lead_payload: Mapping[str, Any],
) -> str:
    """Write lead into shared bank /incoming_leads/{id}."""
    lead_id = str(lead_payload["id"])
    bank_doc = dict(lead_payload)
    bank_doc["bankStatus"] = LEAD_STATUS
    bank_doc["bankStatusLabel"] = LEAD_STATUS_LABEL
    _log_crm("Writing BANK incoming_leads/%s …", lead_id)
    await _fb_put(client, f"incoming_leads/{lead_id}", bank_doc)
    _log_crm("BANK write OK incoming_leads/%s", lead_id)
    return lead_id


async def save_lead_to_deals(
    client: httpx.AsyncClient,
    lead_payload: Mapping[str, Any],
) -> str:
    """Mirror into /deals so the card appears in CRM B2B kanban."""
    lead_id = str(lead_payload["id"])
    _log_crm("Writing DEAL deals/%s …", lead_id)
    await _fb_put(client, f"deals/{lead_id}", dict(lead_payload))
    _log_crm("DEAL write OK deals/%s", lead_id)
    return lead_id


async def save_lead_to_partner_section(
    client: httpx.AsyncClient,
    partner: Mapping[str, Any],
    lead_payload: Mapping[str, Any],
) -> str:
    """
    Duplicate into partner's CRM section:
      /partner_leads/{partnerId}/{leadId}
    """
    lead_id = str(lead_payload["id"])
    partner_id = str(partner.get("id") or partner.get("login") or "").strip()
    if not partner_id:
        raise RuntimeError("Partner id missing for partner_leads write")
    safe_partner = _fb_safe_key(partner_id)
    doc = dict(lead_payload)
    doc["ownerPartnerId"] = partner_id
    if partner.get("name"):
        doc["ownerPartnerName"] = partner.get("name")
    path = f"partner_leads/{safe_partner}/{lead_id}"
    _log_crm("Writing PARTNER %s …", path)
    await _fb_put(client, path, doc)
    _log_crm("PARTNER write OK %s", path)
    return lead_id


# ---------------------------------------------------------------------------
# Lead quality status updates (CRM / Telegram bot webhook)
# ---------------------------------------------------------------------------

async def _find_lead_doc(
    client: httpx.AsyncClient,
    *,
    lead_id: str = "",
    phone: str = "",
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """Locate lead in incoming_leads by id or phone."""
    if lead_id:
        data = await _fb_get(client, f"incoming_leads/{lead_id}")
        if isinstance(data, dict) and data:
            return lead_id, data

    phone_norm = re.sub(r"\D+", "", str(phone or ""))
    if not phone_norm:
        return None, None

    bank = await _fb_get(client, "incoming_leads")
    if not isinstance(bank, dict):
        return None, None
    for lid, doc in bank.items():
        if not isinstance(doc, dict):
            continue
        wl = doc.get("websiteLead") or {}
        cand = re.sub(
            r"\D+",
            "",
            str(wl.get("phone") or doc.get("qualification", {}).get("contact") or ""),
        )
        if cand and (cand == phone_norm or cand.endswith(phone_norm[-9:]) or phone_norm.endswith(cand[-9:])):
            return str(lid), doc
    return None, None


async def update_lead_quality_status(
    *,
    status: str,
    lead_id: str = "",
    phone: str = "",
    actor: str = "Telegram bot",
    actor_login: str = "",
    comment: str = "",
    source: str = "webhook",
    archive: Optional[bool] = None,
) -> dict[str, Any]:
    """
    Patch lead quality status on incoming_leads (+ partner_leads / deals mirrors).
    Logs statusHistory + activity entry.
    If archive=True (or status is spam/disqualified and archive is not False),
    marks the lead as archived so it disappears from the farmers' lead bank.
    """
    result: dict[str, Any] = {"ok": False, "lead_id": None, "error": None}
    if not crm_configured():
        result["error"] = "CRM not configured"
        return result

    raw_status = str(status or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not raw_status:
        result["error"] = "status is required"
        return result
    to_key, to_label = normalize_lead_quality_status(raw_status)
    # Auto-archive spam (incl. legacy non_targeted / disqualified) unless archive=False
    should_archive = bool(archive) if archive is not None else to_key == "spam"
    # Accept canonical + legacy keys (soft-mapped); reject true unknowns
    known = (
        set(LEAD_QUALITY_STATUSES)
        | set(LEAD_QUALITY_LEGACY_MAP.keys())
        | {
            "целевой",
            "нецелевой",
            "квалифицированный",
            "некондиционный",
            "спам",
            "ошибочный",
            "отказ",
            "отказной",
            "refusal",
            "rejected",
            "reject",
            "холодный",
            "тёплый",
            "теплый",
            "переговоры",
            "оплата",
            "оплачено",
            "повторный",
            "won",
            "successful",
            "success",
            "sale",
            "paid",
            "qualified_lead",
        }
    )
    if raw_status not in known and to_key == DEFAULT_LEAD_QUALITY and raw_status not in {
        "qualified",
        "cold",
    }:
        result["error"] = f"Unknown status: {status}. Allowed: {', '.join(LEAD_QUALITY_STATUSES)}"
        return result

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            found_id, doc = await _find_lead_doc(client, lead_id=lead_id, phone=phone)
            if not found_id or not doc:
                result["error"] = "Lead not found"
                return result

            from_key = str(
                doc.get("leadQualityStatus") or doc.get("leadStatus") or DEFAULT_LEAD_QUALITY
            )
            from_key, from_label = normalize_lead_quality_status(from_key)
            now_ms = _now_ms()
            status_unchanged = from_key == to_key
            merged = dict(doc)

            if not status_unchanged:
                now_iso = _now_iso()
                history = list(doc.get("statusHistory") or [])
                history.append(
                    build_status_history_entry(
                        from_status=from_key,
                        to_status=to_key,
                        actor=actor,
                        actor_login=actor_login,
                        comment=comment,
                        source=source,
                    )
                )
                activity = list(doc.get("activity") or [])
                activity.append(
                    {
                        "id": f"act_{uuid.uuid4().hex[:10]}",
                        "type": "lead_status_change",
                        "text": f"Статус качества: {from_label} → {to_label}"
                        + (f"\n{comment}" if comment else ""),
                        "author": actor or "Система",
                        "createdAt": now_ms,
                        "fromStatus": from_key,
                        "toStatus": to_key,
                    }
                )
                dates = dict(doc.get("dates") or {})
                dates[to_key] = now_iso

                patch = {
                    # Canonical quality keys (OCI sync reads these + statusHistory.to)
                    "status": to_key,
                    "leadStatus": to_key,
                    "leadStatusLabel": to_label,
                    "leadQualityStatus": to_key,
                    "leadQualityStatusLabel": to_label,
                    "statusHistory": history,
                    "activity": activity,
                    "dates": dates,
                    "updatedAt": now_ms,
                }
                if should_archive:
                    patch.update(
                        {
                            "archived": True,
                            "archiveComment": comment or f"Автоархив: {to_label}",
                            "archivedAt": now_ms,
                            "archivedBy": actor or "Система",
                            "bankStatus": "archived",
                            "bankStatusLabel": "Архив (спам)",
                        }
                    )

                merged.update(patch)
                await _fb_put(client, f"incoming_leads/{found_id}", merged)

                partner_id = str(
                    doc.get("responsible_partner")
                    or doc.get("responsiblePartnerId")
                    or (doc.get("distribution") or {}).get("partnerId")
                    or ""
                )
                if partner_id:
                    try:
                        await _fb_put(
                            client,
                            f"partner_leads/{_fb_safe_key(partner_id)}/{found_id}",
                            merged,
                        )
                    except Exception as pe:  # noqa: BLE001
                        log.warning("partner_leads status mirror failed: %s", pe)

                try:
                    deal_patch = {
                        "leadStatus": to_key,
                        "leadStatusLabel": to_label,
                        "leadQualityStatus": to_key,
                        "leadQualityStatusLabel": to_label,
                        "updatedAt": now_ms,
                    }
                    for k, v in deal_patch.items():
                        await _fb_put(client, f"deals/{found_id}/{k}", v)
                except Exception as de:  # noqa: BLE001
                    log.warning("deals status mirror failed: %s", de)

                interm_id = str(doc.get("intermediaryId") or "")
                if interm_id:
                    try:
                        interm_patch: dict[str, Any] = {
                            "leadStatus": to_key,
                            "leadStatusLabel": to_label,
                            "leadQualityStatus": to_key,
                            "leadQualityStatusLabel": to_label,
                            "updatedAt": now_ms,
                        }
                        if should_archive:
                            interm_patch.update(
                                {
                                    "archived": True,
                                    "archiveComment": comment or f"Автоархив: {to_label}",
                                    "archivedAt": now_ms,
                                    "archivedBy": actor or "Система",
                                }
                            )
                        for k, v in interm_patch.items():
                            await _fb_put(client, f"intermediaries/{interm_id}/{k}", v)
                    except Exception as ie:  # noqa: BLE001
                        log.warning("intermediaries status mirror failed: %s", ie)

                log.info(
                    "Lead quality status updated id=%s %s → %s by %s archive=%s",
                    found_id,
                    from_key,
                    to_key,
                    actor,
                    should_archive,
                )
            elif should_archive and not merged.get("archived"):
                # Status already set, but archive requested (e.g. retry from CRM quick action)
                archive_patch = {
                    "archived": True,
                    "archiveComment": comment or f"Автоархив: {to_label}",
                    "archivedAt": now_ms,
                    "archivedBy": actor or "Система",
                    "bankStatus": "archived",
                    "bankStatusLabel": "Архив (спам)",
                    "updatedAt": now_ms,
                }
                merged.update(archive_patch)
                await _fb_put(client, f"incoming_leads/{found_id}", merged)
                partner_id = str(
                    doc.get("responsible_partner")
                    or doc.get("responsiblePartnerId")
                    or (doc.get("distribution") or {}).get("partnerId")
                    or ""
                )
                if partner_id:
                    try:
                        await _fb_put(
                            client,
                            f"partner_leads/{_fb_safe_key(partner_id)}/{found_id}",
                            merged,
                        )
                    except Exception as pe:  # noqa: BLE001
                        log.warning("partner_leads archive mirror failed: %s", pe)
                interm_id = str(doc.get("intermediaryId") or "")
                if interm_id:
                    try:
                        for k, v in archive_patch.items():
                            await _fb_put(client, f"intermediaries/{interm_id}/{k}", v)
                    except Exception as ie:  # noqa: BLE001
                        log.warning("intermediaries archive mirror failed: %s", ie)
                log.info("Lead archived (status unchanged) id=%s status=%s", found_id, to_key)
            else:
                log.info(
                    "Lead quality status unchanged id=%s status=%s — still checking Google Ads OCI",
                    found_id,
                    to_key,
                )

            # Offline Conversion Import → Google Ads (best-effort, never blocks CRM).
            # STRICT filter inside bridge: only Qualified_Lead / Closed_Deal.
            # Spam / raw / refuse are ignored. Retries ok when status unchanged.
            ads_meta: dict[str, Any] = await _push_ads_conversion_safe(
                client,
                lead_doc=merged,
                lead_id=found_id,
                status_key=to_key,
                conversion_time_ms=now_ms,
            )

            result.update(
                {
                    "ok": True,
                    "lead_id": found_id,
                    "from": from_key,
                    "status": to_key,
                    "status_label": to_label,
                    "unchanged": status_unchanged,
                    "archived": bool(merged.get("archived")),
                    "google_ads": ads_meta,
                }
            )
            return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        log.exception("update_lead_quality_status failed: %s", exc)
        return result


async def _push_ads_conversion_safe(
    client: httpx.AsyncClient,
    *,
    lead_doc: Mapping[str, Any],
    lead_id: str,
    status_key: str,
    conversion_time_ms: int,
) -> dict[str, Any]:
    """
    Call send_offline_conversion_for_lead; never raise to the CRM status pipeline.
    Persists googleAdsConversion marker on the lead for idempotency / debugging.
    """
    ads_meta: dict[str, Any] = {"attempted": False}
    try:
        from google_ads_conversions import (  # local module next to api.py
            ads_config_status,
            ads_configured,
            send_offline_conversion_for_lead,
        )

        if not ads_configured():
            cfg = ads_config_status()
            ads_meta["skipped"] = True
            ads_meta["error"] = "ads_disabled_or_unconfigured"
            ads_meta["config"] = cfg
            log.warning(
                "Google Ads OCI skipped (unconfigured) lead=%s status=%s reason=%s missing=%s",
                lead_id,
                status_key,
                cfg.get("reason"),
                cfg.get("missing"),
            )
            try:
                await _fb_put(
                    client,
                    f"incoming_leads/{lead_id}/googleAdsConversion",
                    {
                        "ok": False,
                        "skipped": True,
                        "status": status_key,
                        "error": "ads_disabled_or_unconfigured",
                        "config_reason": cfg.get("reason"),
                        "at": conversion_time_ms,
                    },
                )
            except Exception as me:  # noqa: BLE001
                log.warning("googleAdsConversion marker write failed: %s", me)
            return ads_meta

        ads_meta["attempted"] = True
        ads_result = await send_offline_conversion_for_lead(
            lead_doc,
            status=status_key,
            lead_id=lead_id,
            conversion_time_ms=conversion_time_ms,
        )
        ads_meta.update(
            {
                "ok": ads_result.get("ok"),
                "skipped": ads_result.get("skipped"),
                "bucket": ads_result.get("bucket"),
                "error": ads_result.get("error"),
                "gclid": ads_result.get("gclid"),
                "conversion_name": ads_result.get("conversion_name")
                or ads_result.get("display_name"),
            }
        )
        # Audit logs are emitted inside send_offline_conversion_for_lead
        # ([OCI Ignored] / [OCI Sent]). Keep a compact CRM-side line too.
        log.info(
            "Google Ads OCI lead=%s status=%s bucket=%s conversion=%s ok=%s skipped=%s err=%s",
            lead_id,
            status_key,
            ads_result.get("bucket"),
            ads_result.get("conversion_name") or ads_result.get("display_name"),
            ads_result.get("ok"),
            ads_result.get("skipped"),
            ads_result.get("error"),
        )
        try:
            marker = {
                "ok": bool(ads_result.get("ok")),
                "skipped": bool(ads_result.get("skipped")),
                "status": status_key,
                "bucket": ads_result.get("bucket"),
                "conversion_name": ads_result.get("conversion_name")
                or ads_result.get("display_name"),
                "error": ads_result.get("error"),
                "at": conversion_time_ms,
                "action": (ads_result.get("upload") or {}).get("action"),
                "value": (ads_result.get("upload") or {}).get("value"),
                "currency": (ads_result.get("upload") or {}).get("currency"),
                "transport": ads_result.get("transport")
                or (ads_result.get("upload") or {}).get("transport"),
                "requestId": ads_result.get("google_request_id")
                or (ads_result.get("upload") or {}).get("request_id"),
                "partialFailure": bool(ads_result.get("partial_failure")),
            }
            await _fb_put(
                client,
                f"incoming_leads/{lead_id}/googleAdsConversion",
                marker,
            )
            if ads_result.get("ok"):
                patch: dict[str, Any] = {
                    "gclid_sent": True,
                }
                bucket = str(ads_result.get("bucket") or "")
                if bucket in {"qualified", "closed_won"}:
                    patch[f"ociSentByBucket/{bucket}"] = True
                    patch[f"ociSentByStatus/{bucket}"] = True
                try:
                    await _fb_patch(client, f"incoming_leads/{lead_id}", patch)
                except Exception as pe:  # noqa: BLE001
                    log.warning("ociSentByStatus patch failed: %s", pe)
        except Exception as me:  # noqa: BLE001
            log.warning("googleAdsConversion marker write failed: %s", me)
    except Exception as ae:  # noqa: BLE001
        ads_meta["error"] = str(ae)
        log.exception("Google Ads offline conversion hook failed: %s", ae)
    return ads_meta


async def push_google_ads_conversion(
    *,
    lead_id: str = "",
    phone: str = "",
    status: str = "",
) -> dict[str, Any]:
    """
    Load lead from Firebase and upload Offline Conversion for its (or given) status.
    Used by CRM UI after it already wrote quality status to RTDB.
    """
    result: dict[str, Any] = {"ok": False, "lead_id": None, "error": None}
    if not crm_configured():
        result["error"] = "CRM not configured"
        return result
    try:
        from google_ads_conversions import (
            ads_config_status,
            ads_configured,
            send_offline_conversion_for_lead,
        )

        cfg = ads_config_status()
        if not ads_configured():
            result["ok"] = True
            result["skipped"] = True
            result["error"] = "ads_disabled_or_unconfigured"
            result["config"] = cfg
            # Persist diagnostic marker when we can resolve the lead
            try:
                async with httpx.AsyncClient(timeout=25.0) as client:
                    found_id, _doc = await _find_lead_doc(
                        client, lead_id=lead_id, phone=phone
                    )
                    if found_id:
                        result["lead_id"] = found_id
                        await _fb_put(
                            client,
                            f"incoming_leads/{found_id}/googleAdsConversion",
                            {
                                "ok": False,
                                "skipped": True,
                                "status": status or "",
                                "error": "ads_disabled_or_unconfigured",
                                "config_reason": cfg.get("reason"),
                                "at": _now_ms(),
                            },
                        )
            except Exception as me:  # noqa: BLE001
                log.warning("googleAdsConversion marker write failed: %s", me)
            return result

        async with httpx.AsyncClient(timeout=25.0) as client:
            found_id, doc = await _find_lead_doc(client, lead_id=lead_id, phone=phone)
            if not found_id or not doc:
                result["error"] = "Lead not found"
                return result
            to_key = str(status or doc.get("leadQualityStatus") or doc.get("leadStatus") or "").strip()
            if not to_key:
                result["error"] = "status is required"
                return result
            to_key, _ = normalize_lead_quality_status(to_key)
            ads_result = await send_offline_conversion_for_lead(
                doc,
                status=to_key,
                lead_id=found_id,
                conversion_time_ms=_now_ms(),
            )
            marker = {
                "ok": bool(ads_result.get("ok")),
                "skipped": bool(ads_result.get("skipped")),
                "status": to_key,
                "bucket": ads_result.get("bucket"),
                "error": ads_result.get("error"),
                "at": _now_ms(),
                "action": (ads_result.get("upload") or {}).get("action"),
                "value": (ads_result.get("upload") or {}).get("value"),
                "currency": (ads_result.get("upload") or {}).get("currency"),
                "display_name": ads_result.get("display_name")
                or (ads_result.get("upload") or {}).get("display_name"),
                "click_id_type": ads_result.get("click_id_type"),
                "transport": ads_result.get("transport")
                or (ads_result.get("upload") or {}).get("transport"),
                "requestId": ads_result.get("google_request_id")
                or (ads_result.get("upload") or {}).get("request_id"),
                "partialFailure": bool(ads_result.get("partial_failure")),
            }
            try:
                await _fb_put(client, f"incoming_leads/{found_id}/googleAdsConversion", marker)
                if ads_result.get("ok"):
                    patch: dict[str, Any] = {"gclid_sent": True}
                    bucket = str(ads_result.get("bucket") or "")
                    if bucket in {"qualified", "closed_won"}:
                        patch[f"ociSentByBucket/{bucket}"] = True
                        patch[f"ociSentByStatus/{bucket}"] = True
                    await _fb_patch(client, f"incoming_leads/{found_id}", patch)
            except Exception as me:  # noqa: BLE001
                log.warning("googleAdsConversion marker write failed: %s", me)
            result.update(
                {
                    "ok": bool(ads_result.get("ok") or ads_result.get("skipped")),
                    "lead_id": found_id,
                    "status": to_key,
                    "google_ads": ads_result,
                    "config": cfg,
                }
            )
            if ads_result.get("error") and not ads_result.get("skipped") and not ads_result.get("ok"):
                result["ok"] = False
                result["error"] = ads_result.get("error")
            return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        log.exception("push_google_ads_conversion failed: %s", exc)
        return result


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

async def distribute_website_lead(lead: Mapping[str, Any]) -> dict[str, Any]:
    """
    Full pipeline for POST /webhook/lead:
      1) pick partner (Round-Robin)
      2) build standard lead template (column «Новый лид»)
      3) save to bank /incoming_leads  (critical)
      4) save to /deals                (best-effort)
      5) duplicate to /partner_leads   (critical)

    Never raises to the caller — returns {ok, lead_id, partner, error?}.
    """
    result: dict[str, Any] = {
        "ok": False,
        "lead_id": None,
        "partner": None,
        "bank_path": None,
        "deal_path": None,
        "partner_path": None,
        "error": None,
        "errors": [],
    }

    _log_crm(
        "distribute_website_lead START name=%s phone=%s db_root=%s token=%s",
        lead.get("name"),
        lead.get("phone"),
        _db_root() or "(empty)",
        "yes" if _crm_token() else "NO",
    )

    if not crm_configured():
        result["error"] = (
            "CRM_DB_URL / CRM_API_URL is empty — set in .env "
            "(example: https://crm-tikkurila-default-rtdb.firebaseio.com)"
        )
        _log_crm(result["error"], level=logging.ERROR)
        return result

    if not _crm_token():
        _log_crm(
            "CRM_API_TOKEN empty — writing without auth "
            "(needs Firebase rules allowing auth == null on incoming_leads / partner_leads)",
            level=logging.WARNING,
        )

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            partners = await ensure_lead_partners(client)
            partner, _cursor = await pick_next_partner_round_robin(client, partners)

            lead_id = f"web_{uuid.uuid4().hex[:16]}"
            payload = build_lead_template(lead, lead_id=lead_id, partner=partner)
            _log_crm(
                "Built lead payload id=%s keys=%s",
                lead_id,
                sorted(payload.keys()),
            )

            # Critical path: bank first
            await save_lead_to_bank(client, payload)

            # Partner copy — critical for farmer UI
            await save_lead_to_partner_section(client, partner, payload)

            # Deals mirror — non-fatal
            try:
                await save_lead_to_deals(client, payload)
                result["deal_path"] = f"deals/{lead_id}"
            except Exception as deal_exc:
                result["errors"].append(f"deals: {deal_exc}")
                _log_crm("DEAL write failed (non-fatal): %s", deal_exc, level=logging.ERROR)

            safe_partner = _fb_safe_key(str(partner.get("id") or ""))
            result.update(
                {
                    "ok": True,
                    "lead_id": lead_id,
                    "partner": {
                        "id": partner.get("id"),
                        "login": partner.get("login"),
                        "name": partner.get("name"),
                    },
                    "bank_path": f"incoming_leads/{lead_id}",
                    "partner_path": f"partner_leads/{safe_partner}/{lead_id}",
                }
            )
            _log_crm(
                "Lead distributed OK id=%s partner=%s bank=%s partner_path=%s",
                lead_id,
                partner.get("name"),
                result["bank_path"],
                result["partner_path"],
            )
            return result
    except httpx.RequestError as exc:
        result["error"] = f"CRM network error: {exc}"
        _log_crm("%s (Telegram path continues)", result["error"], level=logging.ERROR)
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        _log_crm(
            "CRM distribution FAILED (Telegram path continues): %s",
            exc,
            level=logging.ERROR,
        )
        log.exception("CRM distribution traceback")
        return result


async def send_lead_to_crm(lead: Mapping[str, Any]) -> Optional[str]:
    """
    Compatibility wrapper: returns lead id or None.
    Prefer distribute_website_lead() when partner info is needed.
    """
    result = await distribute_website_lead(lead)
    return result.get("lead_id") if result.get("ok") else None
