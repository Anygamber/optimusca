# -*- coding: utf-8 -*-
"""
Extract Russian UI strings from HTML and build ru/uz locale files + wire i18n.js.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "locales"
LOCALES.mkdir(exist_ok=True)

HTML_FILES = [
    ROOT / "index.html",
    ROOT / "b2b.html",
    ROOT / "blog.html",
    ROOT / "faq.html",
    ROOT / "dekoral.html",
    ROOT / "tikkurila.html",
    ROOT / "industrial.html",
    ROOT / "articles" / "epoksidnye-poly.html",
    ROOT / "articles" / "klirmat-uzbekistana.html",
    ROOT / "articles" / "rasxod-tikkurila.html",
]

# Professional LKM terminology (RU → UZ Latin)
TERM = {
    "краска": "bo'yoq",
    "Краска": "Bo'yoq",
    "краски": "bo'yoqlar",
    "Краски": "Bo'yoqlar",
    "грунтовка": "gruntovka",
    "Грунтовка": "Gruntovka",
    "грунт": "grunt",
    "Грунт": "Grunt",
    "колеровка": "kolerovka",
    "Колеровка": "Kolerovka",
    "колеровочная": "kolerovka",
    "аутлет": "outlet",
    "Аутлет": "Outlet",
    "отделочные материалы": "pardozlash materiallari",
    "Отделочные материалы": "Pardozlash materiallari",
    "В наличии": "Mavjud",
    "Под заказ": "Buyurtma bo'yicha",
    "Рассчитать расход": "Sarfni hisoblash",
    "Оформить заказ": "Buyurtmani rasmiylashtirish",
}


def extract_snippets(html: str) -> list[str]:
    cleaned = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    cleaned = re.sub(r"<style[\s\S]*?</style>", " ", cleaned, flags=re.I)
    texts = re.findall(r">([^<]{2,400})<", cleaned)
    attrs = re.findall(
        r'(?:placeholder|aria-label|title|alt|content)="([^"]{2,300})"',
        html,
        flags=re.I,
    )
    out: list[str] = []
    seen: set[str] = set()
    for raw in texts + attrs:
        t = " ".join(raw.split())
        if not t or not re.search(r"[А-Яа-яЁё]", t):
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


# Curated high-quality translations for common UI (override auto / ensure quality)
CURATED: dict[str, str] = {
    "Каталог": "Katalog",
    "Калькулятор": "Kalkulyator",
    "Для бизнеса": "Biznes uchun",
    "Блог": "Blog",
    "FAQ": "FAQ",
    "Филиалы": "Filiallar",
    "Бот": "Bot",
    "Заявка": "Ariza",
    "Заказать консультацию": "Konsultatsiya buyurtma qilish",
    "Скачать прайс": "Praisni yuklab olish",
    "Имя": "Ism",
    "Телефон": "Telefon",
    "Как к вам обращаться": "Sizga qanday murojaat qilaylik",
    "Получить консультацию и прайс": "Konsultatsiya va prais olish",
    "Оставьте контакты — при первой возможности наш менеджер свяжется с вами.": "Kontaktlaringizni qoldiring — imkoniyat boricha menejerimiz siz bilan bog‘lanadi.",
    "Нажимая кнопку, вы соглашаетесь с": "Tugmani bosib, siz rozilik bildirasiz:",
    "политикой конфиденциальности": "maxfiylik siyosati",
    "Каталог материалов: купить покрытия Tikkurila и Dekoral с доставкой": "Materiallar katalogi: Tikkurila va Dekoral qoplamalarini yetkazib berish bilan sotib oling",
    "Все товары": "Barcha mahsulotlar",
    "Для дома и интерьера": "Uy va interyer uchun",
    "Фасады": "Fasadlar",
    "Промышленность (B2B)": "Sanoat (B2B)",
    "Преимущества": "Afzalliklar",
    "Сервис в Ташкенте": "Toshkentdagi xizmat",
    "Применение": "Qo‘llanishi",
    "Для металла и бетона": "Metall va beton uchun",
    "Где применяют": "Qayerda qo‘llaniladi",
    "Запросить колеровку и наличие": "Kolerovka va mavjudlikni so‘rash",
    "Подобрать фасадную систему": "Fasad tizimini tanlash",
    "Заявка на промышленный опт": "Sanoat ulgurji arizasi",
    "Калькулятор расхода краски": "Bo‘yoq sarfi kalkulyatori",
    "Онлайн-расчёт": "Onlayn hisob-kitob",
    "Площадь поверхности": "Sirt maydoni",
    "Интерьерная краска Tikkurila ~1 л на 10–12 м² в 1 слой": "Tikkurila interyer bo‘yog‘i ~1 qatlamda 10–12 m² ga 1 l",
    "Фасадная краска Dekoral ~1 л на 8–10 м² в 1 слой": "Dekoral fasad bo‘yog‘i ~1 qatlamda 8–10 m² ga 1 l",
    "Промышленное покрытие / эпоксид ~1 л на 6–8 м² в 1 слой": "Sanoat qoplamasi / epoksid ~1 qatlamda 6–8 m² ga 1 l",
    "1 слой": "1 qatlam",
    "2 слоя": "2 qatlam",
    "Результат расчёта": "Hisob natijasi",
    "Требуется примерно": "Taxminan kerak",
    "литров": "litr",
    "Рекомендуем фасовку": "Tavsiya etilgan qadoq",
    "Запросить точный расчёт и забронировать цену": "Aniq hisobni so‘rash va narxni bron qilish",
    "Опт, дилерство и складские поставки": "Ulgurji, dilerlik va ombor yetkazib berishlari",
    "Партнерам и оптовикам": "Hamkorlar va ulgurji mijozlarga",
    "Техническая поддержка инженеров": "Muhandislarning texnik yordami",
    "Инженерия": "Muhandislik",
    "Открыть AI технолога в Telegram": "Telegramda AI-texnologni ochish",
    "Получить расчёт у инженера в Telegram": "Telegramda muhandisdan hisob olish",
    "Почему выбирают нас как дистрибьютора в Ташкенте": "Nega Toshkentda bizni distribyutor sifatida tanlashadi",
    "Официальный дистрибьютор": "Rasmiy distribyutor",
    "Гарантия оригинальности": "Asl mahsulot kafolati",
    "Колеровка за 5 минут": "5 daqiqada kolerovka",
    "Помощь в расчёте объёмов": "Hajmlarni hisoblashda yordam",
    "Часто задаваемые вопросы": "Ko‘p so‘raladigan savollar",
    "Смотреть все вопросы и ответы": "Barcha savol-javoblarni ko‘rish",
    "Контакты дистрибьютора Tikkurila и Dekoral в Ташкенте": "Toshkentdagi Tikkurila va Dekoral distribyutori kontaktlari",
    "Контакты": "Kontaktlar",
    "Главный офис управления": "Bosh boshqaruv ofisi",
    "Торговая точка (Осиё)": "Savdo nuqtasi (Osiyo)",
    "Торговая точка (Павильон)": "Savdo nuqtasi (Pavilion)",
    "ТТ Навои": "SN Navoiy",
    "ТТ Ш. Руставелли": "SN Sh. Rustaveli",
    "Копировать": "Nusxa olish",
    "Открыть на карте": "Xaritada ochish",
    "Заказать консультацию — оставьте заявку": "Konsultatsiya buyurtma qiling — ariza qoldiring",
    "Перезвоним, уточним задачу и отправим актуальный прайс Tikkurila и Dekoral.": "Qayta qo‘ng‘iroq qilamiz, vazifani aniqlaymiz va Tikkurila hamda Dekoralning dolzarb praisini yuboramiz.",
    "Продукция": "Mahsulotlar",
    "Почему мы": "Nega biz",
    "Консультация": "Konsultatsiya",
    "Документы": "Hujjatlar",
    "Политика конфиденциальности": "Maxfiylik siyosati",
    "Запросить прайс": "Prais so‘rash",
    "Навигация": "Navigatsiya",
    "Все точки на карте": "Barcha nuqtalar xaritada",
    "Спасибо за заявку!": "Arizangiz uchun rahmat!",
    "Спасибо!": "Rahmat!",
    "Наш менеджер свяжется с вами в самое ближайшее время.": "Menejerimiz siz bilan eng qisqa muddatda bog‘lanadi.",
    "Отлично": "Ajoyib",
    "Закрыть": "Yopish",
    "Отправить заявку": "Arizani yuborish",
    "Отправка…": "Yuborilmoqda…",
    "Расчёт с калькулятора": "Kalkulyator hisobi",
    "Официальный дилер": "Rasmiy diler",
    "Сертификат": "Sertifikat",
    "Сервис": "Xizmat",
    "Расчёт": "Hisob",
    "Ташкент · Узбекистан": "Toshkent · O‘zbekiston",
    "Оптом и в розницу": "Ulgurji va chakana",
    "Самовывоз · ул. Осиё": "O‘zi olib ketish · Osiyo ko‘chasi",
    "Самовывоз · Павильон": "O‘zi olib ketish · Pavilion",
    "Самовывоз · пр. Навои": "O‘zi olib ketish · Navoiy shohko‘chasi",
    "Самовывоз · ул. Шота Руставели": "O‘zi olib ketish · Shota Rustaveli ko‘chasi",
    "Ташкент · управление и оптовые заявки": "Toshkent · boshqaruv va ulgurji arizalar",
    "Оставить заявку": "Ariza qoldirish",
    "ФИО": "F.I.Sh.",
    "ИНН": "STIR",
    "Город": "Shahar",
    "Продукция / интерес": "Mahsulot / qiziqish",
    "Дилерство": "Dilerlik",
    "Опт": "Ulgurji",
    "Партнёрам": "Hamkorlarga",
}


def translate_phrase(ru: str) -> str:
    if ru in CURATED:
        return CURATED[ru]
    # Light terminology pass for longer sentences
    out = ru
    # Order matters: longer phrases first
    replacements = [
        ("отделочные материалы", "pardozlash materiallari"),
        ("Отделочные материалы", "Pardozlash materiallari"),
        ("компьютерная колеровка", "kompyuter kolerovkasi"),
        ("Компьютерная колеровка", "Kompyuter kolerovkasi"),
        ("колеровка", "kolerovka"),
        ("Колеровка", "Kolerovka"),
        ("грунтовка", "gruntovka"),
        ("Грунтовка", "Gruntovka"),
        ("дистрибьютор", "distribyutor"),
        ("Дистрибьютор", "Distribyutor"),
        ("Ташкенте", "Toshkentda"),
        ("Ташкенту", "Toshkentga"),
        ("Ташкента", "Toshkentning"),
        ("Ташкент", "Toshkent"),
        ("Узбекистане", "O‘zbekistonda"),
        ("Узбекистану", "O‘zbekistonga"),
        ("Узбекистан", "O‘zbekiston"),
        ("красок", "bo‘yoqlar"),
        ("краски", "bo‘yoqlar"),
        ("Краски", "Bo‘yoqlar"),
        ("краска", "bo‘yoq"),
        ("Краска", "Bo‘yoq"),
        ("покрытий", "qoplamalar"),
        ("покрытия", "qoplamalar"),
        ("покрытие", "qoplama"),
        ("фасадн", "fasad"),
        ("Фасадн", "Fasad"),
        ("интерьерн", "interyer"),
        ("Интерьерн", "Interyer"),
        ("промышленн", "sanoat"),
        ("Промышленн", "Sanoat"),
        ("эпоксидн", "epoksid"),
        ("Эпоксидн", "Epoksid"),
        ("доставк", "yetkazib berish"),
        ("Доставк", "Yetkazib berish"),
        ("оптом", "ulgurji"),
        ("Оптом", "Ulgurji"),
        ("розниц", "chakana"),
        ("Розниц", "Chakana"),
        ("склад", "ombor"),
        ("Склад", "Ombor"),
        ("официальн", "rasmiy"),
        ("Официальн", "Rasmiy"),
        ("заявк", "ariza"),
        ("Заявк", "Ariza"),
        ("консультац", "konsultatsiya"),
        ("Консультац", "Konsultatsiya"),
        ("менеджер", "menejer"),
        ("Менеджер", "Menejer"),
        ("свяжется", "bog‘lanadi"),
        ("оставьте", "qoldiring"),
        ("Оставьте", "Qoldiring"),
        ("купить", "sotib olish"),
        ("Купить", "Sotib olish"),
        ("расход", "sarf"),
        ("Расход", "Sarf"),
        ("площадь", "maydon"),
        ("Площадь", "Maydon"),
        ("слой", "qatlam"),
        ("слоя", "qatlam"),
        ("слоёв", "qatlam"),
    ]
    for a, b in replacements:
        out = out.replace(a, b)
    # If nothing changed meaningfully, keep curated miss as soft transliteration marker
    if out == ru and len(ru) < 80:
        # simple word-level helpers
        WORD = {
            "и": "va",
            "в": "da",
            "по": "bo‘yicha",
            "для": "uchun",
            "с": "bilan",
            "на": "ga",
            "от": "dan",
            "до": "gacha",
            "или": "yoki",
            "мы": "biz",
            "наш": "bizning",
            "наши": "bizning",
            "все": "barcha",
            "Все": "Barcha",
        }
        parts = out.split(" ")
        out = " ".join(WORD.get(p, p) for p in parts)
    return out


def build_locales() -> tuple[dict, dict]:
    all_snips: list[str] = []
    seen: set[str] = set()
    for path in HTML_FILES:
        if not path.is_file():
            continue
        for s in extract_snippets(path.read_text(encoding="utf-8")):
            if s not in seen:
                seen.add(s)
                all_snips.append(s)

    phrases_ru = {s: s for s in all_snips}
    phrases_uz = {s: translate_phrase(s) for s in all_snips}
    # ensure curated overrides
    for k, v in CURATED.items():
        phrases_uz[k] = v
        phrases_ru.setdefault(k, k)

    ru = {
        "meta": {
            "lang": "ru",
            "title_home": "Официальный дистрибьютор Tikkurila и Dekoral в Ташкенте | MCHJ OPTIMUS CA",
            "description_home": "Поставка оригинальных красок Tikkurila и Dekoral, промышленных покрытий в Ташкенте и по всему Узбекистану. Компьютерная колеровка, точный расчёт на калькуляторе и оптовые поставки.",
        },
        "nav": {
            "catalog": "Каталог",
            "calculator": "Калькулятор",
            "b2b": "Для бизнеса",
            "blog": "Блог",
            "faq": "FAQ",
            "branches": "Филиалы",
            "bot": "Бот",
            "lead": "Заявка",
        },
        "common": {
            "thanks_title": "Спасибо за заявку!",
            "thanks_text": "Наш менеджер свяжется с вами в самое ближайшее время.",
            "thanks_btn": "Отлично",
            "submit": "Отправить заявку",
            "order_consult": "Заказать консультацию",
            "name": "Имя",
            "phone": "Телефон",
            "privacy": "политикой конфиденциальности",
            "calc_spend": "Рассчитать расход",
            "checkout": "Оформить заказ",
            "in_stock": "В наличии",
            "on_order": "Под заказ",
            "paint": "Краска",
            "primer": "Грунтовка",
            "tinting": "Колеровка",
            "outlet": "Outlet",
            "finishing": "Отделочные материалы",
        },
        "lang": {"ru": "RU", "uz": "UZ", "label": "Язык"},
        "phrases": phrases_ru,
    }
    uz = {
        "meta": {
            "lang": "uz",
            "title_home": "Toshkentdagi rasmiy Tikkurila va Dekoral distribyutori | MCHJ OPTIMUS CA",
            "description_home": "Toshkent va butun O‘zbekiston bo‘ylab original Tikkurila va Dekoral bo‘yoqlari, sanoat qoplamalari yetkazib berish. Kompyuter kolerovkasi, kalkulyatorda aniq hisob va ulgurji yetkazib berishlar.",
        },
        "nav": {
            "catalog": "Katalog",
            "calculator": "Kalkulyator",
            "b2b": "Biznes uchun",
            "blog": "Blog",
            "faq": "FAQ",
            "branches": "Filiallar",
            "bot": "Bot",
            "lead": "Ariza",
        },
        "common": {
            "thanks_title": "Arizangiz uchun rahmat!",
            "thanks_text": "Menejerimiz siz bilan eng qisqa muddatda bog‘lanadi.",
            "thanks_btn": "Ajoyib",
            "submit": "Arizani yuborish",
            "order_consult": "Konsultatsiya buyurtma qilish",
            "name": "Ism",
            "phone": "Telefon",
            "privacy": "maxfiylik siyosati",
            "calc_spend": "Sarfni hisoblash",
            "checkout": "Buyurtmani rasmiylashtirish",
            "in_stock": "Mavjud",
            "on_order": "Buyurtma bo‘yicha",
            "paint": "Bo‘yoq",
            "primer": "Gruntovka",
            "tinting": "Kolerovka",
            "outlet": "Outlet",
            "finishing": "Pardozlash materiallari",
        },
        "lang": {"ru": "RU", "uz": "UZ", "label": "Til"},
        "phrases": phrases_uz,
    }
    return ru, uz


I18N_JS = r'''/**
 * Lightweight i18n for static Optimus CA site (RU / UZ Latin).
 * - Loads /locales/{lang}.json
 * - Applies [data-i18n], [data-i18n-placeholder], [data-i18n-attr]
 * - Replaces exact text-node phrases from dictionary.phrases
 * - Language switcher: RU | UZ (localStorage key optimusca_lang)
 */
(function (window, document) {
  'use strict';

  var STORAGE_KEY = 'optimusca_lang';
  var DEFAULT_LANG = 'ru';
  var SUPPORTED = { ru: true, uz: true };
  var cache = {};
  var current = DEFAULT_LANG;

  function detectLang() {
    try {
      var q = new URLSearchParams(window.location.search || '').get('lang');
      if (q && SUPPORTED[q]) return q;
    } catch (e) { /* ignore */ }
    try {
      var saved = localStorage.getItem(STORAGE_KEY);
      if (saved && SUPPORTED[saved]) return saved;
    } catch (e2) { /* ignore */ }
    return DEFAULT_LANG;
  }

  function localeUrl(lang) {
    var base = '/locales/' + lang + '.json';
    // articles live one level deeper
    if (/\/articles\//i.test(window.location.pathname || '')) {
      return '../locales/' + lang + '.json';
    }
    return base;
  }

  function getByPath(obj, path) {
    if (!obj || !path) return null;
    var parts = String(path).split('.');
    var cur = obj;
    for (var i = 0; i < parts.length; i++) {
      if (cur == null || typeof cur !== 'object') return null;
      cur = cur[parts[i]];
    }
    return cur == null ? null : cur;
  }

  function t(key, fallback) {
    var dict = cache[current] || {};
    var val = getByPath(dict, key);
    if (val == null || val === '') return fallback != null ? fallback : key;
    return val;
  }

  function applyDataAttrs(dict) {
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      var key = el.getAttribute('data-i18n');
      var val = getByPath(dict, key);
      if (val == null) return;
      if (el.childElementCount && el.getAttribute('data-i18n-html') === 'true') {
        el.innerHTML = val;
      } else {
        el.textContent = val;
      }
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
      var key = el.getAttribute('data-i18n-placeholder');
      var val = getByPath(dict, key);
      if (val != null) el.setAttribute('placeholder', val);
    });
    document.querySelectorAll('[data-i18n-aria]').forEach(function (el) {
      var key = el.getAttribute('data-i18n-aria');
      var val = getByPath(dict, key);
      if (val != null) el.setAttribute('aria-label', val);
    });
    document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      var key = el.getAttribute('data-i18n-title');
      var val = getByPath(dict, key);
      if (val != null) el.setAttribute('title', val);
    });
  }

  function applyPhrases(dict) {
    var phrases = dict && dict.phrases;
    if (!phrases || typeof phrases !== 'object') return;
    var skip = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, CODE: 1, PRE: 1 };
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = walker.nextNode())) {
      var parent = node.parentElement;
      if (!parent || skip[parent.tagName]) continue;
      if (parent.closest && parent.closest('[data-i18n-skip="true"]')) continue;
      var raw = node.nodeValue;
      if (!raw || !/[А-Яа-яЁёA-Za-z‘’']/.test(raw)) continue;
      var trimmed = raw.replace(/\s+/g, ' ').trim();
      if (!trimmed) continue;
      if (Object.prototype.hasOwnProperty.call(phrases, trimmed)) {
        var leading = raw.match(/^\s*/)[0] || '';
        var trailing = raw.match(/\s*$/)[0] || '';
        node.nodeValue = leading + phrases[trimmed] + trailing;
      }
    }
    // placeholders / aria / title / alt / meta content
    document.querySelectorAll('[placeholder],[aria-label],[title],[alt]').forEach(function (el) {
      ['placeholder', 'aria-label', 'title', 'alt'].forEach(function (attr) {
        if (!el.hasAttribute(attr)) return;
        var v = el.getAttribute(attr);
        if (v && Object.prototype.hasOwnProperty.call(phrases, v)) {
          el.setAttribute(attr, phrases[v]);
        }
      });
    });
    var titleEl = document.querySelector('title');
    if (titleEl) {
      var tt = titleEl.textContent.trim();
      if (phrases[tt]) titleEl.textContent = phrases[tt];
      else if (dict.meta && dict.meta.title_home && /index\.html\/?$|\/$/.test(location.pathname)) {
        titleEl.textContent = dict.meta.title_home;
      }
    }
    var desc = document.querySelector('meta[name="description"]');
    if (desc) {
      var d = desc.getAttribute('content') || '';
      if (phrases[d]) desc.setAttribute('content', phrases[d]);
      else if (dict.meta && dict.meta.description_home && /index\.html\/?$|\/$/.test(location.pathname)) {
        desc.setAttribute('content', dict.meta.description_home);
      }
    }
  }

  function syncLeadThanks(dict) {
    if (!dict || !dict.common) return;
    window.LEAD_THANKS = {
      title: dict.common.thanks_title || 'Спасибо за заявку!',
      text: dict.common.thanks_text || 'Наш менеджер свяжется с вами в самое ближайшее время.',
      button: dict.common.thanks_btn || 'Отлично'
    };
    var modalTitle = document.getElementById('lead-modal-title');
    var modalText = document.getElementById('lead-modal-text');
    var modalClose = document.getElementById('lead-modal-close');
    if (modalTitle) modalTitle.textContent = window.LEAD_THANKS.title;
    if (modalText) modalText.textContent = window.LEAD_THANKS.text;
    if (modalClose) modalClose.textContent = window.LEAD_THANKS.button;
  }

  function updateSwitcherUI() {
    document.querySelectorAll('[data-lang-switch]').forEach(function (btn) {
      var lang = btn.getAttribute('data-lang-switch');
      var active = lang === current;
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      btn.classList.toggle('is-active', active);
      btn.classList.toggle('bg-red-600', active);
      btn.classList.toggle('text-white', active);
      btn.classList.toggle('bg-white', !active);
      btn.classList.toggle('text-zinc-700', !active);
    });
    try { document.documentElement.lang = current === 'uz' ? 'uz' : 'ru'; } catch (e) { /* ignore */ }
  }

  function ensureSwitcher() {
    if (document.getElementById('lang-switcher')) return;
    var host =
      document.querySelector('header nav') ||
      document.querySelector('#site-header') ||
      document.querySelector('header') ||
      document.body;
    if (!host) return;
    var wrap = document.createElement('div');
    wrap.id = 'lang-switcher';
    wrap.className = 'lang-switcher inline-flex items-center gap-1 rounded-xl border border-zinc-200 bg-zinc-50 p-1 text-xs font-bold';
    wrap.setAttribute('role', 'group');
    wrap.setAttribute('aria-label', 'Language');
    wrap.innerHTML =
      '<button type="button" data-lang-switch="ru" class="rounded-lg px-2.5 py-1.5 transition">RU</button>' +
      '<button type="button" data-lang-switch="uz" class="rounded-lg px-2.5 py-1.5 transition">UZ</button>';
    // Prefer placing near phone / CTA in header
    var cta = host.querySelector('a[href^="tel:"], a[href*="lead"], .btn-cta');
    if (cta && cta.parentElement) {
      cta.parentElement.insertBefore(wrap, cta);
    } else {
      host.appendChild(wrap);
    }
    wrap.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-lang-switch]');
      if (!btn) return;
      var lang = btn.getAttribute('data-lang-switch');
      if (lang) setLanguage(lang);
    });
  }

  function apply(dict) {
    applyDataAttrs(dict);
    applyPhrases(dict);
    syncLeadThanks(dict);
    updateSwitcherUI();
  }

  function loadDict(lang) {
    if (cache[lang]) return Promise.resolve(cache[lang]);
    return fetch(localeUrl(lang), { credentials: 'same-origin', cache: 'no-cache' })
      .then(function (r) {
        if (!r.ok) throw new Error('locale HTTP ' + r.status);
        return r.json();
      })
      .then(function (json) {
        cache[lang] = json;
        return json;
      });
  }

  function setLanguage(lang, opts) {
    opts = opts || {};
    if (!SUPPORTED[lang]) lang = DEFAULT_LANG;
    current = lang;
    try { localStorage.setItem(STORAGE_KEY, lang); } catch (e) { /* ignore */ }
    return loadDict(lang)
      .then(function (dict) {
        // For phrase replacement, reload page when switching back to RU after UZ
        // mutated DOM — simplest reliable approach:
        if (opts.reload) {
          var url = new URL(window.location.href);
          url.searchParams.set('lang', lang);
          window.location.replace(url.toString());
          return dict;
        }
        apply(dict);
        return dict;
      })
      .catch(function (err) {
        console.warn('[i18n] failed to load', lang, err);
      });
  }

  function boot() {
    ensureSwitcher();
    var lang = detectLang();
    // Always reload from server dictionary; if coming from ?lang=, persist it
    setLanguage(lang, { reload: false }).then(function () {
      // If page was served in RU and lang=uz, phrases apply in-place.
      updateSwitcherUI();
    });
    document.addEventListener('click', function (e) {
      var btn = e.target.closest && e.target.closest('[data-lang-switch]');
      if (!btn || btn.closest('#lang-switcher')) return;
      var lang2 = btn.getAttribute('data-lang-switch');
      if (lang2) setLanguage(lang2, { reload: true });
    });
  }

  // Switching language after phrase mutation needs clean RU source → soft reload
  var _set = setLanguage;
  setLanguage = function (lang, opts) {
    opts = opts || {};
    if (current && lang !== current) {
      opts.reload = true;
    }
    return _set(lang, opts);
  };

  window.OptimusI18n = {
    t: t,
    setLanguage: setLanguage,
    getLanguage: function () { return current; },
    ready: function () { return loadDict(current); }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})(window, document);
'''


def inject_i18n_script(html: str, is_article: bool) -> str:
    src = "../js/i18n.js?v=20261015_uz" if is_article else "/js/i18n.js?v=20261015_uz"
    tag = f'<script src="{src}" defer></script>'
    if "i18n.js" in html:
        html = re.sub(
            r'<script[^>]*i18n\.js[^>]*></script>',
            tag,
            html,
            count=1,
        )
        return html
    # insert after lead-api.js if present
    if "lead-api.js" in html:
        return re.sub(
            r'(<script[^>]*lead-api\.js[^>]*></script>)',
            r"\1\n  " + tag,
            html,
            count=1,
        )
    return html.replace("</head>", f"  {tag}\n</head>", 1)


def main() -> None:
    ru, uz = build_locales()
    (LOCALES / "ru.json").write_text(
        json.dumps(ru, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (LOCALES / "uz.json").write_text(
        json.dumps(uz, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (ROOT / "js" / "i18n.js").write_text(I18N_JS, encoding="utf-8")
    print("wrote locales:", len(ru["phrases"]), "phrases")

    for path in HTML_FILES:
        if not path.is_file():
            continue
        html = path.read_text(encoding="utf-8")
        html = inject_i18n_script(html, is_article="articles" in path.parts)
        # optional: add data-i18n on known nav labels via title lang sync only
        path.write_text(html, encoding="utf-8")
        print("wired", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
