(() => {
  "use strict";

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[ch]));
  }

  function socialState(account, platform) {
    return account?.social_accounts?.[platform]?.status || "NOT_CREATED";
  }

  function render(root, options = {}) {
    const existing = options.account || null;
    const number = existing ? String(existing.account_number || 0).padStart(2, "0") : "";
    const emailReady = Boolean(existing?.email);
    const profileReady = Boolean(existing?.profile_ready || (existing?.display_name && existing?.username && existing?.avatar_path));

    root.innerHTML =
      '<div class="am-page-stack">' +
        '<section class="am-hero">' +
          '<div class="am-hero-copy">' +
            '<span class="am-eyebrow">ACCOUNT WORKFLOW</span>' +
            '<h2>' + (existing ? "Account " + number : "Create account") + '</h2>' +
            '<p>' + (existing
              ? "Email и creator profile сохраняются локально. К соцсетям подключим этот Account на следующих этапах."
              : "Создай постоянную связку. Каждый следующий шаг можно выполнить позже.") + '</p>' +
          '</div>' +
          '<div class="am-hero-actions">' +
            (existing
              ? '<span class="am-connection ok"><span class="am-connection-dot"></span>' + esc(existing.status || "CREATED") + '</span>'
              : '') +
            '<button type="button" class="am-btn" data-back>← Accounts</button>' +
          '</div>' +
        '</section>' +

        (!existing
          ? '<section class="am-panel am-create-start">' +
              '<div class="am-create-mark">01</div>' +
              '<div><h2>Создай основу Account</h2><p>Создаётся локальная запись SQLite и отдельный browser profile path.</p></div>' +
              '<button type="button" class="am-btn primary" data-create-draft>Create Account</button>' +
            '</section>'
          : '<div class="am-wizard">' +
              '<section class="am-wizard-step ' + (emailReady ? "done" : "active") + '">' +
                '<div class="am-wizard-index">1</div>' +
                '<div class="am-wizard-body">' +
                  '<div class="am-wizard-title"><span>Email alias</span><span class="am-state ' + (emailReady ? "free" : "used") + '">' + (emailReady ? "READY" : "REQUIRED") + '</span></div>' +
                  '<p>' + (emailReady
                    ? "Назначен alias: " + esc(existing.email)
                    : "Выбери свободный addy.io alias или создай новый.") + '</p>' +
                  '<div class="am-wizard-actions">' +
                    '<button type="button" class="am-btn primary" data-alias>' + (emailReady ? "Change alias" : "Choose alias") + '</button>' +
                  '</div>' +
                '</div>' +
              '</section>' +

              '<section class="am-wizard-step ' + (profileReady ? "done" : (emailReady ? "active" : "locked")) + '">' +
                '<div class="am-wizard-index">2</div>' +
                '<div class="am-wizard-body">' +
                  '<div class="am-wizard-title"><span>Creator profile</span><span class="am-badge ' + (profileReady ? "ready" : "") + '">' + (profileReady ? "READY" : "STEP 2") + '</span></div>' +
                  (profileReady
                    ? '<div class="am-profile-preview">' +
                        '<img src="/api/account-manager/accounts/' + Number(existing.id) + '/avatar?v=' + encodeURIComponent(existing.updated_at || "") + '" alt="">' +
                        '<div><strong>' + esc(existing.display_name) + '</strong><span>@' + esc(existing.username) + '</span><p>' + esc(existing.bio || "") + '</p></div>' +
                      '</div>'
                    : '<p>Сгенерируй нейтральное название creator-профиля, username, bio и локальный avatar. Потом всё можно отредактировать.</p>') +
                  '<div class="am-wizard-actions">' +
                    '<button type="button" class="am-btn primary" data-profile-generate ' + (emailReady ? "" : "disabled") + '>' + (profileReady ? "Regenerate profile" : "Generate profile") + '</button>' +
                    (profileReady ? '<button type="button" class="am-btn" data-profile-edit>Edit profile</button>' : '') +
                  '</div>' +
                '</div>' +
              '</section>' +

              '<section class="am-wizard-step locked">' +
                '<div class="am-wizard-index">3</div>' +
                '<div class="am-wizard-body">' +
                  '<div class="am-wizard-title"><span>Social networks</span><span class="am-badge">NEXT</span></div>' +
                  '<p>Подключение собственных TikTok / Instagram / YouTube профилей и browser sessions будет отдельным модулем.</p>' +
                  '<div class="am-network-mini">' +
                    '<span>TikTok <b>' + esc(socialState(existing, "tiktok")) + '</b></span>' +
                    '<span>Instagram <b>' + esc(socialState(existing, "instagram")) + '</b></span>' +
                    '<span>YouTube <b>' + esc(socialState(existing, "youtube")) + '</b></span>' +
                  '</div>' +
                '</div>' +
              '</section>' +
            '</div>') +

        (existing
          ? '<section class="am-panel am-account-tech">' +
              '<div><span>Database</span><b>data/account_manager.db</b></div>' +
              '<div><span>Browser profile</span><b>' + esc(existing.browser_profile_path || "—") + '</b></div>' +
              '<div><span>Account ID</span><b>' + Number(existing.id) + '</b></div>' +
            '</section>'
          : '') +
      '</div>';

    root.querySelector("[data-create-draft]")?.addEventListener("click", () => options.onCreateDraft?.());
    root.querySelector("[data-alias]")?.addEventListener("click", () => options.onChooseAlias?.(existing));
    root.querySelector("[data-profile-generate]")?.addEventListener("click", () => options.onGenerateProfile?.(existing));
    root.querySelector("[data-profile-edit]")?.addEventListener("click", () => options.onEditProfile?.(existing));
    root.querySelector("[data-back]")?.addEventListener("click", () => options.onBack?.());
  }

  window.PostingTTIICreateAccount = { render };
})();
