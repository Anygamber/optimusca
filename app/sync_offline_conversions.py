#!/usr/bin/env python3
"""
Batch sync: Firebase lead quality statuses → Google Ads Offline Conversion Import.

Usage (from app/ or site root with PYTHONPATH):
  python sync_offline_conversions.py
  python sync_offline_conversions.py --dry-run --limit 50
  python sync_offline_conversions.py --force   # re-upload even if marker ok

Also exposed as POST /admin/push-oci (see server.py).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import httpx

# Allow `python app/sync_offline_conversions.py` and Passenger app/ imports
_HERE = Path(__file__).resolve().parent
_SITE = _HERE.parent
for _p in (_HERE, _SITE):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

log = logging.getLogger("leads.oci_sync")

# STRICT: only Qualified_Lead / Closed_Deal leave for Google Ads.
# Spam, raw/new, refuse, archive are intentionally excluded.
OCI_ELIGIBLE_STATUSES = frozenset(
    {
        # Qualified_Lead
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
        # Closed_Deal
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

# Google Ads click-conversion lookback is ~90 days; keep a small safety margin.
DEFAULT_OCI_MAX_AGE_DAYS = 90
_MIN_CLICK_ID_LEN = 10
_INVALID_CLICK_MARKERS = (
    "test",
    "dummy",
    "placeholder",
    "xxx",
    "null",
    "undefined",
)


def _max_age_days(override: Optional[int] = None) -> int:
    if override is not None and override > 0:
        return int(override)
    try:
        return max(1, int(os.getenv("OCI_MAX_AGE_DAYS") or DEFAULT_OCI_MAX_AGE_DAYS))
    except ValueError:
        return DEFAULT_OCI_MAX_AGE_DAYS


def _to_ms(value: Any) -> Optional[int]:
    """Normalize Firebase / ISO timestamps to epoch milliseconds."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str) and "T" in value:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        n = float(value)
        if n > 1e12:
            return int(n)
        if n > 1e9:
            return int(n * 1000)
    except (TypeError, ValueError, OSError):
        return None
    return None


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    log.setLevel(level)
    logging.getLogger("leads.google_ads").setLevel(level)
    # File log next to server google_ads.log when possible
    log_dir = _SITE / "logs"
    if not log_dir.is_dir():
        log_dir = _HERE / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "google_ads.log", encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        for name in ("leads.oci_sync", "leads.google_ads", "leads.crm"):
            lg = logging.getLogger(name)
            if not any(
                isinstance(h, logging.FileHandler)
                and getattr(h, "baseFilename", "").endswith("google_ads.log")
                for h in lg.handlers
            ):
                lg.addHandler(fh)
    except OSError as exc:
        log.warning("Could not open google_ads.log: %s", exc)


def _lead_status(doc: Mapping[str, Any]) -> str:
    """
    Resolve current quality status from Firebase lead doc.

    Prefer latest statusHistory.to (authoritative after CRM edits), then
    leadQualityStatus / leadStatus / status. Soft-maps legacy keys via API.
    """
    from api import normalize_lead_quality_status

    hist_to = ""
    hist = doc.get("statusHistory")
    if isinstance(hist, list) and hist:
        for entry in reversed(hist):
            if not isinstance(entry, Mapping):
                continue
            to_val = entry.get("to") or entry.get("toStatus") or entry.get("status")
            if to_val:
                hist_to = str(to_val).strip()
                break

    raw = (
        hist_to
        or doc.get("leadQualityStatus")
        or doc.get("leadStatus")
        or doc.get("status")
        or ""
    )
    key, _ = normalize_lead_quality_status(raw)
    return key


def _truthy_flag(value: Any) -> bool:
    return value is True or str(value).lower() in {"1", "true", "yes"}


