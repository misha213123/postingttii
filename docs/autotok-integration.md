# TikTok via AutoTok in PostingTTII (Windows)

This integration uses the already installed `autotok` command-line application.
It does **not** need Docker or WSL, and it does not copy saved TikTok cookies
or passwords into this GitHub repository.

## Before switching branches

Your Windows `postingttii` folder may have uncommitted changes not present on
GitHub. Always run:

```powershell
cd "C:\Users\blackpansel\Desktop\postingttii"
git status --short
```

If you see changed files, back them up or commit them before switching branches.
Do not run `git reset --hard`.

## Launch

```powershell
cd "C:\Users\blackpansel\Desktop\postingttii"
uv --version
autotok --version
autotok accounts check account1
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

Open http://127.0.0.1:8765.

### TikTok accounts

- Click **Импорт** in TikTok slot 1 and enter `account1` to reuse the session
  already created with `autotok login -n account1`.
- Click **Войти** in slot 2, give the new session a name such as `account2`,
  and sign in using the local browser AutoTok opens. It currently uses its
  Playwright browser and is not guaranteed to use Microsoft Edge.
- The **Отключить** button removes the slot from PostingTTII, but does not delete
  the AutoTok session from your computer.

### Posting schedule

The default batch mode distributes one unique file to one selected target
(round-robin). Files whose SHA-256 was already posted through PostingTTII
are filtered out. Confirmed successful files are moved from
`videos/inbox` to `videos/posted`, not irreversibly deleted.

A default series has two successful publications: 90 seconds between them,
then a 20-minute pause. Account-specific cooldown can make the interval longer.
The legacy mode (one file to all accounts) is still available via the toggle.

TikTok AutoTok uploads use `-vi 0` (public) and require the CLI's
`Published. Video id:` confirmation before a file is marked completed.
TikTok can still apply its own visibility and account restrictions; verify a
new connection with one test publication before running a large queue.

A cancelled queue stops before the next upload; an upload already in flight
may finish. Avoid retrying ambiguous uploads until you have checked TikTok,
otherwise you can produce duplicates.

## Important

- Do not commit `.env`, `data/`, `~/.autotok/`, or browser sessions to Git.
- AutoTok is a third-party tool, not TikTok's official Content Posting API.
  Respect the platform's requirements and your account's permissions.
- This feature branch is intended to be locally tested before merging.
