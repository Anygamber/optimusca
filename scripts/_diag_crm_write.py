"""One-shot Firebase write diagnostic for website leads."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import api  # noqa: E402


async def main() -> None:
    print("db_root=", api._db_root())
    print("token_set=", bool(api._crm_token()))
    print("crm_configured=", api.crm_configured())

    import httpx

    async with httpx.AsyncClient(timeout=20) as client:
        for path in ("incoming_leads", "partner_leads", "lead_distribution", "deals"):
            url = api._auth_url(path)
            show = url.split("?")[0]
            resp = await client.get(url, headers=api._headers())
            body = (resp.text or "")[:160]
            print(f"GET {path}: status={resp.status_code} url={show} body={body!r}")

    result = await api.distribute_website_lead(
        {
            "name": "Diag Lead",
            "phone": "+998900099988",
            "area": 5,
            "source": "diag",
            "paint_type": "test",
        }
    )
    print("distribute=", json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
