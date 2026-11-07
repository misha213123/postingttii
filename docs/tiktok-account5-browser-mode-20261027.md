# TikTok account5: optional Studio browser mode and code-5 diagnostics

This patch is based on `feature/instagram-cleanup-tg-media-mode-20261025`
(commit `6c2069e`). It does not change Instagram, YouTube, Telegram Studio,
account linking, or existing passwords/sessions.

## What changed

- AutoTok API uploads remain the **default** (`TIKTOK_ACCOUNT2_BROWSER=0`).
- If TikTok slot 2 is linked to an AutoTok session called `account5` (or
  another valid alias), `TIKTOK_ACCOUNT2_BROWSER=1` deliberately uses the
  existing visible TikTok Studio browser uploader for that slot instead of the
  AutoTok CLI route. The browser route was previously hard-coded to alias
  `account2`, even when slot 2 was linked to `account5`.
- The browser route does **not** activate as an automatic retry/fallback after
  an API error. It is an explicit choice made before the next upload.
- TikTok `Invalid parameters (status_code=5)` now pauses that TikTok target
  for the **remainder of the current batch**, while other targets continue.
  Subsequent items for that TikTok target are marked as skipped-with-error,
  and are **not** sent to TikTok.
- AutoTok command results write local metadata-only files in
  `data/tiktok_autotok_diagnostics/`. These contain file name and size,
  caption **length and hash (not text)**, account alias, exit/status code,
  and outcome. They never store raw CLI output or session cookies.
- The experimental browser uploader still respects TikTok Studio restrictions,
  does not bypass verification, and never retries an ambiguous Post response.

## Read-only preflight for account5

First stop the current posting queue and finish any in-flight uploads.
For a connected `account5` in TikTok **slot 2**, run in Windows PowerShell:

```powershell
cd "C:\Users\blackpansel\Desktop\postingttii"
autotok accounts check account5
.\.venv\Scripts\python.exe -c "from app.services.tiktok_browser_upload import probe_account2_browser; print(probe_account2_browser('account5'))"
```

The probe opens Chrome and only checks the upload page. It does **not**
select/upload a video, type a caption, or click Post.

If the probe reports `ready: True`, you may **opt in** for the next
*new* upload by setting `TIKTOK_ACCOUNT2_BROWSER=1` in your local `.env`
and restarting PostingTTII. Do **not** put secrets in the repository.

The browser mode always targets whatever account is linked to TikTok slot 2.
It does not change slot 1 and does not try both upload methods. The original
AutoTok CLI mode can be restored with `TIKTOK_ACCOUNT2_BROWSER=0` followed
by a restart.

Do not reuse the same video after a failure without checking whether TikTok
already published it: absence of a local confirmation does not prove no post
was created. For initial testing, select **one new clip** and **only** the
intended TikTok target. Some UI or account states can still cause failure;
this feature is not a guarantee of successful publication.

## Tests

```powershell
.\.venv\Scripts\python.exe -m compileall -q app
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_tiktok_autotok_connection.py"
```

Local records/cookies/videos remain in `data/`, `.autotok/`, and
`videos/` as before. The patch does not touch them.

## Caption cleanup

The caption generator now explicitly avoids outputting filenames. All TikTok
publish paths also strip the local video basename from saved captions, including
labels stuck to the first word (for example `tg_ekmzk_65Тикток...`). Other
platforms and video filenames on disk are unchanged.

Test without posting:
```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_tiktok_caption_filename.py"
```

## TikTok Studio copyright review

If TikTok Studio displays **"The copyright check is incomplete"**, the
browser worker reports the pending review rather than clicking `Post now`
automatically. This is not an upload confirmation. Check TikTok Studio > Posts
before reattempting any video. Allow TikTok to complete the review.
