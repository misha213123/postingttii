# PostingTTII TikTok Review Site

A standalone English-language review build for TikTok Login Kit + Content Posting API.

This folder is intentionally separate from the main PostingTTII UI. Deploy it as its own web service and use that exact public domain in the TikTok Developer Portal and in the review demo video.

## What this build demonstrates

- Clear product purpose for creators.
- TikTok Login / authorization for up to 3 separate creator accounts.
- Only the required scopes: `user.info.basic` and `video.publish`.
- Creator Info is queried immediately before rendering the Direct Post screen.
- The creator sees the destination account, current privacy options, comments / Duet / Stitch availability, disclosure toggles and editable caption.
- The user explicitly selects a video and checks a consent box before any upload starts.
- File upload is sent through TikTok's Direct Post API.
- Publish status is polled and shown to the user.
- Privacy Policy, Terms of Service and Data Deletion pages are public.
- TikTok client secret never appears in browser code.
- Access / refresh tokens are encrypted at rest.
- No promotional watermark is added to uploaded videos.

## TikTok Developer Portal configuration

Use the deployed site's exact HTTPS domain.

Recommended values:

- Website URL: `https://YOUR-DOMAIN/`
- Terms of Service URL: `https://YOUR-DOMAIN/terms`
- Privacy Policy URL: `https://YOUR-DOMAIN/privacy`
- Redirect URI: `https://YOUR-DOMAIN/auth/tiktok/callback`
- Products: Login Kit + Content Posting API / Direct Post
- Scopes: `user.info.basic`, `video.publish`

Verify ownership of every configured URL/property before submitting. For apps created after September 9, 2024, TikTok requires verification of Terms, Privacy, and Web/Desktop URLs. Content Posting also requires URL ownership verification.

## Environment

Copy `.env.example` to `.env` and fill real values.

Important: use a real support email and your real legal/company/operator name. Do not submit placeholders to TikTok.

## Local run

```powershell
cd tiktok-review-site
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# edit .env
uvicorn app:app --host 127.0.0.1 --port 8877
```

Open:

```
http://127.0.0.1:8877
```

For TikTok review, use the deployed HTTPS version, not localhost.

## Render

This folder includes `render.yaml`. You can deploy it as a separate Render web service.

If you need account connections to survive restarts/redeploys, attach a persistent disk and set `DATA_DIR` to that mounted path.

## URL verification helper

TikTok can verify a URL prefix using a downloaded signature file. If you use that method, set:

- `TIKTOK_VERIFICATION_FILENAME` to the exact filename TikTok gives you, including `.txt`
- `TIKTOK_VERIFICATION_CONTENT` to the exact file contents

The service will expose it at:

`https://YOUR-DOMAIN/<verification-file>.txt`

DNS verification is preferable when you control a custom domain.

## Demo video checklist

The review video should be recorded on the same deployed domain submitted as the Website URL and should show the real integration end to end:

1. Open the PostingTTII review site and briefly show the product purpose.
2. Click **Connect TikTok**.
3. Show the TikTok authorization/consent screen.
4. Return to PostingTTII with the authorized creator visible.
5. Choose the connected TikTok account.
6. Show that current creator information loads from TikTok.
7. Select a short video you own / are allowed to publish.
8. Edit the caption.
9. Choose one of the privacy options returned for that creator.
10. Show Comments, Duet and Stitch controls.
11. Show the commercial-content and AI-generated-content disclosure controls.
12. Check the explicit consent checkbox.
13. Click **Publish to TikTok**.
14. Show the processing status and final result.
15. Briefly show Privacy Policy, Terms and Data Deletion pages.

Do not request products/scopes in the Developer Portal that are not demonstrated in the video.

## Important

This build is designed around TikTok's documented 2026 review and Content Posting requirements, but no implementation can guarantee approval. TikTok performs a manual review and may request changes or additional evidence.
