/**
 * Google Tag Manager bootstrap for static Optimus CA pages.
 *
 * Set container ID before this file (any one works):
 *   window.GTM_ID = 'GTM-XXXXXXX';
 *   window.GTM_CONTAINER_ID = 'GTM-XXXXXXX';
 *   window.NEXT_PUBLIC_GTM_ID = 'GTM-XXXXXXX';
 * Or meta: <meta name="gtm-id" content="GTM-XXXXXXX">
 *
 * Injects official head snippet + body noscript iframe.
 */
(function (window, document) {
  'use strict';

  function resolveGtmId() {
    var candidates = [
      window.GTM_ID,
      window.GTM_CONTAINER_ID,
      window.NEXT_PUBLIC_GTM_ID,
      window.__ENV && (window.__ENV.GTM_ID || window.__ENV.NEXT_PUBLIC_GTM_ID || window.__ENV.GTM_CONTAINER_ID)
    ];
    var i;
    for (i = 0; i < candidates.length; i++) {
      var v = String(candidates[i] || '').trim();
      if (/^GTM-[A-Z0-9]+$/i.test(v)) return v.toUpperCase();
    }
    try {
      var meta = document.querySelector('meta[name="gtm-id"], meta[name="GTM_ID"]');
      if (meta) {
        var m = String(meta.getAttribute('content') || '').trim();
        if (/^GTM-[A-Z0-9]+$/i.test(m)) return m.toUpperCase();
      }
    } catch (e) { /* ignore */ }
    return '';
  }

  function bootGtm(id) {
    if (!id || window.__gtmBootstrapped) return false;
    window.__gtmBootstrapped = true;
    window.GTM_ID = id;
    window.GTM_CONTAINER_ID = id;

    if (typeof window !== 'undefined') {
      window.dataLayer = window.dataLayer || [];
      window.dataLayer.push({ 'gtm.start': new Date().getTime(), event: 'gtm.js' });
    }

    var firstScript = document.getElementsByTagName('script')[0];
    var gtmScript = document.createElement('script');
    gtmScript.async = true;
    gtmScript.src = 'https://www.googletagmanager.com/gtm.js?id=' + encodeURIComponent(id);
    if (firstScript && firstScript.parentNode) {
      firstScript.parentNode.insertBefore(gtmScript, firstScript);
    } else if (document.head) {
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
      if (document.body) {
        document.body.insertBefore(ns, document.body.firstChild);
      }
    }

    if (document.body) injectNoscript();
    else document.addEventListener('DOMContentLoaded', injectNoscript);
    return true;
  }

  window.OptimusGTM = {
    getId: resolveGtmId,
    boot: function () {
      return bootGtm(resolveGtmId());
    }
  };

  // Early boot (safe if ID empty — no-op)
  bootGtm(resolveGtmId());
})(window, document);
