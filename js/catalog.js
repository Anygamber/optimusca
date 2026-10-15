(function () {
  var SPEC_LABELS = {
    base: "База",
    gloss: "Блеск",
    coverage: "Расход",
    density: "Плотность",
    dry_residue: "Сухой остаток",
    drying: "Высыхание",
    application: "Нанесение",
    thinner: "Разбавитель",
    wash: "Стойкость к мытью",
    layers: "Слои",
    pack: "Фасовка"
  };

  var CAT_LABELS = {
    interior: "Интерьер",
    facade: "Фасад",
    primer: "Грунты",
    metal: "Металл",
    wood: "Дерево",
    floor: "Полы",
    furniture: "Мебель",
    industrial: "Промышленные",
    cleaner: "Очистка / растворители",
    filler: "Шпаклёвки",
    other: "Другое"
  };

  function ensureStyles() {
    if (document.getElementById("catalog-runtime-css")) return;
    var style = document.createElement("style");
    style.id = "catalog-runtime-css";
    style.textContent = [
      ".catalog-chip{cursor:pointer}",
      ".catalog-card{background:rgba(255,255,255,.78);border:1px solid rgba(255,255,255,.45);border-radius:1.5rem;padding:1.25rem;box-shadow:0 16px 36px -10px rgba(0,0,0,.12)}",
      ".catalog-card h3{margin:.25rem 0 0;font-size:1.125rem;font-weight:600;color:#18181b}",
      ".catalog-card p{margin:.5rem 0 0;font-size:.875rem;line-height:1.5;font-weight:400;color:#52525b}",
      ".catalog-card dl{margin:1rem 0 0;display:grid;grid-template-columns:1fr;gap:0 .75rem}",
      "@media (min-width:640px){.catalog-card dl{grid-template-columns:1fr 1fr}}",
      ".catalog-card dt{font-size:.75rem;font-weight:500;letter-spacing:.04em;text-transform:uppercase;color:#71717a}",
      ".catalog-card dd{margin:0 0 .5rem;font-size:.875rem;font-weight:400;color:#27272a}",
      ".catalog-card a{display:inline-flex;margin-top:1rem;font-size:.875rem;font-weight:600;color:#dc2626;text-decoration:none}",
      ".catalog-card a:hover{text-decoration:underline}",
      ".catalog-grid{display:grid;gap:1.25rem}",
      "@media (min-width:768px){.catalog-grid{grid-template-columns:1fr 1fr}}"
    ].join("");
    document.head.appendChild(style);
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function specRows(specs) {
    if (!specs) return "";
    var html = "";
    Object.keys(SPEC_LABELS).forEach(function (key) {
      if (!specs[key]) return;
      html +=
        "<dt>" + SPEC_LABELS[key] + "</dt><dd>" + escapeHtml(specs[key]) + "</dd>";
    });
    return html;
  }

  function cardHtml(item) {
    var cat = CAT_LABELS[item.category] || item.category || "";
    var purpose = item.purpose
      ? "<p>" + escapeHtml(item.purpose) + "</p>"
      : "";
    var specs = specRows(item.specs);
    var dl = specs ? "<dl>" + specs + "</dl>" : "";
    var pdf = item.pdf
      ? '<a href="' + escapeHtml(item.pdf) + '" target="_blank" rel="noopener">Скачать TDS (PDF)</a>'
      : "";
    return (
      '<article class="catalog-card">' +
      '<p style="margin:0;font-size:.75rem;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:#dc2626">' +
      escapeHtml(cat) +
      "</p><h3>" +
      escapeHtml(item.name || "Без названия") +
      "</h3>" +
      purpose +
      dl +
      pdf +
      "</article>"
    );
  }

  function uniqueCats(items) {
    var seen = {};
    items.forEach(function (it) {
      seen[it.category || "other"] = true;
    });
    return Object.keys(CAT_LABELS).filter(function (k) {
      return seen[k];
    });
  }

  function resolveUrl(rel) {
    try {
      return new URL(rel, document.baseURI).href;
    } catch (e) {
      return rel;
    }
  }

  function loadItems(root) {
    var brand =
      root.getAttribute("data-catalog-brand") ||
      root.getAttribute("data-catalog-brand");
    var store = window.CATALOG_EMBED || window.CATALOG_EMBED || window.CATALOG_DATA || {};
    var embedded = brand && store[brand];
    if (embedded && embedded.length) {
      return Promise.resolve(embedded);
    }
    var jsonUrl =
      root.getAttribute("data-catalog-src") ||
      root.getAttribute("data-catalog-src");
    if (!jsonUrl) {
      return Promise.reject(new Error("Нет data-catalog-src"));
    }
    return fetch(resolveUrl(jsonUrl)).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function render(root, items, query, cat) {
    var q = (query || "").trim().toLowerCase();
    var filtered = items.filter(function (it) {
      if (cat && cat !== "all" && it.category !== cat) return false;
      if (!q) return true;
      var hay = ((it.name || "") + " " + (it.purpose || "")).toLowerCase();
      return hay.indexOf(q) !== -1;
    });
    var grid = root.querySelector("[data-catalog-grid]");
    var empty = root.querySelector("[data-catalog-empty]");
    var count = root.querySelector("[data-catalog-count]");
    if (count) count.textContent = String(filtered.length);
    if (!grid) return;
    if (!filtered.length) {
      grid.innerHTML = "";
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;
    grid.innerHTML = filtered.map(cardHtml).join("");
  }

  function bindFilters(root, items) {
    var search = root.querySelector("[data-catalog-search]");
    var chips = root.querySelector("[data-catalog-chips]");
    var state = { q: "", cat: "all" };

    if (chips) {
      chips.innerHTML =
        '<button type="button" data-cat="all" class="catalog-chip is-active rounded-full border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-bold text-red-700">Все</button>' +
        uniqueCats(items)
          .map(function (c) {
            return (
              '<button type="button" data-cat="' +
              c +
              '" class="catalog-chip rounded-full border border-white/50 bg-white/80 px-3 py-1.5 text-xs font-bold text-zinc-700">' +
              escapeHtml(CAT_LABELS[c] || c) +
              "</button>"
            );
          })
          .join("");

      chips.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-cat]");
        if (!btn) return;
        state.cat = btn.getAttribute("data-cat");
        chips.querySelectorAll(".catalog-chip").forEach(function (el) {
          el.classList.remove("is-active", "bg-red-50", "border-red-200", "text-red-700");
          el.classList.add("bg-white/80", "border-white/50", "text-zinc-700");
        });
        btn.classList.add("is-active", "bg-red-50", "border-red-200", "text-red-700");
        btn.classList.remove("bg-white/80", "border-white/50", "text-zinc-700");
        render(root, items, state.q, state.cat);
      });
    }

    if (search) {
      search.addEventListener("input", function () {
        state.q = search.value;
        render(root, items, state.q, state.cat);
      });
    }
    render(root, items, state.q, state.cat);
  }

  function showError(root, message) {
    var empty = root.querySelector("[data-catalog-empty]");
    if (empty) {
      empty.textContent = message;
      empty.hidden = false;
      empty.classList.remove("hidden");
    }
  }

  function initRoot(root) {
    if (!root || root.getAttribute("data-catalog-ready") === "1") return;
    root.setAttribute("data-catalog-ready", "1");
    ensureStyles();
    loadItems(root)
      .then(function (items) {
        if (!Array.isArray(items)) throw new Error("Каталог не массив");
        bindFilters(root, items);
      })
      .catch(function (err) {
        console.error("Catalog load error:", err);
        showError(root, "Не удалось загрузить каталог. Проверьте js/catalog-data-*.js и консоль браузера.");
      });
  }

  function boot() {
    document.querySelectorAll("[data-catalog-brand]").forEach(initRoot);
  }

  window.initProductCatalog = function (rootId, jsonUrl, brand) {
    var root = typeof rootId === "string" ? document.getElementById(rootId) : rootId;
    if (!root) return;
    if (brand) root.setAttribute("data-catalog-brand", brand);
    if (jsonUrl) root.setAttribute("data-catalog-src", jsonUrl);
    initRoot(root);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
