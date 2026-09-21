(() => {
  "use strict";

  const state = {
    status: null,
    aliases: [],
    counts: { total: 0, free: 0, used: 0 },
    accounts: [],
    filter: "ALL",
    search: "",
    preferredAccountId: null
  };

  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));

  async function render(root, options = {}) {
    state.preferredAccountId = options.preferredAccountId || null;
    root.innerHTML = '<div class="am-empty">Подключаю Aliases...</div>';

    try {
      const [status, aliasesPayload, accountsPayload] = await Promise.all([
        options.api("/api/account-manager/addy/status"),
        options.api("/api/account-manager/aliases"),
        options.api("/api/account-manager/accounts")
      ]);

      state.status = status;
      state.aliases = aliasesPayload.aliases || [];
      state.counts = aliasesPayload.counts || { total: 0, free: 0, used: 0 };
      state.accounts = accountsPayload.accounts || [];
      paint(root, options);
    } catch (error) {
      root.innerHTML =
        '<div class="am-error">Не удалось загрузить aliases: ' + esc(error.message) + '</div>';
    }
  }

  function paint(root, options) {
    const status = state.status || {};
    const connected = Boolean(status.connected);
    const domains = status.domain_options || [];
    const filtered = state.aliases.filter(item => {
      if (state.filter !== "ALL" && item.state !== state.filter) return false;
      if (!state.search) return true;
      const q = state.search.toLowerCase();
      const assigned = item.assigned_account || {};
      return [
        item.email,
        item.domain,
        item.description,
        assigned.display_name,
        assigned.username,
        assigned.account_number
      ].some(value => String(value || "").toLowerCase().includes(q));
    });

    root.innerHTML =
      '<div class="am-page-stack">' +
        '<section class="am-hero">' +
          '<div class="am-hero-copy">' +
            '<span class="am-eyebrow">EMAIL INFRASTRUCTURE</span>' +
            '<h2>Aliases</h2>' +
            '<p>addy.io aliases для новых связок TikTok / Instagram / YouTube. Токен остаётся только на backend.</p>' +
          '</div>' +
          '<div class="am-hero-actions">' +
            '<span class="am-connection ' + (connected ? "ok" : "off") + '">' +
              '<span class="am-connection-dot"></span>' +
              (connected ? "addy.io connected" : "addy.io not connected") +
            '</span>' +
            '<button type="button" class="am-btn" data-refresh>↻ Refresh</button>' +
            '<button type="button" class="am-btn primary" data-create ' + (connected ? "" : "disabled") + '>+ Create alias</button>' +
          '</div>' +
        '</section>' +

        '<div class="am-metrics">' +
          '<div class="am-metric"><span>Aliases</span><b>' + Number(state.counts.total || 0) + '</b><small>в addy.io</small></div>' +
          '<div class="am-metric good"><span>Free</span><b>' + Number(state.counts.free || 0) + '</b><small>можно назначить</small></div>' +
          '<div class="am-metric used"><span>Used</span><b>' + Number(state.counts.used || 0) + '</b><small>уже привязаны</small></div>' +
          '<div class="am-metric"><span>Default domain</span><b class="am-metric-domain">' + esc(status.default_domain || "—") + '</b><small>' + domains.length + ' доступно</small></div>' +
        '</div>' +

        (!status.configured
          ? '<section class="am-notice warn">' +
              '<div class="am-notice-icon">!</div>' +
              '<div><strong>Нужно подключить addy.io</strong>' +
              '<p>Добавь API token в локальный <code>.env</code> как <code>ADDY_API_TOKEN=...</code>, затем перезапусти PostingTTII. Сам токен в интерфейс не выводится.</p></div>' +
            '</section>'
          : (!connected
            ? '<section class="am-notice danger"><div class="am-notice-icon">!</div><div><strong>addy.io недоступен</strong><p>' + esc(status.message || "Проверь API token") + '</p></div></section>'
            : '')) +

        '<section class="am-panel am-table-panel">' +
          '<div class="am-table-toolbar">' +
            '<div class="am-segmented">' +
              '<button type="button" class="' + (state.filter === "ALL" ? "active" : "") + '" data-filter="ALL">All</button>' +
              '<button type="button" class="' + (state.filter === "FREE" ? "active" : "") + '" data-filter="FREE">Free</button>' +
              '<button type="button" class="' + (state.filter === "USED" ? "active" : "") + '" data-filter="USED">Used</button>' +
            '</div>' +
            '<input class="am-search" data-search placeholder="Поиск alias..." value="' + esc(state.search) + '">' +
          '</div>' +
          '<div class="am-alias-list" data-list></div>' +
        '</section>' +
      '</div>';

    const list = root.querySelector("[data-list]");
    if (!status.configured) {
      list.innerHTML = '<div class="am-empty compact">Aliases появятся здесь после подключения addy.io.</div>';
    } else if (!filtered.length) {
      list.innerHTML = '<div class="am-empty compact">По текущему фильтру aliases не найдены.</div>';
    } else {
      list.innerHTML = filtered.map(item => aliasRow(item)).join("");
    }

    root.querySelector("[data-refresh]")?.addEventListener("click", () => render(root, options));
    root.querySelector("[data-create]")?.addEventListener("click", () => createAlias(root, options));

    root.querySelectorAll("[data-filter]").forEach(button => {
      button.addEventListener("click", () => {
        state.filter = button.dataset.filter || "ALL";
        paint(root, options);
      });
    });

    root.querySelector("[data-search]")?.addEventListener("input", event => {
      state.search = event.target.value || "";
      paint(root, options);
      const input = root.querySelector("[data-search]");
      input?.focus();
      if (input) input.setSelectionRange(input.value.length, input.value.length);
    });

    root.querySelectorAll("[data-assign]").forEach(button => {
      button.addEventListener("click", () => {
        const item = state.aliases.find(alias => alias.id === button.dataset.assign);
        if (item) assignAlias(item, root, options);
      });
    });

    root.querySelectorAll("[data-unassign]").forEach(button => {
      button.addEventListener("click", () => {
        const item = state.aliases.find(alias => alias.id === button.dataset.unassign);
        if (item) unassignAlias(item, root, options);
      });
    });
  }

  function aliasRow(item) {
    const assigned = item.assigned_account || null;
    const used = item.state === "USED";
    const accountLabel = assigned
      ? "Account " + String(assigned.account_number || 0).padStart(2, "0")
      : "Не назначен";

    return '<article class="am-alias-row">' +
      '<div class="am-alias-main">' +
        '<div class="am-alias-email">' + esc(item.email || "—") + '</div>' +
        '<div class="am-alias-meta">' +
          '<span>' + esc(item.domain || "addy.io") + '</span>' +
          (item.description ? '<span>• ' + esc(item.description) + '</span>' : '') +
        '</div>' +
      '</div>' +
      '<div class="am-alias-owner">' +
        '<span class="am-state ' + (used ? "used" : "free") + '">' + esc(item.state) + '</span>' +
        '<span>' + esc(accountLabel) + '</span>' +
      '</div>' +
      '<div class="am-alias-action">' +
        (used
          ? '<button type="button" class="am-btn subtle" data-unassign="' + esc(item.id) + '">Unassign</button>'
          : '<button type="button" class="am-btn primary small" data-assign="' + esc(item.id) + '">Assign</button>') +
      '</div>' +
    '</article>';
  }

  async function createAlias(root, options) {
    const status = state.status || {};
    const domains = status.domain_options || [];
    const form = document.createElement("div");
    form.className = "am-form-grid";
    form.innerHTML =
      '<div class="am-field full"><label>Domain</label><select class="am-input" name="domain"></select></div>' +
      '<div class="am-field"><label>Format</label><select class="am-input" name="format">' +
        '<option value="random_characters">Random characters</option>' +
        '<option value="uuid">UUID</option>' +
        '<option value="random_words">Random words</option>' +
        '<option value="custom">Custom</option>' +
      '</select></div>' +
      '<div class="am-field"><label>Local part</label><input class="am-input" name="local_part" placeholder="only for custom" disabled></div>' +
      '<div class="am-field full"><label>Description</label><input class="am-input" name="description" value="PostingTTII"></div>';

    const domainSelect = form.querySelector('[name="domain"]');
    for (const domain of domains) {
      const option = document.createElement("option");
      option.value = domain;
      option.textContent = domain;
      if (domain === status.default_domain) option.selected = true;
      domainSelect.appendChild(option);
    }

    if (!domains.length && status.default_domain) {
      const option = document.createElement("option");
      option.value = status.default_domain;
      option.textContent = status.default_domain;
      domainSelect.appendChild(option);
    }

    const formatSelect = form.querySelector('[name="format"]');
    const localInput = form.querySelector('[name="local_part"]');
    formatSelect.value = ["random_characters", "uuid", "random_words", "custom"].includes(status.default_format)
      ? status.default_format
      : "random_characters";
    localInput.disabled = formatSelect.value !== "custom";
    formatSelect.addEventListener("change", () => {
      localInput.disabled = formatSelect.value !== "custom";
      if (!localInput.disabled) localInput.focus();
    });

    const save = await options.modal({
      title: "Create addy.io alias",
      body: form,
      actions: [
        { label: "Cancel", value: false },
        { label: "Create alias", value: true, className: "primary" }
      ]
    });
    if (!save) return;

    try {
      const created = await options.api("/api/account-manager/aliases", {
        method: "POST",
        body: JSON.stringify({
          domain: domainSelect.value || "",
          format: formatSelect.value,
          local_part: localInput.value || "",
          description: form.querySelector('[name="description"]').value || "PostingTTII"
        })
      });
      options.toast("Alias создан: " + (created.email || "готов"));
      await render(root, options);
    } catch (error) {
      options.toast("Alias не создан: " + error.message);
    }
  }

  async function assignAlias(item, root, options) {
    const accounts = state.accounts || [];
    if (!accounts.length) {
      options.toast("Сначала создай Account");
      return;
    }

    const form = document.createElement("div");
    form.innerHTML =
      '<div class="am-field"><label>Alias</label><div class="am-readonly">' + esc(item.email || "—") + '</div></div>' +
      '<div class="am-field" style="margin-top:12px"><label>Assign to Account</label><select class="am-input" name="account"></select></div>';

    const select = form.querySelector('[name="account"]');
    for (const account of accounts) {
      const option = document.createElement("option");
      option.value = String(account.id);
      option.textContent =
        "Account " + String(account.account_number || 0).padStart(2, "0") +
        (account.display_name ? " — " + account.display_name : "") +
        (account.email ? " [" + account.email + "]" : "");
      if (Number(account.id) === Number(state.preferredAccountId)) option.selected = true;
      select.appendChild(option);
    }

    const confirmed = await options.modal({
      title: "Assign alias",
      body: form,
      actions: [
        { label: "Cancel", value: false },
        { label: "Assign", value: true, className: "primary" }
      ]
    });
    if (!confirmed) return;

    try {
      await options.api("/api/account-manager/aliases/" + encodeURIComponent(item.id) + "/assign", {
        method: "POST",
        body: JSON.stringify({
          account_id: Number(select.value),
          email: item.email
        })
      });
      options.toast("Alias назначен");
      state.preferredAccountId = null;
      await render(root, options);
    } catch (error) {
      options.toast("Не удалось назначить alias: " + error.message);
    }
  }

  async function unassignAlias(item, root, options) {
    const account = item.assigned_account;
    if (!account?.account_id) return;

    const confirmed = await options.modal({
      title: "Unassign alias?",
      body:
        '<p style="margin:0;color:#cbd5e1;font-size:12px;line-height:1.6">' +
        'Alias <strong>' + esc(item.email) + '</strong> будет отвязан от Account ' +
        String(account.account_number || 0).padStart(2, "0") +
        '. Сам alias в addy.io не удаляется.</p>',
      actions: [
        { label: "Cancel", value: false },
        { label: "Unassign", value: true, className: "danger" }
      ]
    });
    if (!confirmed) return;

    try {
      await options.api("/api/account-manager/accounts/" + Number(account.account_id) + "/alias/unassign", {
        method: "POST",
        body: "{}"
      });
      options.toast("Alias отвязан");
      await render(root, options);
    } catch (error) {
      options.toast("Не удалось отвязать alias: " + error.message);
    }
  }

  window.PostingTTIIAliases = { render };
})();
