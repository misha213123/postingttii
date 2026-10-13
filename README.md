# postingttii

Локальный мультипостер для коротких видео:

- 2× TikTok
- 2× Instagram Reels
- 2× YouTube Shorts
- папка `videos/inbox` как очередь на публикацию
- OpenAI генерирует описание и хештеги
- локальная панель управления в браузере

Проект использует официальные API платформ и OAuth. Секреты и токены не коммитятся в Git.

## Возврат Telegram Studio и обложек + необязательный Instagram OAuth через Tor

Эта ветка основана на `fix/instagram-meta-concurrency-20261009`. В ней **без
изменений** сохранены `static/index.html`, Telegram Studio, обложки, работа с
восемью Instagram-слотами и существующая логика публикаций.

Tor Browser применяется **только для открытия официальной страницы OAuth**.
Видео и обложки публикуются прежним способом через Meta API. Tor не создаёт
постоянные браузерные cookies; для каждого выбранного слота требуется отдельная
полная установка Tor Browser для Windows.

Настройка в локальном `.env` (примеры путей, нужно заменить на свои):

```dotenv
INSTAGRAM_OAUTH_BROWSER=tor
INSTAGRAM_TOR_SLOTS=5,6,7,8
INSTAGRAM_TOR_BROWSER_5=C:\\Tor-Instagram-5
INSTAGRAM_TOR_BROWSER_6=C:\\Tor-Instagram-6
INSTAGRAM_TOR_BROWSER_7=C:\\Tor-Instagram-7
INSTAGRAM_TOR_BROWSER_8=C:\\Tor-Instagram-8
```

Если `INSTAGRAM_OAUTH_BROWSER=default` или параметр отсутствует — остаётся
старое подключение. Не выбранные `INSTAGRAM_TOR_SLOTS` слоты тоже используют
старый OAuth. Tor Browser стирает браузерные cookies после закрытия, OAuth-токены
для Meta API сохраняются в старом зашифрованном хранилище.

### HTTPS-туннель только для Instagram media и callback

Основной сервер (`127.0.0.1:8765`) **не стоит открывать публично**:
панель Account Manager не защищена паролем. Вместо этого запусти gateway:

```powershell
.\\.venv\\Scripts\\python.exe -m uvicorn media_gateway:app --host 127.0.0.1 --port 8766 --no-access-log
```

Затем в отдельном PowerShell:

```powershell
cloudflared tunnel --url http://127.0.0.1:8766
```

`PUBLIC_BASE_URL` должен равняться выданному домену HTTPS,
`INSTAGRAM_REDIRECT_URI` — тому же домену с
`/auth/instagram/callback` (и совпадать с Redirect URI в Meta).
Оба адреса временные при Cloudflare Quick Tunnel.

*Gateway допускает только GET/HEAD `/media/{token}` и GET
`/auth/instagram/callback`. Остальная панель остаётся локальной.*

### Панель: только подключённые аккаунты и кнопка Tor

В панели не отображаются незаполненные слоты YouTube, Instagram и TikTok.
Существующие подключения, обложки, Telegram Studio и очередь остаются как
раньше. Для новых подключений над списком Instagram появились две кнопки:

- **Подключить Meta** — вход в обычном браузере для свободных слотов 1–5.
- **Подключить Tor аккаунт** — выбор свободного слота 5–8 и открытие
  официального Instagram OAuth в индивидуальной установке Tor Browser.

Для использования кнопки Tor в локальном `.env` нужно задать реальные пути
`INSTAGRAM_TOR_BROWSER_5` ... `INSTAGRAM_TOR_BROWSER_8`. Если слоту 6–8
нужно подключение, по существующей архитектуре приложения также требуются
`INSTAGRAM_CLIENT_ID_2`, `INSTAGRAM_CLIENT_SECRET_2`,
`INSTAGRAM_REDIRECT_URI_2`; слот 5 использует основной Meta App. Redirect URI
должен совпадать с настройками приложения Meta.

Кнопка **не создаёт фиктивный статус CONNECTED**: после OAuth сервер получает
токен и сохраняет аккаунт в существующее зашифрованное хранилище. Только
после успешного завершения аккаунт появляется в панели и в списке
публикаций; страница автоматически обновляет этот список во время входа.
При публикации Reels по-прежнему используются официальные Meta API,
публичный HTTPS источник видео и ранее выбранные обложки. **Tor Browser
сам по себе не предоставляет разрешение API-публикации** и его cookies
не сохраняются после завершения приватной сессии.
