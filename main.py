"""Dojo social pipeline, scheduled entry point.

Current stage (build order step 4): SFTP scan, download, ffprobe validation,
move rejects, caption generation, email summary. Publishing (YouTube,
Instagram) is not built yet, so valid clips are reported (with their
generated copy, as a preview) but LEFT IN pending/. Once publishing lands,
valid clips will move to done/ only after both platforms confirm.
"""

import sys
import tempfile
import traceback
from datetime import datetime, timezone

from pipeline import config, notify
from pipeline.captions import generate_captions
from pipeline.sftp_client import WatchFolder
from pipeline.validate import validate_video


def log(msg: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{stamp}] {msg}", flush=True)


def run() -> int:
    valid: list[dict] = []                 # {base, description, copy}
    rejected: list[tuple[str, str]] = []   # (base, reasons)
    errors: list[str] = []

    log("Pipeline run starting")
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
                    if ok:
                        log(f"  valid: {base}, generating captions")
                        copy = generate_captions(description)
                        log(f"  captions ready for {base}")
                        valid.append(
                            {"base": base, "description": description, "copy": copy}
                        )
                        # Publishing not built yet: leave the pair in pending/
                        # so a later run picks it up once publishing exists.
                    else:
                        reasons = "; ".join(problems)
                        log(f"  rejected: {base} ({reasons})")
                        folder.move_pair(base, config.REJECTED_DIR)
                        rejected.append((base, reasons))
                except Exception:
                    err = f"{base}: {traceback.format_exc(limit=3)}"
                    log(f"  ERROR on {err}")
                    errors.append(err)
    except Exception:
        err = traceback.format_exc(limit=5)
        log(f"FATAL: {err}")
        errors.append(f"Run failed before processing completed:\n{err}")

    if valid or rejected or errors:
        try:
            notify.send_summary(*build_summary(valid, rejected, errors))
            log("Summary email sent")
        except Exception:
            log(f"Could not send summary email: {traceback.format_exc(limit=3)}")
    else:
        log("Nothing to do, no email sent")

    log("Pipeline run finished")
    return 1 if errors else 0


def build_summary(
    valid: list[dict],
    rejected: list[tuple[str, str]],
    errors: list[str],
) -> tuple[str, str]:
    parts = []
    if valid:
        parts.append(f"{len(valid)} valid")
    if rejected:
        parts.append(f"{len(rejected)} rejected")
    if errors:
        parts.append(f"{len(errors)} error(s)")
    subject = "Dojo clips: " + ", ".join(parts)

    lines = []
    if valid:
        lines.append("Valid clips (publishing not built yet, left in pending/).")
        lines.append("Generated copy below is a preview of what will post:")
        lines.append("")
        for item in valid:
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
        lines.append("Errors:")
        for err in errors:
            lines.append(f"  {err}")
        lines.append("")
    return subject, "\n".join(lines)


if __name__ == "__main__":
    sys.exit(run())
