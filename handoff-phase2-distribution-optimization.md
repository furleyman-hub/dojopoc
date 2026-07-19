# Handoff: Dojo Social Media Automation, Phase 2 (Distribution Optimization)

Written 2026-07-19 at the end of Phase 1, for whoever (human or Claude
session) picks up work on branch `claude/distribution-optimization-phase2`.
Phase 1 is done, verified end to end with real accounts, and lives
untouched on `main` / `claude/new-project-setup-ekxsqh` at commit
`854756a`. This branch exists so Phase 2 work can't disturb that.

Read `dojo-social-automation-handoff.md` first (the original spec, still
at repo root and unmodified). This document covers everything built
since, in more operational detail than the README, plus the open ideas
Phase 2 was created to pursue.

## 1. What this system does, end to end

A person uploads an MP4 clip plus a one-line description on a web page.
From there, fully automated:

1. The clip lands via PHP on the TigerTech web host, in
   `socialClips/pending/` as `<base>.mp4` + `<base>.txt`.
2. A Railway cron job runs `python main.py` on a schedule (currently
   every 20 minutes, set in the Railway dashboard, not in code).
3. Each run connects over SFTP, finds new complete pairs, validates them
   with ffprobe, generates Instagram/YouTube copy with the Anthropic
   API, uploads to YouTube (Data API v3) and Instagram (Graph API Reels
   container flow), and moves the pair to `done/` once both platforms
   confirm.
4. Posting is throttled to a rolling schedule (`POST_INTERVAL_HOURS`,
   default 24) so a bulk upload of many clips drips out over time,
   oldest upload first, instead of posting all at once.
5. A summary email goes out via Resend, but only when something
   happened (post, new queue entry, rejection, or error), a quiet run
   with nothing to do sends nothing, so a pause in filming doesn't spam.

Verified working end to end on 2026-07-19: a real clip went from the
upload page through validation, caption generation, a YouTube Short, and
an Instagram Reel, into `done/`, with a confirmation email carrying
working links to both live posts
(`https://youtube.com/shorts/1DQVhlBt-Ao`,
`https://www.instagram.com/reel/Da-ioUQjdEd/`).

## 2. Repo layout

```
main.py                     entry point, one scheduled run per invocation
pipeline/
  config.py                 env vars, required vs optional, validation thresholds
  sftp_client.py             WatchFolder: SFTP access to pending/done/rejected
  validate.py                ffprobe checks (size, duration, container, aspect)
  captions.py                Anthropic API: description -> per-platform copy
  publish_youtube.py         YouTube Data API v3 upload, non-interactive OAuth
  publish_instagram.py       Graph API Reels container flow
  token_refresh.py           IG long-lived token: persist on host, refresh weekly
  notify.py                  Resend summary email
brand_voice.txt              editable voice/style config read by captions.py
railway.json                 Railway build/deploy config (cron is in the dashboard, not here)
Dockerfile                   python:3.12-slim, apt-get ffmpeg, pip install, python main.py
web/                         deployed manually to TigerTech, not by Railway
  social-upload.html         upload page: per-clip description, immediate independent upload
  social-upload.php          saves <base>.mp4 + <base>.txt into socialClips/pending/
scripts/
  get_youtube_refresh_token.py   one-time local run to mint YT_REFRESH_TOKEN
.env.example                 documents every env var, required and optional
dojo-social-automation-handoff.md   the original spec (section 8 = the 8-step build order Phase 1 followed)
```

## 3. Environment variables (Railway)

All of these are read in `pipeline/config.py`. Required vars call
`require_env()` and raise `RuntimeError` immediately if missing/blank.
There are no silent defaults for anything account-specific, by design,
so a forgotten variable fails loudly instead of quietly running against
the wrong account.

Required, no default:
- `SFTP_HOST`, `SFTP_USER`, `SFTP_PASS`, SFTP login for the web host.
- `SFTP_BASE_PATH`, absolute path from the SFTP login to the
  `socialClips` folder. The account is **not chrooted**, so this is a
  real absolute filesystem path, not a path relative to a jail. POC
  value: `/var/www/html/ju/julianfox.com/dojopoc/socialClips`. A new
  hosting account's path is a fresh guess, see section 7 below for the
  self-diagnosis tool.
