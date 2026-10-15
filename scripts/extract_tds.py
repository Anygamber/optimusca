# -*- coding: utf-8 -*-
"""Extract TDS/PDS fields from Dekoral and Tikkurila PDFs into JSON catalogs."""
from __future__ import annotations

import json
import re
import shutil
import unicodedata
from pathlib import Path

from pdfminer.high_level import extract_text as pdfminer_extract
from pypdf import PdfReader

ROOT = Path(r"C:\Users\user\Projects\Website")
SRC_DEK = ROOT / "Products" / "Dekoral"
SRC_TIK = ROOT / "Products" / "Tikkurila"
OUT_DATA = ROOT / "data"
OUT_TDS_DEK = ROOT / "tds" / "dekoral"
OUT_TDS_TIK = ROOT / "tds" / "tikkurila"

TIK_CATEGORIES = [
    ("wood-oil", ("Valtti", "Vinha", "Pika-Teho", "Supi", "Terrace")),
    ("industrial", ("Tema", "Fontecryl", "Fontedur", "Fontefire", "Fontefloor", "Dicco", "Akvilac", "Akvi ", "Temaline", "Grafotherm")),
    ("metal", ("Panssari", "Rostex", "Miranol")),
    ("floor", ("Betolux", "Parketti", "Pontti Floor")),
    ("furniture", ("Helmi", "Empire", "Intact", "Kiva", "Unica", "Merit")),
    ("facade", ("Finngard",)),
    ("primer", ("Primer", "Pohjuste", "Otex", "Euro Primer", "Uni Primer", "Isolator")),
    ("filler", ("Presto", "Colofill", "Spakkeli", "Hole-in-1")),
    ("cleaner", ("pesu", "Homeenpoisto", "Maalipesu", "Pensselipesu", "Panssaripesu", "Ohenne", "Lakkabensiini", "Ruiskuohenne")),
    ("interior", ("Euro", "Harmony", "Joker", "Luja", "Feelings", "Argentum", "Liitu", "Mute", "Taika")),
]

DEK_CATEGORIES = [
    ("facade", ("Facade", "Фасад", "Master")),
    ("metal", ("Antykor", "anticorrosive", "Multimetal", "Emakol", "chlorokauczuk", "express", "kaloryfer", "Emalia")),
    ("primer", ("Primer", "Podklad", "грунт")),
    ("floor", ("betonu", "бетон", "Antypo")),
    ("interior", ("Bathroom", "Interior", "Living", "Kitchen", "ceiling", "Washable", "Ванн", "Интерьер", "потолок", "Моющ")),
]


