from pathlib import Path
import json, re
root = Path(r"C:\Users\user\Projects\Website")
html = (root / "tikkurila.html").read_text(encoding="utf-8")
print("css", re.search(r"main\.(page[^\s{]+)", html).group(1))
print("html main", re.search(r"<main class=.([^\"]+).", html).group(1))
print("init", re.findall(r"init\w+Catalog\([^)]*\)", html))
print("scripts", re.findall(r"<script src=\"([^\"]+)\"", html))
tik = json.loads((root / "data" / "tikkurila-catalog.json").read_text(encoding="utf-8"))
dek = json.loads((root / "data" / "dekoral-catalog.json").read_text(encoding="utf-8"))
print("counts", len(tik), len(dek), list(tik[0].keys()))
