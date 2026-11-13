"""Collect Symphony color system data from the public Tikkurila KZ catalog."""
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = "https://tikkurila.kz"
COLLECTION = BASE + "/dlya-professionalov/kollektsiya/symphony-color-system"
OUT = Path(__file__).resolve().parents[1] / "data" / "symphony-colors.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; OptimusCA-catalog/1.0)"}

CATEGORIES = [
    ("17254", "whites", "Белые"),
    ("17255", "yellows-oranges", "Желтые и оранжевые"),
    ("17250", "greens", "Зеленые"),
    ("17252", "reds", "Красные"),
    ("17247", "blues-violets", "Синие и фиолетовые"),
    ("17253", "warm-earth", "Теплые цвета природы"),
    ("17249", "cold-earth", "Холодные цвета природы"),
]

ARTICLE_RE = re.compile(
    r'<article class="node node--type-color node--view-mode-search-grid.*?</article>',
    re.S,
)
CODE_RE = re.compile(r"^([A-Z]{1,4}\d{2,4})\s*(.*)$")


def fetch(url, attempts=4):
    last = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=45) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:
            last = exc
            time.sleep(0.6 * (i + 1))
    raise RuntimeError(f"{url} :: {last}")


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#039;", "'")
    return re.sub(r"\s+", " ", text).strip()


def parse_cards(html):
    colors = []
    for block in ARTICLE_RE.findall(html):
        hex_m = re.search(r"background-color:\s*(#[0-9A-Fa-f]{3,8})", block)
        id_m = re.search(r'data-id="(\d+)"', block)
        title_m = re.search(r'data-title="([^"]*)"', block)
        href_m = re.search(r'href="(/dlya-professionalov/cveta/[^"]+)"', block)
        label_m = re.search(r'class="color-title">\s*<span>\s*([^<]+)', block)
        img_m = re.search(r'<img[^>]+src="([^"]+)"', block)
        label = clean(label_m.group(1) if label_m else "")
        code, name = "", ""
        matched = CODE_RE.match(label)
        if matched:
            code, name = matched.group(1), matched.group(2).strip()
        else:
            name = label
        data_title = clean(title_m.group(1) if title_m else "")
        if not name and data_title and data_title != code:
            name = data_title
        if name == code:
            name = ""
        image = ""
        if img_m:
            image = img_m.group(1)
            if image.startswith("/"):
                image = BASE + image
        colors.append(
            {
                "id": id_m.group(1) if id_m else "",
                "code": code,
                "name": name,
                "hex": (hex_m.group(1).upper() if hex_m else ""),
                "url": BASE + href_m.group(1) if href_m else "",
                "image": image,
            }
        )
    return colors


def listing_pages(extra_query=""):
    found = []
    seen = set()
    page = 0
    while page < 80:
        url = COLLECTION + "?" + (extra_query + "&" if extra_query else "") + f"page={page}"
        if page == 0 and not extra_query:
            url = COLLECTION
        elif page == 0:
            url = COLLECTION + "?" + extra_query
        html = fetch(url)
        cards = parse_cards(html)
        new = [c for c in cards if c["id"] and c["id"] not in seen]
        for c in new:
            seen.add(c["id"])
        found.extend(new)
        has_next = 'rel="next"' in html or "Следующая страница" in html
        print(f"page {page}: {len(cards)} cards, {len(new)} new, total {len(found)}", flush=True)
        if not cards or not has_next:
            break
        page += 1
    return found


def hex_to_rgb(value):
    h = value.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) != 6:
        return None
    return [int(h[i : i + 2], 16) for i in (0, 2, 4)]


def parse_detail(html):
    info = {}
    for header, desc in re.findall(
        r'color-info--header">\s*([^<]+)\s*</p>\s*<p class="color-info--description">\s*([^<]+)',
        html,
    ):
        info[clean(header).lower()] = clean(desc)
    description = ""
    desc_m = re.search(r'class="color-description">(.*?)</div>', html, re.S)
    if desc_m:
        description = clean(desc_m.group(1))
    collections = []
    block = re.search(r"details__color-collections(.*?)</details>", html, re.S)
    if block:
        collections = [clean(x) for x in re.findall(r"<a [^>]*>([^<]+)</a>", block.group(1))]
    return {
        "rgb_text": info.get("rgb", ""),
        "lab": info.get("lab", ""),
        "ncs": info.get("ncs", ""),
        "description": description,
        "collections": collections,
    }


def enrich(color):
    if not color.get("url"):
        return color
    html = fetch(color["url"])
    color.update(parse_detail(html))
    return color


def main():
    print("listing", flush=True)
    colors = listing_pages()
    by_id = {c["id"]: c for c in colors}
    print("categories", flush=True)
    for cid, slug, label in CATEGORIES:
        rows = listing_pages(f"filter_color_category%5B{cid}%5D={cid}")
        for row in rows:
            item = by_id.get(row["id"])
            if not item:
                row["categories"] = [{"id": cid, "slug": slug, "label": label}]
                by_id[row["id"]] = row
                continue
            item.setdefault("categories", [])
            if not any(x["id"] == cid for x in item["categories"]):
                item["categories"].append({"id": cid, "slug": slug, "label": label})
    ordered = list(by_id.values())
    print(f"enrich {len(ordered)}", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(enrich, c): c["id"] for c in ordered}
        for fut in as_completed(futures):
            done += 1
            try:
                fut.result()
            except Exception as exc:
                cid = futures[fut]
                by_id[cid]["error"] = str(exc)
            if done % 100 == 0 or done == len(ordered):
                print(f"enriched {done}/{len(ordered)}", flush=True)
                save(ordered)
    save(ordered)
    print("wrote", OUT, "count", len(ordered), flush=True)


def save(colors):
    payload = {
        "source": COLLECTION,
        "collection": "Symphony color system",
        "market": "tikkurila.kz",
        "announced_count": 2436,
        "count": len(colors),
        "categories": [
            {"id": cid, "slug": slug, "label": label} for cid, slug, label in CATEGORIES
        ],
        "colors": [],
    }
    for c in colors:
        rgb = hex_to_rgb(c.get("hex") or "")
        payload["colors"].append(
            {
                "id": c.get("id") or "",
                "code": c.get("code") or "",
                "name": c.get("name") or "",
                "hex": c.get("hex") or "",
                "rgb": rgb,
                "rgb_text": c.get("rgb_text") or "",
                "lab": c.get("lab") or "",
                "ncs": c.get("ncs") or "",
                "description": c.get("description") or "",
                "categories": c.get("categories") or [],
                "collections": c.get("collections") or [],
                "url": c.get("url") or "",
                "image": c.get("image") or "",
                "error": c.get("error") or "",
            }
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
