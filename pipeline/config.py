"""Configuration for the pipeline. All account-specific values come from
environment variables (Railway env vars in production, a local .env for dev)
so the same code can later be redeployed for the dojo's real accounts.
"""

import os
import posixpath

try:
    from dotenv import load_dotenv

    load_dotenv()  # local runs read .env; on Railway this finds nothing
except ImportError:
    pass


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


# SFTP. The account is NOT jailed to socialClips/ itself, it lands at the
# web host account root (verified July 2026: SFTP for julianfox.com lands
# at the account root, and the real path down to the watch folder is
# dojopoc/socialClips). SFTP_BASE_PATH is that path from the SFTP login
# root down to (and including) socialClips/, so it becomes config, not a
# hardcoded assumption, since the dojo's real account will have its own
# subdomain folder name here.
SFTP_HOST = require_env("SFTP_HOST")
SFTP_USER = require_env("SFTP_USER")
SFTP_PASS = require_env("SFTP_PASS")
SFTP_PORT = int(os.environ.get("SFTP_PORT", "22"))
SFTP_BASE_PATH = os.environ.get("SFTP_BASE_PATH", "dojopoc/socialClips").strip().strip("/")

PENDING_DIR = posixpath.join(SFTP_BASE_PATH, "pending")
DONE_DIR = posixpath.join(SFTP_BASE_PATH, "done")
REJECTED_DIR = posixpath.join(SFTP_BASE_PATH, "rejected")

# Notifications (Resend)
RESEND_API_KEY = require_env("RESEND_API_KEY")
NOTIFY_EMAIL = require_env("NOTIFY_EMAIL")
NOTIFY_FROM = os.environ.get("NOTIFY_FROM", "onboarding@resend.dev").strip()

# Caption generation (Anthropic API)
ANTHROPIC_API_KEY = require_env("ANTHROPIC_API_KEY")
CAPTION_MODEL = os.environ.get("CAPTION_MODEL", "claude-opus-4-8").strip()
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAND_VOICE_FILE = os.environ.get("BRAND_VOICE_FILE", "").strip() or os.path.join(
    _REPO_ROOT, "brand_voice.txt"
)

# Publishing. PUBLISH_ENABLED=false runs everything except the actual
# posting (validation + captions + email preview), useful while testing
# the plumbing or before all credentials are in place.
PUBLISH_ENABLED = os.environ.get("PUBLISH_ENABLED", "true").strip().lower() not in (
    "false", "0", "no", "off",
)

# Public HTTPS base for clips in pending/ (Instagram fetches the video
# server-side from a URL; it cannot be pushed as bytes)
PUBLIC_CLIP_BASE_URL = os.environ.get(
    "PUBLIC_CLIP_BASE_URL", "https://dojopoc.julianfox.com/socialClips/pending"
).strip().rstrip("/")

# YouTube. Test uploads stay private until final testing (spec section 3);
# flip YT_PRIVACY_STATUS to "public" when ready.
YT_PRIVACY_STATUS = os.environ.get("YT_PRIVACY_STATUS", "private").strip()
YT_CATEGORY_ID = os.environ.get("YT_CATEGORY_ID", "17").strip()  # 17 = Sports

# Instagram token refresh cadence (spec section 6.1: weekly is plenty;
# token must be at least 24h old to refresh)
IG_TOKEN_FILE = posixpath.join(SFTP_BASE_PATH, "ig_token.json")  # persisted on the SFTP host
IG_REFRESH_INTERVAL_DAYS = 7

# Credentials that are only needed when actually publishing are validated
# at use time with require_env(), not at import, so the pipeline can run
# with PUBLISH_ENABLED=false before every credential exists:
#   YT_CLIENT_SECRET_JSON, YT_REFRESH_TOKEN, IG_ACCESS_TOKEN, IG_USER_ID

# Validation thresholds (spec section 5)
MAX_DURATION_SECONDS = 90
MAX_FILE_MB = 500
TARGET_ASPECT = 9 / 16          # width / height for vertical video
ASPECT_TOLERANCE = 0.10         # relative tolerance
