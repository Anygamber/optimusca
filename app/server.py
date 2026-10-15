"""
FastAPI backend for Website leads + dealer applications.

Deploy: this file MUST live next to main.py (inside the cPanel app/ folder).

Endpoints:
  POST /webhook/lead           — JSON retail leads → Telegram + CRM (+ gclid/UTM)
  POST /webhook/lead/status    — update lead quality status (CRM / Telegram bot)
  POST /webhook/lead/ads-conversion — Offline Conversion Import → Google Ads (by lead_id)
  POST /admin/push-oci         — batch OCI sync from Firebase (token: OCI_SYNC_TOKEN)
  POST /api/sync-conversions   — alias of /admin/push-oci
  POST /webhook/dealer         — form-data dealer applications → Telegram + CRM
  GET  /health                 — status + Telegram / CRM / Ads configuration flags
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional, Union

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Form, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

APP_DIR = Path(__file__).resolve().parent
SITE_ROOT = APP_DIR.parent

# Site root on sys.path so `import api` resolves to Website/api.py (CRM client)
if str(SITE_ROOT) not in sys.path:
    sys.path.insert(0, str(SITE_ROOT))


def _resolve_site_root() -> Path:
    """HTML + .env may sit in app/ or one level above (document root)."""
    candidates = [
        APP_DIR,
        APP_DIR.parent,
        Path.cwd(),
        Path.cwd().parent,
    ]
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return APP_DIR.parent if (APP_DIR.parent / "index.html").exists() else APP_DIR


ROOT = _resolve_site_root()

# Load .env files; later paths override earlier ones so app/.env wins on
# Passenger (Application root = app/) and fresh GOOGLE_ADS_* replace stale keys.
_env_loaded: list[Path] = []
for env_path in (SITE_ROOT / ".env", ROOT / ".env", APP_DIR / ".env"):
    if env_path.is_file() and env_path.resolve() not in {p.resolve() for p in _env_loaded}:
        load_dotenv(env_path, override=True)
        _env_loaded.append(env_path)
if not _env_loaded:
    load_dotenv(APP_DIR / ".env", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("leads")

# Durable Google Ads OCI log (Passenger stderr alone is easy to lose)
_ads_log_dir = ROOT / "logs"
try:
    _ads_log_dir.mkdir(parents=True, exist_ok=True)
    _ads_fh = logging.FileHandler(_ads_log_dir / "google_ads.log", encoding="utf-8")
    _ads_fh.setLevel(logging.INFO)
    _ads_fh.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.getLogger("leads.google_ads").addHandler(_ads_fh)
    logging.getLogger("leads.crm").addHandler(_ads_fh)
except OSError as _ads_log_err:  # pragma: no cover
    log.warning("Could not open logs/google_ads.log: %s", _ads_log_err)

# Prefer app/api.py (cPanel Application root = app/), then site-root api.py
_crm_import_error = ""
try:
    from api import (
        LEAD_QUALITY_STATUSES,
        crm_configured,
        distribute_website_lead,
        push_google_ads_conversion,
        send_lead_to_crm,
        update_lead_quality_status,
    )

    log.info("CRM module loaded: api.py (sys.path ok), crm_configured=%s", crm_configured())
except ImportError as _imp_exc:  # pragma: no cover
    _crm_import_error = str(_imp_exc)
    log.error("api.py import failed: %s — CRM push disabled until file is deployed", _imp_exc)
    LEAD_QUALITY_STATUSES = {}  # type: ignore[misc]

    def crm_configured() -> bool:  # type: ignore[misc]
        return False

    async def distribute_website_lead(_lead: Any) -> dict[str, Any]:  # type: ignore[misc]
        msg = f"api.py not found / import error: {_crm_import_error}"
        log.error(msg)
        print(f"[leads] {msg}", flush=True)
        return {"ok": False, "lead_id": None, "partner": None, "error": msg}

    async def send_lead_to_crm(_lead: Any) -> Optional[str]:  # type: ignore[misc]
        return None

    async def update_lead_quality_status(**_kwargs: Any) -> dict[str, Any]:  # type: ignore[misc]
        return {"ok": False, "error": f"api.py not found: {_crm_import_error}"}

    async def push_google_ads_conversion(**_kwargs: Any) -> dict[str, Any]:  # type: ignore[misc]
        return {"ok": False, "error": f"api.py not found: {_crm_import_error}"}

# Fallback credentials (overridden by .env when set). Used so a stale host .env
# with a revoked token does not silently disable Telegram notify.
_DEFAULT_TELEGRAM_BOT_TOKEN = "8972524669:AAGiDzqqkXOAFCB875CwZsPHOgg8nWbyOS0"
_DEFAULT_TELEGRAM_CHAT_ID = "8678902051"

_PLACEHOLDER_VALUES = {
    "",
    "ваш_токен_бота",
    "ваш_chat_id",
    "your_bot_token",
    "your_chat_id",
    "CHANGEME",
}


def _clean_secret(value: str) -> str:
    v = (value or "").strip()
    if v in _PLACEHOLDER_VALUES:
        return ""
    return v


TELEGRAM_BOT_TOKEN = (
    _clean_secret(os.getenv("TELEGRAM_BOT_TOKEN", "")) or _DEFAULT_TELEGRAM_BOT_TOKEN
)
TELEGRAM_CHAT_ID = (
    _clean_secret(os.getenv("TELEGRAM_CHAT_ID", "")) or _DEFAULT_TELEGRAM_CHAT_ID
)
TELEGRAM_HTTP_TIMEOUT = 3.0


def telegram_configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def parse_chat_id(raw: str) -> Union[int, str]:
    s = (raw or "").strip()
    if not s:
        return s
    if s.startswith("@"):
        return s
    try:
        return int(s)
    except ValueError:
        return s


def init_app(app: FastAPI) -> FastAPI:
    """Attach middleware, lead routes and static files. Returns the same app."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "*",
            "https://optimusca.uz",
            "https://www.optimusca.uz",
            "http://optimusca.uz",
            "http://www.optimusca.uz",
            "http://127.0.0.1:8010",
            "http://localhost:8010",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["*"],
        max_age=86400,
    )
    try:
        from portfolio import init_portfolio_db, router as portfolio_router

        init_portfolio_db()
        app.include_router(portfolio_router)
        log.info("B2B Portfolio API mounted at /api/b2b/…")
    except Exception as portfolio_exc:  # noqa: BLE001
        log.warning("B2B Portfolio API not mounted: %s", portfolio_exc)
    _register_routes(app)
    log.info("Site ROOT for static files: %s", ROOT)
    return app


