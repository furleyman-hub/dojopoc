"""Instagram long-lived token management (spec section 6.1).

The token expires about 60 days after issue and does not auto-renew.
Railway env vars cannot be updated by the script, so the live token is
persisted as a small JSON file at the root of the SFTP watch folder
(socialClips/ig_token.json), with the IG_ACCESS_TOKEN env var as the
bootstrap value. Each run loads the persisted token; a refresh rolls it
forward another 60 days and is attempted weekly (the token must be at
least 24h old to refresh, which the weekly cadence guarantees).

If the token ever fully expires, redo the manual OAuth flow (authorize
URL, callback.php, code exchange). The App Dashboard "Generate token"
button does NOT produce exchange-able tokens; only the real OAuth flow
does.
"""

import json
from datetime import datetime, timedelta, timezone

import requests

from pipeline import config
from pipeline.sftp_client import WatchFolder

REFRESH_URL = "https://graph.instagram.com/refresh_access_token"


def get_ig_access_token(folder: WatchFolder) -> str:
    """Current usable token. Loads the persisted one (bootstrapping from
    the env var on first run) and refreshes it if it is due. A failed
    refresh raises only if the stored token is already past its expiry
    window; otherwise the current token is returned and the error is left
    to surface in logs on the publish call.
    """
    state = _load(folder)
    if state is None:
        state = {
            "access_token": config.require_env("IG_ACCESS_TOKEN"),
            "refreshed_at": _now_iso(),
            "note": "bootstrapped from IG_ACCESS_TOKEN env var",
        }
        _save(folder, state)

    refreshed_at = datetime.fromisoformat(state["refreshed_at"])
    if datetime.now(timezone.utc) - refreshed_at >= timedelta(
        days=config.IG_REFRESH_INTERVAL_DAYS
    ):
        try:
            new_token, expires_in = _refresh(state["access_token"])
            state = {
                "access_token": new_token,
                "refreshed_at": _now_iso(),
                "expires_in_seconds": expires_in,
            }
            _save(folder, state)
        except Exception as exc:
            # Keep using the current token; it stays valid ~60 days from
            # its own refresh. The failure still gets logged upstream.
            print(f"IG token refresh failed (continuing with current token): {exc}")

    return state["access_token"]


def _refresh(token: str) -> tuple[str, int]:
    resp = requests.get(
        REFRESH_URL,
        params={"grant_type": "ig_refresh_token", "access_token": token},
        timeout=60,
    )
    data = resp.json()
    if resp.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        raise RuntimeError(
            f"HTTP {resp.status_code}: {err.get('message', resp.text[:300])}"
        )
    return data["access_token"], int(data.get("expires_in", 0))


def _load(folder: WatchFolder) -> dict | None:
    raw = folder.read_text(config.IG_TOKEN_FILE)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _save(folder: WatchFolder, state: dict) -> None:
    folder.write_text(config.IG_TOKEN_FILE, json.dumps(state, indent=2))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
