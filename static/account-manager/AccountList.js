(() => {
  "use strict";

  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));

  function statusClass(status) {
    const s = String(status || "").toUpperCase();
    if (s === "READY" || s === "CONNECTED") return "ready connected";
    if (s.includes("ACTION") || s.includes("NEED")) return "action";
    if (s === "FAILED" || s === "ERROR") return "failed";
    return "";
  }

  function accountLabel(account) {
    return "Account " + String(account.account_number || 0).padStart(2, "0");
  }

  function render(root, payload, handlers) {
    const accounts = payload.accounts || [];
    const counts = payload.counts || {};
    root.innerHTML =
      '<div class="am-metrics">' +
        '<div class="am-metric"><span>Total</span><b>' + Number(counts.total || 0) + '</b></div>' +
        '<div class="am-metric"><span>Ready</span><b>' + Number(counts.ready || 0) + '</b></div>' +
        '<div class="am-metric"><span>Creating</span><b>' + Number(counts.creating || 0) + '</b></div>' +
        '<div class="am-metric"><span>Action required</span><b>' + Number(counts.action_required || 0) + '</b></div>' +
      '</div>' +
      '<div class="am-top-actions" style="margin-bottom:12px">' +
        '<button type="button" class="am-btn primary" data-create>+ CREATE NEW ACCOUNT</button>' +
        '<button type="button" class="am-btn" data-refresh>REFRESH</button>' +
      '</div>' +
      '<div class="' + (accounts.length ? "am-grid" : "") + '" data-account-grid></div>';

    const grid = root.querySelector("[data-account-grid]");
    if (!accounts.length) {
      grid.innerHTML =
        '<div class="am-empty">' +
          '<h3>Пока нет Account связок</h3>' +
          '<div>Создай первую запись. Email, identity и браузерная автоматизация подключаются следующими фазами.</div>' +
        '</div>';
    } else {
      grid.innerHTML = accounts.map(account => {
        const social = account.social_accounts || {};
        const socials = ["tiktok", "instagram", "youtube"].map(platform => {
          const item = social[platform] || { status: "NOT_CREATED" };
          return '<div class="am-social"><b>' + esc(platform) + '</b><span class="am-status ' +
            statusClass(item.status) + '">' + esc(item.status || "NOT_CREATED") + '</span></div>';
        }).join("");

        return '<article class="am-card" data-account-id="' + Number(account.id) + '">' +
          '<div class="am-card-head">' +
            '<div><div class="am-card-title">' + esc(accountLabel(account)) + '</div>' +
            '<div class="am-subtitle">ID ' + Number(account.id) + '</div></div>' +
            '<span class="am-badge ' + statusClass(account.status) + '">' + esc(account.status || "CREATED") + '</span>' +
          '</div>' +
          '<dl class="am-kv">' +
            '<dt>Email</dt><dd>' + esc(account.email || "—") + '</dd>' +
            '<dt>Name</dt><dd>' + esc(account.display_name || "—") + '</dd>' +
            '<dt>Username</dt><dd>' + esc(account.username || "—") + '</dd>' +
            '<dt>Browser</dt><dd>' + esc(account.browser_profile_path || "—") + '</dd>' +
          '</dl>' +
          '<div class="am-socials">' + socials + '</div>' +
          '<div class="am-card-actions">' +
            '<button type="button" class="am-btn" data-action="open">OPEN</button>' +
            '<button type="button" class="am-btn" data-action="edit">EDIT</button>' +
            '<button type="button" class="am-btn" data-action="continue">CONTINUE SETUP</button>' +
            '<button type="button" class="am-btn" data-action="browser">OPEN BROWSER</button>' +
            '<button type="button" class="am-btn danger" data-action="delete">DELETE</button>' +
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