class LeadPayload(BaseModel):
    """Incoming lead from website forms or Telegram bot webhooks."""

    model_config = ConfigDict(extra="ignore")

    name: str
    phone: str
    area: Optional[float] = None
    paint_type: Optional[str] = None
    volume: Optional[float] = None
    source: Optional[str] = None
    # Google Ads / сквозная аналитика
    gclid: Optional[str] = None
    gbraid: Optional[str] = None
    wbraid: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    utm_term: Optional[str] = None
    landing_page: Optional[str] = None
    referrer: Optional[str] = None
    lead_quality_status: Optional[str] = None


class LeadStatusPayload(BaseModel):
    """Update lead quality status from CRM UI bridge or Telegram bot."""

    model_config = ConfigDict(extra="ignore")

    status: str
    lead_id: Optional[str] = None
    phone: Optional[str] = None
    actor: Optional[str] = None
    actor_login: Optional[str] = None
    comment: Optional[str] = None
    # True → archive lead (auto for spam/disqualified if omitted)
    archive: Optional[bool] = None


class AdsConversionPayload(BaseModel):
    """Push Offline Conversion to Google Ads for an existing CRM lead."""

    model_config = ConfigDict(extra="ignore")

    lead_id: Optional[str] = None
    phone: Optional[str] = None
    status: Optional[str] = None


