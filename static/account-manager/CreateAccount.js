(() => {
  "use strict";

  function render(root, options = {}) {
    const existing = options.account || null;
    const number = existing ? String(existing.account_number || 0).padStart(2, "0") : "";
    root.innerHTML =
      '<div class="am-create-layout">' +
        '<section class="am-panel">' +
          '<h2>' + (existing ? "Continue Account " + number : "CREATE ACCOUNT") + '</h2>' +
          '<p>Phase 3 создаёт постоянную запись Account в SQLite. addy.io, генерация identity и Playwright будут подключены по следующим фазам без переписывания этой базы.</p>' +
          '<div class="am-step"><div class="am-step-row"><strong>1. Email</strong><span class="am-badge">PHASE 4</span></div><small>Существующий alias или новый addy.io alias.</small></div>' +
          '<div class="am-step"><div class="am-step-row"><strong>2. Identity</strong><span class="am-badge">PHASE 5</span></div><small>Name, username, bio, password и avatar.</small></div>' +
          '<div class="am-step"><div class="am-step-row"><strong>3. Social Networks</strong><span class="am-badge">PHASE 6–9</span></div><small>TikTok, Instagram и YouTube через persistent browser profile.</small></div>' +
          (existing
            ? '<button type="button" class="am-btn" style="margin-top:14px" data-back-accounts>BACK TO ACCOUNTS</button>'
            : '<button type="button" class="am-btn primary" style="margin-top:14px" data-create-draft>CREATE DRAFT ACCOUNT</button>') +
        '</section>' +
        '<aside class="am-panel">' +
          '<h2>State machine</h2>' +
          '<p>' + (existing
            ? "Текущий статус: " + (existing.status || "CREATED")
            : "После создания запись получит статус CREATED и не потеряется после перезапуска приложения.") + '</p>' +
          '<div class="am-step"><strong>Database</strong><small>data/account_manager.db</small></div>' +
          '<div class="am-step"><strong>Browser Profile</strong><small>' + (existing?.browser_profile_path || "создастся автоматически для Account") + '</small></div>' +
        '</aside>' +
      '</div>';

    root.querySelector("[data-create-draft]")?.addEventListener("click", () => options.onCreateDraft?.());
    root.querySelector("[data-back-accounts]")?.addEventListener("click", () => options.onBack?.());
  }

  window.PostingTTIICreateAccount = { render };
})();
