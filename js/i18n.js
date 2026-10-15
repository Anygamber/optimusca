/**
 * Lightweight i18n for static Optimus CA site (UZ Latin / RU).
 * Default: uz. Switcher: compact UZ/RU dropdown in header actions.
 */
(function (window, document) {
  'use strict';

  var STORAGE_KEY = 'optimusca_lang';
  var DEFAULT_LANG = 'uz';
  var SUPPORTED = { ru: true, uz: true };
  var cache = {};
  var current = DEFAULT_LANG;

  /* Recognizable rectangular flags (UZ / RU) */
  var FLAG_UZ =
    '<svg class="lang-flag-svg" viewBox="0 0 36 24" xmlns="http://www.w3.org/2000/svg" role="img" aria-hidden="true">' +
    '<rect width="36" height="8" y="0" fill="#0099B5"/>' +
    '<rect width="36" height="8" y="8" fill="#FFFFFF"/>' +
    '<rect width="36" height="8" y="16" fill="#1EB53A"/>' +
    '<rect width="36" height="1.2" y="7.4" fill="#CE1126"/>' +
    '<rect width="36" height="1.2" y="15.4" fill="#CE1126"/>' +
    '<circle cx="7.2" cy="4" r="2.1" fill="none" stroke="#FFFFFF" stroke-width="0.85"/>' +
    '<circle cx="8.1" cy="4" r="1.65" fill="#0099B5"/>' +
    '<circle cx="11.8" cy="2.5" r="0.4" fill="#FFFFFF"/>' +
    '<circle cx="13.2" cy="3.4" r="0.38" fill="#FFFFFF"/>' +
    '<circle cx="12" cy="4.6" r="0.38" fill="#FFFFFF"/>' +
    '<circle cx="13.8" cy="5.1" r="0.35" fill="#FFFFFF"/>' +
    '</svg>';

  var FLAG_RU =
    '<svg class="lang-flag-svg" viewBox="0 0 36 24" xmlns="http://www.w3.org/2000/svg" role="img" aria-hidden="true">' +
    '<rect width="36" height="8" y="0" fill="#FFFFFF"/>' +
    '<rect width="36" height="8" y="8" fill="#0039A6"/>' +
    '<rect width="36" height="8" y="16" fill="#D52B1E"/>' +
    '</svg>';

  var CHEVRON =
    '<svg class="lang-dd-chevron" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">' +
    '<path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.17l3.71-3.94a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd"/>' +
    '</svg>';

  var SWITCHER_HTML =
    '<button type="button" class="lang-dd-toggle" aria-haspopup="listbox" aria-expanded="false" aria-label="Til / Язык">' +
      '<span class="lang-dd-flag" data-lang-current-flag>' + FLAG_UZ + '</span>' +
      '<span class="lang-dd-code" data-lang-current-code>UZ</span>' +
      CHEVRON +
    '</button>' +
    '<div class="lang-dd-menu" role="listbox" hidden>' +
      '<button type="button" role="option" data-lang-switch="uz" class="lang-dd-option" title="O‘zbekcha" aria-label="O‘zbek tili">' +
        FLAG_UZ + '<span>O‘zbekcha</span>' +
      '</button>' +
      '<button type="button" role="option" data-lang-switch="ru" class="lang-dd-option" title="Русский" aria-label="Русский язык">' +
        FLAG_RU + '<span>Русский</span>' +
      '</button>' +
    '</div>';

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
    if (/\/articles\//i.test(window.location.pathname || '')) {
      return '../locales/' + lang + '.json';
    }
    return '/locales/' + lang + '.json';
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
      if (parent.closest && parent.closest('[data-i18n-skip="true"], #lang-switcher')) continue;
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
    document.querySelectorAll('[placeholder],[aria-label],[title],[alt]').forEach(function (el) {
      if (el.closest && el.closest('#lang-switcher')) return;
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
      title: dict.common.thanks_title || "Arizangiz uchun rahmat!",
      text: dict.common.thanks_text || "Menejerimiz siz bilan eng qisqa muddatda bog‘lanadi.",
      button: dict.common.thanks_btn || 'Ajoyib'
    };
    var modalTitle = document.getElementById('lead-modal-title');
    var modalText = document.getElementById('lead-modal-text');
    var modalClose = document.getElementById('lead-modal-close');
    if (modalTitle) modalTitle.textContent = window.LEAD_THANKS.title;
    if (modalText) modalText.textContent = window.LEAD_THANKS.text;
    if (modalClose) modalClose.textContent = window.LEAD_THANKS.button;
  }

  function setDropdownOpen(open) {
    var root = document.getElementById('lang-switcher');
    if (!root) return;
    var toggle = root.querySelector('.lang-dd-toggle');
    var menu = root.querySelector('.lang-dd-menu');
    if (!toggle || !menu) return;
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) menu.removeAttribute('hidden');
    else menu.setAttribute('hidden', '');
    root.classList.toggle('is-open', !!open);
  }

  function updateSwitcherUI() {
    document.querySelectorAll('[data-lang-switch]').forEach(function (btn) {
      var lang = btn.getAttribute('data-lang-switch');
      var active = lang === current;
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
      btn.classList.toggle('is-active', active);
    });
    var flagHost = document.querySelector('[data-lang-current-flag]');
    var codeHost = document.querySelector('[data-lang-current-code]');
    if (flagHost) flagHost.innerHTML = current === 'uz' ? FLAG_UZ : FLAG_RU;
    if (codeHost) codeHost.textContent = current === 'uz' ? 'UZ' : 'RU';
    try {
      document.documentElement.lang = current === 'uz' ? 'uz' : 'ru';
      document.documentElement.setAttribute('data-lang', current);
    } catch (e) { /* ignore */ }
    setDropdownOpen(false);
  }

  function mountSwitcher(el) {
    var actions = document.querySelector('#site-header .header-actions');
    var bar = document.querySelector('#site-header .header-bar');

    if (actions) {
      var phone = actions.querySelector('.header-phone, a[href^="tel:"]');
      var toggle = actions.querySelector('#nav-toggle');
      if (phone) {
        actions.insertBefore(el, phone);
      } else if (toggle && toggle.nextSibling) {
        actions.insertBefore(el, toggle.nextSibling);
      } else if (toggle) {
        actions.appendChild(el);
      } else {
        actions.insertBefore(el, actions.firstChild);
      }
      return;
    }

    if (bar) {
      var cta = null;
      var kids = bar.children;
      for (var i = 0; i < kids.length; i++) {
        if (kids[i].classList && kids[i].classList.contains('btn-cta')) cta = kids[i];
        else if (kids[i].matches && kids[i].matches('a.btn-cta')) cta = kids[i];
      }
      if (!cta) cta = bar.querySelector(':scope > .btn-cta, :scope > a.btn-cta');
      if (cta) bar.insertBefore(el, cta);
      else bar.appendChild(el);
      return;
    }

    var header = document.querySelector('#site-header') || document.querySelector('header') || document.body;
    if (header) header.appendChild(el);
  }

  function ensureSwitcher() {
    var existing = document.getElementById('lang-switcher');
    if (existing) {
      if (!existing.querySelector('.lang-dd-toggle') || !existing.querySelector('[data-lang-switch]')) {
        existing.innerHTML = SWITCHER_HTML;
      }
      existing.className = 'lang-switcher';
      existing.setAttribute('role', 'group');
      existing.setAttribute('aria-label', 'Til / Язык');
      existing.setAttribute('data-i18n-skip', 'true');
      mountSwitcher(existing);
      return;
    }

    var wrap = document.createElement('div');
    wrap.id = 'lang-switcher';
    wrap.className = 'lang-switcher';
    wrap.setAttribute('role', 'group');
    wrap.setAttribute('aria-label', 'Til / Язык');
    wrap.setAttribute('data-i18n-skip', 'true');
    wrap.innerHTML = SWITCHER_HTML;
    mountSwitcher(wrap);
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
    var prev = current;
    current = lang;
    try { localStorage.setItem(STORAGE_KEY, lang); } catch (e) { /* ignore */ }

    if (opts.reload && prev && prev !== lang) {
      try {
        var url = new URL(window.location.href);
        url.searchParams.set('lang', lang);
        window.location.replace(url.toString());
      } catch (e2) {
        window.location.reload();
      }
      return Promise.resolve(null);
    }

    return loadDict(lang)
      .then(function (dict) {
        apply(dict);
        return dict;
      })
      .catch(function (err) {
        console.warn('[i18n] failed to load', lang, err);
      });
  }

  function bootMobileNav() {
    var toggle = document.getElementById('nav-toggle');
    var panel = document.getElementById('mobile-nav');
    if (!toggle || !panel) return;
    toggle.addEventListener('click', function () {
      var open = toggle.getAttribute('aria-expanded') === 'true';
      toggle.setAttribute('aria-expanded', open ? 'false' : 'true');
      panel.classList.toggle('is-open', !open);
    });
    panel.querySelectorAll('a').forEach(function (a) {
      a.addEventListener('click', function () {
        toggle.setAttribute('aria-expanded', 'false');
        panel.classList.remove('is-open');
      });
    });
  }

  function bootLangDropdown() {
    var root = document.getElementById('lang-switcher');
    if (!root) return;
    var toggle = root.querySelector('.lang-dd-toggle');
    if (!toggle) return;

    toggle.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      var open = toggle.getAttribute('aria-expanded') === 'true';
      setDropdownOpen(!open);
    });

    document.addEventListener('click', function (e) {
      if (!root.contains(e.target)) setDropdownOpen(false);
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') setDropdownOpen(false);
    });
  }

  function boot() {
    ensureSwitcher();
    bootLangDropdown();
    bootMobileNav();
    var lang = detectLang();
    setLanguage(lang, { reload: false }).then(function () {
      updateSwitcherUI();
    });
    document.addEventListener('click', function (e) {
      var btn = e.target.closest && e.target.closest('[data-lang-switch]');
      if (!btn) return;
      var lang2 = btn.getAttribute('data-lang-switch');
      if (!lang2 || lang2 === current) {
        setDropdownOpen(false);
        return;
      }
      setLanguage(lang2, { reload: true });
    });
  }

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
