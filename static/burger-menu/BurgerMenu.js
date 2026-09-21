(() => {
  "use strict";

  const GROUPS = [
    {
      label: "Workspace",
      items: [
        { id: "dashboard", title: "Dashboard", icon: "▦", note: "Очередь публикаций" }
      ]
    },
    {
      label: "Account Manager",
      items: [
        { id: "accounts", title: "Accounts", icon: "◎", note: "Все связки аккаунтов" },
        { id: "create-account", title: "Create Account", icon: "+", note: "Новая связка" },
        { id: "aliases", title: "Aliases", icon: "@", note: "addy.io email aliases" }
      ]
    },
    {
      label: "Automation",
      items: [
        { id: "browser-profiles", title: "Browser Profiles", icon: "◫", note: "Persistent sessions" },
        { id: "autoposting-accounts", title: "Autoposting Accounts", icon: "↗", note: "Аккаунты публикации" },
        { id: "logs", title: "Logs", icon: "≡", note: "История действий" },
        { id: "settings", title: "Settings", icon: "⚙", note: "Настройки модуля" }
      ]
    }
  ];

  const ITEMS = GROUPS.flatMap(group => group.items);
  const ACCOUNT_MANAGER_ROUTES = new Set(
    ITEMS.map(item => item.id).filter(id => id !== "dashboard")
  );

  let overlay = null;
  let panel = null;
  let burger = null;
  let lastFocused = null;
  let accountManagerPromise = null;

  function currentRoute() {
    const value = String(location.hash || "").replace(/^#/, "");
    return ITEMS.some(item => item.id === value) ? value : "dashboard";
  }

  function setExpanded(open) {
    burger?.setAttribute("aria-expanded", open ? "true" : "false");
  }

  async function refreshQuickStats() {
    const accountsEl = panel?.querySelector("[data-pt-accounts]");
    const addyEl = panel?.querySelector("[data-pt-addy]");
    if (!accountsEl || !addyEl) return;

    accountsEl.textContent = "—";
    addyEl.textContent = "checking";
    addyEl.className = "pt-mini-value";

    try {
      const [accountsResponse, addyResponse] = await Promise.all([
        fetch("/api/account-manager/accounts", { cache: "no-store" }),
        fetch("/api/account-manager/addy/status", { cache: "no-store" })
      ]);

      if (accountsResponse.ok) {
        const data = await accountsResponse.json();
        accountsEl.textContent = String(data.counts?.total ?? 0);
      }

      if (addyResponse.ok) {
        const data = await addyResponse.json();
        addyEl.textContent = data.connected ? "online" : (data.configured ? "error" : "off");
        addyEl.className = "pt-mini-value " + (data.connected ? "ok" : data.configured ? "warn" : "");
      }
    } catch {
      addyEl.textContent = "offline";
      addyEl.className = "pt-mini-value warn";
    }
  }

  function openMenu() {
    if (!overlay || overlay.classList.contains("open")) return;
    lastFocused = document.activeElement;
    overlay.classList.add("open");
    overlay.setAttribute("aria-hidden", "false");
    setExpanded(true);
    document.body.dataset.ptMenuOpen = "1";
    refreshQuickStats();
    setTimeout(() => panel?.querySelector(".pt-menu-close")?.focus(), 0);
  }

  function closeMenu(options = {}) {
    const restoreFocus = options.restoreFocus !== false;
    if (!overlay || !overlay.classList.contains("open")) return;
    overlay.classList.remove("open");
    overlay.setAttribute("aria-hidden", "true");
    setExpanded(false);
    delete document.body.dataset.ptMenuOpen;
    if (restoreFocus && lastFocused && typeof lastFocused.focus === "function") {
      lastFocused.focus();
    }
  }

  function toggleMenu() {
    if (overlay?.classList.contains("open")) closeMenu();
    else openMenu();
  }

  function paintActive(route) {
    document.querySelectorAll(".pt-menu-item").forEach(button => {
      const active = button.dataset.route === route;
      button.classList.toggle("active", active);
      button.setAttribute("aria-current", active ? "page" : "false");
    });
  }

  function loadStyle(href) {
    if (document.querySelector('link[href="' + href + '"]')) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    document.head.appendChild(link);
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector('script[src="' + src + '"]');
      if (existing) {
        if (existing.dataset.loaded === "1") return resolve();
        existing.addEventListener("load", resolve, { once: true });
        existing.addEventListener("error", reject, { once: true });
        return;
      }

      const script = document.createElement("script");
      script.src = src;
      script.defer = true;
      script.addEventListener("load", () => {
        script.dataset.loaded = "1";
        resolve();
      }, { once: true });
      script.addEventListener("error", reject, { once: true });
      document.head.appendChild(script);
    });
  }

  async function ensureAccountManager() {
    if (window.PostingTTIIAccountManager) return;
    if (accountManagerPromise) return accountManagerPromise;

    accountManagerPromise = (async () => {
      loadStyle("/static/account-manager/account-manager.css");
      await loadScript("/static/account-manager/AccountList.js");
      await loadScript("/static/account-manager/CreateAccount.js");
      await loadScript("/static/account-manager/Aliases.js");
      await loadScript("/static/account-manager/BrowserProfiles.js");
      await loadScript("/static/account-manager/AccountManager.js");
    })();

    try {
      await accountManagerPromise;
    } catch (error) {
      accountManagerPromise = null;
      throw error;
    }
  }

  async function navigate(route) {
    paintActive(route);

    if (route === "dashboard") {
      if (location.hash) history.replaceState(null, "", location.pathname + location.search);
      closeMenu();
      window.PostingTTIIAccountManager?.close();
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }

    history.replaceState(null, "", "#" + route);
    closeMenu({ restoreFocus: false });

    if (ACCOUNT_MANAGER_ROUTES.has(route)) {
      try {
        await ensureAccountManager();
        await window.PostingTTIIAccountManager.navigate(route);
      } catch (error) {
        alert("Не удалось открыть Account Manager: " + error.message);
      }
    }
  }

  function createNavButton(item) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pt-menu-item";
    button.dataset.route = item.id;
    button.innerHTML =
      '<span class="pt-menu-item-icon" aria-hidden="true">' + item.icon + '</span>' +
      '<span class="pt-menu-item-text">' +
        '<span class="pt-menu-item-title"></span>' +
        '<span class="pt-menu-item-note"></span>' +
      '</span>' +
      '<span class="pt-menu-chevron" aria-hidden="true">›</span>';
    button.querySelector(".pt-menu-item-title").textContent = item.title;
    button.querySelector(".pt-menu-item-note").textContent = item.note;
    button.addEventListener("click", () => navigate(item.id));
    return button;
  }

  function buildMenu() {
    if (document.querySelector(".pt-burger-button")) return;

    burger = document.createElement("button");
    burger.type = "button";
    burger.className = "pt-burger-button";
    burger.setAttribute("aria-label", "Открыть PostingTTII menu");
    burger.setAttribute("aria-expanded", "false");
    burger.setAttribute("aria-controls", "ptAccountMenu");
    burger.innerHTML =
      '<span class="pt-burger-icon" aria-hidden="true"></span>' +
      '<span class="pt-burger-label">Menu</span>';
    burger.addEventListener("click", toggleMenu);

    overlay = document.createElement("div");
    overlay.className = "pt-menu-overlay";
    overlay.id = "ptAccountMenu";
    overlay.setAttribute("aria-hidden", "true");

    panel = document.createElement("aside");
    panel.className = "pt-menu-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", "PostingTTII menu");

    const header = document.createElement("div");
    header.className = "pt-menu-header";
    header.innerHTML =
      '<div class="pt-brand">' +
        '<div class="pt-brand-mark">PT</div>' +
        '<div><div class="pt-menu-title">PostingTTII</div><div class="pt-menu-subtitle">Creator workspace</div></div>' +
      '</div>' +
      '<button type="button" class="pt-menu-close" aria-label="Закрыть меню">×</button>';

    const content = document.createElement("div");
    content.className = "pt-menu-content";

    const statusCard = document.createElement("div");
    statusCard.className = "pt-workspace-card";
    statusCard.innerHTML =
      '<div class="pt-workspace-card-top">' +
        '<div><span>ACCOUNT MANAGER</span><strong>Workspace</strong></div>' +
        '<button type="button" class="pt-quick-add" data-quick-add>+ New</button>' +
      '</div>' +
      '<div class="pt-mini-stats">' +
        '<div><span>Accounts</span><b data-pt-accounts>—</b></div>' +
        '<div><span>addy.io</span><b class="pt-mini-value" data-pt-addy>—</b></div>' +
      '</div>';
    statusCard.querySelector("[data-quick-add]").addEventListener("click", () => navigate("create-account"));
    content.appendChild(statusCard);

    const nav = document.createElement("nav");
    nav.className = "pt-menu-nav";
    nav.setAttribute("aria-label", "PostingTTII navigation");

    for (const group of GROUPS) {
      const section = document.createElement("section");
      section.className = "pt-menu-section";

      const label = document.createElement("div");
      label.className = "pt-menu-group-label";
      label.textContent = group.label;
      section.appendChild(label);

      const list = document.createElement("div");
      list.className = "pt-menu-group-list";
      for (const item of group.items) list.appendChild(createNavButton(item));
      section.appendChild(list);
      nav.appendChild(section);
    }

    content.appendChild(nav);

    const footer = document.createElement("div");
    footer.className = "pt-menu-footer";
    footer.innerHTML =
      '<div class="pt-local-dot"></div>' +
      '<div><strong>Local workspace</strong><span>127.0.0.1:8765 · Profile + Browser</span></div>';

    panel.append(header, content, footer);
    overlay.appendChild(panel);
    document.body.append(burger, overlay);

    panel.querySelector(".pt-menu-close").addEventListener("click", () => closeMenu());

    overlay.addEventListener("click", event => {
      if (event.target === overlay) closeMenu();
    });

    panel.addEventListener("click", event => event.stopPropagation());

    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && overlay.classList.contains("open")) {
        event.preventDefault();
        closeMenu();
      }
    });

    window.addEventListener("hashchange", () => {
      const route = currentRoute();
      paintActive(route);
      if (route !== "dashboard") navigate(route);
    });

    const route = currentRoute();
    paintActive(route);
    if (route !== "dashboard") navigate(route);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildMenu, { once: true });
  } else {
    buildMenu();
  }

  window.PostingTTIIBurgerMenu = {
    open: openMenu,
    close: closeMenu,
    toggle: toggleMenu,
    navigate
  };
})();