class OciSyncPayload(BaseModel):
    """Batch Offline Conversion sync from Firebase → Google Ads."""

    model_config = ConfigDict(extra="ignore")

    limit: int = Field(default=500, ge=1, le=2000)
    dry_run: bool = False
    force: bool = False
    ids: Optional[list[str]] = None
    sleep_s: float = Field(default=0.15, ge=0.0, le=5.0)
    max_age_days: Optional[int] = Field(
        default=None,
        ge=1,
        le=365,
        description="Skip leads/conversions older than N days (default 90)",
    )


def _oci_sync_token() -> str:
    return (
        os.getenv("OCI_SYNC_TOKEN")
        or os.getenv("ADMIN_API_TOKEN")
        or ""
    ).strip()


def _require_oci_token(
    x_oci_token: Optional[str] = None,
    authorization: Optional[str] = None,
    token_query: Optional[str] = None,
) -> None:
    expected = _oci_sync_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Set OCI_SYNC_TOKEN (or ADMIN_API_TOKEN) in .env before calling /admin/push-oci",
        )
    provided = (x_oci_token or "").strip()
    if not provided and authorization:
        auth = authorization.strip()
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
        else:
            provided = auth
    if not provided and token_query:
        provided = token_query.strip()
    if not provided or provided != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing OCI sync token")


def format_lead_telegram_message(
    *,
    name: str,
    phone: str,
    gclid: Optional[str] = None,
    details: str = "",
) -> str:
    """Canonical Telegram duplicate for website leads."""
    source = (gclid or "").strip() or "—"
    detail = (details or "").strip() or "—"
    return (
        "🔔 Новая заявка с сайта!\n"
        f"👤 Имя: {name or '—'}\n"
        f"📞 Телефон: {phone or '—'}\n"
        f"🎯 Источник / GCLID: {source}\n"
        f"📝 Детали: {detail}"
    )


async def telegram_send_message(text: str) -> dict[str, Any]:
    """
    Autonomous Telegram notify. Never raises — never affects HTTP status.
    timeout={TELEGRAM_HTTP_TIMEOUT}s. Returns {ok, error?, status_code?}.
    """
    result: dict[str, Any] = {"ok": False, "error": None, "status_code": None}
    if not telegram_configured():
        result["error"] = "telegram_not_configured"
        log.warning("Telegram not configured — background notify skipped")
        return result

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chat_id = parse_chat_id(TELEGRAM_CHAT_ID)
    try:
        async with httpx.AsyncClient(timeout=TELEGRAM_HTTP_TIMEOUT) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
            )
    except httpx.TimeoutException as exc:
        result["error"] = f"timeout: {exc}"
        log.warning("Telegram timeout (non-blocking, %ss): %s", TELEGRAM_HTTP_TIMEOUT, exc)
        return result
    except httpx.RequestError as exc:
        result["error"] = f"network: {exc}"
        log.warning("Telegram network error (non-blocking): %s", exc)
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        log.warning("Telegram unexpected error (non-blocking): %s", exc)
        return result

    result["status_code"] = resp.status_code
    try:
        data: dict[str, Any] = resp.json()
    except json.JSONDecodeError:
        data = {"ok": False, "description": resp.text}

    if resp.status_code >= 400 or not data.get("ok"):
        detail = data.get("description") or resp.text or f"HTTP {resp.status_code}"
        result["error"] = str(detail)[:500]
        log.warning(
            "Telegram sendMessage failed (non-blocking) HTTP %s: %s",
            resp.status_code,
            result["error"],
        )
        return result

    result["ok"] = True
    log.info("Telegram message delivered to chat_id=%s", chat_id)
    return result


