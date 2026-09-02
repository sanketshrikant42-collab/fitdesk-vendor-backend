"""
Telegram notification helper.

Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in settings.py (see README.md
for how to get these from @BotFather) to get an instant push notification
on your phone whenever a gym owner raises a query — in addition to email.

If either setting is missing, this silently does nothing, so it's safe to
leave unconfigured.
"""

import urllib.request
import urllib.parse
import json

from django.conf import settings


def send_telegram_notification(text):
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(settings, "TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }).encode("utf-8")

    try:
        request = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
            return bool(body.get("ok"))
    except Exception:
        # A failed Telegram push should never block the query from saving,
        # or take down the request if email notification already worked.
        return False
