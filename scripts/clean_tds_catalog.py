# -*- coding: utf-8 -*-
"""Clean extracted TDS JSON: names from filenames, drop junk specs, recategorize."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(r"C:\Users\user\Projects\Website")
DATA = ROOT / "data"

SPEC_LABELS = {
    "base": "База",
    "gloss": "Блеск",
    "coverage": "Расход",
    "density": "Плотность",
    "dry_residue": "Сухой остаток",
    "drying": "Высыхание",
    "application": "Нанесение",
    "thinner": "Разбавитель",
    "wash": "Стойкость к мытью",
    "layers": "Слои",
    "pack": "Фасовка",
}

JUNK_SPEC = re.compile(
    r"^(разбавитель|способ нанесения|время высыхания|плотность|стойкость|расход|блеск|"
    r"база|цвет|фасовка|технические|дата редакции|может быть окрашен|можно колеровать|"
    r"\(%\)|класс)",
    re.I,
)

TIK_MAP = [
    ("wood", ("Valtti", "Vinha", "Pika-Teho", "Supi")),
    ("industrial", ("Tema", "Fontecryl", "Fontedur", "Fontefire", "Fontefloor", "Dicco", "Akvilac", "Akvi", "Temaline", "Grafotherm")),
    ("metal", ("Panssari", "Rostex", "Miranol")),
    ("floor", ("Betolux", "Parketti", "Pontti")),
    ("furniture", ("Helmi", "Empire", "Intact", "Kiva", "Unica", "Merit", "Liitu")),
    ("facade", ("Finngard",)),
    ("primer", ("Primer", "Pohjuste", "Otex", "Hydrosoli", "Isolator")),
    ("filler", ("Presto", "Colofill", "Spakkeli", "Hole-in-1")),
    ("cleaner", ("pesu", "Homeenpoisto", "Ohenne", "Lakkabensiini", "Ruiskuohenne")),
    ("interior", ("Euro", "Harmony", "Joker", "Luja", "Feelings", "Argentum", "Mute", "Taika", "Assa", "Yassa")),
]

DEK_MAP = [
    ("interior", ("Bathroom", "Interior", "Living", "Kitchen", "ceiling", "Washable", "Ванн", "Интерьер", "потолк", "Моющаяся", "ГОСТИН", "КУХН", "ДЛЯ ПОТОЛ", "стен и потол")),
    ("facade", ("Facade", "Фасад", "Master")),
    ("metal", ("Antykor", "anticorrosive", "Multimetal", "Emakol", "chlorokauczuk", "express", "kaloryfer", "Emalia", "Rdze")),
    ("primer", ("Primer", "Podklad", "грунт", "Gрунт")),
    ("floor", ("Farba do betonu", "для бетона", "полы", "Antypo", "краска для пола")),
]


def decode_utf8_hex_runs(s: str) -> str:
    def repl(m: re.Match[str]) -> str:
        parts = [p for p in m.group(0).split("_") if p]
        try:
            return bytes(int(p, 16) for p in parts).decode("utf-8")
        except Exception:
            return m.group(0)

    return re.sub(r"(?:[0-9A-Fa-f]{2}_)+[0-9A-Fa-f]{2}", repl, s)


def pretty_from_file(source: str, brand: str) -> str:
    stem = Path(source).stem
    stem = re.sub(r"^(dekoral_|tikkurila_)", "", stem, flags=re.I)
    stem = re.sub(r"_KK-KZ_PDS_Tikkurila.*$", "", stem, flags=re.I)
    stem = re.sub(r"_KK-KZ_PDS_Dekoral.*$", "", stem, flags=re.I)
    stem = re.sub(r"_RUS(_Final)?.*$", "", stem, flags=re.I)
    stem = re.sub(r"_PDS.*$", "", stem, flags=re.I)
    stem = re.sub(r"_TDS.*$", "", stem, flags=re.I)
    stem = stem.replace("Dekoral_", "").replace("dekoral_", "")
    # encoded Ässä -> professional line
    stem = stem.replace("_C3_84ss_C3_A4_", "Assa ")
    stem = stem.replace("C3_84ss_C3_A4_", "Assa ")
    stem = stem.replace("C3_B6ljy", "oljy")
    stem = stem.replace("Paneeli-_C3_84ss_C3_A4", "Paneeli-Assa")
    stem = stem.replace("Parketti-_C3_84ss_C3_A4", "Parketti-Assa")
    stem = decode_utf8_hex_runs(stem)
    stem = re.sub(r"_+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" ._")
    if brand == "tikkurila":
        return stem or "Tikkurila"
    if stem and not stem.lower().startswith("dekoral"):
        return "Dekoral " + stem
    return stem or "Dekoral"


def tidy_pdf_name(name: str) -> str:
    n = re.sub(r"\s+", " ", name).strip()
    n = re.sub(r"\s*[—–-]\s*Область применения.*$", "", n, flags=re.I)
    n = re.sub(r"\s*[—–-]\s*Технические.*$", "", n, flags=re.I)
    return n.strip(" —–-")


def categorize(name: str, blob: str, brand: str) -> str:
    table = DEK_MAP if brand == "dekoral" else TIK_MAP
    hay = f"{name} {blob}"
    for cat, keys in table:
        for k in keys:
            if k.lower() in hay.lower():
                return cat
    return "other"


def clean_specs(specs: dict) -> dict:
    out = {}
    for k, v in (specs or {}).items():
        if not v or not str(v).strip():
            continue
        val = re.sub(r"\s+", " ", str(v)).strip()
        val = re.sub(r"Дата редакции.*$", "", val).strip()
        if len(val) < 2 or JUNK_SPEC.search(val):
            continue
        if k == "coverage" and not re.search(r"\d.*(м²|м2|л|/l|l\b)", val, re.I):
            continue
        if k == "gloss" and len(val) > 40:
            continue
        if k == "density" and "сухой остаток" in val.lower():
            continue
        if val.lower() in {"waterborne", "вода"}:
            if k == "thinner":
                out[k] = "Вода"
            continue
        out[k] = val[:180]
    return out


def clean_text(val: str | None) -> str | None:
    if not val:
        return None
    t = re.sub(r"\s+", " ", val).strip()
    if len(t) < 40:
        return None
    return t[:420]


def clean_list(path: Path, brand: str) -> list[dict]:
    items = json.loads(path.read_text(encoding="utf-8"))
    out = []
    seen = set()
    for it in items:
        name = pretty_from_file(it.get("sourceFile") or "", brand)
        pdf_name = tidy_pdf_name(it.get("name") or "")
        if (
            brand == "dekoral"
            and 8 < len(pdf_name) < 90
            and "характеристик" not in pdf_name.lower()
            and "инструмент" not in pdf_name.lower()
            and "рекомендуемые" not in pdf_name.lower()
        ):
            name = pdf_name
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        purpose = clean_text(it.get("purpose")) or clean_text(it.get("objects")) or clean_text(it.get("summary"))
        blob = f"{name} {purpose or ''}"
        rec = {
            "id": it.get("id") or name.lower().replace(" ", "-"),
            "brand": brand,
            "name": name,
            "category": categorize(name, blob, brand),
            "purpose": purpose,
            "specs": clean_specs(it.get("specs") or {}),
            "pdf": it.get("pdf"),
        }
        src = it.get("sourceFile") or ""
        if brand == "dekoral" and not rec["purpose"] and ("KZ_PDS" in src or name.lower() in {"dekoral kz", "dekoral"}):
            continue
        out.append(rec)
    return out


def main() -> None:
    dek = clean_list(DATA / "dekoral-products.json", "dekoral")
    tik = clean_list(DATA / "tikkurila-products.json", "tikkurila")
    (DATA / "dekoral-catalog.json").write_text(json.dumps(dek, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "tikkurila-catalog.json").write_text(json.dumps(tik, ensure_ascii=False, indent=2), encoding="utf-8")
    print("catalog dekoral", len(dek), "tikkurila", len(tik))


if __name__ == "__main__":
    main()
