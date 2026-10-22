/* Reels eligibility panel. Meta API has no archive action: never show a fake success. */
(() => {
  const shell = document.querySelector(".shell");
  if (!shell || document.getElementById("reelsArchivePanel")) return;

  const panel = document.createElement("section");
  panel.id = "reelsArchivePanel";
  panel.className = "card queue";
  panel.innerHTML = [
    '<div class="queue-head"><div>',
    '<h2>Reels · контроль архива</h2>',
    '<div class="muted" style="font-size:12px;margin-top:5px">',
    'Автоматическая проверка спустя 24 часа · меньше 2 500 просмотров',
    '</div></div><button class="secondary" id="reelsArchiveCheck">Проверить сейчас</button></div>',
    '<div class="note" id="reelsArchiveCapability">',
    'Meta API не умеет архивировать Reels. Здесь показываются кандидаты; скрыть ролик можно в Instagram.',
    '</div>',
    '<div class="muted" id="reelsArchiveSummary" style="font-size:12px;margin-bottom:9px">Загрузка...</div>',
    '<div id="reelsArchiveErrors" class="bad" style="font-size:12px;margin-bottom:8px"></div>',
    '<div class="job-list" id="reelsArchiveItems"></div>',
  ].join("");
  shell.appendChild(panel);

  const byId = id => document.getElementById(id);
  const button = byId("reelsArchiveCheck");

  function formatDate(ts) {
    return Number.isFinite(Number(ts)) && Number(ts) > 0
      ? new Date(Number(ts) * 1000).toLocaleString("ru-RU")
      : "—";
  }

  async function request(path, payload) {
    const resp = await fetch(path, {
      method: "POST",
      headers: payload ? {"Content-Type": "application/json"} : {},
      body: payload ? JSON.stringify(payload) : undefined,
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Ошибка запроса");
    return data;
  }

  function render(data) {
    byId("reelsArchiveCapability").textContent =
      "Проверка каждые " + data.check_interval_minutes + " мин при запущенном приложении. " +
      "Автоархив через Meta API недоступен. Кнопка «Архивировано» отмечает действие только локально.";
    byId("reelsArchiveSummary").textContent =
      "Ниже порога: " + data.pending_count +
      " · Последняя проверка: " + formatDate(data.last_scan_at) +
      (data.running ? " · Выполняется..." : "") +
      " · Проверяются последние " + data.max_media_per_account + " публикаций на аккаунт";
    const errors = Object.values(data.accounts || {})
      .filter(a => a && (a.error || a.errors))
      .map(a => (a.label || "Аккаунт") + ": " +
        (a.error || a.errors + " роликов без доступной статистики"));
    byId("reelsArchiveErrors").textContent = errors.join(" | ");

    const root = byId("reelsArchiveItems");
    root.replaceChildren();
    const pending = data.pending || [];
    if (!pending.length) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.style.fontSize = "12px";
      empty.textContent = "Сейчас нет подтверждённых кандидатов. При отсутствии разрешения insights переподключи Instagram.";
      root.appendChild(empty);
      return;
    }
    for (const item of pending) {
      const card = document.createElement("div");
      card.className = "job";
      const title = document.createElement("div");
      title.style.fontSize = "13px";
      title.style.marginBottom = "5px";
      title.textContent =
        (item.account || "Instagram") + " · " + item.views + " просмотров";
      card.appendChild(title);

      const details = document.createElement("div");
      details.className = "muted";
      details.style.fontSize = "11px";
      details.textContent = "Опубликовано: " + formatDate(item.published_at);
      card.appendChild(details);

      const actions = document.createElement("div");
      actions.className = "toolbar";
      actions.style.marginTop = "8px";

      if (typeof item.permalink === "string" &&
          /^https:\/\/(www\.)?instagram\.com\//.test(item.permalink)) {
        const open = document.createElement("a");
        open.href = item.permalink;
        open.target = "_blank";
        open.rel = "noopener noreferrer";
        open.className = "btn secondary";
        open.textContent = "Открыть Reel";
        actions.appendChild(open);
      }

      for (const [label, endpoint] of [
        ["Архивировано вручную", "/api/reels-archive/mark-archived"],
        ["Не архивировать", "/api/reels-archive/dismiss"],
      ]) {
        const action = document.createElement("button");
        action.className = "secondary";
        action.textContent = label;
        action.addEventListener("click", async () => {
          action.disabled = true;
          try {
            const updated = await request(endpoint, {
              account_id: String(item.account_id),
              media_id: String(item.media_id),
            });
            render(updated);
          } catch (err) {
            byId("reelsArchiveErrors").textContent = err.message;
          } finally {
            action.disabled = false;
          }
        });
        actions.appendChild(action);
      }
      card.appendChild(actions);
      root.appendChild(card);
    }
  }

  async function refresh() {
    try {
      const response = await fetch("/api/reels-archive/status");
      if (!response.ok) throw new Error("Не удалось получить состояние автоархива");
      render(await response.json());
    } catch (err) {
      byId("reelsArchiveErrors").textContent = err.message;
    }
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Проверяю...";
    try {
      render(await request("/api/reels-archive/check"));
    } catch (err) {
      byId("reelsArchiveErrors").textContent = err.message;
    } finally {
      button.disabled = false;
      button.textContent = "Проверить сейчас";
    }
  });

  refresh();
  setInterval(refresh, 60000);
})();