- `RESEND_API_KEY`, `NOTIFY_EMAIL`, email notifications.
- `ANTHROPIC_API_KEY`, caption generation.
- `PUBLIC_CLIP_BASE_URL`, public HTTPS URL for `pending/`, since
  Instagram's Graph API fetches the video server-side from a URL rather
  than accepting an upload of bytes. POC value:
  `https://dojopoc.julianfox.com/socialClips/pending`.

Required only when actually publishing (validated at use time, so
`PUBLISH_ENABLED=false` works before these exist):
- `YT_CLIENT_ID` + `YT_CLIENT_SECRET` (preferred, two plain values from
  Google Cloud Console) or `YT_CLIENT_SECRET_JSON` (full
  `client_secret.json` content or a file path, do not paste just the
  bare `GOCSPX-...` secret value into this one, that's a real mistake
  the code now detects and explains).
- `YT_REFRESH_TOKEN`, minted once via
  `scripts/get_youtube_refresh_token.py`.
- `IG_ACCESS_TOKEN`, bootstrap only; the live token lives on the SFTP
  host at `socialClips/ig_token.json` and refreshes itself.
- `IG_USER_ID`.

Optional, with defaults:
- `SFTP_PORT` (22)
- `NOTIFY_FROM` (`onboarding@resend.dev`, which only delivers to your
  own Resend account email, set a verified sender for real delivery)
- `CAPTION_MODEL` (`claude-opus-4-8`)
- `BRAND_VOICE_FILE` (`brand_voice.txt` at repo root)
- `PUBLISH_ENABLED` (`true`; set `false` to validate + queue + preview
  without actually posting or advancing the schedule)
- `POST_INTERVAL_HOURS` (`24`; `0` disables throttling and posts
  everything found in one run)
- `YT_PRIVACY_STATUS` (`private`; flip to `public` for real visibility)
- `YT_CATEGORY_ID` (`17` = Sports)
- `SFTP_DEBUG_LIST_TREE` (`false`) / `SFTP_DEBUG_LIST_PATH` (`.`), see
  section 7.

Diagnostic-only, not read by code but worth knowing about: `IG_APP_ID`,
`IG_APP_SECRET` are held for a future manual OAuth redo (spec section
6.1) and are not used by the running pipeline.

## 4. Per-clip state machine

Every clip pair gets a JSON state file at
`pending/<base>.state.json` on the SFTP host, persisted across runs
(Railway containers are ephemeral, the host is not). Keys accumulate as
the clip progresses:

- `validated: true`, ffprobe passed. Set once, checked on every run
  after so a clip already in the queue is never re-validated.
- `copy`, the generated `{instagram_caption, youtube_title,
  youtube_description}` dict, set once per clip right before the first
  publish attempt.
- `youtube`, `{id, url}`, set once the YouTube upload succeeds.
- `instagram`, `{id, permalink}`, set once the Instagram publish
  succeeds.

The pair only moves out of `pending/` (to `done/` or `rejected/`) once
its terminal condition is reached; the state file moves alongside it.
This means a mid-publish crash (say, Instagram's API is down after
YouTube already succeeded) leaves the clip in `pending/` with
`youtube` set but not `instagram`. The next run finds it, sees it's
"partially published," and finishes only the missing platform.
YouTube is never re-uploaded. See `main.py`'s `_publish_clip()`.

## 5. The three-phase run (`main.py: run()`)

Each invocation of `main.py` does, in order:

