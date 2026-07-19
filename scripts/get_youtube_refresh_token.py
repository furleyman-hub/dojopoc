"""One-time local script to obtain a YouTube refresh token.

Run this on your own machine (not Railway). It opens a browser window for
Google sign-in, then prints the refresh token. Paste that value into the
Railway env var YT_REFRESH_TOKEN (and your local .env).

IMPORTANT: before running this, publish the OAuth consent screen to
Production in Google Cloud Console (APIs and Services > OAuth consent
screen > Publish app). It stays unverified, which is fine for own-account
use. If the app is left in Testing mode, Google expires refresh tokens
after about 7 days, which defeats the purpose.

Usage (PowerShell):
    python scripts\get_youtube_refresh_token.py C:\path\to\client_secret.json
"""

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

# The pipeline only needs upload.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/get_youtube_refresh_token.py <path to client_secret.json>")
        sys.exit(1)

    client_secret_path = sys.argv[1]
    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)

    # access_type=offline is what yields a refresh token.
    # prompt=consent forces Google to issue a new one even if this account
    # already authorized the app before (otherwise refresh_token comes back
    # empty on repeat runs).
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        print("ERROR: Google did not return a refresh token.")
        print("Revoke the app's access at https://myaccount.google.com/permissions")
        print("and run this script again.")
        sys.exit(1)

    print()
    print("Success. Set this in Railway (and your local .env):")
    print()
    print(f"YT_REFRESH_TOKEN={creds.refresh_token}")
    print()
    print("Keep it secret. Do not commit it.")


if __name__ == "__main__":
    main()
