/**
 * Safe dataLayer helpers + CTA click tracking (GTM / Google Ads).
 * Works on static pages (RU + UZ). SSR-safe guards included.
 */
(function (window, document) {
  'use strict';

  function isBrowser() {
    return typeof window !== 'undefined' && typeof document !== 'undefined';
  }

  function currentLang() {
    if (!isBrowser()) return 'uz';
    try {
      if (window.OptimusI18n && typeof window.OptimusI18n.getLanguage === 'function') {
        var lang = window.OptimusI18n.getLanguage();
        if (lang === 'ru' || lang === 'uz') return lang;
      }
    } catch (e) { /* ignore */ }
    try {
      var saved = localStorage.getItem('optimusca_lang');
      if (saved === 'ru' || saved === 'uz') return saved;
    } catch (e2) { /* ignore */ }
    var htmlLang = (document.documentElement.lang || '').toLowerCase();
    if (htmlLang.indexOf('ru') === 0) return 'ru';
    return 'uz';
  }

  /**
   * Safe dataLayer.push — never throws on SSR / missing dataLayer.
   * @param {object} payload
   */
  function pushDataLayer(payload) {
    if (!isBrowser()) return false;
    try {
      window.dataLayer = window.dataLayer || [];
      if (!payload || typeof payload !== 'object') return false;
      window.dataLayer.push(payload);
      return true;
    } catch (err) {
      try { console.warn('[dataLayer] push failed', err); } catch (e) { /* ignore */ }
      return false;
    }
  }

  function normalizePhone(raw) {
    var digits = String(raw || '').replace(/\D/g, '');
    if (!digits) return '';
    if (digits.indexOf('998') === 0 && digits.length >= 12) return '+' + digits.slice(0, 12);
    if (digits.length === 9) return '+998' + digits;
    if (String(raw || '').trim().charAt(0) === '+') return '+' + digits;
    return '+' + digits;
  }

  /**
   * Lead conversion event for GTM → Google Ads.
   */
  function trackGenerateLead(opts) {
    opts = opts || {};
    var phone = normalizePhone(opts.user_phone || opts.phone || '');
    return pushDataLayer({
      event: 'generate_lead',
      form_name: opts.form_name || 'consultation_form',
      page_language: opts.page_language || currentLang(),
      user_phone: phone || undefined,
      event_category: 'lead',
      event_label: opts.source || opts.form_name || 'consultation_form'
    });
  }

  function trackClick(eventName, extra) {
    var payload = { event: eventName, page_language: currentLang() };
    if (extra && typeof extra === 'object') {
      Object.keys(extra).forEach(function (k) {
        payload[k] = extra[k];
      });
    }
    return pushDataLayer(payload);
  }

  function isPriceCta(el) {
    if (!el) return false;
    if (el.getAttribute('data-dl-event') === 'download_price_list') return true;
    var text = (el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
    return (
      text.indexOf('скачать прайс') !== -1 ||
      text.indexOf('praisni yuklab') !== -1 ||
      text.indexOf('прайс') !== -1 && text.indexOf('скач') !== -1 ||
      text.indexOf('yuklab olish') !== -1
    );
  }

  function bindCtaTracking() {
    if (!isBrowser() || window.__optimusDlClicksBound) return;
    window.__optimusDlClicksBound = true;

    document.addEventListener(
      'click',
      function (e) {
        var el = e.target && e.target.closest ? e.target.closest('a, button') : null;
        if (!el) return;

        var forced = el.getAttribute('data-dl-event');
        if (forced) {
          trackClick(forced, {
            link_url: el.getAttribute('href') || '',
            link_text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80)
          });
          return;
        }

        var href = (el.getAttribute('href') || '').trim();

        // Telegram bot
        if (/t\.me\//i.test(href) || /telegram\.me\//i.test(href)) {
          trackClick('click_telegram_bot', { link_url: href });
          return;
        }

        // Phone click
        if (/^tel:/i.test(href)) {
          trackClick('click_phone', {
            link_url: href,
            user_phone: normalizePhone(href.replace(/^tel:/i, ''))
          });
          return;
        }

        // Price list CTA
        if (isPriceCta(el)) {
          trackClick('download_price_list', { link_url: href });
        }
      },
      true
    );
  }

  window.pushDataLayer = pushDataLayer;
  window.trackGenerateLead = trackGenerateLead;
  window.OptimusDataLayer = {
    push: pushDataLayer,
    trackGenerateLead: trackGenerateLead,
    trackClick: trackClick,
    currentLang: currentLang
  };

  if (isBrowser()) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', bindCtaTracking);
    } else {
      bindCtaTracking();
    }
  }
})(typeof window !== 'undefined' ? window : {}, typeof document !== 'undefined' ? document : {});
