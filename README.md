# Dojo Social Media Automation (POC)

Pipeline that publishes short vertical clips to Instagram Reels and YouTube
Shorts. A person uploads an MP4 plus a short description on a web page;
everything after that is automated. Full spec: `dojo-social-automation-handoff.md`.

## Build status (spec section 8)

1. [x] `scripts/get_youtube_refresh_token.py` (one-time local run still needed, see below)
2. [x] Web upload page + PHP endpoint
3. [x] Pipeline skeleton: SFTP scan/download/move + ffprobe validation + Resend summary (no publishing yet)
4. [x] Caption generation (Anthropic API)
5. [x] YouTube publishing (private visibility for tests)
6. [x] Instagram publishing (container flow)
7. [x] IG token refresh job
8. [x] Railway cron config; end-to-end test with real credentials still to run

## Layout

```
main.py                 entry point, one scheduled run per invocation
pipeline/
  config.py             env vars + validation thresholds
  sftp_client.py        watch folder access (pending/done/rejected)
  validate.py           ffprobe checks
  captions.py           Anthropic API: description to per-platform copy
  publish_youtube.py    YouTube Data API v3 upload (non-interactive auth)
  publish_instagram.py  Graph API Reels container flow
  token_refresh.py      IG long-lived token: persist on host, refresh weekly
  notify.py             Resend summary email
brand_voice.txt         editable voice/style config for caption generation
railway.json            cron schedule (every 20 min) for Railway
web/                    deploy manually to TigerTech (not by Railway)
  social-upload.html    upload page: per-clip description, immediate upload
  social-upload.php     saves <base>.mp4 + <base>.txt into socialClips/pending/
scripts/
  get_youtube_refresh_token.py   one-time local run
```

## Setup

### 1. YouTube refresh token (one-time, local machine)

1. In Google Cloud Console, publish the OAuth consent screen to Production
   (APIs and Services > OAuth consent screen > Publish app). Testing mode
   refresh tokens die after about 7 days.
2. On your machine (PowerShell):

   ```
   pip install google-auth-oauthlib
   python scripts\get_youtube_refresh_token.py C:\path\to\client_secret.json
   ```

3. Sign in as the account that owns the target channel, approve past the
   unverified-app warning, and copy the printed `YT_REFRESH_TOKEN` into
   Railway env vars.

### 2. Web upload page (TigerTech)

1. Upload `web/social-upload.html` and `web/social-upload.php` to the
   `dojopoc.julianfox.com` document root (next to `socialClips/`).
2. In cPanel MultiPHP INI Editor for the subdomain, set `upload_max_filesize`
   and `post_max_size` to at least `512M`, and `max_execution_time` high
   enough for a 500 MB upload (300 or more).
3. Open `https://dojopoc.julianfox.com/social-upload.html`, add a clip, type
   a description, upload. Confirm the pair lands in `socialClips/pending/`.

The page validates in the browser (MP4 only, vertical ~9:16, 90 s and
500 MB hard limits, 60 s recommended) and each clip uploads immediately and
independently with its own status.

### 3. Pipeline (Railway)

Set these env vars on the Railway service (see `.env.example`):
`SFTP_HOST`, `SFTP_USER`, `SFTP_PASS`, `RESEND_API_KEY`, `NOTIFY_EMAIL`,
`ANTHROPIC_API_KEY`, and optionally `NOTIFY_FROM` (a verified Resend
sender), `CAPTION_MODEL`, and `BRAND_VOICE_FILE`.

`Dockerfile` (python:3.12-slim, `apt-get install ffmpeg`, pip install,
`python main.py`) is the build. `railway.json` forces the Dockerfile
builder and sets a cron schedule of every 20 minutes; each run:

1. connects to the host over SFTP (`SFTP_BASE_PATH`, see below)
2. lists complete pairs in `pending/` (an `.mp4` with a matching `.txt`;
   half-uploaded singles are skipped until complete)
