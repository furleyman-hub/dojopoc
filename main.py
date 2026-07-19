"""Dojo social pipeline, scheduled entry point. Each run:

1. connect to the web host over SFTP and list complete pairs in pending/,
   ordered oldest upload first
2. validate NEW arrivals immediately with ffprobe (once per clip, result
   recorded in the clip's state file): failures move to rejected/ and are
   reported right away, valid clips are queued
3. finish any partially published clip (one platform confirmed, the other
   not), regardless of the posting throttle: half-posted is worse than two
   posts close together
4. if the posting throttle allows (POST_INTERVAL_HOURS since the last
   post), publish the OLDEST queued clip: generate copy (Anthropic API),
   upload to YouTube, publish to Instagram, and move the pair to done/
   only after BOTH platforms confirm
5. send one plain-text Resend summary IF anything happened (posted, newly
   queued, rejected, or errored). Runs where nothing happened, including
   "clips are waiting but not due yet" and "pending/ is empty", send no
   email at all.

This makes bulk uploads roll out on a schedule: upload ten clips at once,
and with POST_INTERVAL_HOURS=24 one posts per day, oldest first, until
the queue drains. POST_INTERVAL_HOURS=0 restores post-everything-now.
The last-posted timestamp persists on the SFTP host as
schedule_state.json (Railway containers are ephemeral).

Partial failure handling: publish progress is persisted per clip in
pending/<base>.state.json on the host (validated flag, generated copy,
YouTube result, Instagram result). A later run resumes from that state,
so a platform that already succeeded is never posted twice, and the pair
stays in pending/ until both have succeeded.

PUBLISH_ENABLED=false previews instead of posting: new arrivals are
still validated and queued, and the clip that WOULD post next gets its
copy generated and shown in the email, but nothing posts, nothing moves
to done/, and the schedule clock does not advance.

SFTP_DEBUG_LIST_TREE=true skips all of the above and instead emails a
recursive directory listing from the SFTP login root. Use this once to
find the real path to socialClips/ if SFTP_BASE_PATH is wrong (a
FileNotFoundError on list_pending_pairs means it is), then set
SFTP_BASE_PATH correctly and turn this back off.
"""

import json
import os
import posixpath
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone

from pipeline import config, notify
from pipeline.captions import generate_captions
from pipeline.publish_instagram import publish_to_instagram
from pipeline.publish_youtube import publish_to_youtube
from pipeline.sftp_client import WatchFolder
from pipeline.token_refresh import get_ig_access_token
from pipeline.validate import validate_video


def log(msg: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{stamp}] {msg}", flush=True)


def running_commit() -> str:
    """The git commit this container was built from, per Railway's own
    build metadata (empty string if not running on Railway, or if the
    Dockerfile build didn't promote the ARG to ENV). Use this to check
    whether a given run actually reflects the latest push, rather than
    assuming it, since a stale deployment can otherwise look identical to
    a real bug for several rounds.
    """
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "").strip()
    return sha[:12] if sha else "unknown"


