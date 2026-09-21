(() => {
  "use strict";

  function socialState(account, platform) {
    return account?.social_accounts?.[platform]?.status || "NOT_CREATED";
  }

  function render(root, options = {}) {
    const existing = options.account || null;
    const number = existing ? String(existing.account_number || 0).padStart(2, "0") : "";
    const emailReady = Boolean(existing?.email);
    const identityReady = Boolean(existing?.display_name && existing?.username);

    root.innerHTML =
      '<div class="am-page-stack">' +
        '<section class="am-hero">' +
          '<div class="am-hero-copy">' +
            '<span class="am-eyebrow">ACCOUNT WORKFLOW</span>' +
            '<h2>' + (existing ? "Account " + number : "Create account") + '</h2>' +
            '<p>' + (existing
              ? "Продолжай с того места, где остановился. Состояние хранится в SQLite."
              : "Создай постоянную связку. Каждый следующий шаг можно выполнить позже.") + '</p>' +
          '</div>' +
          '<div class="am-hero-actions">' +
            (existing
              ? '<span class="am-connection ok"><span class="am-connection-dot"></span>' + (existing.status || "CREATED") + '</span>'
              : '') +
            '<button type="button" class="am-btn" data-back>← Accounts</button>' +
          '</div>' +
        '</section>' +

        (!existing
          ? '<section class="am-panel am-create-start">' +
              '<div class="am-create-mark">01</div>' +
              '<div><h2>Создай основу Account</h2><p>Сейчас создаётся только локальная запись и отдельный browser profile path. Никаких регистраций в соцсетях ещё не запускается.</p></div>' +
              '<button type="button" class="am-btn primary" data-create-draft>Create Account</button>' +
            '</section>'
          : '<div class="am-wizard">' +
              '<section class="am-wizard-step ' + (emailReady ? "done" : "active") + '">' +
                '<div class="am-wizard-index">1</div>' +
                '<div class="am-wizard-body">' +
                  '<div class="am-wizard-title"><span>Email alias</span><span class="am-state ' + (emailReady ? "free" : "used") + '">' + (emailReady ? "READY" : "REQUIRED") + '</span></div>' +
                  '<p>' + (emailReady
                    ? "Alias уже назначен: " + existing.email
                    : "Выбери свободный addy.io alias или создай новый.") + '</p>' +
                  '<div class="am-wizard-actions">' +
                    '<button type="button" class="am-btn primary" data-alias>' + (emailReady ? "Change alias" : "Choose alias") + '</button>' +
                  '</div>' +
                '</div>' +
              '</section>' +

              '<section class="am-wizard-step ' + (identityReady ? "done" : (emailReady ? "active" : "locked")) + '">' +
                '<div class="am-wizard-index">2</div>' +
                '<div class="am-wizard-body">' +
                  '<div class="am-wizard-title"><span>Identity</span><span class="am-badge">' + (identityReady ? "READY" : "PHASE 5") + '</span></div>' +
                  '<p>Name, username, bio, password и avatar будут генерироваться следующим модулем.</p>' +
                '</div>' +
              '</section>' +

              '<section class="am-wizard-step locked">' +
                '<div class="am-wizard-index">3</div>' +
                '<div class="am-wizard-body">' +
                  '<div class="am-wizard-title"><span>Social networks</span><span class="am-badge">PHASE 6–9</span></div>' +
                  '<div class="am-network-mini">' +
                    '<span>TikTok <b>' + socialState(existing, "tiktok") + '</b></span>' +
                    '<span>Instagram <b>' + socialState(existing, "instagram") + '</b></span>' +
                    '<span>YouTube <b>' + socialState(existing, "youtube") + '</b></span>' +
                  '</div>' +
                '</div>' +
              '</section>' +
            '</div>') +

        (existing
          ? '<section class="am-panel am-account-tech">' +
              '<div><span>Database</span><b>data/account_manager.db</b></div>' +
              '<div><span>Browser profile</span><b>' + (existing.browser_profile_path || "—") + '</b></div>' +
              '<div><span>Account ID</span><b>' + existing.id + '</b></div>' +
            '</section>'
          : '') +
      '</div>';

    root.querySelector("[data-create-draft]")?.addEventListener("click", () => options.onCreateDraft?.());
    root.querySelector("[data-alias]")?.addEventListener("click", () => options.onChooseAlias?.(existing));
    root.querySelector("[data-back]")?.addEventListener("click", () => options.onBack?.());
  }

  window.PostingTTIICreateAccount = { render };
})();
