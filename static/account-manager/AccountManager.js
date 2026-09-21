(() => {
  "use strict";

  let root = null;
  let contentNode = null;
  let titleNode = null;
  let subtitleNode = null;
  let selectedAccountId = null;

  const ROUTE_TITLES = {
    accounts: ["Accounts", "Все созданные связки аккаунтов"],
    "create-account": ["Create Account", "Мастер создания новой связки"],
    aliases: ["Aliases", "Управление email aliases"],
    "browser-profiles": ["Browser Profiles", "Persistent browser sessions"],
    "autoposting-accounts": ["Autoposting Accounts", "Интеграция с текущим PostingTTII"],
    logs: ["Logs", "Журнал Account Manager"],
    settings: ["Settings", "Настройки Account Manager"]
  };

  const esc = value => window.PostingTTIIAccountList?.esc(value) || String(value ?? "");

  function ensureRoot() {
    if (root) return root;

    root = document.createElement("section");
    root.className = "am-root hidden";
    root.innerHTML =
      '<div class="am-shell">' +
        '<header class="am-topbar">' +
          '<div><div class="am-title" data-am-title>Accounts</div><div class="am-subtitle" data-am-subtitle></div></div>' +
          '<div class="am-top-actions"><button type="button" class="am-btn" data-am-dashboard>← Dashboard</button></div>' +
        '</header>' +
        '<main data-am-content></main>' +
      '</div>';

    document.body.appendChild(root);
    contentNode = root.querySelector("[data-am-content]");
    titleNode = root.querySelector("[data-am-title]");
    subtitleNode = root.querySelector("[data-am-subtitle]");
    root.querySelector("[data-am-dashboard]").addEventListener("click", () => {
      if (window.PostingTTIIBurgerMenu?.navigate) window.PostingTTIIBurgerMenu.navigate("dashboard");
      else close();
    });

    return root;
  }

  function setHeader(route) {
    const pair = ROUTE_TITLES[route] || [route, ""];
    titleNode.textContent = pair[0];
    subtitleNode.textContent = pair[1];
  }

  function toast(message) {
    const el = document.createElement("div");
    el.className = "am-toast";
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2400);
  }

  function modal(options) {
    return new Promise(resolve => {
      const backdrop = document.createElement("div");
      backdrop.className = "am-modal-backdrop";

      const box = document.createElement("div");
      box.className = "am-modal";
      box.innerHTML =
        '<div class="am-modal-head"><h3></h3><button type="button" class="am-btn" data-close>×</button></div>' +
        '<div class="am-modal-body"></div>' +
        '<div class="am-modal-actions"></div>';

      box.querySelector("h3").textContent = options.title || "";
      const bodyNode = box.querySelector(".am-modal-body");

      if (typeof options.body === "string") {
        bodyNode.innerHTML = options.body;
      } else if (options.body) {
        bodyNode.appendChild(options.body);
      }

      const actionsNode = box.querySelector(".am-modal-actions");
      let finished = false;

      const finish = value => {
        if (finished) return;
        finished = true;
        backdrop.remove();
        resolve(value);
      };

      box.querySelector("[data-close]").addEventListener("click", () => finish(null));

      for (const action of options.actions || []) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "am-btn" + (action.className ? " " + action.className : "");
        button.textContent = action.label;
        button.addEventListener("click", () => finish(action.value));
        actionsNode.appendChild(button);
      }

      backdrop.addEventListener("click", event => {
        if (event.target === backdrop) finish(null);
      });

      box.addEventListener("click", event => event.stopPropagation());
      backdrop.appendChild(box);
      document.body.appendChild(backdrop);
    });
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {})
      }
    });

    if (response.status === 204) return null;

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || ("HTTP " + response.status));
    }

    return data;
  }

  async function loadAccounts() {
    contentNode.innerHTML = '<div class="am-empty">Загружаю Accounts...</div>';

    try {
      const payload = await api("/api/account-manager/accounts");
      window.PostingTTIIAccountList.render(contentNode, payload, {
        onCreate: () => navigate("create-account"),
        onRefresh: loadAccounts,
        onAction: handleAccountAction
      });
    } catch (error) {
      contentNode.innerHTML =
        '<div class="am-error">Не удалось загрузить Accounts: ' + esc(error.message) + '</div>';
    }
  }

  async function fetchAccount(id) {
    return api("/api/account-manager/accounts/" + Number(id));
  }

  async function showDetails(account) {
    const social = account.social_accounts || {};
    const number = String(account.account_number || 0).padStart(2, "0");

    await modal({
      title: "Account " + number,
      body:
        '<dl class="am-kv">' +
          '<dt>Email</dt><dd>' + esc(account.email || "—") + '</dd>' +
          '<dt>Name</dt><dd>' + esc(account.display_name || "—") + '</dd>' +
          '<dt>Username</dt><dd>' + esc(account.username || "—") + '</dd>' +
          '<dt>Bio</dt><dd>' + esc(account.bio || "—") + '</dd>' +
          '<dt>Browser</dt><dd>' + esc(account.browser_profile_path || "—") + '</dd>' +
          '<dt>Status</dt><dd>' + esc(account.status || "CREATED") + '</dd>' +
          '<dt>TikTok</dt><dd>' + esc(social.tiktok?.status || "NOT_CREATED") + '</dd>' +
          '<dt>Instagram</dt><dd>' + esc(social.instagram?.status || "NOT_CREATED") + '</dd>' +
          '<dt>YouTube</dt><dd>' + esc(social.youtube?.status || "NOT_CREATED") + '</dd>' +
        '</dl>',
      actions: [{ label: "CLOSE", value: true }]
    });
  }

  async function editAccount(account) {
    const form = document.createElement("form");
    form.className = "am-form-grid";
    form.innerHTML =
      '<div class="am-field full"><label>Email</label><input class="am-input" name="email" type="email"></div>' +
      '<div class="am-field"><label>Display Name</label><input class="am-input" name="display_name"></div>' +
      '<div class="am-field"><label>Username</label><input class="am-input" name="username"></div>' +
      '<div class="am-field full"><label>Bio</label><textarea class="am-textarea" name="bio"></textarea></div>';

    form.elements.email.value = account.email || "";
    form.elements.display_name.value = account.display_name || "";
    form.elements.username.value = account.username || "";
    form.elements.bio.value = account.bio || "";

    const save = await modal({
      title: "Edit Account " + String(account.account_number || 0).padStart(2, "0"),
      body: form,
      actions: [
        { label: "CANCEL", value: false },
        { label: "SAVE", value: true, className: "primary" }
      ]
    });

    if (!save) return;

    try {
      await api("/api/account-manager/accounts/" + account.id, {
        method: "PATCH",
        body: JSON.stringify({
          email: form.elements.email.value,
          display_name: form.elements.display_name.value,
          username: form.elements.username.value,
          bio: form.elements.bio.value
        })
      });
      toast("Account сохранён");
      await loadAccounts();
    } catch (error) {
      toast("Ошибка: " + error.message);
    }
  }

  async function deleteAccount(account) {
    const ok = await modal({
      title: "Удалить Account?",
      body:
        '<p style="margin:0;color:#cbd5e1;font-size:12px;line-height:1.55">' +
        'Account ' + String(account.account_number || 0).padStart(2, "0") +
        ' будет удалён из Account Manager вместе с social/job записями. ' +
        'Текущие OAuth аккаунты PostingTTII этим действием не удаляются.</p>',
      actions: [
        { label: "CANCEL", value: false },
        { label: "DELETE", value: true, className: "danger" }
      ]
    });

    if (!ok) return;

    try {
      await api("/api/account-manager/accounts/" + account.id, { method: "DELETE" });
      toast("Account удалён");
      await loadAccounts();
    } catch (error) {
      toast("Ошибка удаления: " + error.message);
    }
  }

  async function handleAccountAction(action, id) {
    try {
      const account = await fetchAccount(id);

      if (action === "open") return showDetails(account);
      if (action === "edit") return editAccount(account);
      if (action === "delete") return deleteAccount(account);

      if (action === "continue") {
        selectedAccountId = id;
        return navigate("create-account");
      }

      if (action === "browser") {
        return toast("OPEN BROWSER подключим в Phase 6 вместе с Playwright persistent profile.");
      }
    } catch (error) {
      toast("Ошибка: " + error.message);
    }
  }

  async function renderCreate() {
    let account = null;

    if (selectedAccountId) {
      try {
        account = await fetchAccount(selectedAccountId);
      } catch (error) {
        selectedAccountId = null;
      }
    }

    window.PostingTTIICreateAccount.render(contentNode, {
      account,
      onCreateDraft: async () => {
        try {
          const created = await api("/api/account-manager/accounts", {
            method: "POST",
            body: "{}"
          });
          selectedAccountId = created.id;
          toast("Account " + String(created.account_number || 0).padStart(2, "0") + " создан");
          navigate("accounts");
        } catch (error) {
          toast("Не удалось создать Account: " + error.message);
        }
      },
      onBack: () => navigate("accounts")
    });
  }

  function placeholder(route) {
    const routeTitle = ROUTE_TITLES[route]?.[0] || route;
    contentNode.innerHTML =
      '<section class="am-panel">' +
        '<h2>' + esc(routeTitle) + '</h2>' +
        '<p>Каркас раздела подключён к Account Manager. Функциональность будет добавлена своей отдельной фазой, не затрагивая текущий автопостинг.</p>' +
      '</section>';
  }

  async function navigate(route) {
    ensureRoot();

    if (!ROUTE_TITLES[route]) route = "accounts";

    root.classList.remove("hidden");
    setHeader(route);

    if (route !== "create-account") {
      selectedAccountId = null;
    }

    if (route === "accounts") {
      await loadAccounts();
    } else if (route === "create-account") {
      await renderCreate();
    } else if (route === "aliases") {
      window.PostingTTIIAliases.render(contentNode);
    } else {
      placeholder(route);
    }
  }

  function close() {
    ensureRoot();
    root.classList.add("hidden");
    selectedAccountId = null;
    history.replaceState(null, "", location.pathname + location.search);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  window.addEventListener("postingttii:navigate", event => {
    const route = event.detail?.route;
    if (route && route !== "dashboard") {
      navigate(route);
    }
  });

  window.PostingTTIIAccountManager = {
    navigate,
    close
  };
})();