def run() -> int:
    commit = running_commit()
    if os.environ.get("SFTP_DEBUG_LIST_TREE", "").strip().lower() in ("true", "1", "yes", "on"):
        return _debug_list_tree()

    posted: list[dict] = []      # {base, youtube, instagram}
    previewed: list[dict] = []   # {base, description, copy} (publish disabled)
    queued: list[str] = []       # newly validated this run, waiting for a slot
    rejected: list[tuple[str, str]] = []   # (base, reasons)
    errors: list[str] = []
    waiting: list[str] = []      # validated clips still queued after this run
    schedule: dict = {}
    ig_holder = {"token": None}

    log(f"Pipeline run starting (commit {commit})")
    if not config.PUBLISH_ENABLED:
        log("PUBLISH_ENABLED=false: preview mode, nothing will post")
    if config.POST_INTERVAL_HOURS > 0:
        log(f"Posting throttle: at most one clip per {config.POST_INTERVAL_HOURS:g}h")

    try:
        with WatchFolder() as folder, tempfile.TemporaryDirectory() as workdir:
            pairs = folder.list_pending_pairs()
            log(f"Found {len(pairs)} complete pair(s) in pending/ (oldest first)")

            states: dict[str, dict] = {}
            local: dict[str, tuple[str, str]] = {}  # base -> (video, txt) paths

            # Phase 1: validate new arrivals once, so a bad clip in a bulk
            # upload is rejected (and emailed about) right away instead of
            # days later when its slot comes up, and never consumes a slot.
            for base in list(pairs):
                try:
                    state = _load_state(folder, base)
                    states[base] = state
                    if state.get("validated"):
                        continue
                    log(f"Validating new arrival {base}")
                    local[base] = folder.download_pair(base, workdir)
                    ok, problems = validate_video(local[base][0])
                    if ok:
                        state["validated"] = True
                        _save_state(folder, base, state)
                        queued.append(base)
                        log(f"  queued for posting: {base}")
                    else:
                        reasons = "; ".join(problems)
                        log(f"  rejected: {base} ({reasons})")
                        folder.move_pair(base, config.REJECTED_DIR)
                        _move_state(folder, base, config.REJECTED_DIR)
                        rejected.append((base, reasons))
                        pairs.remove(base)
                except Exception:
                    err = f"{base}: {traceback.format_exc(limit=3)}"
                    log(f"  ERROR validating {err}")
                    errors.append(err)
                    if base in pairs:
                        pairs.remove(base)

            schedule = _load_schedule(folder)

            # Phase 2: finish partially published clips first, ignoring the
            # throttle: one platform is already live, and leaving the other
            # half unposted for a day is worse than two posts close
            # together. Completing one consumes this interval's slot.
            partials = [
                b for b in pairs
                if "youtube" in states.get(b, {}) or "instagram" in states.get(b, {})
            ]
            if partials and not config.PUBLISH_ENABLED:
                log(f"{len(partials)} partially published clip(s) left alone (preview mode)")
            elif config.PUBLISH_ENABLED:
                for base in partials:
                    try:
                        log(f"Resuming partially published {base}")
                        item = _publish_clip(folder, base, states[base], workdir, local, ig_holder)
                        posted.append(item)
                        pairs.remove(base)
                        _advance_schedule(folder, schedule)
                    except Exception:
                        err = f"{base}: {traceback.format_exc(limit=3)}"
                        log(f"  ERROR on {err}")
                        errors.append(err)
                        pairs.remove(base)

            # Phase 3: post from the queue, oldest first, at most one clip
            # per POST_INTERVAL_HOURS (0 = no limit, drain the queue).
            fresh = [
                b for b in pairs
                if states.get(b, {}).get("validated")
                and "youtube" not in states[b] and "instagram" not in states[b]
            ]
            if config.PUBLISH_ENABLED:
                while fresh and _is_due(schedule):
                    base = fresh.pop(0)
                    try:
                        log(f"Posting {base} (its turn in the schedule)")
                        item = _publish_clip(folder, base, states[base], workdir, local, ig_holder)
                        posted.append(item)
                        _advance_schedule(folder, schedule)
                    except Exception:
                        err = f"{base}: {traceback.format_exc(limit=3)}"
                        log(f"  ERROR on {err}")
                        errors.append(err)
                        # Stop rather than trying the next clip: the failure
                        # may be platform-wide, and this clip (possibly now
                        # half-posted) must resume before anything else.
                        break
            elif fresh and _is_due(schedule):
                base = fresh[0]
                try:
                    video_path, text_path = _fetch_pair(folder, base, workdir, local)
                    with open(text_path, encoding="utf-8", errors="replace") as fh:
                        description = fh.read().strip()
                    log(f"Preview: {base} would post next; generating its copy")
                    previewed.append(
                        {"base": base, "description": description,
                         "copy": generate_captions(description)}
                    )
                    fresh.pop(0)
                except Exception:
                    err = f"{base}: {traceback.format_exc(limit=3)}"
                    log(f"  ERROR previewing {err}")
                    errors.append(err)

            waiting = fresh
            if waiting:
                log(f"{len(waiting)} clip(s) waiting in the queue{_next_post_hint(schedule)}")
    except Exception:
        err = traceback.format_exc(limit=5)
        log(f"FATAL: {err}")
        errors.append(f"Run failed before processing completed:\n{err}")

    # Don't announce "queued" for clips that also posted this run.
    posted_bases = {item["base"] for item in posted}
    queued = [b for b in queued if b not in posted_bases]

    if posted or previewed or queued or rejected or errors:
        try:
            notify.send_summary(*build_summary(
                posted, previewed, queued, rejected, errors, waiting, schedule, commit
            ))
            log("Summary email sent")
        except Exception:
            log(f"Could not send summary email: {traceback.format_exc(limit=3)}")
    else:
        log("Nothing new this run, no email sent")

    log("Pipeline run finished")
    return 1 if errors else 0


