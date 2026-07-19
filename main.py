"""Dojo social pipeline, scheduled entry point. One run:

1. connect to the web host over SFTP and list complete pairs in pending/
2. download and re-validate each video with ffprobe; failures move to
   rejected/ and are reported
3. generate platform copy from the description (Anthropic API)
4. publish to YouTube and Instagram; a pair moves to done/ only after
   BOTH platforms confirm
5. send one plain-text Resend summary

Partial failure handling: publish progress is persisted per clip in
pending/<base>.state.json on the host (generated copy, YouTube result,
Instagram result). A later run resumes from that state, so a platform
that already succeeded is never posted twice, and the pair stays in
pending/ until both have succeeded.

PUBLISH_ENABLED=false skips step 4: clips are validated and their copy
is previewed in the email, but nothing posts and nothing moves to done/.

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
from datetime import datetime, timezone

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


def run() -> int:
    if os.environ.get("SFTP_DEBUG_LIST_TREE", "").strip().lower() in ("true", "1", "yes", "on"):
        return _debug_list_tree()

    posted: list[dict] = []      # {base, youtube, instagram}
    previewed: list[dict] = []   # {base, description, copy} (publish disabled)
    rejected: list[tuple[str, str]] = []   # (base, reasons)
    errors: list[str] = []
    ig_token = None

    log("Pipeline run starting")
    if not config.PUBLISH_ENABLED:
        log("PUBLISH_ENABLED=false: preview mode, nothing will post")

    try:
        with WatchFolder() as folder, tempfile.TemporaryDirectory() as workdir:
            pairs = folder.list_pending_pairs()
            log(f"Found {len(pairs)} complete pair(s) in pending/")

            for base in pairs:
                try:
                    log(f"Processing {base}")
                    video_path, text_path = folder.download_pair(base, workdir)
                    with open(text_path, encoding="utf-8", errors="replace") as fh:
                        description = fh.read().strip()

                    ok, problems = validate_video(video_path)
                    if not ok:
                        reasons = "; ".join(problems)
                        log(f"  rejected: {base} ({reasons})")
                        folder.move_pair(base, config.REJECTED_DIR)
                        _move_state(folder, base, config.REJECTED_DIR)
                        rejected.append((base, reasons))
                        continue

                    state = _load_state(folder, base)

                    if "copy" not in state:
                        log(f"  generating captions for {base}")
                        state["copy"] = generate_captions(description)
                        if config.PUBLISH_ENABLED:
                            _save_state(folder, base, state)
                    copy = state["copy"]

                    if not config.PUBLISH_ENABLED:
                        previewed.append(
                            {"base": base, "description": description, "copy": copy}
                        )
                        continue

                    if "youtube" not in state:
                        log(f"  uploading {base} to YouTube")
                        state["youtube"] = publish_to_youtube(
                            video_path,
                            copy["youtube_title"],
                            copy["youtube_description"],
                        )
                        _save_state(folder, base, state)
                        log(f"  YouTube done: {state['youtube']['url']}")
                    else:
                        log(f"  YouTube already done for {base} (from state)")

                    if "instagram" not in state:
                        if ig_token is None:
                            ig_token = get_ig_access_token(folder)
                        log(f"  publishing {base} to Instagram")
                        state["instagram"] = publish_to_instagram(
                            base, copy["instagram_caption"], ig_token
                        )
                        _save_state(folder, base, state)
                        log(f"  Instagram done: {state['instagram']['id']}")
                    else:
                        log(f"  Instagram already done for {base} (from state)")

                    # Both platforms confirmed: only now leave pending/.
                    folder.move_pair(base, config.DONE_DIR)
                    _move_state(folder, base, config.DONE_DIR)
                    posted.append(
                        {
                            "base": base,
                            "youtube": state["youtube"],
                            "instagram": state["instagram"],
                        }
                    )
                    log(f"  {base} fully published, moved to done/")
                except Exception:
                    err = f"{base}: {traceback.format_exc(limit=3)}"
                    log(f"  ERROR on {err}")
                    errors.append(err)
    except Exception:
        err = traceback.format_exc(limit=5)
        log(f"FATAL: {err}")
        errors.append(f"Run failed before processing completed:\n{err}")

    if posted or previewed or rejected or errors:
        try:
            notify.send_summary(*build_summary(posted, previewed, rejected, errors))
            log("Summary email sent")
        except Exception:
            log(f"Could not send summary email: {traceback.format_exc(limit=3)}")
    else:
        log("Nothing to do, no email sent")

    log("Pipeline run finished")
    return 1 if errors else 0


def _debug_list_tree() -> int:
    # Defaults to the SFTP login's default directory ("."). Override with
    # SFTP_DEBUG_LIST_PATH (e.g. "/") if that default directory isn't a
    # useful vantage point, without needing another code change/deploy.
    start_path = os.environ.get("SFTP_DEBUG_LIST_PATH", ".").strip() or "."
    log(
        "SFTP_DEBUG_LIST_TREE=true: listing "
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
                "Dojo clips: SFTP tree listing FAILED",
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
    notify.send_summary("Dojo clips: SFTP tree listing", body)
    return 0


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


def build_summary(
    posted: list[dict],
    previewed: list[dict],
    rejected: list[tuple[str, str]],
    errors: list[str],
) -> tuple[str, str]:
    parts = []
    if posted:
        parts.append(f"{len(posted)} posted")
    if previewed:
        parts.append(f"{len(previewed)} previewed")
    if rejected:
        parts.append(f"{len(rejected)} rejected")
    if errors:
        parts.append(f"{len(errors)} error(s)")
    subject = "Dojo clips: " + ", ".join(parts)

    lines = []
    if posted:
        lines.append("Posted to both platforms (moved to done/):")
        for item in posted:
            lines.append(f"  {item['base']}.mp4")
            lines.append(f"    YouTube: {item['youtube']['url']}")
            ig = item["instagram"]
            lines.append(f"    Instagram: {ig.get('permalink') or 'media id ' + ig['id']}")
        lines.append("")
    if previewed:
        lines.append("Validated, publish disabled (left in pending/).")
        lines.append("Generated copy preview:")
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
    return subject, "\n".join(lines)


if __name__ == "__main__":
    sys.exit(run())
