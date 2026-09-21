(() => {
  "use strict";

  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));

  function statusClass(status) {
    const s = String(status || "").toUpperCase();
    if (s === "READY" || s === "CONNECTED" || s === "EMAIL_READY" || s === "PROFILE_READY") return "ready connected";
    if (s.includes("ACTION") || s.includes("NEED")) return "action";
    if (s === "FAILED" || s === "ERROR") return "failed";
    return "";
  }

  function accountLabel(account) {
    return "Account " + String(account.account_number || 0).padStart(2, "0");
  }

  function setupProgress(account) {
    let steps = 0;
    if (account.email) steps += 1;
    if (account.display_name && account.username) steps += 1;
    const social = account.social_accounts || {};
    if (["instagram", "youtube"].some(p => social[p]?.status === "CONNECTED")) steps += 1;
    return Math.round((steps / 3) * 100);
  }

  function socialIcon(platform) {
    if (platform === "instagram") return "IG";
    return "YT";
  }

  function render(root, payload, handlers) {
    const accounts = payload.accounts || [];
    const counts = payload.counts || {};

    root.innerHTML =
      '<div class="am-page-stack">' +
        '<section class="am-hero">' +
          '<div class="am-hero-copy">' +
            '<span class="am-eyebrow">ACCOUNT WORKSPACE</span>' +
            '<h2>Accounts</h2>' +
            '<p>Одна карточка = одна связка Email → Identity → Instagram / YouTube.</p>' +
          '</div>' +
          '<div class="am-hero-actions">' +
            '<button type="button" class="am-btn" data-refresh>↻ Refresh</button>' +
            '<button type="button" class="am-btn primary" data-create>+ New account</button>' +
          '</div>' +
        '</section>' +

        '<div class="am-metrics">' +
          '<div class="am-metric"><span>Total accounts</span><b>' + Number(counts.total || 0) + '</b><small>в локальной базе</small></div>' +
          '<div class="am-metric good"><span>Ready</span><b>' + Number(counts.ready || 0) + '</b><small>полностью настроены</small></div>' +
          '<div class="am-metric"><span>In setup</span><b>' + Number(counts.creating || 0) + '</b><small>нужно продолжить</small></div>' +
          '<div class="am-metric used"><span>Action required</span><b>' + Number(counts.action_required || 0) + '</b><small>нужно вмешательство</small></div>' +
        '</div>' +

        '<div class="' + (accounts.length ? "am-grid" : "") + '" data-account-grid></div>' +
      '</div>';

    const grid = root.querySelector("[data-account-grid]");

    if (!accounts.length) {
      grid.innerHTML =
        '<div class="am-empty">' +
          '<div class="am-empty-mark">+</div>' +
          '<h3>Создай первый Account</h3>' +
          '<div>После создания он сохранится в SQLite и не исчезнет после перезапуска PostingTTII.</div>' +
          '<button type="button" class="am-btn primary" data-empty-create style="margin-top:14px">Create first account</button>' +
        '</div>';
      root.querySelector("[data-empty-create]")?.addEventListener("click", handlers.onCreate);
    } else {
      grid.innerHTML = accounts.map(account => {
        const social = account.social_accounts || {};
        const progress = setupProgress(account);
        const initials = (account.display_name || account.username || accountLabel(account))
          .split(/\s+/).filter(Boolean).slice(0, 2).map(x => x[0]).join("").toUpperCase();

        const socials = ["instagram", "youtube"].map(platform => {
          const item = social[platform] || { status: "NOT_CREATED" };
          return '<div class="am-social-chip">' +
            '<span class="am-social-logo">' + socialIcon(platform) + '</span>' +
            '<span class="am-social-name">' + esc(platform) + '</span>' +
            '<span class="am-status ' + statusClass(item.status) + '">' + esc(item.status || "NOT_CREATED") + '</span>' +
          '</div>';
        }).join("");

        return '<article class="am-card am-account-card" data-account-id="' + Number(account.id) + '">' +
          '<div class="am-card-head">' +
            '<div class="am-account-head">' +
              '<div class="am-avatar">' +
                (account.avatar_path
                  ? '<img src="/api/account-manager/accounts/' + Number(account.id) + '/avatar?v=' + encodeURIComponent(account.updated_at || '') + '" alt="">'
                  : esc(initials || "AC")) +
              '</div>' +
              '<div>' +
                '<div class="am-card-title">' + esc(accountLabel(account)) + '</div>' +
                '<div class="am-subtitle">' + esc(account.display_name || account.username || "Identity not generated") + '</div>' +
              '</div>' +
            '</div>' +
            '<span class="am-badge ' + statusClass(account.status) + '">' + esc(account.status || "CREATED") + '</span>' +
          '</div>' +

          '<div class="am-progress-line"><span style="width:' + progress + '%"></span></div>' +
          '<div class="am-progress-label"><span>Setup progress</span><b>' + progress + '%</b></div>' +

          '<div class="am-account-email">' +
            '<span class="am-mini-label">EMAIL</span>' +
            '<strong>' + esc(account.email || "Alias not assigned") + '</strong>' +
          '</div>' +

          '<div class="am-social-row">' + socials + '</div>' +

          '<div class="am-card-actions">' +
            '<button type="button" class="am-btn primary small" data-action="continue">Continue setup</button>' +
            '<button type="button" class="am-btn subtle" data-action="open">Details</button>' +
            '<button type="button" class="am-btn subtle" data-action="edit">Edit</button>' +
            '<button type="button" class="am-btn subtle" data-action="browser">Browser</button>' +
            '<button type="button" class="am-icon-danger" data-action="delete" title="Delete">×</button>' +
          '</div>' +
        '</article>';
      }).join("");
    }

    root.querySelector("[data-create]")?.addEventListener("click", handlers.onCreate);
    root.querySelector("[data-refresh]")?.addEventListener("click", handlers.onRefresh);

    root.querySelectorAll("[data-account-id]").forEach(card => {
      const id = Number(card.dataset.accountId);
      card.querySelectorAll("[data-action]").forEach(button => {
        button.addEventListener("click", () => handlers.onAction(button.dataset.action, id));
      });
    });
  }

  window.PostingTTIIAccountList = { render, accountLabel, esc };
})();
