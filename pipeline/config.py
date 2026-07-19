"""Configuration for the pipeline. All account-specific values come from
environment variables (Railway env vars in production, a local .env for dev)
so the same code can later be redeployed for the dojo's real accounts.
"""

import os

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


# SFTP (account is jailed to socialClips/ as its home directory, so these
# paths are relative to that home)
SFTP_HOST = require_env("SFTP_HOST")
SFTP_USER = require_env("SFTP_USER")
SFTP_PASS = require_env("SFTP_PASS")
SFTP_PORT = int(os.environ.get("SFTP_PORT", "22"))

PENDING_DIR = "pending"
DONE_DIR = "done"
REJECTED_DIR = "rejected"

# Notifications (Resend)
RESEND_API_KEY = require_env("RESEND_API_KEY")
NOTIFY_EMAIL = require_env("NOTIFY_EMAIL")
NOTIFY_FROM = os.environ.get("NOTIFY_FROM", "onboarding@resend.dev").strip()

# Validation thresholds (spec section 5)
MAX_DURATION_SECONDS = 90
MAX_FILE_MB = 500
TARGET_ASPECT = 9 / 16          # width / height for vertical video
ASPECT_TOLERANCE = 0.10         # relative tolerance
