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

All env vars are read in `pipeline/config.py`; `.env.example` has the
same list with the POC's actual values filled in where useful. The
account-specific ones (host, paths, credentials) are required with no
defaults in code, so a forgotten variable fails the run loudly instead
of quietly touching the wrong account, and the POC to dojo transition
is entirely a matter of changing Railway variables plus
`brand_voice.txt`, never editing code.

#### Required, no default (the run fails immediately if missing)

| Variable | What it should contain |
|---|---|
| `SFTP_HOST` | Hostname of the TigerTech SFTP login. |
| `SFTP_USER` | SFTP username. |
| `SFTP_PASS` | SFTP password. |
| `SFTP_BASE_PATH` | Absolute filesystem path (not relative, the account is not chrooted) to the `socialClips` folder, e.g. `/var/www/html/ju/julianfox.com/dojopoc/socialClips`. Confirm via FileZilla for a new hosting account; see "If SFTP can't find pending/done/rejected" below if it's wrong. |
| `PUBLIC_CLIP_BASE_URL` | Public HTTPS URL that serves the `pending/` directory's contents, e.g. `https://dojopoc.julianfox.com/socialClips/pending`. Instagram's Graph API fetches the video server-side from this URL, it does not accept an uploaded file. |
| `RESEND_API_KEY` | API key from the Resend account used for summary emails. |
| `NOTIFY_EMAIL` | Address that receives the run summary emails. |
| `ANTHROPIC_API_KEY` | Anthropic API key, used to generate captions/title/description/tags. |

#### Required only when actually publishing (validated at use time, so `PUBLISH_ENABLED=false` works before these exist)

| Variable | What it should contain |
|---|---|
| `YT_CLIENT_ID` | OAuth Client ID, from Google Cloud Console > Credentials > OAuth 2.0 Client IDs > the Desktop client. Preferred over `YT_CLIENT_SECRET_JSON`. |
| `YT_CLIENT_SECRET` | The matching OAuth Client secret, copied as its own plain value from the same Console page. Do not paste this into `YT_CLIENT_SECRET_JSON`. |
| `YT_CLIENT_SECRET_JSON` | Alternative to the two vars above: the full downloaded `client_secret.json` file's content, or a path to that file on disk. Leave blank if using `YT_CLIENT_ID`/`YT_CLIENT_SECRET`. |
| `YT_REFRESH_TOKEN` | Minted once by running `scripts/get_youtube_refresh_token.py` locally (see Setup section 1 above). |
| `IG_ACCESS_TOKEN` | Bootstrap value only, used the first time the pipeline runs. After that the live token is persisted and self-refreshed at `socialClips/ig_token.json` on the host. |
| `IG_USER_ID` | The Instagram Business/Creator account's numeric user ID (the POC value is `17841412338243421`; the dojo account will have its own). |

#### Optional, with defaults

