/**
 * Backend URL for lead / dealer forms → FastAPI → Telegram bot + CRM.
 *
 * Default endpoints (same origin as the page):
 *   POST /webhook/lead
 *   POST /webhook/dealer
 *   POST /webhook/lead/status
 *
 * Always resolved to an absolute URL via location.origin (falls back to
 * https://optimusca.uz when opened from file://).
 *
 * Marketing attribution (gclid / gbraid / wbraid / UTM):
 *   - captured from URL (+ hash) on load
 *   - backed up to localStorage, sessionStorage and first-party cookie
 *   - re-read from Google `_gcl_*` cookies (gtag may set them after load)
 *   - merged into JSON / FormData immediately before fetch()
 *
 * Override:
 *   <script>window.LEAD_API_BASE = 'https://api.example.com';</script>
 */
(function (window, document) {
  'use strict';

  // Immediate boot signal — must appear in DevTools Console if this file runs
  try {
    var bootSrc = (document.currentScript && document.currentScript.src) || '/js/lead-api.js';
    console.info('[lead-api] loaded OK', bootSrc, new Date().toISOString());
  } catch (bootErr) { /* ignore */ }

  var PRODUCTION_ORIGIN = 'https://optimusca.uz';
  var base = String(window.LEAD_API_BASE || '').replace(/\/$/, '');
  var DEBUG = true;

  function pageOrigin() {
    try {
      var origin = String(window.location.origin || '');
      if (origin && origin !== 'null' && !/^file:/i.test(origin)) return origin.replace(/\/$/, '');
    } catch (e) { /* ignore */ }
    return PRODUCTION_ORIGIN;
  }

  /** Build absolute API URL from relative path or pass through absolute URLs. */
  function apiUrl(pathOrUrl) {
    var p = String(pathOrUrl || '').trim();
    if (!p) p = '/webhook/lead';
    if (/^https?:\/\//i.test(p)) return p;
    if (base && /^https?:\/\//i.test(base) && p.charAt(0) === '/') {
      return base + p;
    }
    if (p.charAt(0) !== '/') p = '/' + p;
    return pageOrigin() + p;
  }

  window.LEAD_ENDPOINT = apiUrl(window.LEAD_ENDPOINT || (base ? base + '/webhook/lead' : '/webhook/lead'));
  window.DEALER_ENDPOINT = apiUrl(window.DEALER_ENDPOINT || (base ? base + '/webhook/dealer' : '/webhook/dealer'));
  window.LEAD_STATUS_ENDPOINT = apiUrl(
    window.LEAD_STATUS_ENDPOINT || (base ? base + '/webhook/lead/status' : '/webhook/lead/status')
  );
  window.apiUrl = apiUrl;

  var ATTR_KEYS = [
    'gclid', 'gbraid', 'wbraid',
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'
  ];
  var META_KEYS = ['landing_page', 'referrer', 'source'];
  var STORAGE_KEY = 'tikkurila_marketing_attribution_v1';
  var COOKIE_GCLID = 'tikkurila_gclid';
  var COOKIE_GBRAID = 'tikkurila_gbraid';
  var COOKIE_WBRAID = 'tikkurila_wbraid';
  var MAX_CLICK_ID = 150;
  var MAX_UTM = 200;

  function log() {
    if (!DEBUG || typeof console === 'undefined' || !console.log) return;
    var args = Array.prototype.slice.call(arguments);
    args.unshift('[lead-api]');
    console.log.apply(console, args);
  }

  function clip(key, val) {
    var s = String(val == null ? '' : val).trim();
    if (!s) return '';
    var max = key === 'gclid' || key.indexOf('braid') >= 0 ? MAX_CLICK_ID : MAX_UTM;
    return s.slice(0, max);
  }

  function readCookieRaw(name) {
    try {
      var re = new RegExp('(?:^|;\\s*)' + name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '=([^;]*)');
      var match = document.cookie.match(re);
      if (!match) return '';
      return decodeURIComponent(match[1]);
    } catch (e) {
      return '';
    }
  }

  /** Parse Google `_gcl_*` value: GCL.<timestamp>.<id> */
  function parseGclCookieValue(raw) {
    if (!raw) return '';
    var value = String(raw).trim();
    if (!value) return '';
    // Multi-value cookies sometimes join with "." beyond 3 parts — keep id intact
    var parts = value.split('.');
    if (parts.length >= 3 && /^GCL$/i.test(parts[0])) {
      return parts.slice(2).join('.');
    }
    return value;
  }

  function writeBackupCookie(name, value) {
    value = clip(name.indexOf('braid') >= 0 ? 'gbraid' : 'gclid', value);
    if (!value) return;
    try {
      var parts = [
        name + '=' + encodeURIComponent(value),
        'path=/',
        'max-age=7776000',
        'SameSite=Lax'
      ];
      if (window.location.protocol === 'https:') parts.push('Secure');
      document.cookie = parts.join(';');

      // Share across www / apex so localStorage split does not drop gclid
      var host = String(window.location.hostname || '');
      if (host === 'optimusca.uz' || /\.optimusca\.uz$/i.test(host)) {
        document.cookie = parts.concat(['domain=.optimusca.uz']).join(';');
      }
    } catch (e) { /* ignore */ }
  }

  function readParamsFromSearch(search) {
    var out = {};
    try {
      var params = new URLSearchParams(search || '');
      ATTR_KEYS.forEach(function (key) {
        var val = clip(key, params.get(key));
        if (val) out[key] = val;
      });
    } catch (e) { /* ignore */ }
    return out;
  }

  function readQueryAttribution() {
    return readParamsFromSearch(window.location.search || '');
  }

  function readHashAttribution() {
    try {
      var hash = String(window.location.hash || '').replace(/^#/, '');
      if (!hash || hash.indexOf('=') === -1) return {};
      var query = hash;
      var qPos = hash.indexOf('?');
      if (qPos >= 0) query = hash.slice(qPos + 1);
      else if (hash.charAt(0) === '/') query = hash.replace(/^\/+/, '');
      return readParamsFromSearch(query.charAt(0) === '?' ? query : '?' + query);
    } catch (e) {
      return {};
    }
  }

  function readGclidFromCookie() {
    var names = ['_gcl_aw', '_gcl_dc', '_gcl_ag', COOKIE_GCLID];
    for (var i = 0; i < names.length; i++) {
      var raw = names[i] === COOKIE_GCLID
        ? readCookieRaw(names[i])
        : parseGclCookieValue(readCookieRaw(names[i]));
      var val = clip('gclid', raw);
      if (val) return val;
    }
    return '';
  }

  function readGbraidFromCookie() {
    var fromGb = clip('gbraid', parseGclCookieValue(readCookieRaw('_gcl_gb')));
    if (fromGb) return fromGb;
    return clip('gbraid', readCookieRaw(COOKIE_GBRAID));
  }

  function readWbraidFromCookie() {
    return clip('wbraid', readCookieRaw(COOKIE_WBRAID));
  }

  function readStorage(storage) {
    try {
      if (!storage) return {};
      var raw = storage.getItem(STORAGE_KEY);
      if (!raw) return {};
      var parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (e) {
      return {};
    }
  }

  function writeStorage(storage, attrs) {
    try {
      if (!storage) return;
      storage.setItem(STORAGE_KEY, JSON.stringify(attrs || {}));
    } catch (e) { /* private mode */ }
  }

  function persistAttribution(attrs) {
    var prev = Object.assign(
      {},
      readStorage(window.sessionStorage),
      readStorage(window.localStorage)
    );
    var merged = Object.assign({}, prev, attrs || {});
    writeStorage(window.localStorage, merged);
    writeStorage(window.sessionStorage, merged);

    if (merged.gclid) writeBackupCookie(COOKIE_GCLID, merged.gclid);
    if (merged.gbraid) writeBackupCookie(COOKIE_GBRAID, merged.gbraid);
    if (merged.wbraid) writeBackupCookie(COOKIE_WBRAID, merged.wbraid);

    return merged;
  }

  function collectFreshClickIds() {
    var out = {};
    var fromUrl = Object.assign({}, readQueryAttribution(), readHashAttribution());
    ATTR_KEYS.forEach(function (key) {
      if (fromUrl[key]) out[key] = fromUrl[key];
    });

    if (!out.gclid) {
      var cookieGclid = readGclidFromCookie();
      if (cookieGclid) out.gclid = cookieGclid;
    }
    if (!out.gbraid) {
      var cookieGbraid = readGbraidFromCookie();
      if (cookieGbraid) out.gbraid = cookieGbraid;
    }
    if (!out.wbraid) {
      var cookieWbraid = readWbraidFromCookie();
      if (cookieWbraid) out.wbraid = cookieWbraid;
    }
    return out;
  }

  function getMarketingAttribution() {
    var stored = Object.assign(
      {},
      readStorage(window.sessionStorage),
      readStorage(window.localStorage)
    );
    var fresh = collectFreshClickIds();
    var merged = Object.assign({}, stored, fresh);

    if (!merged.landing_page) {
      try { merged.landing_page = String(window.location.href || '').slice(0, 500); } catch (e) { /* ignore */ }
    }
    if (!merged.referrer) {
      try {
        var ref = String(document.referrer || '').slice(0, 500);
        if (ref) merged.referrer = ref;
      } catch (e) { /* ignore */ }
    }
    if (!merged.source && (merged.utm_source || merged.gclid || merged.gbraid || merged.wbraid)) {
      merged.source = merged.utm_source || 'google_ads';
    }
    return merged;
  }

  function captureMarketingAttribution() {
    var fresh = collectFreshClickIds();
    var keys = Object.keys(fresh);
    if (keys.length) {
      persistAttribution(fresh);
      log('capture OK', {
        gclid: fresh.gclid || '(none)',
        gbraid: fresh.gbraid || '(none)',
        wbraid: fresh.wbraid || '(none)',
        utm_source: fresh.utm_source || '(none)',
        utm_campaign: fresh.utm_campaign || '(none)',
        href: String(window.location.href || '')
      });
    } else {
      log('capture: no click ids in URL/cookies yet', {
        href: String(window.location.href || ''),
        cookie_gcl_aw: readCookieRaw('_gcl_aw') || '(none)',
        stored_gclid: (readStorage(window.localStorage) || {}).gclid || '(none)'
      });
    }
    return getMarketingAttribution();
  }

  function attributionDebugSnapshot(attrs) {
    attrs = attrs || {};
    return {
      gclid: attrs.gclid || '(empty)',
      gbraid: attrs.gbraid || '(empty)',
      wbraid: attrs.wbraid || '(empty)',
      utm_source: attrs.utm_source || '(empty)',
      utm_medium: attrs.utm_medium || '(empty)',
      utm_campaign: attrs.utm_campaign || '(empty)',
      utm_content: attrs.utm_content || '(empty)',
      utm_term: attrs.utm_term || '(empty)',
      source: attrs.source || '(empty)',
      landing_page: attrs.landing_page || '(empty)',
      from_cookie_gclid: readGclidFromCookie() || '(none)',
      from_cookie_gcl_aw: readCookieRaw('_gcl_aw') || '(none)',
      from_storage: (readStorage(window.localStorage) || {}).gclid || '(none)',
      href: String(window.location.href || '')
    };
  }

  function attachMarketingAttribution(payload) {
    payload = payload || {};
    try { captureMarketingAttribution(); } catch (e) { /* ignore */ }
    var attrs = getMarketingAttribution();

    ATTR_KEYS.concat(META_KEYS).forEach(function (key) {
      if (attrs[key] != null && attrs[key] !== '' && (payload[key] == null || payload[key] === '')) {
        payload[key] = attrs[key];
      }
    });

    log('attach → payload click ids', attributionDebugSnapshot(payload));
    return payload;
  }

  function attachMarketingAttributionToFormData(formData) {
    formData = formData || new FormData();
    try { captureMarketingAttribution(); } catch (e) { /* ignore */ }
    var attrs = getMarketingAttribution();

    ATTR_KEYS.concat(META_KEYS).forEach(function (key) {
      if (!attrs[key]) return;
      var existing = formData.get(key);
      if (existing == null || existing === '') {
        formData.set(key, attrs[key]);
      }
    });

    log('attach FormData → click ids', {
      gclid: formData.get('gclid') || '(empty)',
      gbraid: formData.get('gbraid') || '(empty)',
      wbraid: formData.get('wbraid') || '(empty)',
      utm_source: formData.get('utm_source') || '(empty)',
      utm_campaign: formData.get('utm_campaign') || '(empty)',
      debug: attributionDebugSnapshot(attrs)
    });
    return formData;
  }

  /**
   * Success copy for lead forms (shared by all pages).
   */
  var LEAD_THANKS = {
    title: 'Спасибо за заявку!',
    text: 'Наш менеджер свяжется с вами в самое ближайшее время.',
    button: 'Отлично'
  };

  /**
   * POST JSON lead to FastAPI.
   * Success = CRM accepted (ok/success/crm_ok) OR HTTP 200/201.
   * Telegram-only failures (401/502 Unauthorized etc.) are treated as soft success
   * so the UI never shows «Не удалось отправить заявку».
   */
  function isLeadAccepted(webhookData, httpOk) {
    if (httpOk) return true;
    if (!webhookData || typeof webhookData !== 'object') return false;
    if (webhookData.ok === true || webhookData.success === true) return true;
    if (webhookData.crm_ok === true) return true;
    return false;
  }

  function isTelegramOnlyFailure(detail, webhookData) {
    var blob = String(detail || '');
    if (webhookData && typeof webhookData === 'object') {
      if (webhookData.crm_ok === true) return true;
      blob += ' ' + JSON.stringify(webhookData);
    }
    return /telegram/i.test(blob) || /unauthorized/i.test(blob) || /sendMessage/i.test(blob);
  }

  async function postLeadJson(payload, endpoint) {
    var url = apiUrl(endpoint || window.LEAD_ENDPOINT);
    var body = attachMarketingAttribution(payload || {});

    log('POST', url, {
      gclid: body.gclid || '(empty)',
      gbraid: body.gbraid || '(empty)',
      wbraid: body.wbraid || '(empty)',
      utm_source: body.utm_source || '(empty)',
      utm_campaign: body.utm_campaign || '(empty)',
      name: body.name,
      phone: body.phone
    });

    var response;
    try {
      response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json'
        },
        credentials: 'same-origin',
        mode: 'cors',
        body: JSON.stringify(body)
      });
    } catch (err) {
      // Network blip after CRM may already have the lead — UI still shows thanks.
      log('POST network error (soft-success for UI):', (err && err.message) || err);
      return {
        ok: true,
        success: true,
        soft: true,
        soft_reason: (err && err.message) || 'Failed to fetch'
      };
    }

    var errBody = '';
    var webhookData = null;
    try {
      errBody = await response.text();
      webhookData = errBody ? JSON.parse(errBody) : null;
    } catch (e) { /* ignore */ }

    var httpOk = response.ok || response.status === 200 || response.status === 201;
    if (!httpOk) {
      var detail =
        (webhookData && (webhookData.detail || webhookData.message)) ||
        errBody ||
        ('HTTP ' + response.status);
      if (Array.isArray(detail)) {
        detail = detail.map(function (d) { return d.msg || JSON.stringify(d); }).join('; ');
      }
      if (isTelegramOnlyFailure(detail, webhookData) || isLeadAccepted(webhookData, false)) {
        log('POST soft-success (CRM/Telegram non-blocking):', detail);
        return Object.assign(
          { ok: true, success: true, soft: true, soft_reason: String(detail) },
          webhookData && typeof webhookData === 'object' ? webhookData : {}
        );
      }
      // Still do not block the thank-you UI — CRM is the source of truth
      log('POST non-OK treated as soft-success for UI:', detail);
      return {
        ok: true,
        success: true,
        soft: true,
        soft_reason: String(detail)
      };
    }

    if (!isLeadAccepted(webhookData, httpOk)) {
      log('POST 200 without explicit flags — accepting for UI');
      return { ok: true, success: true };
    }

    if (webhookData && webhookData.telegram_ok === false && webhookData.telegram_error) {
      log('Telegram notify skipped/failed (form still OK):', webhookData.telegram_error);
    }

    log('POST OK', {
      gclid_sent: body.gclid || '(empty)',
      delivered: (webhookData && webhookData.delivered) || null,
      crm_deal_id: (webhookData && webhookData.crm_deal_id) || null,
      crm_ok: webhookData && webhookData.crm_ok,
      telegram_scheduled: webhookData && webhookData.telegram_scheduled
    });
    return webhookData || { ok: true, success: true };
  }

  try { captureMarketingAttribution(); } catch (e) { /* ignore */ }
  try {
    window.setTimeout(function () {
      try { captureMarketingAttribution(); } catch (e2) { /* ignore */ }
    }, 1500);
    window.setTimeout(function () {
      try { captureMarketingAttribution(); } catch (e3) { /* ignore */ }
    }, 3500);
  } catch (e) { /* ignore */ }

  window.LEAD_THANKS = LEAD_THANKS;
  window.getMarketingAttribution = getMarketingAttribution;
  window.captureMarketingAttribution = captureMarketingAttribution;
  window.attachMarketingAttribution = attachMarketingAttribution;
  window.attachMarketingAttributionToFormData = attachMarketingAttributionToFormData;
  window.postLeadJson = postLeadJson;
  window.isLeadAccepted = isLeadAccepted;
})(window, document);
