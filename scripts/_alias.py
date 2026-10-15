from pathlib import Path
js = Path(r"C:\Users\user\Projects\Website\js")
for name in ["catalog-data-tikkurila.js", "catalog-data-dekoral.js"]:
    p = js / name
    t = p.read_text(encoding="utf-8")
    alias = "window.CATALOG_EMBED=window.CATALOG_EMBED||window.CATALOG_EMBED||{};window.CATALOG_EMBED=window.CATALOG_EMBED;\n"
    if "window.CATALOG_EMBED=window.CATALOG_EMBED" not in t:
        p.write_text(alias + t, encoding="utf-8")
        print("aliased", name)
    else:
        print("already", name)
