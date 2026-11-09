"""Daily "did you record today's khoroch?" push reminder.

`send_due()` is called every ~15 minutes by an outside scheduler (see views.cron_reminders). It sends each opted-in
user one reminder per day, once their chosen Bangladesh time has come, unless they already recorded something today.
"""
import datetime as dt
import json
import logging

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from pywebpush import WebPushException, webpush

from .models import Profile, Transaction

logger = logging.getLogger(__name__)

TITLE = "KhorochAi"
BODY = "আজকের খরচ লিখেছেন? KhorochAi তে ১০ সেকেন্ডে লিখে ফেলুন।"
LATE_AFTER = dt.timedelta(hours=3)   # a reminder this long after its time is skipped rather than sent at an odd hour


def _push(subscription):
    """Send to one device. Returns "sent", "gone" (the device unsubscribed: forget it) or "error" (try again later)."""
    try:
        webpush(
            subscription_info={"endpoint": subscription.endpoint,
                               "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}},
            data=json.dumps({"title": TITLE, "body": BODY, "url": "/"}, ensure_ascii=False),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": "mailto:" + (settings.EMAIL_HOST_USER or "noreply@khorochai.vercel.app")},
            ttl=3 * 3600, timeout=10,
        )
        return "sent"
    except WebPushException as e:
        status = getattr(e.response, "status_code", None)
        if status in (404, 410):
            return "gone"
        logger.warning("push failed (%s): %s", status, e)
    except Exception:
        logger.exception("push failed")
    return "error"


def send_due(now=None):
    now = timezone.localtime(now)
    today = now.date()
    counts = {"users": 0, "sent": 0, "removed": 0, "skipped": 0}
    profiles = (Profile.objects.filter(daily_reminder=True, user__is_active=True, reminder_time__lte=now.time())
                .exclude(last_reminded=today).select_related("user"))
    for profile in profiles:
        due = timezone.make_aware(dt.datetime.combine(today, profile.reminder_time))
        user = profile.user
        recorded = Transaction.objects.filter(user=user).filter(Q(date=today) | Q(created_at__date=today)).exists()
        if now - due > LATE_AFTER or recorded:
            counts["skipped"] += 1
            profile.last_reminded = today
            profile.save(update_fields=["last_reminded"])
            continue
        outcomes = []
        for sub in user.push_subscriptions.all():
            outcome = _push(sub)
            outcomes.append(outcome)
            if outcome == "gone":
                sub.delete()
                counts["removed"] += 1
        counts["sent"] += outcomes.count("sent")
        if outcomes.count("sent") or "error" not in outcomes:   # only a temporary failure is worth another try
            counts["users"] += 1
            profile.last_reminded = today
            profile.save(update_fields=["last_reminded"])
    return counts
