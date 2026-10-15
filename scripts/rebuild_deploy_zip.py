# -*- coding: utf-8 -*-
import ast
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
ast.parse((root / "app" / "server.py").read_text(encoding="utf-8"))
print("server.py syntax OK")

new_v = "v=20261014_thanks"
for p in list(root.glob("*.html")) + list((root / "articles").glob("*.html")):
    t = p.read_text(encoding="utf-8")
    t2 = (
        t.replace("v=20261003b", new_v)
        .replace("v=20261013a", new_v)
        .replace("v=20261014a", new_v)
        .replace("v=20261014b", new_v)
    )
    if t2 != t:
        p.write_text(t2, encoding="utf-8")
        print("bumped", p.name)

out = root / "deploy_lead_telegram_fix.zip"
files = [
    "app/server.py",
    "js/lead-api.js",
    "index.html",
    "b2b.html",
    "dekoral.html",
    "tikkurila.html",
    "industrial.html",
    "blog.html",
    "faq.html",
    "articles/epoksidnye-poly.html",
    "articles/klirmat-uzbekistana.html",
    "articles/rasxod-tikkurila.html",
]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for f in files:
        z.write(root / f, f)
print("ZIP", out, out.stat().st_size)

js = (root / "js" / "lead-api.js").read_text(encoding="utf-8")
srv = (root / "app" / "server.py").read_text(encoding="utf-8")
print("frontend isLeadAccepted=", "isLeadAccepted" in js)
print("backend BackgroundTasks=", "BackgroundTasks" in srv)
print("backend telegram_notify_background=", "telegram_notify_background" in srv)
print(
    "lead schedules background=",
    "background_tasks.add_task(telegram_notify_background" in srv,
)
