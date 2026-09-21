(() => {
  "use strict";

  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));

  async function render(root, options = {}) {
    root.innerHTML = '<div class="am-empty">Проверяю browser profiles...</div>';

    try {
      const payload = await options.api("/api/account-manager/browser/profiles");
      paint(root, payload, options);
    } catch (error) {
      root.innerHTML = '<div class="am-error">Не удалось загрузить browser profiles: ' + esc(error.message) + '</div>';
    }
  }

  function paint(root, payload, options) {
    const browser = payload.browser || {};
    const profiles = payload.profiles || [];

    root.innerHTML =
      '<div class="am-page-stack">' +
        '<section class="am-hero">' +
          '<div class="am-hero-copy">' +
            '<span class="am-eyebrow">LOCAL BROWSER SESSIONS</span>' +
            '<h2>Browser Profiles</h2>' +
            '<p>Каждый Account открывается в отдельном Chrome/Edge user-data-dir. Логины и подтверждения выполняются вручную в обычном браузере.</p>' +
          '</div>' +
          '<div class="am-hero-actions">' +
            '<span class="am-connection ' + (browser.available ? "ok" : "off") + '">' +
              '<span class="am-connection-dot"></span>' +
              (browser.available ? "browser ready" : "browser not found") +
            '</span>' +
            '<button type="button" class="am-btn" data-refresh>↻ Refresh</button>' +
          '</div>' +
        '</section>' +

        (!browser.available
          ? '<section class="am-notice warn"><div class="am-notice-icon">!</div><div><strong>Chrome/Edge не найден</strong><p>' +
              esc(browser.message || "Установи Chrome") +
              '</p></div></section>'
          : '<section class="am-panel am-browser-meta"><div><span>Detected browser</span><b>' + esc(browser.browser || "—") + '</b></div></section>') +

        '<section class="am-panel am-table-panel">' +
          '<div class="am-browser-list" data-browser-list></div>' +
        '</section>' +
      '</div>';

    const list = root.querySelector("[data-browser-list]");
    if (!profiles.length) {
      list.innerHTML = '<div class="am-empty compact">Сначала создай Account.</div>';
    } else {
      list.innerHTML = profiles.map(profile => row(profile, browser.available)).join("");
    }

    root.querySelector("[data-refresh]")?.addEventListener("click", () => render(root, options));

    root.querySelectorAll("[data-open-browser]").forEach(button => {
      button.addEventListener("click", async () => {
        const accountId = Number(button.dataset.accountId);
        const target = button.dataset.openBrowser || "home";
        button.disabled = true;
        try {
          const result = await options.api("/api/account-manager/accounts/" + accountId + "/browser/open", {
            method: "POST",
            body: JSON.stringify({ target })
          });
          options.toast(result.message || "Browser открыт");
          await render(root, options);
        } catch (error) {
          options.toast("Browser: " + error.message);
          button.disabled = false;
        }
      });
    });

    root.querySelectorAll("[data-close-browser]").forEach(button => {
      button.addEventListener("click", async () => {
        const accountId = Number(button.dataset.accountId);
        button.disabled = true;
        try {
          const result = await options.api("/api/account-manager/accounts/" + accountId + "/browser/close", {
            method: "POST",
            body: "{}"
          });
          options.toast(result.message || "Browser закрыт");
          await render(root, options);
        } catch (error) {
          options.toast("Browser: " + error.message);
          button.disabled = false;
        }
      });
    });
  }

  function row(profile, browserAvailable) {
    const label = "Account " + String(profile.account_number || 0).padStart(2, "0");
    return '<article class="am-browser-row">' +
      '<div class="am-browser-profile">' +
        '<div class="am-avatar">' + esc((profile.display_name || profile.username || label).slice(0, 2).toUpperCase()) + '</div>' +
        '<div><strong>' + esc(label) + '</strong><span>' + esc(profile.display_name || profile.username || profile.email || "Profile not configured") + '</span></div>' +
      '</div>' +
      '<div class="am-browser-path">' +
        '<span>PROFILE PATH</span><b>' + esc(profile.browser_profile_path || "—") + '</b>' +
      '</div>' +
      '<div class="am-browser-state">' +
        '<span class="am-state ' + (profile.running ? "free" : "") + '">' + (profile.running ? "RUNNING" : "CLOSED") + '</span>' +
      '</div>' +
      '<div class="am-browser-actions">' +
        '<button type="button" class="am-btn subtle small" data-open-browser="home" data-account-id="' + Number(profile.account_id) + '" ' + (browserAvailable ? "" : "disabled") + '>Open</button>' +
        '<button type="button" class="am-btn subtle small" data-open-browser="tiktok" data-account-id="' + Number(profile.account_id) + '" ' + (browserAvailable ? "" : "disabled") + '>TikTok</button>' +
        '<button type="button" class="am-btn subtle small" data-open-browser="instagram" data-account-id="' + Number(profile.account_id) + '" ' + (browserAvailable ? "" : "disabled") + '>Instagram</button>' +
        '<button type="button" class="am-btn subtle small" data-open-browser="youtube" data-account-id="' + Number(profile.account_id) + '" ' + (browserAvailable ? "" : "disabled") + '>YouTube</button>' +
        '<button type="button" class="am-btn danger small" data-close-browser data-account-id="' + Number(profile.account_id) + '">Close</button>' +
      '</div>' +
    '</article>';
  }

  window.PostingTTIIBrowserProfiles = { render };
})();