def slugify(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = re.sub(r"[^\w\s.-]", "", name, flags=re.UNICODE)
    name = re.sub(r"\s+", "-", name.strip())
    return name[:80] or "product"


def normalize_spaces(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\u00ad", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def collapse_char_newlines(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return text
    short = sum(1 for ln in lines if len(ln.strip()) <= 2)
    if short / max(len(lines), 1) < 0.45:
        return text
    buf = []
    word = []
    for ln in lines:
        s = ln.strip()
        if s == "":
            if word:
                buf.append("".join(word))
                word = []
            buf.append("")
            continue
        if len(s) == 1:
            word.append(s)
        else:
            if word:
                buf.append("".join(word))
                word = []
            buf.append(s)
    if word:
        buf.append("".join(word))
    return "\n".join(buf)


def undouble_headers(text: str) -> str:
    text = re.sub(r"^(.+)\n\1\s*$", r"\1", text, flags=re.M)
    text = re.sub(r"([А-ЯA-Z]{3,})\1", r"\1", text)
    return text


def pypdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return collapse_char_newlines("\n".join(parts))


def pdfminer_text(path: Path) -> str:
    try:
        return pdfminer_extract(str(path)) or ""
    except Exception:
        return ""


def extract_raw(path: Path, brand: str) -> str:
    if brand == "tikkurila":
        text = pdfminer_text(path)
        if len(text) < 400:
            text = pypdf_text(path)
    else:
        text = pypdf_text(path)
        if len(text) < 200:
            text = pdfminer_text(path)
    return undouble_headers(normalize_spaces(text))


def first_match(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, re.I | re.S)
    if not m:
        return None
    val = re.sub(r"\s+", " ", m.group(1)).strip()
    return val[:500] if val else None


def clip_section(text: str, start: str, ends: list[str], max_len: int = 700) -> str | None:
    m = re.search(start, text, re.I)
    if not m:
        return None
    rest = text[m.end() :]
    cut = len(rest)
    for end in ends:
        em = re.search(end, rest, re.I)
        if em:
            cut = min(cut, em.start())
    val = re.sub(r"\s+", " ", rest[:cut]).strip(" :.\n")
    if not val:
        return None
    return val[:max_len]


def name_from_filename(path: Path, brand: str) -> str:
    stem = path.stem
    stem = re.sub(r"^(dekoral_|tikkurila_)", "", stem, flags=re.I)
    stem = re.sub(r"_KK-KZ_PDS_Tikkurila.*$", "", stem, flags=re.I)
    stem = re.sub(r"_RUS(_Final)?.*$", "", stem, flags=re.I)
    stem = re.sub(r"_PDS.*$", "", stem, flags=re.I)
    stem = re.sub(r"_TDS.*$", "", stem, flags=re.I)
    stem = stem.replace("_", " ").replace("-", " ")
    stem = re.sub(r"\s+", " ", stem).strip(" ._")
    if brand == "dekoral" and stem and not stem.lower().startswith("dekoral"):
        stem = "Dekoral " + stem
    return stem or path.stem


def tikkurila_name(text: str, path: Path) -> str:
    m = re.search(r"TIKKURILA\s*\n([A-Z0-9][A-Z0-9 \-_/]{2,60})\s*\n([^\n]{0,80})?", text)
    if m:
        en = re.sub(r"\s+", " ", m.group(1)).strip()
        ru = (m.group(2) or "").strip()
        if ru and not ru.upper().startswith("ТИП") and "СПЕЦИФИКАЦИЯ" not in ru.upper():
            return f"{en} / {ru}" if ru != en else en
        return en
    return name_from_filename(path, "tikkurila")


def dekoral_name(text: str, path: Path) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    skip = ("ТОО", "Тиккурила", "Республика", "телефон", "dekoral-paints", "Казахстан")
    for i, ln in enumerate(lines[:30]):
        if any(s in ln for s in skip):
            continue
        if len(ln) < 4:
            continue
        if ln[0].isupper():
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if nxt and 8 < len(nxt) < 90 and not nxt.startswith("ОБЛАСТЬ"):
                return f"{ln} — {nxt}"
            return ln
    return name_from_filename(path, "dekoral")


def categorize(name: str, text: str, brand: str) -> str:
    blob = f"{name} {text[:1500]}"
    table = DEK_CATEGORIES if brand == "dekoral" else TIK_CATEGORIES
    for cat, keys in table:
        for k in keys:
            if k.lower() in blob.lower():
                return cat
    return "other"


def specs_from_text(text: str) -> dict:
    specs = {}
    mapping = [
        ("base", r"(?:База|База краски|Базы)\s*[:\n]?\s*([A-ZА-Я0-9 /,\-]{1,80})"),
        ("gloss", r"(?:Степень блеска|Блеск)\s*[:\n]?\s*([^\n]{3,80})"),
        ("coverage", r"(?:Расход)\s*[:\n]?\s*([^\n]{3,160}(?:\n[^\n]{0,80})?)"),
        ("density", r"(?:Плотность[^\n]{0,20})\s*[:\n]?\s*([^\n]{3,80})"),
        ("dry_residue", r"(?:Сухой остаток[^\n]{0,40})\s*[:\n%]?\s*([^\n]{2,80})"),
        ("layers", r"(?:Количество слоев|Количество слоёв)\s*[:\n]?\s*([^\n]{1,40})"),
        ("drying", r"(?:Время высыхания[^\n]{0,40})\s*[:\n]?\s*([^\n]{3,180})"),
        ("application", r"(?:Способ нанесения)\s*[:\n]?\s*([^\n]{3,120})"),
        ("thinner", r"(?:Разбавитель)\s*[:\n]?\s*([^\n]{2,80})"),
        ("pack", r"(?:Фасовка)\s*[:\n]?\s*([^\n]{3,160}(?:\n[^\n]{0,60})?)"),
        ("wash", r"(?:Стойкость к мытью|Стойкость к влажному истиранию|Стойкость к мокрому истиранию)\s*[:\n]?\s*([^\n]{3,180})"),
    ]
    for key, pat in mapping:
        val = first_match(pat, text)
        if val:
            specs[key] = val[:220]
    if "coverage" not in specs:
        m = re.search(r"(\d+\s*[–\-]\s*\d+\s*м[²2]/л[^\n]{0,80})", text)
        if m:
            specs["coverage"] = re.sub(r"\s+", " ", m.group(1))
    if "gloss" not in specs:
        m = re.search(
            r"\n(глубокоматовый|матовый|полуматовый|полуглянцевый|глянцевый|шелковисто-матовый)[^\n]{0,20}\n",
            text,
            re.I,
        )
        if m:
            specs["gloss"] = m.group(1)
    if "density" not in specs:
        m = re.search(r"(около\s+[\d,.\s\-]+ кг/л[^\n]{0,40})", text, re.I)
        if m:
            specs["density"] = re.sub(r"\s+", " ", m.group(1))
    if "application" not in specs:
        m = re.search(r"(Наносится[^\n]{5,80})", text)
        if m:
            specs["application"] = re.sub(r"\s+", " ", m.group(1))
    if "thinner" not in specs and re.search(r"Разбавитель[:\s]*вода", text, re.I):
        specs["thinner"] = "Вода"
    return specs


def parse_product(path: Path, brand: str) -> dict:
    raw = extract_raw(path, brand)
    if brand == "tikkurila":
        name = tikkurila_name(raw, path)
        purpose = clip_section(
            raw,
            r"ОБЛАСТЬ\s*ПРИМЕНЕНИЯ",
            [r"ОБЪЕКТЫ\s*ПРИМЕНЕНИЯ", r"ТЕХНИЧЕСКИЕ ДАННЫЕ", r"\nТИП\s*\n"],
        )
        ptype = clip_section(raw, r"\nТИП\s*\n", [r"ОБЛАСТЬ", r"ТЕХНИЧЕСКИЕ"])
        objects = clip_section(raw, r"ОБЪЕКТЫ\s*ПРИМЕНЕНИЯ", [r"ТЕХНИЧЕСКИЕ ДАННЫЕ", r"ИНСТРУКЦИЯ"])
    else:
        name = dekoral_name(raw, path)
        purpose = clip_section(
            raw,
            r"ОБЛАСТЬ ПРИМЕНЕНИЯ",
            [r"ОСНОВНЫЕ", r"ТЕХНИЧЕСКИЕ", r"ПРИМЕНЕНИЕ"],
        )
        ptype = None
        objects = None

    specs = specs_from_text(raw)
    cat = categorize(name, raw, brand)
    dest_dir = OUT_TDS_DEK if brand == "dekoral" else OUT_TDS_TIK
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{slugify(path.stem)}.pdf"
    if not dest.exists():
        shutil.copy2(path, dest)

    summary = purpose or ptype or objects or ""
    return {
        "id": slugify(name.lower()),
        "brand": brand,
        "name": name,
        "category": cat,
        "type": ptype,
        "purpose": purpose,
        "objects": objects,
        "specs": specs,
        "pdf": dest.relative_to(ROOT).as_posix(),
        "sourceFile": path.name,
        "summary": summary[:280],
    }


def uniq(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for it in items:
        key = it["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def run() -> None:
    OUT_DATA.mkdir(exist_ok=True)
    dekoral, tikkurila, errors = [], [], []

    for pdf in sorted(SRC_DEK.glob("*.pdf")):
        try:
            dekoral.append(parse_product(pdf, "dekoral"))
            print("OK dekoral", pdf.name)
        except Exception as e:
            errors.append({"file": pdf.name, "error": str(e)})
            print("FAIL dekoral", pdf.name, e)

    for pdf in sorted(SRC_TIK.glob("*.pdf")):
        try:
            tikkurila.append(parse_product(pdf, "tikkurila"))
            print("OK tikkurila", pdf.name)
        except Exception as e:
            errors.append({"file": pdf.name, "error": str(e)})
            print("FAIL tikkurila", pdf.name, e)

    dekoral = uniq(dekoral)
    tikkurila = uniq(tikkurila)

    (OUT_DATA / "dekoral-products.json").write_text(
        json.dumps(dekoral, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DATA / "tikkurila-products.json").write_text(
        json.dumps(tikkurila, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DATA / "tds-extract-errors.json").write_text(
        json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("dekoral", len(dekoral), "tikkurila", len(tikkurila), "errors", len(errors))
    try:
        from notify_done import notify_done

        notify_done()
    except Exception:
        pass


if __name__ == "__main__":
    run()
