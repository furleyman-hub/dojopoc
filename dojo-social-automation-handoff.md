# Dojo Social Media Automation — Project Handoff

**Owner:** Julian Fox
**Date:** July 18, 2026
**Status:** All accounts, credentials, and infrastructure set up and verified. No application code written yet. This document is the complete spec for building it.

---

## 1. Project Overview

A proof-of-concept pipeline that lets a non-technical person publish short promotional video clips to Instagram and YouTube Shorts by uploading them to a web page. Everything after upload is automated.

This POC runs on Julian's personal accounts and domain. Once proven, the same design will be redeployed for a karate dojo's real accounts (the dojo site is a separate domain on the same web host). Build everything so account-specific values are configuration, not hardcoded.

TikTok was evaluated and intentionally excluded (their Content Posting API requires an app audit before public posting). Facebook Page posting is not in scope for v1 but may be added later; the Facebook Page exists and is linked.

## 2. End-to-End Workflow (agreed spec)

1. A person films and edits vertical clips (human step, out of scope)
2. They visit an upload web page and, for each clip, select the MP4 and type a one-or-two-line description (captions/hashtags raw material)
3. Browser-side JavaScript validates each file before/during upload (see §5 thresholds) and warns immediately on failure
4. Each clip+description pair uploads **immediately and independently** as it's added — not batched into one submit. Each item shows its own status (uploading / done / error). Multiple clips per session must be supported.
5. Server-side PHP saves the pair into a watch folder: `video-name.mp4` + `video-name.txt` (same base name) in `socialClips/pending/`
6. A Railway-hosted Python script runs on a schedule (every 15–30 min), connects to the web host via SFTP, and scans `pending/` for unprocessed pairs
7. For each pair: download both files, re-validate the video with ffmpeg (safety net; browser checks can be bypassed)
8. Validation failures: move the pair to `socialClips/rejected/` on the host and include the failure in the notification email. Never publish or silently skip.
9. Valid clips: send the description text to the Anthropic API to generate platform-tailored copy — an Instagram caption (hashtag-friendly) and a YouTube title + description (include "#Shorts")
10. Publish the video to Instagram (Graph API, Reels/vertical video) and YouTube (Data API v3). A vertical video under 3 minutes is automatically classified as a Short — no special endpoint.
11. Only after BOTH platforms confirm success, move the pair from `pending/` to `socialClips/done/` on the host so it is never reprocessed. Partial failure handling: do not move the pair; retry logic or clear error notification required (design decision left to implementation — do not double-post the platform that succeeded).
12. Send a summary email via Resend: what posted, where, links if available, and anything rejected or failed

## 3. Infrastructure Already Set Up (all verified working)

### Web host (TigerTech, cPanel-based shared hosting)
- POC subdomain: `dojopoc.julianfox.com`
- Directories created: `socialClips/pending/`, `socialClips/done/`, `socialClips/rejected/`
- A dedicated SFTP account exists, **jailed to `socialClips/` as its home directory** (least-privilege; it cannot reach site code or other domains on the account)
- The host runs PHP (no framework). An existing pattern for uploads exists on the dojo's site: a PHP endpoint using `move_uploaded_file()` with filename sanitization (`preg_replace('/[^a-zA-Z0-9-_]/', '-', ...)` + timestamp suffix). Follow the same pattern.
- `callback.php` (OAuth catcher used during setup) is deployed at the subdomain root — keep it; it's needed again if Instagram auth must ever be redone from scratch

### Meta / Instagram
- Facebook Page: **DojoTest** (Julian is admin)
- Instagram: **kakiwakeuke**, Professional/Business type, linked to DojoTest
- Meta developer app: **Dojo POC-IG** (Development mode — correct and sufficient; never needs App Review or Live mode for own-account use)
- Permissions granted on the token: `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_messages`, `instagram_business_manage_comments`, `instagram_business_manage_insights`
- kakiwakeuke is an **accepted** Instagram Tester on the app
- A **long-lived Instagram user access token (~60 days)** is in hand, obtained July 18, 2026
- IG user id: `17841412338243421` (app-scoped id also seen: `27222501344118901`)
- Redirect URI registered on the app: `https://dojopoc.julianfox.com/callback.php`

