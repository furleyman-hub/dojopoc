"""Summary email via Resend. Plain and direct, same style as the other
Railway monitor scripts.
"""

import requests

from pipeline import config

RESEND_URL = "https://api.resend.com/emails"


def send_summary(subject: str, body: str) -> None:
    response = requests.post(
        RESEND_URL,
        headers={
            "Authorization": f"Bearer {config.RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": config.NOTIFY_FROM,
            "to": [config.NOTIFY_EMAIL],
            "subject": subject,
            "text": body,
        },
        timeout=30,
    )
    response.raise_for_status()
