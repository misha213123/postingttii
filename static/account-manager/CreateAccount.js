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
    const profileReady = Boolean(
      existing?.profile_ready ||
      (existing?.display_name && existing?.username && existing?.avatar_path)
    );
    const tikTokStatus = socialState(existing, "tiktok");
    const tikTokConnected = tikTokStatus === "CONNECTED";
    const tikTokJob = existing?.creation_jobs?.tiktok || {};

    root.innerHTML =
      '<div class="am-page-stack">' +
        '<section class="am-hero">' +
          '<div class="am-hero-copy">' +
            '<span class="am-eyebrow">ACCOUNT WORKFLOW</span>' +
            '<h2>' + (existing ? "Account " + number : "Create account") + '</h2>' +
            '<p>' + (existing
              ? "Email, creator profile и отдельная browser session сохраняются локально."
              : "Создай постоянную связку. Каждый следующий шаг можно выполнить позже.") + '</p>' +
          '</div>' +
          '<div class="am-hero-actions">' +
            (existing
              ? '<span class="am-connection ok"><span class="am-connection-dot"></span>' +
                  esc(existing.status || "CREATED") +
                '</span>'
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
                  '<div class="am-wizard-title"><span>Email alias</span><span class="am-state ' +
                    (emailReady ? "free" : "used") + '">' + (emailReady ? "READY" : "REQUIRED") + '</span></div>' +
                  '<p>' + (emailReady
                    ? "Назначен alias: " + esc(existing.email)
                    : "Выбери свободный addy.io alias или создай новый.") + '</p>' +
                  '<div class="am-wizard-actions">' +
                    '<button type="button" class="am-btn primary" data-alias>' +
                      (emailReady ? "Change alias" : "Choose alias") +
                    '</button>' +
                  '</div>' +
                '</div>' +
              '</section>' +

              '<section class="am-wizard-step ' +
                (profileReady ? "done" : (emailReady ? "active" : "locked")) + '">' +
                '<div class="am-wizard-index">2</div>' +
                '<div class="am-wizard-body">' +
                  '<div class="am-wizard-title"><span>Creator profile</span><span class="am-badge ' +
                    (profileReady ? "ready" : "") + '">' +
                    (profileReady ? "READY" : "STEP 2") + '</span></div>' +
                  (profileReady
                    ? '<div class="am-profile-preview">' +
                        '<img src="/api/account-manager/accounts/' + Number(existing.id) +
                          '/avatar?v=' + encodeURIComponent(existing.updated_at || "") + '" alt="">' +
                        '<div><strong>' + esc(existing.display_name) + '</strong>' +
                          '<span>@' + esc(existing.username) + '</span>' +
                          '<p>' + esc(existing.bio || "") + '</p></div>' +
                      '</div>'
                    : '<p>Сгенерируй название creator-профиля, username, bio и локальный avatar. Потом всё можно отредактировать.</p>') +
                  '<div class="am-wizard-actions">' +
                    '<button type="button" class="am-btn primary" data-profile-generate ' +
                      (emailReady ? "" : "disabled") + '>' +
                      (profileReady ? "Regenerate profile" : "Generate profile") +
                    '</button>' +
                    (profileReady
                      ? '<button type="button" class="am-btn" data-profile-edit>Edit profile</button>'
                      : '') +
                  '</div>' +
                '</div>' +
              '</section>' +

              '<section class="am-wizard-step ' + (profileReady ? "active" : "locked") + '">' +
                '<div class="am-wizard-index">3</div>' +
                '<div class="am-wizard-body">' +
                  '<div class="am-wizard-title"><span>Platform sessions</span><span class="am-badge">' +
                    (profileReady ? "READY TO OPEN" : "NEXT") + '</span></div>' +
                  '<p>' + (profileReady
                    ? "TikTok теперь открывается обычным Edge без Playwright. Регистрацию и проверки проходишь вручную, а browser profile сохраняет сессию."
                    : "Сначала закончи creator profile.") + '</p>' +
                  '<div class="am-network-mini">' +
                    '<span>TikTok <b>' + esc(tikTokStatus) + '</b></span>' +
                    '<span>Instagram <b>' + esc(socialState(existing, "instagram")) + '</b></span>' +
                    '<span>YouTube <b>' + esc(socialState(existing, "youtube")) + '</b></span>' +
                  '</div>' +

                  (profileReady
                    ? '<div class="am-tiktok-manual">' +
                        '<div class="am-tiktok-head">' +
                          '<div><strong>TikTok · Manual Edge setup</strong>' +
                            '<span>' + esc(tikTokJob.step || (tikTokConnected ? "DONE" : "Ready")) + '</span></div>' +
                          '<span class="am-state ' + (tikTokConnected ? "free" : "used") + '">' +
                            (tikTokConnected ? "CONNECTED" : "MANUAL") +
                          '</span>' +
                        '</div>' +

                        '<div class="am-credential-grid">' +
                          '<div class="am-credential-box">' +
                            '<span>EMAIL</span><b>' + esc(existing.email || "—") + '</b>' +
                            '<button type="button" class="am-copy-btn" data-copy-email>Copy</button>' +
                          '</div>' +
                          '<div class="am-credential-box">' +
                            '<span>PASSWORD</span><b>••••••••••••</b>' +
                            '<button type="button" class="am-copy-btn" data-copy-password>Copy</button>' +
                          '</div>' +
                        '</div>' +

                        '<div class="am-tiktok-note">' +
                          '1. Open TikTok in Edge → 2. Sign up manually → 3. Paste Email/Password → ' +
                          '4. Complete DOB/code/CAPTCHA yourself → 5. When you are logged in, click Mark Connected.' +
                        '</div>' +

                        '<div class="am-wizard-actions">' +
                          '<button type="button" class="am-btn primary" data-tiktok-open>Open TikTok in Edge</button>' +
                          '<button type="button" class="am-btn" data-copy-email>Copy Email</button>' +
                          '<button type="button" class="am-btn" data-copy-password>Copy Password</button>' +
                          (tikTokConnected
                            ? '<button type="button" class="am-btn" disabled>✓ Connected</button>'
                            : '<button type="button" class="am-btn" data-tiktok-connected>Mark Connected</button>') +
                        '</div>' +
                      '</div>' +

                      '<div class="am-wizard-actions">' +
                        '<button type="button" class="am-btn" data-open-platform="instagram">Open Instagram</button>' +
                        '<button type="button" class="am-btn" data-open-platform="youtube">Open YouTube</button>' +
                      '</div>'
                    : '') +
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

    root.querySelector("[data-tiktok-open]")?.addEventListener("click", () => options.onTikTokOpen?.(existing));
    root.querySelectorAll("[data-copy-email]").forEach(button => {
      button.addEventListener("click", () => options.onCopyEmail?.(existing));
    });
    root.querySelectorAll("[data-copy-password]").forEach(button => {
      button.addEventListener("click", () => options.onCopyTikTokPassword?.(existing));
    });
    root.querySelector("[data-tiktok-connected]")?.addEventListener("click", () => options.onMarkTikTokConnected?.(existing));

    root.querySelectorAll("[data-open-platform]").forEach(button => {
      button.addEventListener("click", () => options.onOpenPlatform?.(existing, button.dataset.openPlatform));
    });
    root.querySelector("[data-back]")?.addEventListener("click", () => options.onBack?.());
  }

  window.PostingTTIICreateAccount = { render };
})();