def _lead_created_ms(doc: Mapping[str, Any]) -> Optional[int]:
    """Earliest known lead timestamp (create / first history / websiteLead)."""
    candidates: list[int] = []
    for key in ("createdAt", "claimedAt", "updatedAt"):
        ms = _to_ms(doc.get(key))
        if ms:
            candidates.append(ms)
    hist = doc.get("statusHistory")
    if isinstance(hist, list):
        for entry in hist:
            if not isinstance(entry, Mapping):
                continue
            ms = _to_ms(entry.get("at") or entry.get("ts") or entry.get("time"))
            if ms:
                candidates.append(ms)
    wl = doc.get("websiteLead")
    if isinstance(wl, Mapping):
        for key in ("createdAt", "updatedAt"):
            ms = _to_ms(wl.get(key))
            if ms:
                candidates.append(ms)
    return min(candidates) if candidates else None


def _conversion_time_ms(doc: Mapping[str, Any]) -> int:
    """Prefer last statusHistory.at, then updatedAt / createdAt, else now."""
    hist = doc.get("statusHistory")
    if isinstance(hist, list) and hist:
        last = hist[-1] if isinstance(hist[-1], Mapping) else {}
        ms = _to_ms(last.get("at") or last.get("ts") or last.get("time"))
        if ms:
            return ms
    for key in ("updatedAt", "createdAt", "claimedAt"):
        ms = _to_ms(doc.get(key))
        if ms:
            return ms
    return int(time.time() * 1000)


def _is_click_id_valid(value: str) -> bool:
    raw = str(value or "").strip()
    if len(raw) < _MIN_CLICK_ID_LEN:
        return False
    low = raw.lower()
    if any(marker in low for marker in _INVALID_CLICK_MARKERS):
        return False
    # Reject obvious placeholders like "0", "none"
    if low in {"0", "none", "n/a", "-", "null"}:
        return False
    return True


def _valid_click_identifiers(doc: Mapping[str, Any]) -> dict[str, str]:
    """gclid / gbraid / wbraid present and not empty/placeholder."""
    from google_ads_conversions import extract_click_identifiers

    raw = extract_click_identifiers(doc)
    return {k: v for k, v in raw.items() if _is_click_id_valid(v)}


def _is_expired(
    doc: Mapping[str, Any],
    conversion_ms: int,
    *,
    max_age_days: int,
) -> tuple[bool, str, Optional[int], Optional[int]]:
    """
    Skip conversions Google would reject as outside the ~90-day window.

    Returns (expired?, result_code, age_ref_ms, age_days).
      expired_gclid — lead create / click proxy older than max_age_days
      too_old       — conversion timestamp older than max_age_days
    """
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - int(max_age_days) * 86_400_000
    created_ms = _lead_created_ms(doc)

    if created_ms is not None and created_ms < cutoff_ms:
        age_days = int((now_ms - created_ms) / 86_400_000)
        return True, "expired_gclid", created_ms, age_days

    if conversion_ms and conversion_ms < cutoff_ms:
        age_days = int((now_ms - conversion_ms) / 86_400_000)
        return True, "too_old", conversion_ms, age_days

    return False, "", None, None


def _already_sent(doc: Mapping[str, Any], status: str, *, force: bool) -> tuple[bool, str]:
    """
    Dedup per conversion TYPE (qualified / closed_won), not global gclid_sent.

    Qualified_Lead once + Closed_Deal once per deal. Sending qualified must
    NOT skip a later closed_won upload.
    """
    if force:
        return False, ""

    status = str(status or "").strip()
    if not status:
        return False, ""

    # Resolve bucket for this status (qualified | closed_won | None)
    bucket = ""
    try:
        from google_ads_conversions import resolve_conversion_for_status

        mapping = resolve_conversion_for_status(status)
        bucket = str((mapping or {}).get("bucket") or "")
    except Exception:  # noqa: BLE001
        bucket = status if status in {"qualified", "closed_won"} else ""

    if bucket:
        sent_bucket = doc.get("ociSentByBucket") or {}
        if isinstance(sent_bucket, Mapping) and _truthy_flag(sent_bucket.get(bucket)):
            return True, f"ociSentByBucket[{bucket}]"
        sent_by = doc.get("ociSentByStatus") or {}
        if isinstance(sent_by, Mapping) and _truthy_flag(sent_by.get(bucket)):
            return True, f"ociSentByStatus[{bucket}]"

    # Legacy: exact status key flag
    sent_by = doc.get("ociSentByStatus") or {}
    if isinstance(sent_by, Mapping) and _truthy_flag(sent_by.get(status)):
        return True, f"ociSentByStatus[{status}]"

    marker = doc.get("googleAdsConversion") or {}
    if not isinstance(marker, Mapping):
        return False, ""

    if (
        marker.get("ok")
        and not marker.get("skipped")
        and bucket
        and str(marker.get("bucket") or "") == bucket
    ):
        return True, f"googleAdsConversion_ok_bucket[{bucket}]"

    if (
        marker.get("ok")
        and not marker.get("skipped")
        and str(marker.get("status") or "") == status
    ):
        return True, "googleAdsConversion_ok"

    return False, ""