def _publish_clip(
    folder: WatchFolder,
    base: str,
    state: dict,
    workdir: str,
    local: dict,
    ig_holder: dict,
) -> dict:
    """Publish one clip end to end, resuming from whatever the state file
    already records. Returns the posted item; raises on any failure (state
    is saved after each successful step, so a retry never repeats one).
    """
    video_path, text_path = _fetch_pair(folder, base, workdir, local)
    with open(text_path, encoding="utf-8", errors="replace") as fh:
        description = fh.read().strip()

    if "copy" not in state:
        log(f"  generating captions for {base}")
        state["copy"] = generate_captions(description)
        _save_state(folder, base, state)
    copy = state["copy"]

    if "youtube" not in state:
        log(f"  uploading {base} to YouTube")
        state["youtube"] = publish_to_youtube(
            video_path,
            copy["youtube_title"],
            copy["youtube_description"],
            tags=copy.get("youtube_tags"),
        )
        _save_state(folder, base, state)
        log(f"  YouTube done: {state['youtube']['url']}")
    else:
        log(f"  YouTube already done for {base} (from state)")

    if "instagram" not in state:
        if ig_holder["token"] is None:
            ig_holder["token"] = get_ig_access_token(folder)
        log(f"  publishing {base} to Instagram")
        state["instagram"] = publish_to_instagram(
            base, copy["instagram_caption"], ig_holder["token"]
        )
        _save_state(folder, base, state)
        log(f"  Instagram done: {state['instagram']['id']}")
    else:
        log(f"  Instagram already done for {base} (from state)")

    # Both platforms confirmed: only now leave pending/.
    folder.move_pair(base, config.DONE_DIR)
    _move_state(folder, base, config.DONE_DIR)
    log(f"  {base} fully published, moved to done/")
    return {"base": base, "youtube": state["youtube"], "instagram": state["instagram"]}


def _fetch_pair(folder: WatchFolder, base: str, workdir: str, local: dict) -> tuple[str, str]:
    if base not in local:
        local[base] = folder.download_pair(base, workdir)
    return local[base]


# ---- posting schedule (persisted on the SFTP host) ----

def _load_schedule(folder: WatchFolder) -> dict:
    raw = folder.read_text(config.SCHEDULE_STATE_FILE)
    if raw is None:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_due(schedule: dict) -> bool:
    if config.POST_INTERVAL_HOURS <= 0:
        return True
    last = _parse_ts(schedule.get("last_posted_at"))
    if last is None:
        return True
    elapsed = datetime.now(timezone.utc) - last
    return elapsed >= timedelta(hours=config.POST_INTERVAL_HOURS)


def _advance_schedule(folder: WatchFolder, schedule: dict) -> None:
    """Record that a post just happened. When the post lands within one
    interval of its scheduled slot (the normal case, since the cron only
    fires every N minutes), credit the SLOT time, not the actual time, so
    the daily posting time doesn't creep later by the cron granularity
    every day. After a long gap (empty queue for a while, or first post
    ever), use the actual time so an old anchor can't cause a burst of
    catch-up posts.
    """
    now = datetime.now(timezone.utc)
    interval = timedelta(hours=config.POST_INTERVAL_HOURS)
    last = _parse_ts(schedule.get("last_posted_at"))
    if last is not None and interval > timedelta(0) and now - (last + interval) < interval:
        anchor = last + interval
    else:
        anchor = now
    schedule["last_posted_at"] = anchor.isoformat()
    folder.write_text(config.SCHEDULE_STATE_FILE, json.dumps(schedule, indent=2))


def _next_post_hint(schedule: dict) -> str:
    if config.POST_INTERVAL_HOURS <= 0:
        return ""
    last = _parse_ts(schedule.get("last_posted_at"))
    if last is None:
        return "; next post: next run"
    nxt = last + timedelta(hours=config.POST_INTERVAL_HOURS)
    return f"; next post due after {nxt.strftime('%Y-%m-%d %H:%M UTC')}"


# ---- SFTP debug tree ----

