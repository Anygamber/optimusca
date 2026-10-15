from pathlib import Path
import re, subprocess, sys

root = Path(__file__).resolve().parents[1]
for f in ["js/catalog.js", "js/catalog-data-tikkurila.js", "js/catalog-data-dekoral.js"]:
    r = subprocess.run(["node", "--check", str(root / f)], capture_output=True, text=True)
    print(f, "ok" if r.returncode == 0 else r.stderr)

t = (root / "tikkurila.html").read_text(encoding="utf-8")
j = (root / "js" / "catalog.js").read_text(encoding="utf-8")
print("html attrs", re.findall(r"data-catalog[a-z-]+", t))
print("js attrs", re.findall(r"data-catalog[a-z-]+", j))
print("scripts", re.findall(r'src="js/[^"]+"', t))
print("files", [p.name for p in (root / "js").glob("catalog*")])
d = (root / "js" / "catalog-data-tikkurila.js").read_text(encoding="utf-8")
print("embed prefix", d[:90])
print("js embed refs", re.findall(r"CATALOG_[A-Z]+", j))
print("data embed refs", re.findall(r"CATALOG_[A-Z]+", d)[:3])
