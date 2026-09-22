"""Per-license rate limiting for /api/send-whatsapp-receipt/.

There's no rate-limiting middleware anywhere else in this project, so
this is a small, self-contained check backed by WhatsAppSendLog rather
than an in-process counter -- Render can run multiple gunicorn workers
(and the process restarts on every deploy), so an in-memory counter
would silently under-count and let a compromised license key burn
through far more than the intended limit. Querying the shared database
is the only counter that's actually correct across workers/restarts.
"""

from datetime import timedelta

from django.utils import timezone

from .models import WhatsAppSendLog

# One compromised license key sending WhatsApp messages to arbitrary
# phone numbers through this account's Meta credentials is the threat
# this guards against -- 100/hour is generous for any real gym's receipt
# volume while still bounding the damage.
RATE_LIMIT_PER_HOUR = 100


def is_rate_limited(license_obj):
    cutoff = timezone.now() - timedelta(hours=1)
    count = WhatsAppSendLog.objects.filter(license=license_obj, created_at__gte=cutoff).count()
    return count >= RATE_LIMIT_PER_HOUR