| Variable | Default | What it should contain |
|---|---|---|
| `SFTP_PORT` | `22` | SFTP port, only needed if TigerTech ever changes it. |
| `NOTIFY_FROM` | `onboarding@resend.dev` | Sender address for summary emails. The default only delivers to your own Resend account email; set a verified sender for real delivery to other inboxes. |
| `CAPTION_MODEL` | `claude-opus-4-8` | Anthropic model ID used for caption/tag generation. |
| `BRAND_VOICE_FILE` | `brand_voice.txt` (repo root) | Path to the editable voice/style text file `pipeline/captions.py` reads on every run. Editing this file needs no redeploy. |
| `PUBLISH_ENABLED` | `true` | Set `false` to run validation + caption generation + email preview only, nothing actually posts or moves to `done/`, and the schedule clock does not advance. Useful before all credentials exist or to sanity-check copy before it goes live. |
| `POST_INTERVAL_HOURS` | `24` | Minimum hours between posts (rolling schedule). `24` = one clip per day, `12` = two per day, `0` = post every valid clip immediately, draining the whole queue in one run. |
| `YT_PRIVACY_STATUS` | `private` | `private` while testing, `public` for real visibility. Currently set to `public` on Railway. |
| `YT_CATEGORY_ID` | `17` (Sports) | YouTube category ID for uploaded videos; see Google's category ID list if this ever needs to change. |
| `YT_DEFAULT_LANGUAGE` | empty (field omitted) | BCP-47 language code for the video's metadata language, e.g. `en`. |
| `YT_DEFAULT_AUDIO_LANGUAGE` | falls back to `YT_DEFAULT_LANGUAGE` | BCP-47 code for the spoken audio language. Only set this separately if it should differ from `YT_DEFAULT_LANGUAGE`. |
| `IG_LOCATION_ID` | empty (no tag) | Facebook **Places** page ID (not a generic Facebook Page ID) for the dojo's physical location, tagged on every Reel for local discovery. Find it via Graph API Explorer: `GET /search?type=place&q=<dojo name>&center=<lat>,<lng>`, matched against the dojo's real address. If Instagram rejects the ID, the run logs why and posts untagged rather than failing the clip. |
| `AUDIENCE_WINDOW_ENABLED` | `true` | Set `false` to disable the audience-timing feature below entirely and always post whenever `POST_INTERVAL_HOURS` says a post is due. |
| `AUDIENCE_WINDOW_HOURS` | `3` | Width, in hours, of the daily posting window computed from Instagram's `online_followers` insight (see below). Whole numbers only; `24`+ effectively disables the gating without turning the feature off. |
| `SFTP_DEBUG_LIST_TREE` | `false` | Set `true` for one run to email a recursive directory listing instead of processing clips; see "If SFTP can't find pending/done/rejected" below. |
| `SFTP_DEBUG_LIST_PATH` | `.` (SFTP login's default directory) | Starting path for the debug listing above; try `/` for the true filesystem root. |

#### Diagnostic-only, not read by the running pipeline

| Variable | What it should contain |
|---|---|
| `IG_APP_ID` | Meta App ID, held for a possible future manual OAuth redo (spec section 6.1). |
| `IG_APP_SECRET` | Meta App Secret, same purpose. Was exposed once during setup and should be regenerated in the Meta dashboard before this goes fully live. |

### Distribution features (Phase 2)

Two small, additive levers plus one adaptive one, all designed so an
unset/failed config behaves exactly like Phase 1:

- **Instagram location tagging** (`IG_LOCATION_ID`): tags the dojo's
  Facebook Places page on every Reel for local discovery. A wrong or
  unsupported ID is logged and the clip posts untagged rather than
  failing; see the table above for how to find the correct ID (it must
  be a Places page with an address, not just any Facebook Page).
- **YouTube tags and language** (`YT_DEFAULT_LANGUAGE`,
  `YT_DEFAULT_AUDIO_LANGUAGE`): caption generation now also returns
  8-15 search tags per clip, set on the upload automatically, no config
  needed. The language fields are opt-in via the two env vars above.
- **Audience-informed posting window** (`AUDIENCE_WINDOW_ENABLED`,
  `AUDIENCE_WINDOW_HOURS`): once a week the pipeline pulls Instagram's
  `online_followers` insight (followers online per hour, averaged over
  roughly the last month), finds the best contiguous window of the
  day, and holds a due clip until that window opens. State persists at
  `socialClips/posting_window.json` on the host. This needs the
  account's insights permission and, per Meta, roughly 100+ followers
  with some history before it returns real data; until then (or on any
  API error) it logs why and posts whenever due, the pre-feature
  behavior. A failed refresh retries at most once every 24 hours so a
  missing permission doesn't turn into a wasted API call on every run.

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
5. `YT_PRIVACY_STATUS=public` is already set for real visibility.
6. Regenerate `IG_APP_SECRET` in the Meta dashboard (it was exposed once
   during setup) and update the env var. Still outstanding.
7. Once the pipeline points at the dojo's real Instagram account (not
   the test account): set `IG_LOCATION_ID` to the dojo's Facebook
   Places page ID (see the distribution features section above) and
   confirm one manual run logs no "location tagging failed" line.
8. On the same real account, confirm `AUDIENCE_WINDOW_ENABLED` finds a
   window within the first week or two of runs (check the logs for
   "Audience window refreshed"). The test account's low follower count
   returns no data by design, that is expected and not a bug.

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
