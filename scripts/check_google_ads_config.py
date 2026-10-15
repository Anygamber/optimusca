#!/usr/bin/env python3
"""Validate Google Ads OCI configuration without printing secrets.

Usage (from Website root):
  python scripts/check_google_ads_config.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore

if load_dotenv:
    for p in (ROOT / "app" / ".env", ROOT / ".env"):
        if p.is_file():
            load_dotenv(p, override=False)

from google_ads_conversions import (  # noqa: E402
    CONVERSION_ACTION_DISPLAY_NAMES,
    ads_config_status,
    resolve_conversion_for_status,
)


def main() -> int:
    st = ads_config_status()
    print("Google Ads OCI config check")
    print("-" * 40)
    print("configured:", st.get("configured"))
    print("enabled:   ", st.get("enabled"))
    print("reason:    ", st.get("reason"))
    print("missing:   ", st.get("missing") or [])
    print("customer:  ", st.get("customer_id"))
    print("login MCC: ", st.get("login_customer_id"))
    print("api:       ", st.get("api_version"))
    print()
    print("Expected Google Ads conversion action NAMES (exact):")
    for bucket, name in CONVERSION_ACTION_DISPLAY_NAMES.items():
        mapped = resolve_conversion_for_status(
            {"spam": "spam", "qualified": "qualified", "closed_won": "closed_won"}[bucket]
        )
        action = (mapped or {}).get("action") or "(env missing)"
        print(f"  [{bucket}] {name!r}")
        print(f"           -> {action}")
    print()
    if not st.get("configured"):
        print("ACTION REQUIRED:")
        print("  1. Set a real GOOGLE_ADS_DEVELOPER_TOKEN (not 'test') in .env")
        print("  2. Ensure OAuth client_id/secret + refresh_token are valid")
        print("  3. Deploy the same .env to production (/home/optimus5/app/.env)")
        print("  4. Restart Passenger / the FastAPI app")
        print("  5. GET https://optimusca.uz/health → ads_configured must be true")
        return 1
    print("Config looks ready. Probe a real lead with:")
    print("  python ../build-crm/scripts/diagnose_lead_quality.py --probe-ads --ids <lead_id>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
