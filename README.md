# Dojo Social Media Automation (POC)

Pipeline that publishes short vertical clips to Instagram Reels and YouTube
Shorts. A person uploads an MP4 plus a short description on a web page;
everything after that is automated. Full spec: `dojo-social-automation-handoff.md`.

## Build status (spec section 8)

1. [x] `scripts/get_youtube_refresh_token.py` (one-time local run still needed, see below)
2. [x] Web upload page + PHP endpoint
3. [x] Pipeline skeleton: SFTP scan/download/move + ffprobe validation + Resend summary (no publishing yet)
4. [ ] Caption generation (Anthropic API)
5. [ ] YouTube publishing (private visibility for tests)
6. [ ] Instagram publishing (container flow)
7. [ ] IG token refresh job
8. [ ] Railway cron schedule + end-to-end test

## Layout

```
main.py                 entry point, one scheduled run per invocation
pipeline/
  config.py             env vars + validation thresholds
  sftp_client.py        watch folder access (pending/done/rejected)
  validate.py           ffprobe checks
  notify.py             Resend summary email
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
and optionally `NOTIFY_FROM` (a verified Resend sender).

`nixpacks.toml` installs ffmpeg (for ffprobe) and starts `python main.py`.
Each run:

1. connects to the host over SFTP (account is jailed to `socialClips/`)
2. lists complete pairs in `pending/` (an `.mp4` with a matching `.txt`;
   half-uploaded singles are skipped until complete)
3. downloads each pair and re-validates the video with ffprobe
4. failures move to `rejected/` on the host and are listed in the email
5. valid clips are reported in the email but left in `pending/`, because
   publishing is not built yet; nothing posts anywhere at this stage
6. sends one plain-text Resend summary (no email when nothing happened)

Local test run (PowerShell): copy `.env.example` to `.env`, fill in values,
`pip install -r requirements.txt`, install ffmpeg, then `python main.py`.
Note: in PowerShell use `curl.exe` for any raw API checks, plain `curl` is
an alias for `Invoke-WebRequest`.

Scheduling on Railway (cron every 15 to 30 minutes) is build step 8, after
publishing works.

## Notes for the remaining steps

- Valid clips currently stay in `pending/`. When publishing lands, a pair
  moves to `done/` only after BOTH platforms confirm; partial failures must
  not double-post the platform that succeeded.
- `IG_APP_SECRET` was exposed once during setup. Regenerate it in the Meta
  dashboard before production.
- IG long-lived token expires around Sept 16, 2026 unless refreshed
  (spec section 6.1).
- Keep `callback.php` on the subdomain root; it is needed if Instagram auth
  must ever be redone.
- Brand voice for caption generation should live in a small editable config,
  since the POC will be redeployed for the dojo's real accounts.
