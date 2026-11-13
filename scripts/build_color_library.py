"""Build the compact color-library payload used by colors.html."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = json.loads((ROOT / "data" / "tikkurila-colors.json").read_text(encoding="utf-8"))


def group_of(color):
    code = (color.get("code") or "").upper()
    collections = color.get("collections") or []
    if code.startswith("RAL ") and "-" in code:
        return "RAL Effect"
    if code.startswith("RAL "):
        return "RAL Classic"
    if any("Symphony" in name for name in collections):
        return "Symphony"
    if collections:
        return collections[0]
    return "Другие"


colors = []
for color in source["colors"]:
    rgb = color.get("rgb") or []
    rgb_text = ", ".join(str(channel) for channel in rgb) if len(rgb) == 3 else ""
    colors.append(
        {
            "code": color.get("code") or "",
            "name": color.get("name") or "",
            "hex": color.get("hex") or "",
            "rgb": rgb_text,
            "ncs": color.get("ncs") or "",
            "group": group_of(color),
        }
    )

payload = "window.COLOR_LIBRARY = " + json.dumps(colors, ensure_ascii=False, separators=(",", ":")) + ";\n"
out = ROOT / "js" / "color-library-data.js"
out.write_text(payload, encoding="utf-8")
print(f"{len(colors)} colors -> {out} ({out.stat().st_size} bytes)")
