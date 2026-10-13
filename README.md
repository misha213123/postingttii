# postingttii

Локальный мультипостер для коротких видео:

- 2× TikTok
- 2× Instagram Reels
- 2× YouTube Shorts
- папка `videos/inbox` как очередь на публикацию
- OpenAI генерирует описание и хештеги
- локальная панель управления в браузере

Проект использует официальные API платформ и OAuth. Секреты и токены не коммитятся в Git.

## Instagram: две Meta App + отдельные профили Microsoft Edge (Windows)

Эта ветка основана на стабильной версии с обложками Instagram и Telegram
Studio (`fix/instagram-meta-concurrency-20261009`). Tor Browser и Tor OAuth
**не используются**. Существующие авторизации и обложки не удаляются.

- **Meta 1 · Edge:** Instagram-слоты **1–5**, используются
  `INSTAGRAM_CLIENT_ID`, `INSTAGRAM_CLIENT_SECRET`,
  `INSTAGRAM_REDIRECT_URI`.
- **Meta 2 · Edge:** Instagram-слоты **6–8**, используются
  `INSTAGRAM_CLIENT_ID_2`, `INSTAGRAM_CLIENT_SECRET_2`,
  `INSTAGRAM_REDIRECT_URI_2`.

Для каждого Instagram-слота Edge запускается с отдельным постоянным
`user-data-dir` в `data/edge_instagram_profiles/slot_N`. Повторное открытие
слота использует **тот же профиль**. При первом подключении Instagram
попросит войти и дать доступ Meta-приложению через официальный OAuth.
Вход в Edge сам по себе не означает, что приложение может публиковать:
аккаунт станет «Подключён» после успешного OAuth callback и сохранения
токена. Последующие Reels публикуются прежним способом через Meta API,
а не автоматизацией браузера.

В панели пустые слоты скрыты. Для подключения нажмите «Подключить Meta 1 · Edge»
или «Подключить Meta 2 · Edge», выберите свободный слот и завершите вход
в открывшемся Edge. Подключённый аккаунт появится в панели и в очереди
публикаций; все сохранённые обложки останутся на месте.

Важно: для обеих Meta App зарегистрируйте соответствующие **точные**
HTTPS Redirect URI. Чтобы Reels могли получать видео и обложки, установите
`PUBLIC_BASE_URL` в действующий HTTPS-домен. Если используется Cloudflare Quick
Tunnel, его адрес может измениться после перезапуска.

### Туннель только для Instagram (Media + OAuth callback)

Публично открывать локальную панель на 8765 не требуется. Запустите
основное приложение на `127.0.0.1:8765`, затем gateway:

```powershell
.\\.venv\\Scripts\\python.exe -m uvicorn media_gateway:app --host 127.0.0.1 --port 8766 --no-access-log
```

В отдельном PowerShell:

```powershell
cloudflared tunnel --url http://127.0.0.1:8766
```

У gateway разрешены только `GET/HEAD /media/{token}` и
`GET /auth/instagram/callback`; он не публикует панель управления или
API Account Manager. Задайте HTTPS-туннель в `PUBLIC_BASE_URL`,
`INSTAGRAM_REDIRECT_URI` и `INSTAGRAM_REDIRECT_URI_2`, последним двум
добавив `/auth/instagram/callback`; URI должны совпадать с настройками
соответствующих Meta App.

При переходе с прошлой Tor-ветки **не нужно удалять** `.env`, `data/` или
`videos/`. Tor-переменные, если остались в локальном `.env`, в этой версии
игнорируются.

## Instagram — шаблоны описаний из локального TXT

Автоматические Instagram-описания теперь берутся **только** из файла
`data/instagram_caption_templates.txt`, а не из OpenAI или старых встроенных
текстов. Файл содержит 13 записей из предоставленного пользователем TXT;
каждая запись отделяется строкой `---`. Тексты (включая хештеги и эмодзи)
не редактируются. Для сочетания «название видео + слот аккаунта» выбирается
стабильный шаблон.

Сам TXT не выкладывается в публичный GitHub: папка `data/` исключена из
индекса Git. Чтобы установить шаблоны на своём ПК:

1. Скачайте `instagram_caption_templates.txt` из текущего чата.
2. Поместите файл по адресу
   `C:\Users\blackpansel\Desktop\postingttii\data\instagram_caption_templates.txt`.
3. Или, если исходный файл лежит в папке Downloads, выполните:

```powershell
cd "C:\Users\blackpansel\Desktop\postingttii"
New-Item -ItemType Directory -Force ".\data" | Out-Null
Copy-Item -LiteralPath "$env:USERPROFILE\Downloads\instagram_caption_templates (1).txt" -Destination ".\data\instagram_caption_templates.txt"
```

Путь можно переопределить переменной `INSTAGRAM_CAPTION_TEMPLATES_FILE`
в `.env`. Если путь относительный, он считается от корня репозитория.

Только Instagram использует шаблоны; генерация описаний для TikTok и YouTube
не меняется. В пакетной публикации тексты Instagram больше не создаются
через OpenAI. Если TXT отсутствует либо пустой, запуск Instagram-очереди
завершается понятной ошибкой, без подстановки постороннего текста.
Проверьте, что конкретный шаблон действительно подходит ролику:
некоторые записи в исходном TXT содержат названия брендов и утверждения
о конкретных произведениях.
