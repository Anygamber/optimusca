(function () {
  var colors = (window.COLOR_LIBRARY || []).slice();
  var palette = document.getElementById("color-palette");
  var empty = document.getElementById("color-empty");
  var count = document.getElementById("color-count");
  var search = document.getElementById("color-search");
  var groupSelect = document.getElementById("color-group");
  var loupe = document.getElementById("color-loupe");
  var loupeSwatch = loupe.querySelector(".loupe-swatch");
  var loupeName = loupe.querySelector(".loupe-name");
  var loupeCode = loupe.querySelector(".loupe-code");
  var sheet = document.getElementById("color-sheet");
  var visible = colors;

  function luma(hex) {
    var raw = String(hex || "").replace("#", "");
    if (raw.length === 3) raw = raw.replace(/./g, function (ch) { return ch + ch; });
    var value = parseInt(raw, 16);
    if (!isFinite(value)) return 1;
    function lin(channel) {
      var c = channel / 255;
      return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    }
    return 0.2126 * lin((value >> 16) & 255) + 0.7152 * lin((value >> 8) & 255) + 0.0722 * lin(value & 255);
  }

  colors.forEach(function (color) { color.luma = luma(color.hex); });
  colors.sort(function (a, b) { return a.luma - b.luma; });

  function fillGroups() {
    var seen = {};
    colors.forEach(function (color) {
      var name = color.group || "Другие";
      seen[name] = (seen[name] || 0) + 1;
    });
    Object.keys(seen).sort(function (a, b) {
      return seen[b] - seen[a] || a.localeCompare(b, "ru");
    }).forEach(function (name) {
      var option = document.createElement("option");
      option.value = name;
      option.textContent = name + " (" + seen[name] + ")";
      groupSelect.appendChild(option);
    });
  }

  function matches(color, query) {
    if (!query) return true;
    return [color.name, color.code, color.hex, color.rgb, color.ncs].join(" ").toLowerCase().indexOf(query) !== -1;
  }

  function render() {
    var query = search.value.trim().toLowerCase();
    var group = groupSelect.value;
    visible = colors.filter(function (color) {
      if (group && color.group !== group) return false;
      return matches(color, query);
    });
    var fragment = document.createDocumentFragment();
    visible.forEach(function (color, index) {
      var button = document.createElement("button");
      var title = color.name || color.code || "Без кода";
      button.type = "button";
      button.className = "swatch";
      button.style.backgroundColor = color.hex || "#fff";
      button.dataset.index = String(index);
      button.setAttribute("aria-label", title + (color.code && color.name ? ", " + color.code : ""));
      fragment.appendChild(button);
    });
    palette.innerHTML = "";
    palette.appendChild(fragment);
    empty.hidden = visible.length !== 0;
    count.textContent = visible.length ? visible.length + " оттенков" : "";
    fit();
  }

  function fit() {
    var total = visible.length;
    if (!total) return;
    var rect = palette.parentElement.getBoundingClientRect();
    var gap = 1;
    var width = Math.max(rect.width - 8, 120);
    var height = Math.max(rect.height - 8, 120);
    var cols = Math.max(1, Math.round(Math.sqrt(total * (width / height))));
    var rows = Math.ceil(total / cols);
    var cell = Math.floor(Math.min((width - gap * (cols - 1)) / cols, (height - gap * (rows - 1)) / rows));
    cell = Math.max(cell, 3);
    palette.style.gridTemplateColumns = "repeat(" + cols + ", " + cell + "px)";
    palette.style.gridAutoRows = cell + "px";
    palette.style.setProperty("--swatch-scale", String(Math.min(8, Math.max(2.4, 88 / cell))));
  }

  function placeLoupe(cell, color) {
    var title = color.name || color.code || "Без кода";
    loupeSwatch.style.backgroundColor = color.hex || "#fff";
    loupeName.textContent = title;
    loupeCode.textContent = [color.code, color.ncs ? "NCS " + color.ncs : ""].filter(Boolean).join(" · ");
    loupe.hidden = false;
    var rect = cell.getBoundingClientRect();
    var box = loupe.getBoundingClientRect();
    var left = rect.left + rect.width / 2 - box.width / 2;
    var top = rect.top - box.height - 10;
    if (top < 8) top = rect.bottom + 10;
    left = Math.max(8, Math.min(left, window.innerWidth - box.width - 8));
    if (top + box.height > window.innerHeight - 8) top = Math.max(8, window.innerHeight - box.height - 8);
    loupe.style.left = left + "px";
    loupe.style.top = top + "px";
  }

  function openSheet(color) {
    var title = color.name || color.code || "Без кода";
    document.getElementById("sheet-swatch").style.backgroundColor = color.hex || "#fff";
    document.getElementById("sheet-title").textContent = title;
    document.getElementById("sheet-code").textContent = color.name ? color.code : "";
    document.getElementById("sheet-rgb").textContent = color.rgb || "—";
    document.getElementById("sheet-ncs").textContent = color.ncs || "—";
    document.getElementById("sheet-hex").textContent = color.hex || "—";
    document.getElementById("sheet-group").textContent = color.group || "—";
    if (typeof sheet.showModal === "function") sheet.showModal();
    else sheet.setAttribute("open", "");
  }

  palette.addEventListener("mouseover", function (event) {
    var cell = event.target.closest(".swatch");
    if (!cell || !palette.contains(cell)) return;
    var rect = cell.getBoundingClientRect();
    var originX = rect.left < 96 ? "left" : (rect.right > window.innerWidth - 96 ? "right" : "center");
    var originY = rect.top < 96 ? "top" : (rect.bottom > window.innerHeight - 96 ? "bottom" : "center");
    cell.style.transformOrigin = originX + " " + originY;
    placeLoupe(cell, visible[Number(cell.dataset.index)]);
  });

  palette.addEventListener("mouseleave", function () {
    loupe.hidden = true;
  });

  palette.addEventListener("focusin", function (event) {
    var cell = event.target.closest(".swatch");
    if (!cell) return;
    placeLoupe(cell, visible[Number(cell.dataset.index)]);
  });

  palette.addEventListener("focusout", function () {
    loupe.hidden = true;
  });

  palette.addEventListener("click", function (event) {
    var cell = event.target.closest(".swatch");
    if (!cell) return;
    loupe.hidden = true;
    openSheet(visible[Number(cell.dataset.index)]);
  });

  document.getElementById("sheet-close").addEventListener("click", function () {
    if (typeof sheet.close === "function") sheet.close();
    else sheet.removeAttribute("open");
  });

  sheet.addEventListener("click", function (event) {
    if (event.target === sheet && typeof sheet.close === "function") sheet.close();
  });

  var resizeTimer = 0;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(fit, 80);
  });

  fillGroups();
  search.addEventListener("input", render);
  groupSelect.addEventListener("change", render);
  render();
})();