3. downloads each pair and re-validates the video with ffprobe; a clip
   that fails validation moves to `rejected/` on the host and is listed
   in the email. If ffprobe itself is missing or broken, that's treated
   as an environment error, not a rejected clip: the pair stays in
   `pending/` and is reported as an error, so a build problem can never
   permanently move a good clip out of `pending/`.
4. generates the Instagram caption and YouTube title and description from
   the uploaded text via the Anthropic API, using the voice defined in
   `brand_voice.txt` (edit that file to change the tone; the POC to dojo
   switch is just swapping that file)
5. uploads to YouTube (Data API v3, `YT_PRIVACY_STATUS`, private by
   default) and publishes to Instagram (Graph API Reels container flow;
   Meta fetches the video from `PUBLIC_CLIP_BASE_URL`, so `pending/` must
   be web-readable over HTTPS)
6. moves the pair to `done/` only after BOTH platforms confirm. Publish
   progress is saved per clip in `pending/<base>.state.json` on the host,
   so a partial failure retries next run without double-posting the
   platform that already succeeded.
7. refreshes the Instagram long-lived token weekly. The live token is
   persisted as `socialClips/ig_token.json` on the host (`IG_ACCESS_TOKEN`
   is only the bootstrap value, since the script cannot update Railway
   env vars).
8. sends one plain-text Resend summary: what posted with links, what was
   rejected and why, and any errors (no email when nothing happened)

Set `PUBLISH_ENABLED=false` to run everything except the actual posting:
clips are validated and their generated copy is previewed in the email,
but nothing posts and nothing moves out of `pending/`. Useful for testing
the plumbing before all credentials are in place.

Local test run (PowerShell): copy `.env.example` to `.env`, fill in values,
`pip install -r requirements.txt`, install ffmpeg, then `python main.py`.
Note: in PowerShell use `curl.exe` for any raw API checks, plain `curl` is
an alias for `Invoke-WebRequest`.

## If SFTP can't find pending/done/rejected

`SFTP_BASE_PATH` (default `dojopoc/socialClips`) is the path from the SFTP
login root down to the watch folder. The account is not necessarily jailed
to `socialClips/` itself, it may land at the web host account root instead,
so this is guesswork until verified against the real host. If a run fails
with `FileNotFoundError` on `list_pending_pairs`, set
`SFTP_DEBUG_LIST_TREE=true` on Railway and trigger one run: instead of
processing clips, it emails a recursive directory listing from the SFTP
login root. Use that to correct `SFTP_BASE_PATH`, then set
`SFTP_DEBUG_LIST_TREE` back to `false`.

## Final testing checklist (the remaining human steps)

1. Run the one-time YouTube refresh token script (setup section above) and
   set all env vars on Railway.
2. Confirm `https://dojopoc.julianfox.com/socialClips/pending/` serves
   files over HTTPS (Instagram fetches the video from there). If the
   directory is not web-readable, fix that or point
   `PUBLIC_CLIP_BASE_URL` at a staging path that is.
3. First pass with `PUBLISH_ENABLED=false`: upload a test clip, run once,
   check the preview email.
4. Flip `PUBLISH_ENABLED=true`, run once: YouTube post lands as private,
   Instagram Reel goes live on the test account, pair moves to `done/`.
5. When satisfied, set `YT_PRIVACY_STATUS=public` for real visibility.
6. Regenerate `IG_APP_SECRET` in the Meta dashboard (it was exposed once
   during setup) and update the env var.

## Standing notes

- IG long-lived token expires around Sept 16, 2026 unless refreshed; the
  weekly refresh job rolls it forward 60 days each time. If it ever fully
  expires, redo the manual OAuth flow via `callback.php` (spec section
  6.1; the App Dashboard "Generate token" button does NOT work).
- Keep `callback.php` on the subdomain root.
- Consider purging `done/` after a retention period; clips there are
  web-readable if the directory is.
- Quota is roughly 100 API-published Instagram posts per 24h, irrelevant
  at this volume.
