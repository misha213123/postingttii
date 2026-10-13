# postingttii

Локальный мультипостер для коротких видео:

- 2× TikTok
- 2× Instagram Reels
- 2× YouTube Shorts
- папка `videos/inbox` как очередь на публикацию
- OpenAI генерирует описание и хештеги
- локальная панель управления в браузере

Проект использует официальные API платформ и OAuth. Секреты и токены не коммитятся в Git.

## Instagram OAuth через Tor Browser (необязательно, Windows)

Обычный режим не меняется. Если нужно открыть **официальное OAuth-подключение**
Instagram в Tor Browser, задай в локальном `.env`:

```dotenv
INSTAGRAM_OAUTH_BROWSER=tor
INSTAGRAM_TOR_BROWSER_1=C:\Tor-Instagram-1
INSTAGRAM_TOR_BROWSER_2=C:\Tor-Instagram-2
```

Пути — примеры. Каждый путь должен указывать на **отдельную полную установку
Tor Browser** с `Browser/firefox.exe` и собственным `TorBrowser/Data/Browser/profile.default`.
Нельзя указывать одну и ту же установку двум слотам. Если выбран режим Tor, но указан неверный путь, приложение выдаст ошибку.

Нажимай те же кнопки подключения Instagram в панели. Авторизация открывается
в выбранном Tor Browser, callback возвращается к локальному приложению.
Кнопки и интерфейс не меняются. **Публикации остаются через официальный Instagram API**
с существующими OAuth-токенами, а не через браузерные cookies или Tor.

**Ограничения Tor Browser:** он очищает cookies при закрытии, поэтому браузерный
вход может потребоваться повторить; сохранённые API-токены PostingTTII независимы
от browser cookies. Одновременные экземпляры Tor Browser официально не рекомендуются
и на некоторых системах работают нестабильно. Режим не предназначен для обхода
ограничений Instagram и не гарантирует успешную авторизацию. Для возврата к старому
режиму установи `INSTAGRAM_OAUTH_BROWSER=default`.


## Публичный HTTPS-адрес для Instagram Reels (только медиа)

Не направляй публичный туннель на основную панель `127.0.0.1:8765`:
она не требует пароль. Для Instagram достаточно публичного доступа к
`/media/<token>` и `/auth/instagram/callback`. Добавлен ограниченный локальный gateway.

В трёх разных окнах PowerShell:

1. Основное приложение: `powershell -ExecutionPolicy Bypass -File .\\start.ps1`.
2. Gateway: `.\\.venv\\Scripts\\python.exe -m uvicorn media_gateway:app --host 127.0.0.1 --port 8766 --no-access-log`.
3. HTTPS-туннель: `cloudflared tunnel --url http://127.0.0.1:8766`.

Скопируй появившийся URL вида `https://...trycloudflare.com` в
`PUBLIC_BASE_URL`, а в `INSTAGRAM_REDIRECT_URI` добавь к нему
`/auth/instagram/callback`. Укажи **тот же точный Redirect URI** в настройках
Instagram-приложения в Meta Developer Dashboard, затем **перезапусти основное
приложение**. Media gateway и туннель остаются открытыми.

Туннель временный и при следующем запуске может выдать другой URL.
Gateway пропускает только `GET/HEAD /media/<случайный токен>` и
`GET /auth/instagram/callback`; весь Account Manager и остальные API остаются
недоступными через него. Чтобы не логировать одноразовые OAuth-коды в URL,
запускай uvicorn gateway с `--no-access-log`.
Для постоянной публикации нужен стабильный HTTPS-домен или постоянный
туннель. URL должен быть доступен серверам Meta, иначе Reels не загрузятся.
