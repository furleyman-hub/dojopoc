# Dojo Social Media Automation (POC)

Pipeline that publishes short vertical clips to Instagram Reels and YouTube
Shorts. A person uploads an MP4 plus a short description on a web page;
everything after that is automated. Full spec: `dojo-social-automation-handoff.md`.

## Build status (spec section 8)

All 8 steps complete. First successful end-to-end run July 19, 2026: a
real clip went from the upload page through validation and caption
generation to a YouTube Short and an Instagram Reel, moved to `done/`,
and the summary email carried working links to both posts.

1. [x] `scripts/get_youtube_refresh_token.py`
2. [x] Web upload page + PHP endpoint
3. [x] Pipeline skeleton: SFTP scan/download/move + ffprobe validation + Resend summary
4. [x] Caption generation (Anthropic API)
5. [x] YouTube publishing
6. [x] Instagram publishing (container flow)
7. [x] IG token refresh job
8. [x] Railway cron config + end-to-end test with real credentials

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
railway.json            Railway build/deploy config (cron lives in the dashboard)
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
4. Also set `YT_CLIENT_ID` and `YT_CLIENT_SECRET` on Railway: two separate
   plain values, shown on Google Cloud Console under Credentials > OAuth
   2.0 Client IDs > your Desktop client. Copy each one into its own env
   var. Do not paste just the "Client secret" value into
   `YT_CLIENT_SECRET_JSON`, that one wants the entire downloaded
   `client_secret.json` file's content (or a path to it) and exists only
   as an alternative to the two-var form above.

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

Set these env vars on the Railway service (see `.env.example` for the
POC values): `SFTP_HOST`, `SFTP_USER`, `SFTP_PASS`, `SFTP_BASE_PATH`,
`PUBLIC_CLIP_BASE_URL`, `RESEND_API_KEY`, `NOTIFY_EMAIL`,
`ANTHROPIC_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`,
`YT_REFRESH_TOKEN`, `IG_ACCESS_TOKEN`, `IG_USER_ID`, and optionally
`NOTIFY_FROM` (a verified Resend sender), `CAPTION_MODEL`,
`BRAND_VOICE_FILE`, `POST_INTERVAL_HOURS` (see below; defaults to
24), and the distribution levers `IG_LOCATION_ID` (tag the dojo's own
place page on every Reel for local discovery; if Instagram rejects the
id the clip still posts untagged) and `YT_DEFAULT_LANGUAGE` /
`YT_DEFAULT_AUDIO_LANGUAGE` (BCP-47 codes like `en`; the audio one
falls back to the first). YouTube uploads also carry search tags
generated alongside the captions; no config needed for that.
`AUDIENCE_WINDOW_ENABLED` (default true) refreshes Instagram's
online_followers insight weekly and holds due clips for the daily
`AUDIENCE_WINDOW_HOURS`-hour (default 3) block when the most followers
are online; without insights access or enough followers it logs why
into `socialClips/posting_window.json` and posts whenever due, the old
behavior. The account-specific ones (`SFTP_BASE_PATH`,
`PUBLIC_CLIP_BASE_URL`, all credentials) are required with no defaults
in code, so the POC to dojo transition is entirely a matter of changing
Railway variables and `brand_voice.txt`, never editing code.

### Rolling posting schedule

Bulk uploads roll out on a schedule instead of all posting at once.
`POST_INTERVAL_HOURS` (default 24) is the minimum time between posts:
24 = one clip per day, 12 = two per day, 0 = post everything as soon as
it's found. Upload ten clips at once and, at the default, one posts per
day, oldest upload first, until the queue drains.

How it behaves:

- New arrivals are validated immediately (once per clip) whatever the
  schedule says, so a bad clip in a bulk upload gets its rejection email
  within one cron run and never wastes a posting slot. Valid clips get a
  "queued" confirmation email listing them.