1. **Validate new arrivals.** Every complete pair in `pending/` without
   `state["validated"]` gets downloaded and ffprobe-checked right away,
   regardless of the posting schedule. Good clips get `validated: true`
   and a "queued" line in the summary email. Bad clips move to
   `rejected/` immediately (moving the state file too) and get a
   "rejected" line with reasons. This means a bad clip in a 10-clip bulk
   upload is reported within one cron cycle, not days later when its
   turn in the schedule would otherwise arrive, and it never consumes a
   posting slot.
   - If ffprobe itself can't run (missing binary, broken container),
     that raises `FFprobeNotFoundError`, which is caught as an *error*,
     not a rejection: the clip stays in `pending/` untouched. This
     matters because a build/environment problem must never look
     indistinguishable from "this video is actually bad" and
     permanently move a good clip to `rejected/`.

2. **Resume any partially published clip**, regardless of the
   throttle. A half-posted clip is worse than two posts landing close
   together, so this phase ignores `POST_INTERVAL_HOURS` entirely.
   Completing one of these does still advance the schedule anchor
   (consumes that interval's slot), see section 6.

3. **Post from the queue**, clips that are `validated` but have
   neither `youtube` nor `instagram` set yet, oldest upload first, but
   only if `_is_due(schedule)` says the throttle allows it right now (or
   `POST_INTERVAL_HOURS <= 0`, which drains the entire queue in one
   run). Exactly one clip posts per due run when the throttle is active.
   If `PUBLISH_ENABLED=false`, this phase instead just generates and
   previews the copy for whichever clip would post next, without
   posting or moving anything or touching the schedule clock.

A run's email fires only if `posted or previewed or queued or rejected
or errors`, i.e. only if something happened. A run where clips are
just waiting their turn, or where `pending/` was empty, logs to stdout
but sends nothing. This was an explicit requirement: no notification
noise if clip production simply pauses for a while.

## 6. The rolling posting schedule

Design driver: bulk-upload ten clips, and with `POST_INTERVAL_HOURS=24`
exactly one posts per day, oldest upload first, until the queue drains,
rather than all ten posting the instant the cron next fires.

State lives at `socialClips/schedule_state.json` on the SFTP host (not
in `pending/`, since it's not per-clip), as `{"last_posted_at":
"<ISO-8601 UTC>"}`.

`_is_due()`: true if `POST_INTERVAL_HOURS <= 0`, or if there's no
recorded last post, or if enough time has elapsed since the last one.

`_advance_schedule()`, the one subtle piece of math here, worth
understanding before touching it: when a post lands within one interval
of its *scheduled* slot (the normal case, the cron just fires every N
minutes, it's not going to land exactly on the interval boundary), the
anchor is credited to the slot time, not the actual post time. This
stops the daily posting time from creeping later by the cron's
granularity every single day. But after a long gap (empty queue for a
while, first post ever, or a stale/missing anchor), the anchor uses the
actual current time instead, otherwise an old anchor would make the
system think it owes a burst of catch-up posts and drain the backlog
all at once the moment new clips arrive. Both branches were verified
directly against `main._advance_schedule()` in the test suite (section
9 below), not just observed through end-to-end behavior.

## 7. Self-diagnosis: SFTP path debugging

`SFTP_BASE_PATH` is a guess against the real filesystem until proven.
The SFTP account is not chrooted to `socialClips/`, so a fresh hosting
account could put the login anywhere. If a run fails with
`FileNotFoundError` on `list_pending_pairs`, set
`SFTP_DEBUG_LIST_TREE=true` on Railway and trigger one run: instead of
processing any clips, `main.py` emails a recursive directory listing
from the SFTP login root (or from `SFTP_DEBUG_LIST_PATH` if set, e.g.
`/` for the true filesystem root) via `WatchFolder.list_tree()`. Use
that to correct `SFTP_BASE_PATH`, then turn the debug flag back off.

This exists because the exact same class of bug happened twice during
Phase 1: `SFTP_BASE_PATH` guessed as `socialClips` (wrong, not
chrooted), then as `dojopoc/socialClips` (wrong, relative when it needed
to be absolute), before FileZilla was used to find the real absolute
path. A real code bug was also found and fixed here: `.strip("/")`
(both sides) on an absolute path silently strips the leading slash and
turns it into a relative path resolved against whatever directory the
SFTP login happens to default into, the fix was `.rstrip("/")` only,
trailing slashes exclusively.

## 8. Deployment quirks worth knowing (already fixed, but explains some code shape)

- **Nixpacks was abandoned for a plain Dockerfile.** Getting `ffmpeg`
  (specifically `ffprobe` on PATH) to build reliably via Nixpacks'
  `aptPkgs`/`nixPkgs` config took two broken iterations (one didn't
  reliably put the binary on PATH; the other replaced rather than merged
  the phase's package list and broke Python itself). The Dockerfile is
  simple and has stayed stable since: `python:3.12-slim`, `apt-get
  install ffmpeg`, pip install, `python main.py`. `railway.json` forces
  `"builder": "DOCKERFILE"`.
- **Commit stamping.** A stale Railway deployment can reproduce old
  buggy behavior even after a fix is pushed, and is genuinely
  indistinguishable from a real regression without something to check
  against. `Dockerfile` promotes `RAILWAY_GIT_COMMIT_SHA` (and two other
  Railway build args) from `ARG` to `ENV`, and `main.py`'s
  `running_commit()` reads it, truncated to 12 chars, into every log
  line and every email subject (`Dojo clips [<commit>]: ...`). If a
  fresh run ever shows behavior that doesn't match the latest push,
  check this first.
- **`RUN ffprobe -version` in the Dockerfile** is a deliberate build-time
  sanity check, it fails the build loudly if the ffmpeg install ever
  silently breaks again, instead of surfacing as a runtime
  `FFprobeNotFoundError` days later.
- **Cron lives in the Railway dashboard, not `railway.json`.** Config-
  as-code overrides dashboard settings when a key is present, so
  `cronSchedule` was deliberately left out of `railway.json`, this
  means the interval (currently `*/20 * * * *`) can be changed anytime
  in Service > Settings > Cron Schedule without touching this repo or
  triggering a redeploy.
- **Docker builds cannot be verified inside the Claude Code sandbox**
  used for this work, `docker build` gets a hard 403 pulling
  `python:3.12-slim` from the sandbox's egress-policy-enforcing proxy.
  Verification during Phase 1 instead relied on: `pip install -r
  requirements.txt` resolving cleanly in an isolated venv, `main.py` and
  all `pipeline/*.py` importing cleanly against that dependency set, and
  a local SFTP test harness (section 9) exercising the actual pipeline
  logic. Real Dockerfile/Railway build behavior was only ever confirmed
  by the user's actual Railway deploys.

## 9. How this was tested without touching production

A local SFTP test server (the `sftpserver` Python package, paramiko-
generated RSA host keys) let the full pipeline run against a fake watch
folder on disk, with `main.publish_to_youtube`,
`main.publish_to_instagram`, and `main.generate_captions` monkey-patched
to fakes and `notify.send_summary` capturing emails instead of sending
them. The scheduling feature specifically was checked with 39 assertions
across 8 scenarios (bulk upload with one invalid clip validated/rejected/
queued correctly and exactly one posted; an immediate re-run being fully
silent while throttled; time-traveling the schedule anchor back 25h and
confirming exactly one more clip posts; an empty queue being silent;
a simulated Instagram outage producing a partial-publish state that the
next run resumes without re-uploading to YouTube, and which consumes
that interval's slot; `POST_INTERVAL_HOURS=0` draining the whole queue
in one run; and the anti-drift anchor math in both the on-time and
long-gap cases, called directly against `main._advance_schedule()`).
That harness lived at
`/tmp/.../scratchpad/test_schedule.py` (a sandbox scratch path, not
committed to the repo), if Phase 2 needs to re-verify scheduling
behavior, it's worth rebuilding a similar harness rather than testing
against the real host.

## 10. Standing operational notes

- The Instagram long-lived token expires around 2026-09-16 unless
  refreshed; the weekly refresh job (`pipeline/token_refresh.py`) rolls
  it forward 60 days each successful run. If it ever fully expires, the
  manual OAuth flow must be redone (spec section 6.1, via `callback.php`
 , the Meta App Dashboard's "Generate token" button does not produce an
  exchangeable token).
- `IG_APP_SECRET` was exposed once during setup and should be
  regenerated in the Meta dashboard before this goes fully live (still
  outstanding as of this handoff, check with the user whether it's
  been done).
- `YT_PRIVACY_STATUS` is still `private`; flip to `public` when ready
  for real visibility (also still outstanding, check before assuming
  it's done).
- No em dashes in any output (README, commit messages, code comments,
  emails), this was a standing instruction from the original spec's
  stated owner preference, verified with `grep` before every commit
  throughout Phase 1. Keep doing that in Phase 2.
- `callback.php` needs to stay at the subdomain root for the manual IG
  OAuth flow to keep working if it's ever needed again.
- Quota: roughly 100 API-published Instagram posts per 24h, irrelevant
  at this volume.
- Consider purging `done/` after some retention period, clips there
  stay web-readable if the directory is, and there's no cleanup job.

## 11. What Phase 2 was created to explore (not yet built, not yet agreed)

The user asked, while testing Phase 1 end to end: "is there anything we
can do automatically with the api for the social media platforms to
optimize the video distribution so it is actually seen by our
demographic?" This was discussed but no decision was made before the
user pivoted to requesting this handoff and a new branch instead, the
options below are candidates to bring back to the user, not a queued
task list.

Ideas raised, roughly smallest-change-first:

1. **Instagram location tagging.** The Graph API `/media` container
   call accepts a `location_id` field. Tagging a real-world location
   (the dojo's own place page) is a small, well-known lever for local
   discovery. Would need a one-time lookup of the location's Facebook
   Page/Places ID and a new config var.
2. **Posting-time optimization via audience insights.** Instagram's
   Graph API exposes account-level insights (follower online-time
   distribution) that could inform *when* `POST_INTERVAL_HOURS`-paced
   posts actually go out, rather than just spacing them evenly. This is
   a bigger change: it would mean the schedule stops being "every N
   hours" and starts being "next daily window with the best predicted
   reach," which changes the anchor math in `_advance_schedule()`
   meaningfully.
3. **Caption/hashtag tuning.** `brand_voice.txt` is already a zero-code
   lever for this, the caption/title/description generation prompt
   lives entirely in `pipeline/captions.py`'s `SYSTEM_PROMPT`, and voice
   changes only require editing that text file, no redeploy. Could
   extend this with hashtag-strategy guidance (trending/niche mix, a
   maintained hashtag list) directly in that file rather than in code.
4. **YouTube metadata: tags and language.** The `videos().insert` call
   in `publish_youtube.py` currently only sets `title`, `description`,
   `categoryId`, `privacyStatus`, and
   `selfDeclaredMadeForKids`. Adding a `tags` list (from the Anthropic
   caption-generation call, similar to how captions are generated now)
   and `defaultLanguage`/`defaultAudioLanguage` are both small additions
   to the same request body and would plug into the existing `copy`
   dict shape with one more key.
5. **Weekly analytics digest.** A new, separate scheduled Railway job
   (or a weekly branch inside the existing one) that pulls Instagram
   Insights and YouTube Analytics API data and emails a summary via the
   existing `pipeline/notify.py`, reach, watch time, top-performing
   clips, to inform future filming/captioning decisions. This is the
   largest of the five: new API scopes, new credentials, and a new
   schedule to coordinate against the existing cron.

Recommendation left for whoever picks this up: start with #1 (IG
location tagging) and #4 (YouTube tags/language) together, since both
are small, additive changes to publish calls that already exist and
carry very low risk of breaking anything in the currently-working
system. #2 and #5 are real projects in their own right and deserve their
own scoping conversation with the user before starting.

## 12. Branch state at handoff time

- `main` and `claude/new-project-setup-ekxsqh`: identical, at commit
  `854756a` ("Rolling posting schedule: one clip per
  POST_INTERVAL_HOURS"). Fully working, confirmed by the user via a real
  end-to-end run. Do not rebase or force-push either of these.
- `claude/distribution-optimization-phase2`: branched from that same
  commit, for all Phase 2 work. This document is its first commit.
