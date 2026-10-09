# Instagram: persistent media links for Meta

## Confirmed problem

On 2026-10-09, the Windows PostingTTII server started successfully but
received multiple Meta-origin GET /media/<token> 404 Not Found requests,
mixed with some HTTP 200 responses. Meta clients can still download a
submitted Reel video or cover after the original API request completes.
The old implementation stored token->file only in an in-memory Python dict
and removed it five minutes later. A server restart erased the entire map.

PUBLIC_BASE_URL itself was reachable (HTTP 200), the cover returned image/jpeg
(HTTP 200), and a complete MP4 downloaded with HTTP 200 (20,768,305 bytes).
The tested MP4 had H.264 1080x1920 video and AAC audio. These checks do not
validate a specific /media/<token> URL; the 404 requests in the server log do.

## Fix

The token registry now persists a random token's staged file and expiry
in the git-ignored data/meta_media directory. A hardlink is attempted on
Windows NTFS, with a physical copy fallback. The manifest is stored in
data/meta_media/index.json. Tokens are valid for up to 24 hours, even if
PostingTTII is restarted or the source file is archived.

The staged files are available without authentication to anyone who has
the unpredictable /media/<token> URL while valid. Keep the URL private.
The manifest and staged media are not committed to GitHub.

On manual and batch publication, Instagram media remains available after
Meta returns success/error. Do not delete data/meta_media manually.
Stale copies are cleaned when the application runs again or tokens expire.

## Limitations

URLs generated BEFORE installing the fix are already lost on server restart,
because the old server never stored their token-to-file mapping. Those
requests cannot be restored retroactively by this change.

Meta may still reject a Reel for independent reasons. This fix addresses
the observed 404 symptom; end-to-end publication needs a live Windows test.
Do not automatically retry an uncertain or already published Reel.

## Windows commands

First finish/stop the existing queue and shut down the PostingTTII server
through Ctrl+C. Do not kill it during an in-flight publication.

    cd "C:\Users\blackpansel\Desktop\postingttii"
    git status --short
    git fetch origin
    git switch --track origin/fix/instagram-persistent-media-links-20261009
    .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_public_media.py -v
    py -3.12 -m compileall -q app
    powershell -ExecutionPolicy Bypass -File ".\start.ps1"

From another PowerShell window:

    Start-Process "http://127.0.0.1:8765"

Start with ONE new Instagram test Reel and confirm no fresh
GET /media/<token> 404 logs. Once it succeeds, scale gradually.

To go back after stopping queue and server:

    git switch fix/instagram-meta-concurrency-20261009

Your existing Instagram connections, covers, Telegram Studio, Japanese
captions, YouTube title generation, and TikTok code are not modified.
