"""YouTube upload (Data API v3), non-interactive.

Authenticates with the Desktop OAuth client plus the refresh token minted
once by scripts/get_youtube_refresh_token.py. A vertical video under 3
minutes is automatically classified as a Short; no special endpoint.

Client credentials: prefer YT_CLIENT_ID + YT_CLIENT_SECRET as two plain
values, copied directly from Google Cloud Console (Credentials > OAuth 2.0
Client IDs > your Desktop client shows these as two separate fields). This
is far less error-prone than YT_CLIENT_SECRET_JSON (the full downloaded
client_secret.json content, or a path to that file), which is still
accepted for anyone who prefers it, but invites exactly the mistake of
pasting just the "Client secret" field's bare value into it.
"""

import json
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from pipeline import config

TOKEN_URI = "https://oauth2.googleapis.com/token"


def publish_to_youtube(video_path: str, title: str, description: str) -> dict:
    """Upload one clip. Returns {"id", "url"}. Raises on failure."""
    youtube = build("youtube", "v3", credentials=_credentials(), cache_discovery=False)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": config.YT_CATEGORY_ID,
        },
        "status": {
            "privacyStatus": config.YT_PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = request.next_chunk()

    video_id = response["id"]
    return {"id": video_id, "url": f"https://youtube.com/shorts/{video_id}"}


def _credentials() -> Credentials:
    client_id, client_secret = _client_pair()
    return Credentials(
        token=None,
        refresh_token=config.require_env("YT_REFRESH_TOKEN"),
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
    )


def _client_pair() -> tuple[str, str]:
    """(client_id, client_secret) for the YouTube OAuth Desktop client.

    Preferred: YT_CLIENT_ID + YT_CLIENT_SECRET, both plain values. Falls
    back to YT_CLIENT_SECRET_JSON (inline JSON or a file path) if those
    aren't set.
    """
    client_id = os.environ.get("YT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YT_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return client_id, client_secret

    raw = os.environ.get("YT_CLIENT_SECRET_JSON", "").strip()
    if not raw:
        raise RuntimeError(
            "No YouTube OAuth client configured. Set YT_CLIENT_ID and "
            "YT_CLIENT_SECRET (copy these two values separately from Google "
            "Cloud Console: Credentials > OAuth 2.0 Client IDs > your "
            "Desktop client), or set YT_CLIENT_SECRET_JSON to the full "
            "downloaded client_secret.json content."
        )

    if raw.startswith("GOCSPX-"):
        raise RuntimeError(
            f"YT_CLIENT_SECRET_JSON is set to {raw!r}, which looks like just "
            "the 'Client secret' value from Google Cloud Console, not the "
            "full client_secret.json content or a file path. Set "
            "YT_CLIENT_ID and YT_CLIENT_SECRET instead (two plain values, "
            "copied separately from Credentials > OAuth 2.0 Client IDs > "
            "your Desktop client)."
        )

    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"YT_CLIENT_SECRET_JSON is not valid JSON: {exc}") from exc
    else:
        if not os.path.exists(raw):
            raise RuntimeError(
                f"YT_CLIENT_SECRET_JSON is set to {raw!r}, which is neither "
                "JSON (it doesn't start with '{') nor a file that exists in "
                "this container. Set YT_CLIENT_ID and YT_CLIENT_SECRET "
                "instead (see .env.example)."
            )
        with open(raw, encoding="utf-8") as fh:
            data = json.load(fh)
    section = data.get("installed") or data.get("web") or data
    return section["client_id"], section["client_secret"]