async def telegram_notify_background(text: str) -> None:
    """BackgroundTasks entrypoint — runs after HTTP 200; must never raise."""
    try:
        result = await telegram_send_message(text)
        if not result.get("ok"):
            log.warning(
                "Background Telegram notify failed (ignored for client): %s",
                result.get("error"),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("Background Telegram notify exception (ignored for client): %s", exc)


def _register_routes(app: FastAPI) -> None:
    @app.on_event("startup")
    async def on_startup() -> None:
        if telegram_configured():
            log.info("Telegram configured — leads will also be notified to the bot (best-effort).")
        else:
            log.warning(
                "Telegram NOT configured. Leads still save to CRM/Firebase; "
                "set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env to enable bot notify."
            )
        if crm_configured():
            log.info("CRM configured — leads will also be pushed to CRM_API_URL.")
        else:
            log.warning(
                "CRM NOT configured (CRM_API_URL / CRM_API_TOKEN). "
                "Leads still go to Telegram; CRM push is skipped."
            )
        try:
            from google_ads_conversions import (
                ads_config_status,
                clear_ads_token_cache,
                log_oauth_credentials_startup,
                reload_ads_dotenv,
            )

            reload_ads_dotenv(override=True)
            clear_ads_token_cache("app startup")
            from google_ads_conversions import get_ads_oauth_credentials

            probe = get_ads_oauth_credentials()
            ads_st = ads_config_status()
            log.info(
                "Google Ads OCI at startup: configured=%s reason=%s "
                "CLIENT_ID=%s CLIENT_SECRET=%s refresh_env=%s env_files=%s",
                ads_st.get("configured"),
                ads_st.get("reason"),
                ads_st.get("client_id_masked"),
                probe.get("client_secret_fingerprint"),
                ads_st.get("refresh_token_env"),
                probe.get("env_files"),
            )
            log_oauth_credentials_startup(force=True)
        except Exception as ads_exc:  # noqa: BLE001
            log.warning("Google Ads startup credential log failed: %s", ads_exc)
        try:
            from portfolio import init_portfolio_db

            path = init_portfolio_db()
            log.info("Portfolio DB ready: %s", path)
        except Exception as portfolio_db_exc:  # noqa: BLE001
            log.warning("Portfolio DB init failed: %s", portfolio_db_exc)

    @app.get("/health")
    async def health():
        ads_status: dict[str, Any] = {"configured": False, "reason": "module_unavailable"}
        try:
            from google_ads_conversions import ads_config_status

            ads_status = ads_config_status()
        except Exception as exc:  # noqa: BLE001
            ads_status = {"configured": False, "reason": f"import_error:{exc}"}
        return {
            "ok": True,
            "version": "v2_google_ads_active",
            "telegram_configured": telegram_configured(),
            "crm_configured": crm_configured(),
            "crm_import_error": _crm_import_error or None,
            "crm_db_url": (os.getenv("CRM_DB_URL") or os.getenv("CRM_API_URL") or "")[:120] or None,
            "site_root": str(ROOT),
            "ads_configured": bool(ads_status.get("configured")),
            "ads": ads_status,
            "env_files": [str(p) for p in _env_loaded],
            "server_file": __file__,
        }

    @app.get("/debug-version")
    async def debug_version():
        """Temporary: prove which server.py Passenger actually loaded."""
        return {
            "version": "v2_google_ads_active",
            "file": __file__,
            "cwd": os.getcwd(),
            "sys_path": sys.path[:5],
            "root": str(ROOT),
            "env_files": [str(p) for p in _env_loaded],
        }

    def _attribution_lines(
        *,
        source: Optional[str] = None,
        gclid: Optional[str] = None,
        gbraid: Optional[str] = None,
        wbraid: Optional[str] = None,
        utm_source: Optional[str] = None,
        utm_medium: Optional[str] = None,
        utm_campaign: Optional[str] = None,
        utm_content: Optional[str] = None,
        utm_term: Optional[str] = None,
        landing_page: Optional[str] = None,
    ) -> list[str]:
        """Full marketing attribution for Telegram managers (no truncation of click ids)."""
        lines: list[str] = []
        if source:
            lines.append(f"🌐 Источник: {source}")
        utm_parts = [
            f"source={utm_source}" if utm_source else "",
            f"medium={utm_medium}" if utm_medium else "",
            f"campaign={utm_campaign}" if utm_campaign else "",
            f"content={utm_content}" if utm_content else "",
            f"term={utm_term}" if utm_term else "",
        ]
        utm_parts = [x for x in utm_parts if x]
        if utm_parts:
            lines.append("🏷 UTM: " + " | ".join(utm_parts))
        if gclid:
            lines.append(f"🔗 gclid: {gclid}")
        if gbraid:
            lines.append(f"🔗 gbraid: {gbraid}")
        if wbraid:
            lines.append(f"🔗 wbraid: {wbraid}")
        if landing_page:
            lines.append(f"📄 landing: {landing_page[:220]}")
        return lines

    @app.post("/webhook/lead")
    async def webhook_lead(payload: LeadPayload, background_tasks: BackgroundTasks):
        # 1) CRM first (source of truth for the client response)
        log.info(
            "webhook/lead → CRM distribute start name=%s phone=%s gclid=%s utm=%s crm_configured=%s",
            payload.name,
            payload.phone,
            bool(payload.gclid),
            payload.utm_campaign or payload.utm_source or "-",
            crm_configured(),
        )
        print(
            f"[leads] webhook/lead CRM start name={payload.name!r} configured={crm_configured()}",
            flush=True,
        )
        try:
            crm = await distribute_website_lead(
                {
                    "name": payload.name,
                    "phone": payload.phone,
                    "area": payload.area,
                    "paint_type": payload.paint_type,
                    "volume": payload.volume,
                    "source": payload.source or payload.utm_source or "website",
                    "lead_kind": "retail",
                    "gclid": payload.gclid,
                    "gbraid": payload.gbraid,
                    "wbraid": payload.wbraid,
                    "utm_source": payload.utm_source,
                    "utm_medium": payload.utm_medium,
                    "utm_campaign": payload.utm_campaign,
                    "utm_content": payload.utm_content,
                    "utm_term": payload.utm_term,
                    "landing_page": payload.landing_page,
                    "referrer": payload.referrer,
                    "leadQualityStatus": payload.lead_quality_status,
                }
            )
        except Exception as crm_exc:  # noqa: BLE001
            log.warning("webhook/lead CRM exception (non-blocking): %s", crm_exc)
            crm = {"ok": False, "lead_id": None, "partner": None, "error": str(crm_exc)}

        crm_deal_id = crm.get("lead_id")
        partner = crm.get("partner") or {}
        crm_ok = bool(crm.get("ok"))
        if not crm_ok:
            log.warning(
                "webhook/lead CRM FAILED (client still gets 200): %s",
                crm.get("error") or "unknown",
            )

        # 2) Build autonomous Telegram duplicate (BackgroundTasks → after HTTP 200)
        detail_parts: list[str] = []
        if payload.paint_type:
            detail_parts.append(str(payload.paint_type))
        if payload.area is not None:
            detail_parts.append(f"{payload.area} м²")
        if payload.volume is not None:
            detail_parts.append(f"≈ {payload.volume} л")
        if payload.source or payload.utm_source:
            detail_parts.append(f"src={payload.source or payload.utm_source}")
        if payload.utm_campaign:
            detail_parts.append(f"campaign={payload.utm_campaign}")
        if crm_deal_id:
            detail_parts.append(f"CRM={crm_deal_id}")
        if partner.get("name"):
            detail_parts.append(f"RR={partner.get('name')}")
        tg_text = format_lead_telegram_message(
            name=payload.name,
            phone=payload.phone,
            gclid=payload.gclid or payload.utm_source or payload.source,
            details="; ".join(detail_parts) if detail_parts else "розничная заявка",
        )
        try:
            background_tasks.add_task(telegram_notify_background, tg_text)
            tg_scheduled = True
        except Exception as exc:  # noqa: BLE001
            tg_scheduled = False
            log.warning("Could not schedule Telegram notify (ignored): %s", exc)

        # 3) Always HTTP 200 — Telegram/CRM soft failures never become 5xx
        return {
            "ok": True,
            "success": True,
            "delivered": "crm" if crm_ok else "accepted",
            "telegram_scheduled": tg_scheduled,
            "telegram_ok": None,
            "telegram_error": None,
            "crm_ok": crm_ok,
            "crm_deal_id": crm_deal_id,
            "crm_partner": partner or None,
            "crm_distribution": crm,
            "crm_error": crm.get("error"),
        }

    @app.get("/webhook/lead/statuses")
    async def webhook_lead_statuses():
        return {"ok": True, "statuses": LEAD_QUALITY_STATUSES}

    @app.post("/webhook/lead/status")
    async def webhook_lead_status(payload: LeadStatusPayload):
        if not payload.lead_id and not payload.phone:
            raise HTTPException(status_code=400, detail="Укажите lead_id или phone")
        result = await update_lead_quality_status(
            status=payload.status,
            lead_id=payload.lead_id or "",
            phone=payload.phone or "",
            actor=payload.actor or "Telegram bot",
            actor_login=payload.actor_login or "",
            comment=payload.comment or "",
            source="webhook",
            archive=payload.archive,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=404 if result.get("error") == "Lead not found" else 400, detail=result.get("error") or "update failed")
        return result

    @app.post("/webhook/lead/ads-conversion")
    async def webhook_lead_ads_conversion(payload: AdsConversionPayload):
        """
        CRM / bot: after quality status is saved in Firebase, push Offline Conversion
        to Google Ads using the lead's stored gclid.
        Real-time path (already used by build-crm notifyGoogleAdsOfflineConversion).
        """
        if not payload.lead_id and not payload.phone:
            raise HTTPException(status_code=400, detail="Укажите lead_id или phone")
        result = await push_google_ads_conversion(
            lead_id=payload.lead_id or "",
            phone=payload.phone or "",
            status=payload.status or "",
        )
        if not result.get("ok") and result.get("error") == "Lead not found":
            raise HTTPException(status_code=404, detail="Lead not found")
        if not result.get("ok") and not result.get("skipped"):
            raise HTTPException(
                status_code=502,
                detail=result.get("error") or "Google Ads upload failed",
            )
        return result

    @app.post("/admin/push-oci")
    @app.post("/api/sync-conversions")
    async def admin_push_oci(
        payload: Optional[OciSyncPayload] = None,
        x_oci_token: Optional[str] = Header(default=None, alias="X-OCI-Token"),
        authorization: Optional[str] = Header(default=None),
        token: Optional[str] = Query(default=None, description="Cron-friendly token"),
    ):
        """
        Batch sync: scan Firebase incoming_leads and upload missing Offline Conversions.
        Auth: header X-OCI-Token / Authorization: Bearer <OCI_SYNC_TOKEN> / ?token=
        """
        _require_oci_token(x_oci_token, authorization, token)
        body = payload or OciSyncPayload()
        try:
            from sync_offline_conversions import sync_offline_conversions
        except ImportError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"sync_offline_conversions import failed: {exc}",
            ) from exc

        log.info(
            "admin/push-oci start limit=%s dry_run=%s force=%s ids=%s",
            body.limit,
            body.dry_run,
            body.force,
            len(body.ids or []),
        )
        report = await sync_offline_conversions(
            limit=body.limit,
            dry_run=body.dry_run,
            force=body.force,
            only_ids=body.ids,
            sleep_s=body.sleep_s,
            max_age_days=body.max_age_days,
        )
        return report

    @app.post("/webhook/dealer")
    async def webhook_dealer(
        background_tasks: BackgroundTasks,
        fio: str = Form(...),
        phone: str = Form(...),
        inn: str = Form(...),
        city: str = Form(...),
        product: str = Form(...),
        name: Optional[str] = Form(None),
        form_type: Optional[str] = Form("dealer", alias="type"),
        gclid: Optional[str] = Form(None),
        gbraid: Optional[str] = Form(None),
        wbraid: Optional[str] = Form(None),
        utm_source: Optional[str] = Form(None),
        utm_medium: Optional[str] = Form(None),
        utm_campaign: Optional[str] = Form(None),
        utm_content: Optional[str] = Form(None),
        utm_term: Optional[str] = Form(None),
        landing_page: Optional[str] = Form(None),
        referrer: Optional[str] = Form(None),
        source: Optional[str] = Form(None),
    ):
        person = fio or name or "—"
        try:
            crm = await distribute_website_lead(
                {
                    "name": person,
                    "phone": phone,
                    "inn": inn,
                    "city": city,
                    "paint_type": product,
                    "source": source or utm_source or "website-dealer",
                    "lead_kind": "dealer",
                    "gclid": gclid,
                    "gbraid": gbraid,
                    "wbraid": wbraid,
                    "utm_source": utm_source,
                    "utm_medium": utm_medium,
                    "utm_campaign": utm_campaign,
                    "utm_content": utm_content,
                    "utm_term": utm_term,
                    "landing_page": landing_page,
                    "referrer": referrer,
                }
            )
        except Exception as crm_exc:  # noqa: BLE001
            log.warning("webhook/dealer CRM exception (non-blocking): %s", crm_exc)
            crm = {"ok": False, "lead_id": None, "partner": None, "error": str(crm_exc)}

        crm_deal_id = crm.get("lead_id")
        partner = crm.get("partner") or {}
        crm_ok = bool(crm.get("ok"))
        if not crm_ok:
            log.warning(
                "webhook/dealer CRM FAILED (client still gets 200): %s",
                crm.get("error") or "unknown",
            )

        detail_parts = [
            "дилерство",
            f"ИНН {inn}" if inn else "",
            f"город {city}" if city else "",
            f"продукт {product}" if product else "",
            f"CRM={crm_deal_id}" if crm_deal_id else "",
            f"RR={partner.get('name')}" if partner.get("name") else "",
        ]
        detail_parts = [x for x in detail_parts if x]
        tg_text = format_lead_telegram_message(
            name=person,
            phone=phone,
            gclid=gclid or utm_source or source or "website-dealer",
            details="; ".join(detail_parts) if detail_parts else "заявка на дилерство",
        )
        try:
            background_tasks.add_task(telegram_notify_background, tg_text)
            tg_scheduled = True
        except Exception as exc:  # noqa: BLE001
            tg_scheduled = False
            log.warning("Could not schedule Telegram notify for dealer (ignored): %s", exc)

        return {
            "ok": True,
            "success": True,
            "type": form_type or "dealer",
            "delivered": "crm" if crm_ok else "accepted",
            "telegram_scheduled": tg_scheduled,
            "telegram_ok": None,
            "telegram_error": None,
            "crm_ok": crm_ok,
            "crm_deal_id": crm_deal_id,
            "crm_partner": partner or None,
            "crm_distribution": crm,
            "crm_error": crm.get("error"),
        }

    index_path = ROOT / "index.html"
    if index_path.is_file():
        @app.get("/")
        async def root_index():
            return FileResponse(index_path)

        app.mount("/", StaticFiles(directory=str(ROOT), html=True), name="static")
    else:
        log.warning("index.html not found near %s — static site mount skipped", ROOT)


# Local convenience: uvicorn app.server:app  OR  from app folder uvicorn server:app
app = init_app(FastAPI(title="Tikkurila & Dekoral Website API"))
