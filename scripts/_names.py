from pathlib import Path
html = Path(r"C:\Users\user\Projects\Website\tikkurila.html").read_text(encoding="utf-8")
start = html.find("js/") 
# find catalog-data script
idx = html.find("catalog-data")
print(repr(html[idx:idx+40]))
print("exists data file", Path(r"C:\Users\user\Projects\Website\js\catalog-data-tikkurila.js").exists())
print("exists data file alt", Path(r"C:\Users\user\Projects\Website\js\catalog-data-tikkurila.js").exists())
