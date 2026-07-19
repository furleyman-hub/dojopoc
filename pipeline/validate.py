"""Authoritative video validation with ffprobe (spec section 5).

The browser already checks at upload time, but those checks can be bypassed,
so the pipeline re-validates every clip before anything is published.

Requires ffprobe on PATH (ffmpeg package; see nixpacks.toml for Railway).
"""

import json
import os
import subprocess

from pipeline import config


class FFprobeNotFoundError(RuntimeError):
    """ffprobe itself is missing, an environment problem, not a bad video.
    Raised instead of returned so the caller doesn't reject the clip: a
    missing binary means every clip would fail, and rejecting valid clips
    over an environment problem would move them out of pending/ for good.
    """


def validate_video(path: str) -> tuple[bool, list[str]]:
    """Check one local MP4 against the agreed thresholds.
    Returns (ok, problems). problems is human-readable, for the email.
    Raises FFprobeNotFoundError if ffprobe itself isn't available.
    """
    problems: list[str] = []

    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > config.MAX_FILE_MB:
        problems.append(f"file is {size_mb:.0f} MB (limit {config.MAX_FILE_MB} MB)")

    try:
        probe = _ffprobe(path)
    except FFprobeNotFoundError:
        raise
    except Exception as exc:
        problems.append(f"ffprobe could not read the file ({exc})")
        return False, problems

    fmt = probe.get("format", {})
    format_name = fmt.get("format_name", "")
    if "mp4" not in format_name:
        problems.append(f"container is not MP4 (ffprobe says: {format_name or 'unknown'})")

    try:
        duration = float(fmt.get("duration", 0))
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        problems.append("could not read duration")
    elif duration > config.MAX_DURATION_SECONDS:
        problems.append(
            f"duration is {duration:.0f}s (limit {config.MAX_DURATION_SECONDS}s)"
        )

    width, height = _display_dimensions(probe)
    if not width or not height:
        problems.append("could not read video dimensions")
    else:
        aspect = width / height
        off_target = abs(aspect - config.TARGET_ASPECT) / config.TARGET_ASPECT
        if height <= width or off_target > config.ASPECT_TOLERANCE:
            problems.append(f"not vertical 9:16 (got {width}x{height})")

    return (len(problems) == 0), problems


def _ffprobe(path: str) -> dict:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise FFprobeNotFoundError(
            "ffprobe is not installed or not on PATH (check the Railway build "
            "actually installed ffmpeg; see nixpacks.toml)"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"exit code {result.returncode}")
    return json.loads(result.stdout)


def _display_dimensions(probe: dict) -> tuple[int, int]:
    """Width and height as displayed, accounting for phone rotation metadata
    (a clip stored 1920x1080 with a 90 degree rotation displays as 1080x1920).
    """
    for stream in probe.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)

        rotation = 0
        tags = stream.get("tags") or {}
        try:
            rotation = int(float(tags.get("rotate", 0)))
        except (TypeError, ValueError):
            rotation = 0
        for side_data in stream.get("side_data_list") or []:
            if "rotation" in side_data:
                try:
                    rotation = int(float(side_data["rotation"]))
                except (TypeError, ValueError):
                    pass

        if rotation % 180 != 0:
            width, height = height, width
        return width, height
    return 0, 0
