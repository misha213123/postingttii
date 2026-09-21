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
    const instagramStatus = socialState(existing, "instagram");
    const instagramConnected = instagramStatus === "CONNECTED";
    const instagramStarted = instagramStatus !== "NOT_CREATED";
    const instagramJob = existing?.creation_jobs?.instagram || {};

    root.innerHTML =
      '<div class="am-page-stack">' +
        '<section class="am-hero">' +
          '<div class="am-hero-copy">' +
            '<span class="am-eyebrow">ACCOUNT WORKFLOW</span>' +
            '<h2>' + (existing ? "Account " + number : "Create account") + '</h2>' +
            '<p>' + (existing
              ? "Email alias и отдельная Edge-сессия сохраняются за этим Account."
              : "Создай постоянную связку. Основной интерфейс PostingTTII при этом не меняется.") + '</p>' +
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
              '<div><h2>Создай основу Account</h2><p>Создаётся локальная запись SQLite и отдельный постоянный Edge profile path.</p></div>' +
              '<button type="button" class="am-btn primary" data-create-draft>Create Account</button>' +
            '</section>'
          : '<div class="am-wizard">' +
              '<section class="am-wizard-step ' + (emailReady ? "done" : "active") + '">' +
                '<div class="am-wizard-index">1</div>' +
                '<div class="am-wizard-body">' +
                  '<div class="am-wizard-title"><span>Email alias</span><span class="am-state ' +
                    (emailReady ? "free" : "used") + '">' + (emailReady ? "READY" : "ADDY.IO") + '</span></div>' +
                  '<p>' + (emailReady
                    ? "Назначен alias: " + esc(existing.email)
                    : "Можно выбрать alias вручную. Если не выбирать, при запуске Instagram PostingTTII автоматически возьмёт первый свободный alias из addy.io.") + '</p>' +
                  '<div class="am-wizard-actions">' +
                    '<button type="button" class="am-btn" data-alias>' +
                      (emailReady ? "Change alias" : "Choose alias manually") +
                    '</button>' +
                  '</div>' +
                '</div>' +
              '</section>' +

              '<section class="am-wizard-step ' +
                (profileReady ? "done" : (emailReady ? "active" : "")) + '">' +
                '<div class="am-wizard-index">2</div>' +
                '<div class="am-wizard-body">' +
                  '<div class="am-wizard-title"><span>Creator profile</span><span class="am-badge ' +
                    (profileReady ? "ready" : "") + '">' +
                    (profileReady ? "READY" : "OPTIONAL NOW") + '</span></div>' +
                  (profileReady
                    ? '<div class="am-profile-preview">' +
                        '<img src="/api/account-manager/accounts/' + Number(existing.id) +
                          '/avatar?v=' + encodeURIComponent(existing.updated_at || "") + '" alt="">' +
                        '<div><strong>' + esc(existing.display_name) + '</strong>' +
                          '<span>@' + esc(existing.username) + '</span>' +
                          '<p>' + esc(existing.bio || "") + '</p></div>' +
                      '</div>'
                    : '<p>Профиль можно сгенерировать после назначения alias. Для запуска регистрации Instagram он не обязателен.</p>') +
                  '<div class="am-wizard-actions">' +
                    '<button type="button" class="am-btn" data-profile-generate ' +
                      (emailReady ? "" : "disabled") + '>' +
                      (profileReady ? "Regenerate profile" : "Generate profile") +
                    '</button>' +
                    (profileReady
                      ? '<button type="button" class="am-btn" data-profile-edit>Edit profile</button>'
                      : '') +
                  '</div>' +
                '</div>' +
              '</section>' +

              '<section class="am-wizard-step ' + (instagramConnected ? "done" : "active") + '">' +
                '<div class="am-wizard-index">3</div>' +
                '<div class="am-wizard-body">' +
                  '<div class="am-wizard-title"><span>Instagram · Edge session</span><span class="am-badge ' +
                    (instagramConnected ? "ready" : "") + '">' +
                    (instagramConnected ? "CONNECTED" : "READY TO OPEN") + '</span></div>' +
                  '<p>Для этого Account открывается отдельный постоянный профиль Microsoft Edge. Email берётся из addy.io и автоматически подставляется в форму Instagram.</p>' +

                  '<div class="am-instagram-manual">' +
                    '<div class="am-instagram-head">' +
                      '<div><strong>Instagram registration</strong>' +
                        '<span>' + esc(instagramJob.step || (instagramConnected ? "DONE" : "EDGE REGISTRATION")) + '</span></div>' +
                      '<span class="am-state ' + (instagramConnected ? "free" : "used") + '">' +
                        (instagramConnected ? "CONNECTED" : "MANUAL CHECKS") +
                      '</span>' +
                    '</div>' +

                    '<div class="am-credential-grid">' +
                      '<div class="am-credential-box">' +
                        '<span>EMAIL</span><b>' + esc(existing.email || "будет выбран из addy.io") + '</b>' +
                        (emailReady
                          ? '<button type="button" class="am-copy-btn" data-copy-email>Copy</button>'
                          : '') +
                      '</div>' +
                      '<div class="am-credential-box">' +
                        '<span>EDGE PROFILE</span><b>Account ' + number + '</b>' +
                        '<small>cookies + Instagram session сохраняются отдельно</small>' +
                      '</div>' +
                    '</div>' +

                    '<div class="am-instagram-note">' +
                      '1. Open Instagram in Edge → 2. PostingTTII подставит email → ' +
                      '3. Остальные поля, код подтверждения и проверки проходишь вручную → ' +
                      '4. После успешного входа нажми Mark Connected.' +
                    '</div>' +

                    '<div class="am-wizard-actions">' +
                      (instagramStarted
                        ? '<button type="button" class="am-btn primary" data-instagram-open-account>Open this Instagram account</button>'
                        : '<button type="button" class="am-btn primary" data-instagram-open>Open Instagram in Edge</button>') +
                      (emailReady ? '<button type="button" class="am-btn" data-copy-email>Copy Email</button>' : '') +
                      (instagramConnected
                        ? '<button type="button" class="am-btn" disabled>✓ Connected</button>'
                        : '<button type="button" class="am-btn" data-instagram-connected>Mark Connected</button>') +
                    '</div>' +
                  '</div>' +
                '</div>' +
              '</section>' +
            '</div>') +

        (existing
          ? '<section class="am-panel am-account-tech">' +
              '<div><span>Database</span><b>data/account_manager.db</b></div>' +
              '<div><span>Edge profile</span><b>' + esc(existing.browser_profile_path || "—") + '</b></div>' +
              '<div><span>Account ID</span><b>' + Number(existing.id) + '</b></div>' +
            '</section>'
          : '') +
      '</div>';

    root.querySelector("[data-create-draft]")?.addEventListener("click", () => options.onCreateDraft?.());
    root.querySelector("[data-alias]")?.addEventListener("click", () => options.onChooseAlias?.(existing));
    root.querySelector("[data-profile-generate]")?.addEventListener("click", () => options.onGenerateProfile?.(existing));
    root.querySelector("[data-profile-edit]")?.addEventListener("click", () => options.onEditProfile?.(existing));

    root.querySelector("[data-instagram-open]")?.addEventListener("click", () => options.onInstagramOpen?.(existing));
    root.querySelector("[data-instagram-open-account]")?.addEventListener("click", () => options.onInstagramAccountOpen?.(existing));
    root.querySelectorAll("[data-copy-email]").forEach(button => {
      button.addEventListener("click", () => options.onCopyEmail?.(existing));
    });
    root.querySelector("[data-instagram-connected]")?.addEventListener("click", () => options.onMarkInstagramConnected?.(existing));

    root.querySelector("[data-back]")?.addEventListener("click", () => options.onBack?.());
  }

  window.PostingTTIICreateAccount = { render };
})();
