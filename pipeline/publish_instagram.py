"""Instagram Reels publishing via the Graph API (spec section 6.3).

Two-step container flow: create a media container that points at the clip's
public URL on the web host, poll until Meta finishes processing it, then
publish. Meta fetches the video server-side, so pending/ must be
web-readable at PUBLIC_CLIP_BASE_URL.
"""

import time
import urllib.parse

import requests

from pipeline import config

GRAPH = "https://graph.instagram.com"

POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 600


def publish_to_instagram(base: str, caption: str, access_token: str) -> dict:
    """Publish pending/<base>.mp4 as a Reel. Returns {"id", "permalink"}.
    Raises on failure.
    """
    ig_user_id = config.require_env("IG_USER_ID")
    video_url = f"{config.PUBLIC_CLIP_BASE_URL}/{urllib.parse.quote(base)}.mp4"

    container_id = _create_container(ig_user_id, video_url, caption, access_token)
    _wait_until_finished(container_id, access_token)
    media_id = _publish_container(ig_user_id, container_id, access_token)
    permalink = _get_permalink(media_id, access_token)
    return {"id": media_id, "permalink": permalink}


def _create_container(ig_user_id, video_url, caption, access_token) -> str:
    payload = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "access_token": access_token,
    }
    if config.IG_LOCATION_ID:
        payload["location_id"] = config.IG_LOCATION_ID
        try:
            resp = requests.post(f"{GRAPH}/{ig_user_id}/media", data=payload, timeout=60)
            return _checked(resp, "create media container (with location)")["id"]
        except RuntimeError as exc:
            # A wrong or unsupported location id must never block a post.
            # Log, drop the tag, and fall through to the plain call.
            print(
                f"Instagram location tagging failed (IG_LOCATION_ID="
                f"{config.IG_LOCATION_ID!r}), posting without it: {exc}",
                flush=True,
            )
            del payload["location_id"]

    resp = requests.post(f"{GRAPH}/{ig_user_id}/media", data=payload, timeout=60)
    return _checked(resp, "create media container")["id"]


def _wait_until_finished(container_id, access_token) -> None:
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while True:
        resp = requests.get(
            f"{GRAPH}/{container_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=60,
        )
        status = _checked(resp, "poll container status").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram container {container_id} ended in {status}")
        if time.time() > deadline:
            raise RuntimeError(
                f"Instagram container {container_id} still {status} after "
                f"{POLL_TIMEOUT_SECONDS}s"
            )
        time.sleep(POLL_INTERVAL_SECONDS)


def _publish_container(ig_user_id, container_id, access_token) -> str:
    resp = requests.post(
        f"{GRAPH}/{ig_user_id}/media_publish",
        data={"creation_id": container_id, "access_token": access_token},
        timeout=60,
    )
    return _checked(resp, "publish media container")["id"]


def _get_permalink(media_id, access_token) -> str:
    # Best effort; the post is live even if this lookup fails.
    try:
        resp = requests.get(
            f"{GRAPH}/{media_id}",
            params={"fields": "permalink", "access_token": access_token},
            timeout=60,
        )
        return _checked(resp, "fetch permalink").get("permalink", "")
    except Exception:
        return ""


def _checked(resp: requests.Response, action: str) -> dict:
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        raise RuntimeError(
            f"Instagram API error during {action}: "
            f"HTTP {resp.status_code}, {err.get('message', resp.text[:300])} "
            f"(code {err.get('code')})"
        )
    return data
