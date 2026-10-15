#!/usr/bin/env bash
# Run ON cPanel host after uploading updated .env OR export vars first:
#   export GOOGLE_ADS_CLIENT_ID='...'
#   export GOOGLE_ADS_CLIENT_SECRET='...'
#   export GOOGLE_ADS_REFRESH_TOKEN='...'
#   bash /home/optimus5/app/scripts/deploy_google_ads_oauth.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/home/optimus5/app}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
VENV_PY="${VENV_PY:-/home/optimus5/virtualenv/app/3.11/bin/python}"

: "${GOOGLE_ADS_CLIENT_ID:?Set GOOGLE_ADS_CLIENT_ID}"
: "${GOOGLE_ADS_CLIENT_SECRET:?Set GOOGLE_ADS_CLIENT_SECRET}"
: "${GOOGLE_ADS_REFRESH_TOKEN:?Set GOOGLE_ADS_REFRESH_TOKEN}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found" >&2
  exit 1
fi

cp -a "$ENV_FILE" "${ENV_FILE}.bak.$(date +%Y%m%d_%H%M%S)"

python3 - <<PY
import re
from pathlib import Path
path = Path("$ENV_FILE")
keys = {
    "GOOGLE_ADS_CLIENT_ID": """$GOOGLE_ADS_CLIENT_ID""",
    "GOOGLE_ADS_CLIENT_SECRET": """$GOOGLE_ADS_CLIENT_SECRET""",
    "GOOGLE_ADS_REFRESH_TOKEN": """$GOOGLE_ADS_REFRESH_TOKEN""",
    "REFRESH_TOKEN": """$GOOGLE_ADS_REFRESH_TOKEN""",
}
drop = set(keys)
text = path.read_text(encoding="utf-8", errors="replace")
out = []
for line in text.splitlines():
    m = re.match(r"^([A-Za-z0-9_]+)\s*=", line)
    if m and m.group(1) in drop:
        continue
    out.append(line)
for k, v in keys.items():
    out.append(f"{k}={v}")
path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
print("Updated", path)
PY

echo "Restarting Passenger / python workers…"
pkill -9 -f python || true
mkdir -p "$APP_DIR/tmp"
touch "$APP_DIR/tmp/restart.txt"
sleep 2

echo "Probing OAuth token exchange…"
"$VENV_PY" - <<'PY'
import sys
from pathlib import Path
from dotenv import load_dotenv
import httpx
app = Path("/home/optimus5/app")
load_dotenv(app / ".env", override=True)
sys.path.insert(0, str(app))
from google_ads_conversions import get_ads_oauth_credentials, _mask_secret
c = get_ads_oauth_credentials()
print("CLIENT_ID", _mask_secret(c["client_id"], keep_start=12, keep_end=22))
print("REFRESH source", c["refresh_token_env"])
resp = httpx.post(
    "https://oauth2.googleapis.com/token",
    data={
        "grant_type": "refresh_token",
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "refresh_token": c["refresh_token"],
    },
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    timeout=30.0,
)
print("Status:", resp.status_code, "OK" if resp.status_code == 200 else "FAIL")
body = resp.json()
if resp.status_code == 200:
    print("access_token:", _mask_secret(body.get("access_token", ""), keep_start=8, keep_end=4))
    print("expires_in:", body.get("expires_in"))
else:
    print(body)
    raise SystemExit(1)
PY

echo "OCI dry-run (limit 20)…"
cd "$APP_DIR"
"$VENV_PY" sync_offline_conversions.py --dry-run --limit 20 || true
echo "Done."
