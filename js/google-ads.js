/**
 * Google Ads + dataLayer (Enhanced Conversions ready).
 *
 * Conversion send_to: AW-18450122040/pehrCPyumImDELiK2t1E
 *
 * GTM: set before gtm.js / this file:
 *   <script>window.GTM_ID = 'GTM-XXXXXXX';</script>
 * Prefer loading /js/gtm.js first (official snippet + noscript).
 *
 * Call after successful lead submit:
 *   trackAdsLead('consultation_form', { name, phone, email, value, currency });
 */
(function (window, document) {
  'use strict';

  if (typeof window === 'undefined') return;

  window.dataLayer = window.dataLayer || [];

  window.ADS_CONVERSION_SEND_TO =
    window.ADS_CONVERSION_SEND_TO || 'AW-18450122040/pehrCPyumImDELiK2t1E';
  window.ADS_CONVERSION_ID = window.ADS_CONVERSION_ID || 'AW-18450122040';
  window.ADS_LEAD_VALUE =
    typeof window.ADS_LEAD_VALUE === 'number' ? window.ADS_LEAD_VALUE : 1;
  window.ADS_LEAD_CURRENCY = window.ADS_LEAD_CURRENCY || 'UZS';
  window.GTM_CONTAINER_ID = window.GTM_CONTAINER_ID || window.GTM_ID || window.NEXT_PUBLIC_GTM_ID || '';

  function safePush(payload) {
    if (typeof window === 'undefined') return;
    try {
      if (typeof window.pushDataLayer === 'function') {
        window.pushDataLayer(payload);
        return;
      }
      window.dataLayer = window.dataLayer || [];
      window.dataLayer.push(payload);
    } catch (e) {
      console.warn('[google-ads] dataLayer push failed', e);
    }
  }

  function pageLanguage() {
    if (window.OptimusDataLayer && typeof window.OptimusDataLayer.currentLang === 'function') {
      return window.OptimusDataLayer.currentLang();
    }
    try {
      if (window.OptimusI18n && typeof window.OptimusI18n.getLanguage === 'function') {
        return window.OptimusI18n.getLanguage();
      }
    } catch (e) { /* ignore */ }
    var lang = (document.documentElement.lang || 'uz').toLowerCase();
    return lang.indexOf('ru') === 0 ? 'ru' : 'uz';
  }

  function ensureGtm() {
    if (window.OptimusGTM && typeof window.OptimusGTM.boot === 'function') {
      window.OptimusGTM.boot();
      return;
    }
    var id = String(window.GTM_CONTAINER_ID || window.GTM_ID || window.NEXT_PUBLIC_GTM_ID || '').trim();
    if (!id || !/^GTM-[A-Z0-9]+$/i.test(id)) return;
    if (window.__gtmBootstrapped) return;
    window.__gtmBootstrapped = true;

    safePush({ 'gtm.start': new Date().getTime(), event: 'gtm.js' });

    var firstScript = document.getElementsByTagName('script')[0];
    var gtmScript = document.createElement('script');
    gtmScript.async = true;
    gtmScript.src = 'https://www.googletagmanager.com/gtm.js?id=' + encodeURIComponent(id);
    if (firstScript && firstScript.parentNode) {
      firstScript.parentNode.insertBefore(gtmScript, firstScript);
    } else {
      document.head.appendChild(gtmScript);
    }

    function injectNoscript() {
      if (document.getElementById('gtm-noscript')) return;
      var ns = document.createElement('noscript');
      ns.id = 'gtm-noscript';
      ns.innerHTML =
        '<iframe src="https://www.googletagmanager.com/ns.html?id=' +
        encodeURIComponent(id) +
        '" height="0" width="0" style="display:none;visibility:hidden" title="Google Tag Manager"></iframe>';
      if (document.body) document.body.insertBefore(ns, document.body.firstChild);
    }
    if (document.body) injectNoscript();
    else document.addEventListener('DOMContentLoaded', injectNoscript);
  }

  function normalizePhone(raw) {
    var digits = String(raw || '').replace(/\D/g, '');
    if (!digits) return '';
    if (digits.indexOf('998') === 0 && digits.length >= 12) return '+' + digits.slice(0, 12);
    if (digits.length === 9) return '+998' + digits;
    if (digits.charAt(0) === '0') return '+' + digits.slice(1);
    if (String(raw || '').trim().charAt(0) === '+') return '+' + digits;
    return '+' + digits;
  }

  function splitName(fullName) {
    var parts = String(fullName || '')
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    if (!parts.length) return { first_name: '', last_name: '' };
    if (parts.length === 1) return { first_name: parts[0], last_name: '' };
    return { first_name: parts[0], last_name: parts.slice(1).join(' ') };
  }

  function makeTransactionId(source) {
    var safeSource = String(source || 'lead')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '');
    return safeSource + '-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
  }

  function buildUserData(user) {
    user = user || {};
    var names = splitName(user.name || user.first_name || '');
    var phone = normalizePhone(user.phone || user.phone_number || user.user_phone || '');
    var email = String(user.email || '').trim().toLowerCase();

    var userData = {};
    if (email) userData.email = email;
    if (phone) userData.phone_number = phone;
    if (names.first_name || names.last_name || user.city || user.country) {
      userData.address = {};
      if (names.first_name) userData.address.first_name = names.first_name;
      if (names.last_name) userData.address.last_name = names.last_name;
      if (user.city) userData.address.city = String(user.city).trim();
      userData.address.country = user.country ? String(user.country).trim() : 'UZ';
    }
    return userData;
  }

  function enableEnhancedConversions() {
    if (typeof gtag !== 'function') return;
    gtag('config', window.ADS_CONVERSION_ID, {
      allow_enhanced_conversions: true
    });
  }

  /**
   * @param {string} source
   * @param {object} [user]
   */
  window.trackAdsLead = function (source, user) {
    if (typeof window === 'undefined') return null;
    user = user || {};
    ensureGtm();
    enableEnhancedConversions();

    var transactionId = user.transaction_id || makeTransactionId(source);
    var value =
      typeof user.value === 'number' && !isNaN(user.value)
        ? user.value
        : window.ADS_LEAD_VALUE;
    var currency = user.currency || window.ADS_LEAD_CURRENCY;
    var userData = buildUserData(user);
    var phone = normalizePhone(user.phone || user.user_phone || '');
    var formName = user.form_name || 'consultation_form';
    var lang = user.page_language || pageLanguage();

    // Primary GTM event (Google Ads conversion trigger via dataLayer)
    safePush({
      event: 'generate_lead',
      form_name: formName,
      page_language: lang,
      user_phone: phone || undefined,
      event_category: 'lead',
      event_label: source || formName,
      transaction_id: transactionId,
      value: value,
      currency: currency,
      user_data: userData,
      enhanced_conversion_data: userData
    });

    if (typeof gtag !== 'function') {
      console.warn('gtag is not loaded; Google Ads conversion gtag fallback skipped (dataLayer event sent).');
      return transactionId;
    }

    if (userData && (userData.email || userData.phone_number)) {
      gtag('set', 'user_data', userData);
    }

    gtag('event', 'conversion', {
      send_to: window.ADS_CONVERSION_SEND_TO,
      value: value,
      currency: currency,
      transaction_id: transactionId
    });

    gtag('event', 'generate_lead', {
      event_category: 'lead',
      event_label: source || formName,
      value: value,
      currency: currency,
      transaction_id: transactionId,
      form_name: formName,
      page_language: lang
    });

    return transactionId;
  };

  ensureGtm();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', enableEnhancedConversions);
  } else {
    enableEnhancedConversions();
  }
})(typeof window !== 'undefined' ? window : {}, typeof document !== 'undefined' ? document : {});
