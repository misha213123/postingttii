(() => {
  "use strict";

  const ITEMS = [
    { id: "dashboard", title: "Dashboard", icon: "▦", note: "Текущий автопостинг" },
    { id: "accounts", title: "Accounts", icon: "◎", note: "Связки соцсетей" },
    { id: "create-account", title: "Create Account", icon: "+", note: "Создание новой связки" },
    { id: "aliases", title: "Aliases", icon: "@", note: "Email aliases" },
    { id: "browser-profiles", title: "Browser Profiles", icon: "◫", note: "Persistent browser sessions" },
    { id: "autoposting-accounts", title: "Autoposting Accounts", icon: "↗", note: "Аккаунты публикации" },
    { id: "logs", title: "Logs", icon: "≡", note: "Журнал действий" },
    { id: "settings", title: "Settings", icon: "⚙", note: "Настройки модуля" }
  ];

  const ACCOUNT_MANAGER_ROUTES = new Set([
    "accounts",
    "create-account",
    "aliases",
    "browser-profiles",
    "autoposting-accounts",
    "logs",
    "settings"
  ]);

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

  function openMenu() {
    if (!overlay || overlay.classList.contains("open")) return;
    lastFocused = document.activeElement;
    overlay.classList.add("open");
    overlay.setAttribute("aria-hidden", "false");
    setExpanded(true);
    document.body.dataset.ptMenuOpen = "1";
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
      if (location.hash) {
        history.replaceState(null, "", location.pathname + location.search);
      }
      closeMenu();
      window.PostingTTIIAccountManager?.close();
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }

    history.replaceState(null, "", "#" + route);
    closeMenu();

    if (ACCOUNT_MANAGER_ROUTES.has(route)) {
      try {
        await ensureAccountManager();
        await window.PostingTTIIAccountManager.navigate(route);
      } catch (error) {
        alert("Не удалось открыть Account Manager: " + error.message);
      }
    }
  }

  function buildMenu() {
    if (document.querySelector(".pt-burger-button")) return;

    burger = document.createElement("button");
    burger.type = "button";
    burger.className = "pt-burger-button";
    burger.setAttribute("aria-label", "Открыть меню управления аккаунтами");
    burger.setAttribute("aria-expanded", "false");
    burger.setAttribute("aria-controls", "ptAccountMenu");
    burger.innerHTML = '<span class="pt-burger-icon" aria-hidden="true"></span>';
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
      '<div><div class="pt-menu-title">PostingTTII</div>' +
      '<div class="pt-menu-subtitle">Account Manager</div></div>' +
      '<button type="button" class="pt-menu-close" aria-label="Закрыть меню">×</button>';

    const content = document.createElement("div");
    content.className = "pt-menu-content";

    const label = document.createElement("div");
    label.className = "pt-menu-group-label";
    label.textContent = "Navigation";

    const nav = document.createElement("nav");
    nav.className = "pt-menu-nav";
    nav.setAttribute("aria-label", "Account manager");

    for (const item of ITEMS) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "pt-menu-item";
      button.dataset.route = item.id;
      button.innerHTML =
        '<span class="pt-menu-item-icon" aria-hidden="true">' + item.icon + '</span>' +
        '<span class="pt-menu-item-text">' +
          '<span class="pt-menu-item-title"></span>' +
          '<span class="pt-menu-item-note"></span>' +
        '</span>';
      button.querySelector(".pt-menu-item-title").textContent = item.title;
      button.querySelector(".pt-menu-item-note").textContent = item.note;
      button.addEventListener("click", () => navigate(item.id));
      nav.appendChild(button);
    }

    const footer = document.createElement("div");
    footer.className = "pt-menu-footer";
    footer.textContent = "Phase 3: Accounts UI и постоянная SQLite-база подключены. addy.io и identity идут следующими фазами.";

    content.append(label, nav);
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
