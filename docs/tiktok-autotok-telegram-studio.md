# TikTok (AutoTok) in PostingTTII

This feature branch is based on **feature/telegram-studio**, not main.
It preserves the existing Telegram Studio, Instagram covers, 8 Instagram
account slots, Instagram posting queue, and Instagram-specific captions.

## Local Windows setup

1. **Finish or stop the current posting queue** in the browser and close the
   running PostingTTII Python server (Ctrl+C).
2. Check local changes; if anything is modified, stop and back it up first.
3. Fetch and switch to the TikTok test branch:

```powershell
cd "C:\Users\blackpansel\Desktop\postingttii"
git status --short
git fetch origin
git switch --track origin/feature/autotok-on-telegram-studio-20261009
python -m compileall -q app
autotok accounts check account1
powershell -ExecutionPolicy Bypass -File ".\start.ps1"
```

Open http://127.0.0.1:8765.

**Do not run `git reset --hard`** or delete files from `data`.
If you want to revert the experiment, first stop the queue and Python
server, then `git switch feature/telegram-studio`.

## TikTok accounts

The TikTok card has **Импорт** and **Войти**. For the existing saved session,
click Импорт in TikTok slot 1, enter `account1`, and AutoTok checks the
session. For the second account, click Войти and enter `account2`: AutoTok
opens its own Chromium browser for interactive login. Microsoft Edge is
**not guaranteed** for AutoTok's built-in local login. Its session stays in
your local AutoTok account store and is not committed to Git.

## Publishing

- TikTok is added to the same selectable account list as Instagram/YouTube.
- The existing one-unique-video-per-target assignment and its per-account
  intervals, two-video waves, stop button, and post archive remain unchanged.
- TikTok posts through the installed AutoTok CLI with public visibility
  (`-vi 0`). A `Published. Video id:` response is required for success.
- TikTok captions are produced via the **existing** `OPENAI_API_KEY` and
  `OPENAI_MODEL` settings in `.env`. They include a short Russian description
  and topic-appropriate hashtags. The model does **not** see video contents,
  so provide meaningful context when available.
- Instagram uses its previous OpenAI prompt and Japanese caption formatting;
  no Instagram caption files or publish handlers were modified.
- Confirmed clips are archived to `videos/posted` after the existing
  grace period, rather than deleted immediately.

## Cautions

Start with one new test clip. If TikTok published via AutoTok manually before
this integration, those past videos are not automatically recorded in
PostingTTII history. Exclude them from the batch to avoid reposts.
If the network fails after TikTok accepts a post, verify the TikTok profile
before retrying. An in-flight upload may complete after clicking Stop.

This change has been checked for UI JavaScript syntax and integration source
preservation; live TikTok + Instagram end-to-end testing is required on your
Windows machine before using a large queue. AutoTok is a third-party
automation tool and may need adjustments when TikTok changes.
