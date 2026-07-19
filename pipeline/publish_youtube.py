"""YouTube upload (Data API v3), non-interactive.

Authenticates with the Desktop OAuth client (YT_CLIENT_SECRET_JSON, a file
path or the inlined JSON) plus the refresh token minted once by
scripts/get_youtube_refresh_token.py. A vertical video under 3 minutes is
automatically classified as a Short; no special endpoint.
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
    """client_id and client_secret from YT_CLIENT_SECRET_JSON, which is
    either the JSON itself or a path to the downloaded client_secret file.
    """
    raw = config.require_env("YT_CLIENT_SECRET_JSON")
    if raw.lstrip().startswith("{"):
        data = json.loads(raw)
    else:
        if not os.path.exists(raw):
            raise RuntimeError(f"YT_CLIENT_SECRET_JSON file not found: {raw}")
        with open(raw, encoding="utf-8") as fh:
            data = json.load(fh)
    section = data.get("installed") or data.get("web") or data
    return section["client_id"], section["client_secret"]