def _has_click_id(doc: Mapping[str, Any]) -> bool:
    return bool(_valid_click_identifiers(doc))


async def sync_offline_conversions(
    *,
    limit: int = 500,
    dry_run: bool = False,
    force: bool = False,
    only_ids: Optional[list[str]] = None,
    sleep_s: float = 0.15,
    max_age_days: Optional[int] = None,
) -> dict[str, Any]:
    """
    Scan incoming_leads, upload OCI for eligible statuses not yet sent.
    Returns a report dict suitable for JSON API responses.
    """
    from api import (
        _fb_get,
        _fb_patch,
        _fb_put,
        _now_ms,
        crm_configured,
    )
    from google_ads_conversions import (
        ads_config_status,
        ads_configured,
        clear_ads_token_cache,
        get_ads_oauth_credentials,
        reload_ads_dotenv,
        resolve_conversion_for_status,
        send_offline_conversion_for_lead,
    )

    # Always re-read .env before batch (Passenger / long-lived workers)
    reload_ads_dotenv(override=True)
    clear_ads_token_cache("oci batch sync start")
    creds_probe = get_ads_oauth_credentials()
    log.info(
        "OCI sync credentials probe: auth_mode=%s sa_email=%s sa_file=%s "
        "login_customer_id=%s customer_id=%s env_files=%s",
        creds_probe.get("auth_mode"),
        creds_probe.get("service_account_email"),
        creds_probe.get("service_account_file"),
        creds_probe.get("login_customer_id"),
        creds_probe.get("customer_id"),
        creds_probe.get("env_files"),
    )

    age_days = _max_age_days(max_age_days)
    report: dict[str, Any] = {
        "ok": False,
        "dry_run": dry_run,
        "force": force,
        "max_age_days": age_days,
        "checked": 0,
        "eligible": 0,
        "uploaded": 0,
        "skipped": 0,
        "skipped_no_gclid": 0,
        "skipped_expired": 0,
        "failed": 0,
        "errors": [],
        "items": [],
        "config": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    if not crm_configured():
        report["error"] = "CRM not configured (CRM_DB_URL)"
        log.error(report["error"])
        return report

    cfg = ads_config_status()
    report["config"] = cfg
    if not ads_configured() and not dry_run:
        report["error"] = "ads_disabled_or_unconfigured"
        report["ok"] = False
        log.error("OCI sync aborted: %s missing=%s", cfg.get("reason"), cfg.get("missing"))
        return report

    log.info(
        "OCI sync start dry_run=%s force=%s limit=%s max_age_days=%s ads=%s",
        dry_run,
        force,
        limit,
        age_days,
        cfg.get("reason"),
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Fresh fetch every run — no in-process lead cache between syncs
        raw = await _fb_get(client, "incoming_leads")
        if not isinstance(raw, dict) or not raw:
            report["ok"] = True
            report["error"] = "no_leads"
            log.info("OCI sync: incoming_leads empty")
            return report

        log.info(
            "OCI sync fetched incoming_leads count=%s (fresh Firebase GET)",
            len(raw),
        )

        # Newest first by updatedAt
        rows: list[tuple[str, dict]] = []
        for key, doc in raw.items():
            if not isinstance(doc, dict):
                continue
            lid = str(doc.get("id") or key)
            if only_ids and lid not in only_ids and key not in only_ids:
                continue
            # Shallow copy — never mutate the fetched snapshot shared across loops
            rows.append((lid, dict(doc)))
        rows.sort(key=lambda kv: int(kv[1].get("updatedAt") or 0), reverse=True)

        from google_ads_conversions import (
            extract_click_identifiers,
            hydrate_lead_click_ids,
        )

        for lid, doc in rows:
            if report["checked"] >= max(1, limit):
                break
            report["checked"] += 1

            # Hydrate gclid from activity text before status/click checks
            doc = hydrate_lead_click_ids(doc)
            extracted_ids = extract_click_identifiers(doc)
            extracted_gclid = extracted_ids.get("gclid") or extracted_ids.get(
                "gbraid"
            ) or extracted_ids.get("wbraid") or ""

            status = _lead_status(doc)
            item: dict[str, Any] = {
                "lead_id": lid,
                "status": status,
                "result": None,
            }

            print(
                f"[DEBUG OCI] ID: {lid} | Status: {status} | "
                f"raw_leadQualityStatus={doc.get('leadQualityStatus')!r} "
                f"raw_leadStatus={doc.get('leadStatus')!r} "
                f"raw_status={doc.get('status')!r} | "
                f"Extracted GCLID: {extracted_gclid or '<empty>'} | "
                f"ociSentByStatus: {doc.get('ociSentByStatus')}"
            )
            log.info(
                "[DEBUG OCI] ID: %s | Status: %s | Extracted GCLID: %s | "
                "ociSentByStatus: %s | leadQualityStatus=%s leadStatus=%s status=%s",
                lid,
                status,
                extracted_gclid or "<empty>",
                doc.get("ociSentByStatus"),
                doc.get("leadQualityStatus"),
                doc.get("leadStatus"),
                doc.get("status"),
            )

            # Normalize aliases; STRICT: only Qualified_Lead / Closed_Deal
            mapping = resolve_conversion_for_status(status)
            status_key = (mapping or {}).get("status_key") or status
            bucket = str((mapping or {}).get("bucket") or "")
            if (
                (status not in OCI_ELIGIBLE_STATUSES and status_key not in OCI_ELIGIBLE_STATUSES)
                or not mapping
                or bucket not in {"qualified", "closed_won"}
            ):
                report["skipped"] += 1
                item["result"] = "status_not_eligible"
                log.info(
                    "[OCI Ignored] Deal ID %s: status '%s' is not allowed for Google Ads.",
                    lid,
                    status,
                )
                report["items"].append(item)
                continue

            if not mapping.get("action"):
                report["skipped"] += 1
                item["result"] = "status_not_mapped_or_action_missing"
                report["items"].append(item)
                continue

            already, reason = _already_sent(doc, status_key, force=force)
            if already:
                report["skipped"] += 1
                item["result"] = f"already_sent:{reason}"
                report["items"].append(item)
                log.info(
                    "OCI skip lead=%s status=%s — %s (ociSentByStatus=%s "
                    "marker_status=%s)",
                    lid,
                    status_key,
                    reason,
                    doc.get("ociSentByStatus"),
                    (doc.get("googleAdsConversion") or {}).get("status")
                    if isinstance(doc.get("googleAdsConversion"), dict)
                    else None,
                )
                continue

            clicks = _valid_click_identifiers(doc)
            if not clicks:
                report["skipped"] += 1
                report["skipped_no_gclid"] += 1
                item["result"] = "no_gclid"
                report["items"].append(item)
                log.info(
                    "OCI skip lead=%s status=%s — no_gclid (missing/empty/invalid)",
                    lid,
                    status_key,
                )
                continue

            conv_ms = _conversion_time_ms(doc)
            expired, exp_code, age_ref_ms, age_days_val = _is_expired(
                doc, conv_ms, max_age_days=age_days
            )
            if expired:
                report["skipped"] += 1
                report["skipped_expired"] += 1
                item["result"] = exp_code  # expired_gclid | too_old
                item["conversion_time_ms"] = conv_ms
                item["age_ref_ms"] = age_ref_ms
                item["age_days"] = age_days_val
                item["max_age_days"] = age_days
                report["items"].append(item)
                log.info(
                    "OCI skip lead=%s status=%s — %s age_days=%s max=%s ref_ms=%s",
                    lid,
                    status,
                    exp_code,
                    age_days_val,
                    age_days,
                    age_ref_ms,
                )
                continue

            report["eligible"] += 1
            click_preview = next(iter(clicks.values()), "")[:16]
            item["click_id_type"] = next(iter(clicks))
            item["click_id_preview"] = click_preview + "…"
            item["bucket"] = mapping.get("bucket")
            item["status"] = status_key
            item["conversion_value"] = mapping.get("value")
            item["currency"] = mapping.get("currency")
            item["conversion_time_ms"] = conv_ms

            log.info(
                "OCI candidate lead=%s status=%s bucket=%s value=%s %s click=%s… "
                "time=%s dry_run=%s gclid_sent=%s",
                lid,
                status_key,
                mapping.get("bucket"),
                mapping.get("value"),
                mapping.get("currency"),
                click_preview,
                conv_ms,
                dry_run,
                doc.get("gclid_sent"),
            )

            if dry_run:
                report["skipped"] += 1
                item["result"] = "dry_run_would_upload"
                report["items"].append(item)
                continue

            try:
                ads_result = await send_offline_conversion_for_lead(
                    doc,
                    status=status_key,
                    lead_id=lid,
                    conversion_time_ms=conv_ms,
                )
            except Exception as exc:  # noqa: BLE001
                report["failed"] += 1
                item["result"] = "exception"
                item["error"] = str(exc)[:300]
                report["errors"].append({"lead_id": lid, "error": str(exc)[:300]})
                report["items"].append(item)
                log.exception("OCI upload exception lead=%s: %s", lid, exc)
                continue

            # Treat already_uploaded as success/skip
            if ads_result.get("error") == "already_uploaded":
                report["skipped"] += 1
                item["result"] = "already_uploaded"
                item["ads"] = {
                    "ok": ads_result.get("ok"),
                    "skipped": ads_result.get("skipped"),
                    "error": ads_result.get("error"),
                    "bucket": ads_result.get("bucket"),
                }
                report["items"].append(item)
                log.info("OCI already_uploaded lead=%s status=%s", lid, status)
                continue

            if ads_result.get("skipped") and not ads_result.get("ok"):
                report["skipped"] += 1
                item["result"] = f"skipped:{ads_result.get('error')}"
                item["ads"] = {
                    "error": ads_result.get("error"),
                    "config": ads_result.get("config"),
                }
                report["items"].append(item)
                log.warning(
                    "OCI skipped lead=%s status=%s err=%s",
                    lid,
                    status,
                    ads_result.get("error"),
                )
                continue

            marker = {
                "ok": bool(ads_result.get("ok")),
                "skipped": bool(ads_result.get("skipped")),
                "status": status_key,
                "bucket": ads_result.get("bucket"),
                "error": ads_result.get("error"),
                "at": _now_ms(),
                "action": (ads_result.get("upload") or {}).get("action"),
                "value": (ads_result.get("upload") or {}).get("value"),
                "currency": (ads_result.get("upload") or {}).get("currency"),
                "display_name": ads_result.get("display_name"),
                "click_id_type": ads_result.get("click_id_type"),
                "source": "batch_sync",
                "transport": ads_result.get("transport")
                or (ads_result.get("upload") or {}).get("transport"),
                "requestId": ads_result.get("google_request_id")
                or (ads_result.get("upload") or {}).get("request_id"),
                "partialFailure": bool(ads_result.get("partial_failure")),
            }
            try:
                await _fb_put(
                    client, f"incoming_leads/{lid}/googleAdsConversion", marker
                )
                patch: dict[str, Any] = {
                    "oci_sent_at": _now_ms(),
                }
                if ads_result.get("ok"):
                    patch["gclid_sent"] = True
                    # Per-bucket flags: Qualified_Lead once, Closed_Deal once
                    bucket_ok = str(
                        ads_result.get("bucket") or mapping.get("bucket") or ""
                    )
                    if bucket_ok:
                        patch[f"ociSentByBucket/{bucket_ok}"] = True
                        patch[f"ociSentByStatus/{bucket_ok}"] = True
                    # Persist gclid found in activity text to top-level for next runs
                    if extracted_ids.get("gclid"):
                        patch["gclid"] = extracted_ids["gclid"]
                await _fb_patch(client, f"incoming_leads/{lid}", patch)
            except Exception as me:  # noqa: BLE001
                log.warning("OCI marker write failed lead=%s: %s", lid, me)

            item["ads"] = {
                "ok": ads_result.get("ok"),
                "skipped": ads_result.get("skipped"),
                "error": ads_result.get("error"),
                "bucket": ads_result.get("bucket"),
                "gclid": ads_result.get("gclid"),
                "raw_error": str(ads_result.get("error") or "")[:400],
            }
            if ads_result.get("ok"):
                report["uploaded"] += 1
                item["result"] = "uploaded"
                conv_name = (
                    ads_result.get("conversion_name")
                    or ads_result.get("display_name")
                    or ads_result.get("bucket")
                )
                log.info(
                    "[OCI Sent] Deal ID %s: Conversion '%s' successfully sent to Google Ads.",
                    lid,
                    conv_name,
                )
            else:
                report["failed"] += 1
                item["result"] = "upload_failed"
                item["error"] = ads_result.get("error")
                report["errors"].append(
                    {"lead_id": lid, "error": str(ads_result.get("error"))[:300]}
                )
                log.error(
                    "OCI FAILED lead=%s status=%s err=%s raw=%s",
                    lid,
                    status,
                    ads_result.get("error"),
                    str(ads_result.get("raw"))[:400] if ads_result.get("raw") else "",
                )

            report["items"].append(item)
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)

    report["ok"] = report["failed"] == 0
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    log.info(
        "OCI sync done checked=%s eligible=%s uploaded=%s skipped=%s "
        "skipped_no_gclid=%s skipped_expired=%s failed=%s",
        report["checked"],
        report["eligible"],
        report["uploaded"],
        report["skipped"],
        report["skipped_no_gclid"],
        report["skipped_expired"],
        report["failed"],
    )
    # Cap items in response for large runs (full detail still in log)
    if len(report["items"]) > 100:
        report["items_truncated"] = True
        report["items"] = report["items"][:100]
    return report


def _load_dotenv() -> None:
    """Force-reload .env so rotated GOOGLE_ADS_* apply (override=True)."""
    try:
        from google_ads_conversions import reload_ads_dotenv

        loaded = reload_ads_dotenv(override=True)
        if loaded:
            log.info("OCI sync dotenv loaded: %s", loaded)
            return
    except Exception as exc:  # noqa: BLE001
        log.warning("reload_ads_dotenv failed: %s — falling back", exc)
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    # Site then app — later wins
    for path in (_SITE / ".env", _HERE / ".env"):
        if path.is_file():
            load_dotenv(path, override=True)


def main(argv: Optional[list[str]] = None) -> int:
    _load_dotenv()
    ap = argparse.ArgumentParser(description="Batch Google Ads OCI sync from Firebase")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="Ignore gclid_sent / ok markers")
    ap.add_argument("--ids", default="", help="Comma-separated lead ids")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.15)
    ap.add_argument(
        "--max-age-days",
        type=int,
        default=0,
        help="Skip leads/conversions older than N days (default: OCI_MAX_AGE_DAYS or 90)",
    )
    args = ap.parse_args(argv)
    _setup_logging(args.verbose)
    only = [x.strip() for x in args.ids.split(",") if x.strip()] or None
    report = asyncio.run(
        sync_offline_conversions(
            limit=args.limit,
            dry_run=args.dry_run,
            force=args.force,
            only_ids=only,
            sleep_s=args.sleep,
            max_age_days=args.max_age_days or None,
        )
    )
    # Compact stdout summary
    print(
        "checked=%(checked)s eligible=%(eligible)s uploaded=%(uploaded)s "
        "skipped=%(skipped)s skipped_no_gclid=%(skipped_no_gclid)s "
        "skipped_expired=%(skipped_expired)s failed=%(failed)s ok=%(ok)s"
        % report
    )
    if report.get("error"):
        print("error=", report["error"])
    if report.get("errors"):
        for err in report["errors"][:10]:
            print("  fail", err)
    return 0 if report.get("ok") or report.get("dry_run") else 1


if __name__ == "__main__":
    raise SystemExit(main())
