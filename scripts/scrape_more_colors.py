"""Add every non-Symphony color published on tikkurila.kz."""
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import scrape_symphony as base

ROOT = Path(__file__).resolve().parents[1]
SYMPHONY = ROOT / "data" / "symphony-colors.json"
EXTRA = ROOT / "data" / "tikkurila-extra-colors.json"
MERGED = ROOT / "data" / "tikkurila-colors.json"
SITEMAP = ROOT / "_sitemap_urls.txt"

CODE_PATTERNS = [
    re.compile(r"^(RAL\s+\d{3,4}(?:[- ]\d{1,2}|[- ][A-Z])?)\s*(.*)$", re.I),
    re.compile(r"^(TVT\s*[A-Z]?\s*\d+)\s*(.*)$", re.I),
    re.compile(r"^(\d{3,5}\s+[A-Z]{1,4})\s+(.+)$"),
    re.compile(r"^([A-Z]{1,4}\d{2,4})\s*(.*)$"),
    re.compile(r"^(\d{3,5})\s+(.+)$"),
    re.compile(r"^(\d{3,5})$"),
]


def split_title(title):
    title = base.clean(title)
    for pattern in CODE_PATTERNS:
        matched = pattern.match(title)
        if not matched:
            continue
        code = re.sub(r"\s+", " ", matched.group(1)).upper()
        name = matched.group(2).strip() if matched.lastindex and matched.lastindex >= 2 else ""
        if name.upper() == code:
            name = ""
        return code, name
    return title.upper(), ""


def parse_page(html, url):
    title_m = re.search(r'<h1 class="product-title">(.*?)</h1>', html, re.S)
    title = base.clean(title_m.group(1) if title_m else "")
    code, name = split_title(title)
    hex_m = re.search(
        r"color-header-info[^>]*background-color:\s*(#[0-9A-Fa-f]{3,8})",
        html,
    )
    id_m = re.search(r'data-id="(\d+)"\s+data-title="', html)
    keywords = re.search(r'<meta name="keywords" content="([^"]*)"', html)
    if keywords and not code:
        bits = [base.clean(x) for x in keywords.group(1).split(",")]
        if bits and bits[0]:
            code = bits[0].upper()
            if len(bits) > 1 and not name:
                name = bits[1]
    detail = base.parse_detail(html)
    image_m = re.search(
        r'<img[^>]+src="([^"]+)"[^>]*class="image-style-scale-crop-extra-large',
        html,
    )
    image = ""
    if image_m:
        image = image_m.group(1)
        if image.startswith("/"):
            image = base.BASE + image
    return {
        "id": id_m.group(1) if id_m else "",
        "code": code,
        "name": name,
        "title": title,
        "hex": hex_m.group(1).upper() if hex_m else "",
        "rgb": base.hex_to_rgb(hex_m.group(1) if hex_m else ""),
        "rgb_text": detail.get("rgb_text") or "",
        "lab": detail.get("lab") or "",
        "ncs": detail.get("ncs") or "",
        "description": detail.get("description") or "",
        "categories": [],
        "collections": detail.get("collections") or [],
        "url": url,
        "image": image,
    }


def new_urls(known_slugs):
    found = []
    seen = set()
    for raw in SITEMAP.read_text(encoding="utf-8").splitlines():
        path = re.sub(r"^https?://[^/]+", "", raw.strip())
        if not re.search(r"/cveta/[^/]+$", path):
            continue
        slug = path.rstrip("/").split("/")[-1]
        if slug in known_slugs or slug in seen:
            continue
        seen.add(slug)
        found.append(base.BASE + "/cveta/" + slug)
    return found


def load_extra():
    if not EXTRA.exists():
        return {}
    data = json.loads(EXTRA.read_text(encoding="utf-8"))
    return {item["url"]: item for item in data.get("colors", []) if item.get("url")}


def save_extra(by_url):
    colors = list(by_url.values())
    payload = {
        "source": "https://tikkurila.kz/",
        "note": "Colors on tikkurila.kz that are not in the Symphony extract.",
        "count": len(colors),
        "colors": colors,
    }
    EXTRA.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def merge(symphony_colors, extra_colors):
    seen_codes = {c["code"] for c in symphony_colors}
    added = []
    skipped = 0
    for color in extra_colors:
        code = color.get("code") or ""
        if not color.get("hex") or not code:
            skipped += 1
            continue
        if code in seen_codes:
            skipped += 1
            continue
        seen_codes.add(code)
        added.append(color)
    added.sort(key=lambda c: c["code"])
    payload = {
        "source": "https://tikkurila.kz/",
        "count": len(symphony_colors) + len(added),
        "symphony_count": len(symphony_colors),
        "added_count": len(added),
        "skipped_without_swatch_or_duplicate": skipped,
        "colors": symphony_colors + added,
    }
    MERGED.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main():
    symphony = json.loads(SYMPHONY.read_text(encoding="utf-8"))
    known_slugs = {c["url"].rstrip("/").split("/")[-1] for c in symphony["colors"]}
    urls = new_urls(known_slugs)
    done = load_extra()
    pending = [u for u in urls if u not in done]
    print(f"new urls {len(urls)} already {len(done)} pending {len(pending)}", flush=True)

    def grab(url):
        html = base.fetch(url)
        return parse_page(html, url)

    finished = len(done)
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(grab, url): url for url in pending}
        for fut in as_completed(futures):
            url = futures[fut]
            finished += 1
            try:
                done[url] = fut.result()
            except Exception as exc:
                done[url] = {"url": url, "code": "", "hex": "", "error": str(exc)}
            if finished % 100 == 0 or finished == len(urls):
                save_extra(done)
                print(f"saved {finished}/{len(urls)}", flush=True)
    save_extra(done)
    symphony_colors = []
    for color in symphony["colors"]:
        item = dict(color)
        item.pop("error", None)
        symphony_colors.append(item)
    payload = merge(symphony_colors, list(done.values()))
    print(
        f"merged {payload['count']} symphony {payload['symphony_count']} added {payload['added_count']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
