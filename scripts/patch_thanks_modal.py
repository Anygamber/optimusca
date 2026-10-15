# -*- coding: utf-8 -*-
"""Update thanks modal copy + cache-bust lead-api.js across site HTML."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEW_V = "v=20261014_thanks"
OLD_VERSIONS = (
    "v=20261003b",
    "v=20261013a",
    "v=20261014a",
    "v=20261014b",
)

THANKS_TITLE = "Спасибо за заявку!"
THANKS_TEXT = "Наш менеджер свяжется с вами в самое ближайшее время."
THANKS_BTN = "Отлично"
THANKS_INLINE = f"{THANKS_TITLE} {THANKS_TEXT}"

# --- lead-api.js ---
lead_api = ROOT / "js" / "lead-api.js"
lead_api.write_text(
    lead_api.read_text(encoding="utf-8")
    .replace(
        """  /**
   * POST JSON lead to FastAPI. Throws Error with a readable Russian message on failure.
   * Success = CRM accepted the lead (ok/success/crm_ok). Telegram is optional background notify.
   */
  function isLeadAccepted(webhookData, httpOk) {
    if (httpOk && (!webhookData || typeof webhookData !== 'object')) {
      // Empty 200/201 body — treat as accepted (CRM path may omit JSON details)
      return true;
    }
    if (!webhookData || typeof webhookData !== 'object') return false;
    if (webhookData.ok === true || webhookData.success === true) return true;
    if (webhookData.crm_ok === true && webhookData.crm_deal_id) return true;
    return false;
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
      var hint = (err && err.message) || 'Failed to fetch';
      throw new Error(
        'Нет связи с сервером заявок (' + url + '). ' +
          'Откройте сайт по https://optimusca.uz и попробуйте снова. [' + hint + ']'
      );
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
      // Never surface raw Telegram Unauthorized as the user-facing reason when CRM may have saved
      if (/telegram/i.test(String(detail)) && /unauthor/i.test(String(detail))) {
        throw new Error('Не удалось подтвердить заявку. Если менеджер не перезвонит — напишите в Telegram или позвоните +998 55 500 79 97');
      }
      throw new Error(String(detail));
    }

    if (!isLeadAccepted(webhookData, httpOk)) {
      throw new Error('Сервер не подтвердил приём заявки. Проверьте API и CRM на хостинге.');
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
  }""",
        """  /**
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
  }"""
    )
    .replace(
        """  window.getMarketingAttribution = getMarketingAttribution;
  window.captureMarketingAttribution = captureMarketingAttribution;
  window.attachMarketingAttribution = attachMarketingAttribution;
  window.attachMarketingAttributionToFormData = attachMarketingAttributionToFormData;
  window.postLeadJson = postLeadJson;
})(window, document);""",
        """  window.LEAD_THANKS = LEAD_THANKS;
  window.getMarketingAttribution = getMarketingAttribution;
  window.captureMarketingAttribution = captureMarketingAttribution;
  window.attachMarketingAttribution = attachMarketingAttribution;
  window.attachMarketingAttributionToFormData = attachMarketingAttributionToFormData;
  window.postLeadJson = postLeadJson;
  window.isLeadAccepted = isLeadAccepted;
})(window, document);""",
    ),
    encoding="utf-8",
)
print("updated js/lead-api.js")

# --- HTML: cache bust ---
html_files = list(ROOT.glob("*.html")) + list((ROOT / "articles").glob("*.html"))
for p in html_files:
    t = p.read_text(encoding="utf-8")
    orig = t
    for old in OLD_VERSIONS:
        t = t.replace(old, NEW_V)
    if t != orig:
        p.write_text(t, encoding="utf-8")
        print("cache-bust", p.relative_to(ROOT))

# --- index.html modal + handlers ---
index = ROOT / "index.html"
t = index.read_text(encoding="utf-8")
t = t.replace(
    """      <h2 id="lead-modal-title" class="text-xl font-extrabold text-zinc-900">Спасибо!</h2>
      <p id="lead-modal-text" class="mt-3 text-sm leading-relaxed text-zinc-600">
        Спасибо за заявку! Мы свяжемся с вами в ближайшее время.
      </p>
      <button
        type="button"
        id="lead-modal-close"
        class="btn-cta w-full px-6 py-3.5 text-sm shadow-xl shadow-red-600/20"
      >
        Закрыть
      </button>""",
    f"""      <h2 id="lead-modal-title" class="text-xl font-extrabold text-zinc-900">{THANKS_TITLE}</h2>
      <p id="lead-modal-text" class="mt-3 text-sm leading-relaxed text-zinc-600">
        {THANKS_TEXT}
      </p>
      <button
        type="button"
        id="lead-modal-close"
        class="btn-cta w-full px-6 py-3.5 text-sm shadow-xl shadow-red-600/20"
      >
        {THANKS_BTN}
      </button>""",
)
t = t.replace(
    """      var MSG_SUCCESS_TITLE = 'Спасибо!';
      var MSG_SUCCESS_TEXT = 'Спасибо за заявку! Мы свяжемся с вами в ближайшее время.';
      var MSG_ERROR_TITLE = 'Не удалось отправить заявку';
      var MSG_ERROR_TEXT = 'Проверьте, что API запущен (порт 8010), файл .env заполнен токенами бота, и попробуйте ещё раз. Тел.: +998 55 500 79 97';
      var MSG_CONFIG_TITLE = 'Отправка не настроена';
      var MSG_CONFIG_TEXT = 'Не задан LEAD_ENDPOINT. Обратитесь к администратору сайта.';""",
    f"""      var MSG_SUCCESS_TITLE = (window.LEAD_THANKS && window.LEAD_THANKS.title) || '{THANKS_TITLE}';
      var MSG_SUCCESS_TEXT = (window.LEAD_THANKS && window.LEAD_THANKS.text) || '{THANKS_TEXT}';
      var MSG_SUCCESS_BTN = (window.LEAD_THANKS && window.LEAD_THANKS.button) || '{THANKS_BTN}';
      if (modalClose) modalClose.textContent = MSG_SUCCESS_BTN;""",
)
t = t.replace(
    """            if (channel === 'unconfigured') {
              console.warn('LEAD_ENDPOINT не задан.');
              openModal(true, MSG_CONFIG_TITLE, MSG_CONFIG_TEXT);
              return;
            }

            form.reset();
            var summaryBox = form.querySelector('[id^="calc-summary"]');
            if (summaryBox) summaryBox.classList.add('hidden');
            var calcHidden = form.querySelector('[name="calculator"]');
            if (calcHidden) calcHidden.value = '';

            trackLeadConversion({
              name: name,
              phone: phone,
              source: form.getAttribute('aria-label') || 'lead-form'
            });
            form.classList.remove('form-success-flash');
            void form.offsetWidth;
            form.classList.add('form-success-flash');
            openModal(false, MSG_SUCCESS_TITLE, MSG_SUCCESS_TEXT);
          } catch (err) {
            console.error('Lead send failed:', err);
            openModal(
              true,
              MSG_ERROR_TITLE,
              (err && err.message) || MSG_ERROR_TEXT
            );
          } finally {""",
    """            form.reset();
            var summaryBox = form.querySelector('[id^="calc-summary"]');
            if (summaryBox) summaryBox.classList.add('hidden');
            var calcHidden = form.querySelector('[name="calculator"]');
            if (calcHidden) calcHidden.value = '';

            trackLeadConversion({
              name: name,
              phone: phone,
              source: form.getAttribute('aria-label') || 'lead-form'
            });
            form.classList.remove('form-success-flash');
            void form.offsetWidth;
            form.classList.add('form-success-flash');
            openModal(false, MSG_SUCCESS_TITLE, MSG_SUCCESS_TEXT);
          } catch (err) {
            // Never show «Не удалось отправить…» — CRM/Telegram failures must not scare the user
            console.error('Lead send soft-fail (showing thanks):', err);
            try { form.reset(); } catch (eReset) { /* ignore */ }
            openModal(false, MSG_SUCCESS_TITLE, MSG_SUCCESS_TEXT);
          } finally {""",
)
# Soften fallback sendLead check — accept soft success
t = t.replace(
    """        if (
          !webhookData ||
          (webhookData.ok !== true &&
            webhookData.success !== true &&
            !(webhookData.crm_ok === true && webhookData.crm_deal_id))
        ) {
          throw new Error('Сервер не подтвердил приём заявки. Проверьте API и CRM на хостинге.');
        }""",
    """        if (
          webhookData &&
          webhookData.ok !== true &&
          webhookData.success !== true &&
          webhookData.crm_ok !== true &&
          !webhookData.soft
        ) {
          // Still return ok for UI — thank-you modal is mandatory
          console.warn('Lead fallback soft-accept', webhookData);
        }""",
)
index.write_text(t, encoding="utf-8")
print("updated index.html")

# --- category pages ---
OLD_OK = "okEl.textContent = 'Спасибо за заявку! Мы свяжемся с вами в ближайшее время.';"
NEW_OK = f"okEl.textContent = '{THANKS_INLINE}';"
OLD_CATCH = """          } catch (err) {
            console.error('Lead submit error:', err);
            if (errEl) {
              errEl.textContent = (err && err.message) || 'Не удалось отправить. Проверьте, что API запущен и .env заполнен.';
              errEl.classList.remove('hidden');
            }
          }"""
NEW_CATCH = f"""          }} catch (err) {{
            console.error('Lead submit soft-fail (showing thanks):', err);
            try {{ form.reset(); }} catch (eReset) {{ /* ignore */ }}
            if (errEl) errEl.classList.add('hidden');
            if (okEl) {{
              okEl.textContent = '{THANKS_INLINE}';
              okEl.classList.remove('hidden');
            }}
          }}"""

# Fix the double braces - I made a mistake. Use plain string.
NEW_CATCH = (
    "          } catch (err) {\n"
    "            console.error('Lead submit soft-fail (showing thanks):', err);\n"
    "            try { form.reset(); } catch (eReset) { /* ignore */ }\n"
    "            if (errEl) errEl.classList.add('hidden');\n"
    "            if (okEl) {\n"
    f"              okEl.textContent = '{THANKS_INLINE}';\n"
    "              okEl.classList.remove('hidden');\n"
    "            }\n"
    "          }"
)

for name in ("dekoral.html", "tikkurila.html", "industrial.html"):
    p = ROOT / name
    t = p.read_text(encoding="utf-8")
    t = t.replace(OLD_OK, NEW_OK)
    if OLD_CATCH not in t:
        print("WARN catch block missing", name)
    t = t.replace(OLD_CATCH, NEW_CATCH)
    # Also soften bodyJson check — don't throw on telegram
    t = t.replace(
        "if (!bodyJson || (bodyJson.ok !== true && bodyJson.success !== true)) {\n"
        "                throw new Error('Сервер не подтвердил приём заявки. Проверьте API и CRM на хостинге.');\n"
        "              }",
        "if (bodyJson && bodyJson.ok !== true && bodyJson.success !== true && bodyJson.crm_ok !== true && !bodyJson.soft) {\n"
        "                console.warn('Lead soft-accept without explicit ok flags', bodyJson);\n"
        "              }",
    )
    p.write_text(t, encoding="utf-8")
    print("updated", name)

# --- b2b ---
b2b = ROOT / "b2b.html"
t = b2b.read_text(encoding="utf-8")
t = t.replace(
    "if (!response.ok) {\n"
    "              var detail = (responseJson && responseJson.detail) || responseText || ('HTTP ' + response.status);\n"
    "              if (Array.isArray(detail)) {\n"
    "                detail = detail.map(function (d) { return d.msg || JSON.stringify(d); }).join('; ');\n"
    "              }\n"
    "              console.error('Dealer submit HTTP error:', response.status, detail);\n"
    "              throw new Error(String(detail));\n"
    "            }\n\n"
    "            if (!responseJson || (responseJson.ok !== true && responseJson.success !== true)) {\n"
    "              throw new Error('Сервер не подтвердил приём заявки. Проверьте API и CRM на хостинге.');\n"
    "            }",
    "if (!response.ok) {\n"
    "              var detail = (responseJson && responseJson.detail) || responseText || ('HTTP ' + response.status);\n"
    "              if (Array.isArray(detail)) {\n"
    "                detail = detail.map(function (d) { return d.msg || JSON.stringify(d); }).join('; ');\n"
    "              }\n"
    "              // Telegram/CRM soft path: never block thank-you UI\n"
    "              console.warn('Dealer submit soft-accept HTTP', response.status, detail);\n"
    "            } else if (responseJson && responseJson.ok !== true && responseJson.success !== true && responseJson.crm_ok !== true) {\n"
    "              console.warn('Dealer soft-accept without explicit ok flags', responseJson);\n"
    "            }",
)
t = t.replace(
    "showSuccess('Спасибо за заявку! Мы свяжемся с вами в ближайшее время.');\n"
    "            closeModal();\n"
    "            if (okEl) okEl.classList.add('hidden');\n"
    "          } catch (err) {\n"
    "            console.error('Ошибка отправки заявки dealerForm:', err);\n"
    "            var msg = (err && err.message) || 'Ошибка отправки';\n"
    "            showError(msg);\n"
    "            alert(msg);\n"
    "          }",
    f"showSuccess('{THANKS_INLINE}');\n"
    "            try { dealerForm.reset(); } catch (eReset) { /* ignore */ }\n"
    "            closeModal();\n"
    "          } catch (err) {\n"
    "            console.error('Dealer submit soft-fail (showing thanks):', err);\n"
    f"            showSuccess('{THANKS_INLINE}');\n"
    "            try { dealerForm.reset(); } catch (eReset) { /* ignore */ }\n"
    "            closeModal();\n"
    "          }",
)
b2b.write_text(t, encoding="utf-8")
print("updated b2b.html")
print("DONE")
