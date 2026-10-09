# TikTok account2 — isolated experimental automatic browser uploader

## Context

Instagram, YouTube, and TikTok account1 continue to publish normally.
TikTok account2 is authorized but receives Invalid parameters (status_code=5)
even when AutoTok 2.0.1 is invoked directly. Changing captions and refreshing
its login did not resolve the API rejection.

THIS IS NOT A VERIFIED FIX. The experimental account2 route uses ordinary
TikTok Studio in the locally installed Google Chrome via Playwright instead
of AutoTok's TikTok web upload API. This is third-party browser automation
that may violate TikTok's terms. Do not use it to defeat verification,
age, privacy, or account restrictions. It can also fail when the UI changes.

## Scope

- TikTok slot 1 (account1) is unchanged and still uses AutoTok CLI.
- TikTok slot 2 (account2) uses the experimental browser uploader by default
  only on this test branch. To disable, set TIKTOK_ACCOUNT2_BROWSER=0 in .env.
- Instagram, YouTube, Telegram Studio, covers, captions and queue cadence
  remain unchanged.
- Uses saved local AutoTok account2 cookies, without exposing their values.
- Requires Google Chrome installed locally and the Playwright dependency.
- Counts as success only after TikTok Studio redirects to its content page.
- No automatic retry after clicking Post or after ambiguous outcomes.
- It does not attempt to solve CAPTCHA, override audience restrictions or
  work around any mandatory verification.
- Failure screenshots are stored locally at data/tiktok_browser_errors.
  They may contain private account information; do not post them publicly.

## Safe Windows preflight (does not post)

Stop the current queue, wait for ongoing operations and quit the server with
Ctrl+C. First make sure git status --short is empty. Run in PowerShell:

    cd "C:\Users\blackpansel\Desktop\postingttii"
    git fetch origin
    git switch --track origin/fix/tiktok-account2-browser-publisher-20261009
    py -3.12 -m compileall -q app
    .\.venv\Scripts\python.exe -c "from app.services.tiktok_browser_upload import probe_account2_browser; print(probe_account2_browser())"

The preflight opens Chrome and checks whether the account2 upload form is
accessible with the saved session. Expected output: {'ready': True, ...}.
It does not attach, upload or publish any video.

Then start the normal PostingTTII server:

    powershell -ExecutionPolicy Bypass -File ".\start.ps1"

In another PowerShell window:

    Start-Process "http://127.0.0.1:8765"

For the first live test, use ONE new clip, with ONLY TikTok account2
selected as target. The browser uploader should try to post autonomously.
Verify the result on account2 before any additional attempt. If it fails
or reports an ambiguous outcome, do not blindly retry the same video.

Safe rollback after stopping batch and server:

    git switch feature/autotok-on-telegram-studio-20261009
