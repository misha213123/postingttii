/* Manual Camoufox Firefox sessions with persistent per-account profiles and proxies. */
(() => {
  const root = document.querySelector(".shell");
  if (!root || document.getElementById("instagramSessionPanel")) return;
  const panel = document.createElement("section");
  panel.id = "instagramSessionPanel";
  panel.className = "card queue";
  panel.innerHTML = [
    '<div class="queue-head"><div><h2>Instagram · постоянные профили</h2>',
    '<div class="muted" style="font-size:12px;margin-top:4px">',
    'Один профиль Camoufox на аккаунт. Вход выполняется вручную, cookies остаются на компьютере.',
    '</div></div><button class="secondary" id="sessionRefresh">Обновить</button></div>',
    '<div class="note">Используется Camoufox (Firefox). Старые профили Edge не изменяются. ',
    'Браузеры запускаются только по кнопке, не автоматически. ',
    'Профили Account Manager независимы от слотов Meta API.</div>',
    '<div class="toolbar" style="margin-bottom:10px;align-items:end">',
    '<div class="field" style="margin:0;flex:1;min-width:175px">',
    '<label for="sessionUsername">Instagram username (без @)</label>',
    '<input id="sessionUsername" class="search" style="min-width:0;width:100%" maxlength="64" placeholder="my_account"></div>',
    '<button class="primary" id="sessionCreate">Создать профиль</button></div>',
    '<div id="sessionMessage" class="muted" style="font-size:12px;margin-bottom:8px" role="status"></div>',
    '<div class="job-list" id="sessionList"></div>',
  ].join("");
  root.appendChild(panel);
  const $ = id => document.getElementById(id);
  const message = (text, bad = false) => {
    $("sessionMessage").textContent = text;
    $("sessionMessage").className = bad ? "bad" : "muted";
  };

  async function api(url, payload) {
    const options = payload === undefined ? {} : {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    };
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "HTTP " + response.status);
    }
    return data;
  }

  function actionButton(label, handler) {
    const button = document.createElement("button");
    button.className = "secondary";
    button.textContent = label;
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        message("");
        await handler();
        await refresh();
      } catch (error) {
        message(error.message || "Неизвестная ошибка", true);
      } finally {
        button.disabled = false;
      }
    });
    return button;
  }

  function addProxyEditor(card, id, running) {
    const toggle = document.createElement("button");
    toggle.textContent = "Настройки прокси";
    toggle.className = "secondary";
    toggle.disabled = running;
    toggle.style.marginTop = "8px";
    const editor = document.createElement("div");
    editor.className = "hidden";
    editor.style.marginTop = "8px";

    const mkInput = (label, placeholder, type = "text") => {
      const wrap = document.createElement("div");
      wrap.className = "field";
      const title = document.createElement("label");
      title.textContent = label;
      const field = document.createElement("input");
      field.className = "search";
      field.style.width = "100%";
      field.style.minWidth = "0";
      field.type = type;
      field.placeholder = placeholder;
      wrap.append(title, field);
      editor.appendChild(wrap);
      return field;
    };
    const server = mkInput("Адрес прокси", "http://host:port или socks5://host:port");
    const username = mkInput("Логин (если требуется)", "Логин");
    const password = mkInput("Пароль (пустой — оставить прежний)", "Пароль", "password");
    const visible = document.createElement("label");
    visible.style.fontSize = "12px";
    visible.className = "muted";
    const show = document.createElement("input");
    show.type = "checkbox";
    show.style.marginRight = "6px";
    show.addEventListener("change", () => { password.type = show.checked ? "text" : "password"; });
    visible.append(show, document.createTextNode("Показать пароль"));
    editor.appendChild(visible);

    const row = document.createElement("div");
    row.className = "toolbar";
    row.style.marginTop = "9px";
    const save = document.createElement("button");
    save.className = "primary";
    save.textContent = "Сохранить прокси";
    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        const response = await fetch("/api/account-manager/accounts/" + id + "/camoufox/proxy", {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            server: server.value.trim(),
            username: username.value.trim(),
            password: password.value,
          }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "HTTP " + response.status);
        password.value = "";
        message(data.enabled ? "Прокси сохранён. Будет применён при следующем открытии Camoufox." :
          "Прокси отключён для этого профиля.");
      } catch (error) {
        message(error.message || "Не удалось сохранить прокси", true);
      } finally {
        save.disabled = false;
      }
    });
    const clear = document.createElement("button");
    clear.className = "secondary";
    clear.textContent = "Удалить прокси";
    clear.addEventListener("click", () => { server.value = ""; username.value = ""; password.value = ""; save.click(); });
    row.append(save, clear);
    editor.appendChild(row);
    toggle.addEventListener("click", async () => {
      editor.classList.toggle("hidden");
      if (editor.classList.contains("hidden")) return;
      try {
        const response = await fetch("/api/account-manager/accounts/" + id + "/camoufox/proxy");
        if (!response.ok) throw new Error("Не удалось загрузить настройки прокси");
        const data = await response.json();
        server.value = data.server || "";
        username.value = data.username || "";
        password.value = "";
      } catch (error) {
        message(error.message || "Ошибка прокси", true);
      }
    });
    card.append(toggle, editor);
  }

  async function refresh() {
    try {
      const payload = await api("/api/account-manager/browser/profiles");
      const list = $("sessionList");
      list.replaceChildren();
      if (!payload.browser?.camoufox_available) {
        message("Camoufox не установлен: выполни pip install -r requirements.txt и camoufox fetch.", true);
      }
      if (!payload.profiles?.length) {
        const empty = document.createElement("div");
        empty.className = "muted";
        empty.style.fontSize = "12px";
        empty.textContent = "Нет профилей. Добавь аккаунт выше, чтобы создать отдельную папку сессии.";
        list.appendChild(empty);
        return;
      }
      for (const item of payload.profiles) {
        const id = Number(item.account_id);
        if (!Number.isSafeInteger(id) || id <= 0) continue;
        const card = document.createElement("div");
        card.className = "job";
        const name = document.createElement("b");
        name.style.fontSize = "13px";
        name.textContent = item.username ? "@" + item.username : item.display_name || "Аккаунт #" + item.account_number;
        card.appendChild(name);
        const info = document.createElement("div");
        info.className = item.running ? "good" : "muted";
        info.style.fontSize = "11px";
        info.style.marginTop = "4px";
        info.textContent = item.running
          ? "Браузер открыт — профиль используется"
          : "Браузер закрыт — сохранённая сессия остаётся в профиле";
        card.appendChild(info);
        const actions = document.createElement("div");
        actions.className = "toolbar";
        actions.style.marginTop = "8px";
        if (item.running) {
          const hint = document.createElement("span");
          hint.className = "muted";
          hint.style.fontSize = "11px";
          hint.textContent = "Для сохранения сессии закрой окно Camoufox обычным способом.";
          actions.appendChild(hint);
        } else {
          actions.appendChild(actionButton("Открыть Instagram", () =>
            api("/api/account-manager/accounts/" + id + "/browser/open", {target:"instagram"})));
        }
        card.appendChild(actions);
        addProxyEditor(card, id, item.running);
        list.appendChild(card);
      }
    } catch (error) {
      message(error.message || "Не удалось получить профили", true);
    }
  }

  $("sessionRefresh").addEventListener("click", refresh);
  $("sessionCreate").addEventListener("click", async () => {
    const username = $("sessionUsername").value.trim().replace(/^@/, "");
    if (!/^[A-Za-z0-9._]{1,30}$/.test(username)) {
      message("Укажи корректный Instagram username (до 30 символов).", true);
      return;
    }
    const button = $("sessionCreate");
    button.disabled = true;
    try {
      await api("/api/account-manager/accounts", {username, display_name:username});
      $("sessionUsername").value = "";
      message("Профиль создан. Нажми «Открыть Instagram», чтобы войти вручную.");
      await refresh();
    } catch (error) {
      message(error.message || "Не удалось создать профиль", true);
    } finally {
      button.disabled = false;
    }
  });
  refresh();
  setInterval(refresh, 15000);
})();