def _debug_list_tree() -> int:
    commit = running_commit()
    # Defaults to the SFTP login's default directory ("."). Override with
    # SFTP_DEBUG_LIST_PATH (e.g. "/") if that default directory isn't a
    # useful vantage point, without needing another code change/deploy.
    start_path = os.environ.get("SFTP_DEBUG_LIST_PATH", ".").strip() or "."
    log(
        f"SFTP_DEBUG_LIST_TREE=true (commit {commit}): listing "
        f"{start_path!r} instead of processing clips"
    )
    try:
        with WatchFolder() as folder:
            lines = folder.list_tree(start_path, max_depth=5)
    except Exception:
        err = traceback.format_exc(limit=5)
        log(f"Could not list SFTP tree: {err}")
        try:
            notify.send_summary(
                f"Dojo clips [{commit}]: SFTP tree listing FAILED",
                f"Could not connect or list {start_path!r}:\n\n{err}",
            )
        except Exception:
            log(f"Also could not send the failure email: {traceback.format_exc(limit=3)}")
        return 1

    body = (
        f"Recursive listing from {start_path!r} (directories end in /).\n"
        f"Currently configured SFTP_BASE_PATH: {config.SFTP_BASE_PATH!r}\n\n"
        + "\n".join(lines)
    )
    log("Listing complete, sending it by email")
    notify.send_summary(f"Dojo clips [{commit}]: SFTP tree listing", body)
    return 0


# ---- per-clip publish state (persisted on the SFTP host) ----

def _state_path(directory: str, base: str) -> str:
    return posixpath.join(directory, base + ".state.json")


def _load_state(folder: WatchFolder, base: str) -> dict:
    raw = folder.read_text(_state_path(config.PENDING_DIR, base))
    if raw is None:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def _save_state(folder: WatchFolder, base: str, state: dict) -> None:
    folder.write_text(
        _state_path(config.PENDING_DIR, base), json.dumps(state, indent=2)
    )


def _move_state(folder: WatchFolder, base: str, dest_dir: str) -> None:
    try:
        folder.move_file(
            _state_path(config.PENDING_DIR, base), _state_path(dest_dir, base)
        )
    except OSError:
        pass  # no state file for this pair yet


# ---- summary email ----

def build_summary(
    posted: list[dict],
    previewed: list[dict],
    queued: list[str],
    rejected: list[tuple[str, str]],
    errors: list[str],
    waiting: list[str],
    schedule: dict,
    commit: str = "unknown",
) -> tuple[str, str]:
    parts = []
    if posted:
        parts.append(f"{len(posted)} posted")
    if previewed:
        parts.append(f"{len(previewed)} previewed")
    if queued:
        parts.append(f"{len(queued)} queued")
    if rejected:
        parts.append(f"{len(rejected)} rejected")
    if errors:
        parts.append(f"{len(errors)} error(s)")
    subject = f"Dojo clips [{commit}]: " + ", ".join(parts)

    lines = []
    if posted:
        lines.append("Posted to both platforms (moved to done/):")
        for item in posted:
            lines.append(f"  {item['base']}.mp4")
            lines.append(f"    YouTube: {item['youtube']['url']}")
            ig = item["instagram"]
            lines.append(f"    Instagram: {ig.get('permalink') or 'media id ' + ig['id']}")
        lines.append("")
    if queued:
        lines.append("New clips validated and queued (posting on schedule,")
        lines.append(f"one per {config.POST_INTERVAL_HOURS:g}h, oldest first):")
        for base in queued:
            lines.append(f"  {base}.mp4")
        lines.append("")
    if previewed:
        lines.append("Preview, publish disabled (left in pending/). This clip")
        lines.append("would post next; its generated copy:")
        lines.append("")
        for item in previewed:
            copy = item["copy"]
            lines.append(f"  {item['base']}.mp4")
            lines.append(f'    description: "{item["description"]}"')
            lines.append("    Instagram caption:")
            lines.append(f"      {copy['instagram_caption']}")
            lines.append("    YouTube title:")
            lines.append(f"      {copy['youtube_title']}")
            lines.append("    YouTube description:")
            lines.append(f"      {copy['youtube_description']}")
            if copy.get("youtube_tags"):
                lines.append("    YouTube tags:")
                lines.append(f"      {', '.join(copy['youtube_tags'])}")
            lines.append("")
    if rejected:
        lines.append("Rejected (moved to rejected/):")
        for base, reasons in rejected:
            lines.append(f"  {base}.mp4: {reasons}")
        lines.append("")
    if errors:
        lines.append("Errors (pairs left in pending/, will retry next run without")
        lines.append("double-posting anything that already succeeded):")
        for err in errors:
            lines.append(f"  {err}")
        lines.append("")
    if waiting:
        lines.append(
            f"Queue status: {len(waiting)} clip(s) waiting in pending/"
            + _next_post_hint(schedule)
        )
        lines.append("")
    return subject, "\n".join(lines)


if __name__ == "__main__":
    sys.exit(run())
