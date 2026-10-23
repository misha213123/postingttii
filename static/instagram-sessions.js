/* Local Instagram sessions: manual Edge windows with persistent on-disk profiles.
 * Deliberately does not rotate fingerprints, log in, or bypass Instagram checks.
 */
(() => {
  const root = document.querySelector(".shell");
  if (!root || document.getElementById("instagramSessionPanel")) return;
  const panel = document.createElement("section");
  panel.id = "instagramSessionPanel";
  panel.className = "card queue";
  panel.innerHTML = [
    '<div class="queue-head"><div><h2>Instagram · постоянные профили</h2>',
    '<div class="muted" style="font-size:12px;margin-top:4px">',
    'Один профиль Edge на аккаунт. Вход выполняется вручную; cookies сохраняются на этом компьютере.',
    '</div></div><button class="secondary" id="sessionRefresh">Обновить</button></div>',
    '<div class="note">Здесь используется обычный Edge, без подмены отпечатка и обхода проверок Instagram. ',
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

  async function refresh() {
    try {
      const payload = await api("/api/account-manager/browser/profiles");
      const list = $("sessionList");
      list.replaceChildren();
      if (!payload.browser?.edge_available) {
        message(payload.browser?.edge_message || "Microsoft Edge не найден", true);
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
          actions.appendChild(actionButton("Закрыть браузер", () =>
            api("/api/account-manager/accounts/" + id + "/browser/close", {})));
        } else {
          actions.appendChild(actionButton("Открыть Instagram", () =>
            api("/api/account-manager/accounts/" + id + "/browser/open", {target:"instagram"})));
        }
        card.appendChild(actions);
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
})();
