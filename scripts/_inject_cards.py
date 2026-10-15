from pathlib import Path
import json, html, re
root = Path(r"C:\Users\user\Projects\Website")

def cards(items, n=4):
    out = []
    for it in items[:n]:
        name = html.escape(it.get("name") or "")
        purpose = html.escape((it.get("purpose") or "")[:220])
        pdf = html.escape(it.get("pdf") or "#")
        out.append(
            f'<article class="catalog-card"><h3>{name}</h3><p>{purpose}</p>'
            f'<a href="{pdf}" target="_blank" rel="noopener">Скачать TDS (PDF)</a></article>'
        )
    return "\n          ".join(out)

tik = json.loads((root/"data/tikkurila-catalog.json").read_text(encoding="utf-8"))
dek = json.loads((root/"data/dekoral-catalog.json").read_text(encoding="utf-8"))

def inject(page, snippet):
    text = (root/page).read_text(encoding="utf-8")
    text2, n = re.subn(
        r'(<div data-catalog-grid class="catalog-grid mt-8">)(.*?)(</div>)',
        r'\1\n          ' + snippet.replace("\\", "\\\\") + r'\n        \3',
        text,
        count=1,
        flags=re.S,
    )
    print(page, "replacements", n)
    if n:
        (root/page).write_text(text2, encoding="utf-8")

inject("tikkurila.html", cards(tik))
inject("dekoral.html", cards(dek))
print("tik names", [x["name"] for x in tik[:4]])
