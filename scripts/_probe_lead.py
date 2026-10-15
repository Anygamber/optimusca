import json
import urllib.request

base = "http://127.0.0.1:8000"

# openapi paths
try:
    with urllib.request.urlopen(base + "/openapi.json", timeout=5) as r:
        data = json.loads(r.read().decode())
        print("title:", data.get("info", {}).get("title"))
        print("paths:", sorted(data.get("paths", {}).keys()))
except Exception as e:
    print("openapi ERR", e)

payload = {"name": "Test", "phone": "+998901234567", "area": 10, "paint_type": "x", "volume": None}
req = urllib.request.Request(
    base + "/webhook/lead",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=5) as r:
        print("lead OK", r.status, r.read().decode())
except Exception as e:
    body = getattr(e, "read", lambda: b"")()
    print("lead FAIL", e)
    if body:
        print(body.decode())