### Google / YouTube
- Google Cloud project: **Dojo POC**, YouTube Data API v3 enabled
- OAuth consent: External, Testing mode, Julian's account added as test user
- OAuth client: **Desktop app** type; `client_secret.json` downloaded and held locally
- Scopes configured: `youtube.upload` (plus `youtube.readonly`, added only for a connectivity test — the pipeline only needs upload)
- Verified working end-to-end via `InstalledAppFlow.run_local_server()` + `channels().list(mine=True)`
- Target channel: Julian's personal channel ("Julian Fox", `UCnPb4OY8ggvF73pdPc9KTEQ`). He does not use it and is fine with test content, but **default test uploads to `privacyStatus: "private"`** anyway until final testing.

### Build/deploy
- GitHub repo created (private), with a credentials-safe `.gitignore` committed before any code (`.env`, `client_secret*.json`, `*.token`, venv, `*.mp4`, etc.)
- Railway project created and connected to the repo
- Existing owner pattern worth matching: Julian has other small Railway + Python + Resend monitor scripts; same stack, same style
- Dev machine is Windows (PowerShell); note `curl.exe` vs the PowerShell `curl` alias when writing docs/instructions for him

## 4. Credentials Inventory (names only — values live in Railway env vars / local .env, never in the repo)

| Env var (suggested) | What it is | Status |
|---|---|---|
| `IG_ACCESS_TOKEN` | Instagram long-lived user token | In hand, expires ~Sept 16, 2026 unless refreshed |
| `IG_USER_ID` | 17841412338243421 | Known |
| `IG_APP_SECRET` | Instagram app secret (from the app's Instagram API setup page — NOT App Settings → Basic; the two differ and only the Instagram-product one validates on graph.instagram.com) | ⚠ Was exposed in a chat session — **regenerate in Meta dashboard before production**, then update env |
| `IG_APP_ID` | Instagram app ID | Known |
| `YT_CLIENT_SECRET_JSON` | Google OAuth Desktop client (file or inlined JSON) | In hand |
| `YT_REFRESH_TOKEN` | Google OAuth refresh token | **DOES NOT EXIST YET — see §6.2** |
| `SFTP_HOST` / `SFTP_USER` / `SFTP_PASS` | Scoped TigerTech SFTP account | In hand |
| `ANTHROPIC_API_KEY` | For caption generation | In hand |
| `RESEND_API_KEY` | For notification emails | In hand |
| `NOTIFY_EMAIL` | Where summaries go | Julian's address |

## 5. Validation Thresholds (agreed)

Checked twice: in-browser at upload (instant feedback) and again with ffmpeg/ffprobe in the pipeline (authoritative).

- Container/codec: MP4 only (upload page already restricts to .mp4)
- Aspect ratio: vertical, ~9:16 (allow small tolerance)
- Duration: ≤ 90 seconds hard limit (Instagram Reels ceiling used for this POC); ≤ 60s is the recommendation surfaced to the user, not enforced
- File size: ≤ 500 MB
- Failures at the pipeline stage → `rejected/` + notification. Failures at the browser stage → inline warning, block that item's upload.

**Railway note:** ffmpeg is not in Railway's default Python image — add it via nixpacks config or apt in the build.

## 6. Known Open Items / Gotchas (read carefully)

### 6.1 Instagram token refresh (must be built into the pipeline)
- The long-lived token expires ~60 days from issue and does NOT auto-renew
- Refresh via `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=<current>` — refreshing rolls it forward another 60 days; token must be at least 24h old to refresh
- Build a refresh step into the scheduled job (weekly is plenty) and persist the newest token (Railway env vars can't be self-updated by the script easily — consider storing the live token in a small file on the SFTP host or another persistence spot, with the env var as the bootstrap value; implementation choice open)
- If it ever fully expires: re-run the manual OAuth flow (authorize URL → callback.php → code → POST api.instagram.com/oauth/access_token → GET graph.instagram.com/access_token?grant_type=ig_exchange_token). **The App Dashboard "Generate token" button produces tokens that FAIL the ig_exchange_token call** ("Session key invalid", code 452) — this cost hours of debugging. Only the real OAuth flow yields exchange-able tokens.

### 6.2 YouTube refresh token (one-time setup task, not yet done)
- Current verified flow used an interactive browser popup — unusable on Railway
- Needed: a one-time local script run with `access_type=offline` (and `prompt=consent` to force a refresh token), persist `creds.refresh_token`, then the pipeline authenticates non-interactively with client id/secret + refresh token
- Caveat: OAuth apps in **Testing** mode have refresh tokens that Google expires after ~7 days. Two options: publish the consent screen to Production (stays unverified — fine, only Julian uses it; the scary warning screen is acceptable) or accept re-authing weekly (not acceptable for this use case). **Recommend publishing to Production.**

### 6.3 Instagram publishing mechanics (for implementation)
- Publishing is a 2-step container flow: `POST /{ig-user-id}/media` (with `video_url`, `media_type=REELS`, `caption`) then `POST /{ig-user-id}/media_publish` with the returned container id
- Meta fetches the video **server-side from a public HTTPS URL** — it cannot be pushed as bytes. The clips already live on the web host, so the natural design: the pipeline constructs the public URL of the clip under `dojopoc.julianfox.com/socialClips/pending/...` and hands that to the API. This means `pending/` must be web-readable (or copy clips to a web-readable staging path). Consider whether `done/` files should be purged after a retention period.
- Poll the container's `status_code` until `FINISHED` before calling `media_publish`; video processing takes time
- Quota: ~100 API-published posts per 24h — irrelevant at this volume

### 6.4 Misc
- The upload page and PHP endpoint from §2 do not exist yet (a draft exists but the spec superseded it; build fresh from this doc)
- Anthropic API model/prompting: generate distinct copy per platform from the human's description; keep the dojo's eventual brand voice configurable (a small editable prompt/config file, since the POC → dojo transition will change voice)
- Email notification style: match Julian's existing Resend monitors — plain, direct, no corporate fluff
- Owner preference: direct, concise; **no em dashes in any output**; before modifying working code, save/commit the current state labeled as the last working version (regression point)

## 7. Suggested Repo Structure (open to change)

```
/
├── .gitignore              (exists)
├── README.md
├── requirements.txt
├── main.py                 # entry: scheduled run
├── pipeline/
│   ├── sftp_client.py      # connect, list pending, download, move to done/rejected
│   ├── validate.py         # ffprobe checks per §5
│   ├── captions.py         # Anthropic API: description -> per-platform copy
│   ├── publish_instagram.py
│   ├── publish_youtube.py
│   ├── token_refresh.py    # IG refresh (§6.1), YT non-interactive auth (§6.2)
│   └── notify.py           # Resend summary email
├── web/                    # deployed to TigerTech manually (not by Railway)
│   ├── social-upload.html  # multi-file, per-item caption box, immediate upload, client-side validation
│   └── social-upload.php   # saves mp4+txt pair to socialClips/pending/
└── scripts/
    └── get_youtube_refresh_token.py   # one-time local run (§6.2)
```

## 8. Build Order Recommendation

1. `scripts/get_youtube_refresh_token.py` + publish Google consent screen to Production → closes the last credential gap
2. `web/` upload page + PHP endpoint → gives a real way to feed the pipeline
3. Pipeline skeleton: SFTP scan/download/move + validation + Resend notification (no publishing yet) → verify the plumbing end to end with a test clip
4. Caption generation
5. YouTube publishing (private visibility for tests)
6. Instagram publishing (container flow)
7. IG token refresh job
8. Schedule on Railway (cron), end-to-end test, then flip YouTube test visibility
