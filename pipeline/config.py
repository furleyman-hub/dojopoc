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


# SFTP. SFTP_BASE_PATH is the absolute path to the socialClips watch
# folder as seen from the SFTP login (the account is not chrooted; for
# the POC, confirmed via FileZilla, it is
# /var/www/html/ju/julianfox.com/dojopoc/socialClips). Required with no
# default so the POC to dojo transition can never silently run against
# the wrong account's folders because a variable was forgotten.
# IMPORTANT: only rstrip trailing slashes, never strip a leading "/",
# an absolute path with the leading slash removed silently becomes a
# relative one and resolves against whatever directory the SFTP login
# defaults into.
SFTP_HOST = require_env("SFTP_HOST")
SFTP_USER = require_env("SFTP_USER")
SFTP_PASS = require_env("SFTP_PASS")
SFTP_PORT = int(os.environ.get("SFTP_PORT", "22"))
SFTP_BASE_PATH = require_env("SFTP_BASE_PATH").rstrip("/")

PENDING_DIR = posixpath.join(SFTP_BASE_PATH, "pending")
DONE_DIR = posixpath.join(SFTP_BASE_PATH, "done")
REJECTED_DIR = posixpath.join(SFTP_BASE_PATH, "rejected")

# Posting throttle for rolling schedules: at most one clip publishes per
# this many hours (24 = one per day). 0 disables the throttle and posts
# every valid clip as soon as it's found. Clips beyond the limit stay
# queued in pending/ (validated once on arrival, marked in their state
# file) and post oldest-first on later runs. The last-posted timestamp
# persists on the SFTP host (schedule_state.json) since Railway
# containers are ephemeral.
_raw_interval = os.environ.get("POST_INTERVAL_HOURS", "24").strip() or "24"
try:
    POST_INTERVAL_HOURS = float(_raw_interval)
except ValueError:
    raise RuntimeError(
        f"POST_INTERVAL_HOURS must be a number of hours, got {_raw_interval!r}"
    )
SCHEDULE_STATE_FILE = posixpath.join(SFTP_BASE_PATH, "schedule_state.json")

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
# server-side from a URL; it cannot be pushed as bytes). Required with
# no default for the same reason as SFTP_BASE_PATH: the dojo redeploy
# gets its own domain, and forgetting this variable should fail loudly
# rather than serve clips from the POC subdomain.
PUBLIC_CLIP_BASE_URL = require_env("PUBLIC_CLIP_BASE_URL").rstrip("/")

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
#   YT_CLIENT_ID, YT_CLIENT_SECRET (or YT_CLIENT_SECRET_JSON), YT_REFRESH_TOKEN,
#   IG_ACCESS_TOKEN, IG_USER_ID

# Validation thresholds (spec section 5)
MAX_DURATION_SECONDS = 90
MAX_FILE_MB = 500
TARGET_ASPECT = 9 / 16          # width / height for vertical video
ASPECT_TOLERANCE = 0.10         # relative tolerance
