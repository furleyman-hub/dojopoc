"""Audience-informed posting window (Phase 2 distribution idea #2).

Instagram insights expose online_followers: for each of the last ~30
days, how many of the account's followers were online during each UTC
hour. This module refreshes that data on a weekly cadence, picks the
contiguous AUDIENCE_WINDOW_HOURS-hour block with the highest average
audience, and persists the result on the SFTP host (posting_window.json)
so the scheduler can hold a due clip until the window opens.

Every failure mode degrades to the pre-feature behavior: if the metric
is unavailable (Meta returns nothing under roughly 100 followers), the
token lacks the insights permission, the API errors, or the data is all
zeros, get_posting_window() returns None and "due" means what it always
meant: post at the next cron tick. A failed refresh is retried no more
than once per AUDIENCE_WINDOW_RETRY_HOURS so a broken permission does
not turn into an API call on every 20-minute run.
"""

import json
import traceback
from datetime import datetime, timedelta, timezone

import requests

from pipeline import config

GRAPH = "https://graph.instagram.com"


def get_posting_window(folder, token_getter) -> dict | None:
    """The current posting window, refreshed from the API when stale.

    token_getter is only called when a refresh is actually attempted,
    since obtaining a token may require credentials that a preview-mode
    deployment does not have. Returns the window state dict (with
    start_hour_utc and width_hours) or None, which callers must treat
    as "no restriction".
    """
    if not config.AUDIENCE_WINDOW_ENABLED:
        return None
    state = _load(folder)
    if _needs_refresh(state):
        state = _refresh(folder, state, token_getter)
    if isinstance(state.get("start_hour_utc"), int):
        return state
    return None


def in_window(window: dict | None, now: datetime | None = None) -> bool:
    """True if now falls inside the window (or there is no window)."""
    if not window or not isinstance(window.get("start_hour_utc"), int):
        return True
    now = now or datetime.now(timezone.utc)
    start = window["start_hour_utc"] % 24
    width = int(window.get("width_hours") or config.AUDIENCE_WINDOW_HOURS)
    if width >= 24:
        return True
    return (now.hour - start) % 24 < width


def describe(window: dict | None) -> str:
    """Short human-readable form for logs and emails, or ''."""
    if not window or not isinstance(window.get("start_hour_utc"), int):
        return ""
    start = window["start_hour_utc"] % 24
    width = int(window.get("width_hours") or config.AUDIENCE_WINDOW_HOURS)
    end = (start + width) % 24
    return f"{start:02d}:00-{end:02d}:00 UTC"


def compute_window(hourly_avg: dict[int, float], width: int) -> int | None:
    """Start hour (UTC) of the contiguous `width`-hour block with the
    highest total average audience, wrapping around midnight. None when
    there is no signal at all (empty or all-zero data).
    """
    if not hourly_avg or width <= 0:
        return None
    totals = [float(hourly_avg.get(h, 0.0)) for h in range(24)]
    if not any(totals):
        return None
    best_start, best_sum = 0, float("-inf")
    for start in range(24):
        s = sum(totals[(start + i) % 24] for i in range(width))
        if s > best_sum:
            best_start, best_sum = start, s
    return best_start


def fetch_hourly_averages(ig_user_id: str, access_token: str) -> dict[int, float]:
    """Average follower-online count per UTC hour from the
    online_followers insight (one value dict per day, hour keys as
    strings). Raises on API or shape errors; the caller decides how to
    degrade.
    """
    resp = requests.get(
        f"{GRAPH}/{ig_user_id}/insights",
        params={
            "metric": "online_followers",
            "period": "lifetime",
            "access_token": access_token,
        },
        timeout=60,
    )
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        raise RuntimeError(
            "Instagram API error fetching online_followers: "
            f"HTTP {resp.status_code}, {err.get('message', resp.text[:300])} "
            f"(code {err.get('code')})"
        )

    sums = [0.0] * 24
    days = 0
    for series in data.get("data", []):
        for value in series.get("values", []):
            by_hour = value.get("value")
            if not isinstance(by_hour, dict):
                continue
            days += 1
            for hour_key, count in by_hour.items():
                try:
                    hour = int(hour_key)
                except (TypeError, ValueError):
                    continue
                if 0 <= hour < 24 and isinstance(count, (int, float)):
                    sums[hour] += count
    if days == 0:
        return {}
    return {h: sums[h] / days for h in range(24)}


# ---- persistence and refresh cadence ----

def _load(folder) -> dict:
    raw = folder.read_text(config.AUDIENCE_WINDOW_FILE)
    if raw is None:
        return {}
    try:
        state = json.loads(raw)
    except ValueError:
        return {}
    return state if isinstance(state, dict) else {}


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


def _needs_refresh(state: dict) -> bool:
    now = datetime.now(timezone.utc)
    attempted = _parse_ts(state.get("last_attempt_at"))
    if attempted and now - attempted < timedelta(hours=config.AUDIENCE_WINDOW_RETRY_HOURS):
        return False
    updated = _parse_ts(state.get("updated_at"))
    if updated is None:
        return True
    return now - updated >= timedelta(days=config.AUDIENCE_WINDOW_REFRESH_DAYS)


def _refresh(folder, state: dict, token_getter) -> dict:
    """Attempt one refresh. On success the window fields are replaced;
    on failure the previous window (if any) is kept and the error is
    recorded, so a temporary API problem never discards a working
    window and a permanent one is visible in posting_window.json.
    """
    now = datetime.now(timezone.utc).isoformat()
    state["last_attempt_at"] = now
    try:
        ig_user_id = config.require_env("IG_USER_ID")
        hourly = fetch_hourly_averages(ig_user_id, token_getter())
        start = compute_window(hourly, config.AUDIENCE_WINDOW_HOURS)
        if start is None:
            raise RuntimeError(
                "online_followers returned no usable data (accounts under "
                "roughly 100 followers get none)"
            )
        state.update(
            start_hour_utc=start,
            width_hours=config.AUDIENCE_WINDOW_HOURS,
            hourly_avg={str(h): round(v, 2) for h, v in hourly.items()},
            updated_at=now,
        )
        state.pop("last_error", None)
        print(
            f"Audience window refreshed from online_followers: "
            f"{describe(state)}",
            flush=True,
        )
    except Exception:
        state["last_error"] = traceback.format_exc(limit=2)
        print(
            "Audience window refresh failed (keeping "
            + (f"previous window {describe(state)}" if "start_hour_utc" in state
               else "no window, posting unrestricted")
            + f"), will retry in {config.AUDIENCE_WINDOW_RETRY_HOURS}h:\n"
            + state["last_error"],
            flush=True,
        )
    try:
        folder.write_text(config.AUDIENCE_WINDOW_FILE, json.dumps(state, indent=2))
    except Exception:
        print(
            f"Could not persist posting_window.json: {traceback.format_exc(limit=2)}",
            flush=True,
        )
    return state