- The last-posted time persists on the host as
  `socialClips/schedule_state.json`, so it survives redeploys. The
  posting time stays stable day to day (an on-time post is credited to
  its scheduled slot, so the cron's granularity doesn't make the time
  creep later), and after a gap with an empty queue the schedule
  restarts from the next post rather than bursting out backlog.
- A partially published clip (one platform confirmed, the other failed)
  is finished on the next run regardless of the schedule, since
  half-posted is worse than two posts close together. Completing it
  consumes that interval's slot.
- Runs where nothing happened (clips waiting but not due yet, or
  nothing pending at all) send no email, so a pause in video production
  stays quiet. Emails only fire for posts, new queued clips,
  rejections, or errors, and always include a queue status line when
  clips are waiting.

The cron schedule is managed in the Railway dashboard (Service >
Settings > Cron Schedule; the POC runs `*/20 * * * *`, every 20
minutes). It is deliberately NOT set in `railway.json`, because
config-as-code overrides the dashboard when present; leaving it out
means the interval can be changed in the dashboard anytime without
touching this repo.

`Dockerfile` (python:3.12-slim, `apt-get install ffmpeg`, pip install,
`python main.py`) is the build. `railway.json` forces the Dockerfile
builder; each run:

1. connects to the host over SFTP (`SFTP_BASE_PATH`, see below)
2. lists complete pairs in `pending/` (an `.mp4` with a matching `.txt`;
   half-uploaded singles are skipped until complete), oldest upload first
3. validates NEW arrivals with ffprobe (once per clip, recorded in the
   clip's state file): a clip that fails moves to `rejected/` and is
   listed in the email; valid clips are queued. If ffprobe itself is
   missing or broken, that's treated as an environment error, not a
   rejected clip: the pair stays in `pending/` and is reported as an
   error, so a build problem can never permanently move a good clip out
   of `pending/`.
4. finishes any partially published clip from a previous failed run
   (ignores the posting schedule; see above)
5. if a post is due per `POST_INTERVAL_HOURS`, takes the oldest queued
   clip: generates the Instagram caption and YouTube title/description
   via the Anthropic API using the voice in `brand_voice.txt`, uploads
   to YouTube (`YT_PRIVACY_STATUS`, private by default), publishes to
   Instagram (Graph API Reels container flow; Meta fetches the video
   from `PUBLIC_CLIP_BASE_URL`, so `pending/` must be web-readable over
   HTTPS), and moves the pair to `done/` only after BOTH platforms
   confirm. Publish progress is saved per clip in
   `pending/<base>.state.json`, so a partial failure retries next run
   without double-posting the platform that already succeeded.
6. refreshes the Instagram long-lived token weekly. The live token is
   persisted as `socialClips/ig_token.json` on the host (`IG_ACCESS_TOKEN`
   is only the bootstrap value, since the script cannot update Railway
   env vars).
7. sends one plain-text Resend summary IF anything happened: posts with
   links, newly queued clips, rejections with reasons, errors, and a
   queue status line. Quiet runs send nothing.

Set `PUBLISH_ENABLED=false` to run everything except the actual posting:
new clips are validated and queued, the clip that would post next gets
its generated copy previewed in the email, but nothing posts, nothing
moves to `done/`, and the schedule clock does not advance. Useful for
testing the plumbing before all credentials are in place.

Local test run (PowerShell): copy `.env.example` to `.env`, fill in values,
`pip install -r requirements.txt`, install ffmpeg, then `python main.py`.
Note: in PowerShell use `curl.exe` for any raw API checks, plain `curl` is
an alias for `Invoke-WebRequest`.

## If SFTP can't find pending/done/rejected

`SFTP_BASE_PATH` is the absolute path from the SFTP login to the watch
folder (POC value: `/var/www/html/ju/julianfox.com/dojopoc/socialClips`).
The account is not necessarily jailed to `socialClips/` itself, it may
land at the web host account root instead, so a new account's path is
guesswork until verified against the real host. If a run fails with
`FileNotFoundError` on `list_pending_pairs`, set
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
