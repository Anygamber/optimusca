# -*- coding: utf-8 -*-
"""Diagnose Telegram token + live optimusca.uz lead webhook / lead-api.js."""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

root = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv

    load_dotenv(root / ".env")
    load_dotenv(root / "app" / ".env", override=False)
except Exception:
    pass

token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
print(f"local token_len={len(token)} chat={chat}")

try:
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=20) as r:
        data = json.loads(r.read().decode())
    print(
        "getMe ok=",
        data.get("ok"),
        "user=",
        (data.get("result") or {}).get("username"),
        "desc=",
        data.get("description"),
    )
except Exception as e:
    print("getMe FAIL:", e)

for url in (
    "https://optimusca.uz/js/lead-api.js",
    "https://optimusca.uz/js/lead-api.js?v=20261013a",
):
    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "replace")
        print("---", url)
        print("  len=", len(body))
        print("  isLeadAccepted=", "isLeadAccepted" in body)
        print("  strict_telegram=", "delivered !== 'telegram'" in body)
        print("  old_err=", "доставку в Telegram" in body)
    except Exception as e:
        print(url, "FAIL", e)

try:
    with urllib.request.urlopen("https://optimusca.uz/", timeout=20) as r:
        html = r.read().decode("utf-8", "replace")
    for line in html.splitlines():
        if "lead-api" in line:
            print("HTML script:", line.strip())
except Exception as e:
    print("HTML FAIL", e)

payload = json.dumps(
    {"name": "TEST_TG_CHECK_DELETE", "phone": "+998900000001", "source": "tg-diagnostic"}
).encode()
req = urllib.request.Request(
    "https://optimusca.uz/webhook/lead",
    data=payload,
    headers={"Content-Type": "application/json", "Accept": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=40) as r:
        print("POST /webhook/lead", r.status, r.read().decode("utf-8", "replace")[:600])
except urllib.error.HTTPError as e:
    print("POST /webhook/lead HTTP", e.code, e.read().decode("utf-8", "replace")[:600])
except Exception as e:
    print("POST FAIL", e)
