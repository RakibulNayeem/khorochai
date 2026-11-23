"""Subscription: free trial, paid plans and the payments staff approve.

Users pay outside the app (bKash, Nagad, Rocket, bank) and report the payment; staff check it in the admin panel.
Approving a payment extends Profile.paid_until. Nothing in the app is locked yet when a trial or plan ends.
"""
import datetime as dt

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import APPROVED, PENDING, REJECTED, Payment, Profile

# First bytes of the image types we accept as a payment screenshot. Only these are ever served back.
IMAGE_SIGNATURES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))


def image_type(data):
    """The MIME type of a PNG, JPEG or WebP image, else ""."""
    for signature, mime in IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def trial_end(user):
    """Last day of the free trial. Users who joined before the trial existed get it from TRIAL_START."""
    joined = timezone.localtime(user.date_joined).date()
    return max(joined, settings.TRIAL_START) + dt.timedelta(days=settings.TRIAL_DAYS - 1)


def status(user):
    """What the user has today: plan ("trial", "monthly", "yearly" or "expired"), until when, and any payment
    still waiting for review."""
    today = timezone.localdate()
    profile = Profile.objects.filter(user=user).first()
    if profile and profile.paid_until and profile.paid_until >= today:
        plan, ends = profile.plan, profile.paid_until
    elif trial_end(user) >= today:
        plan, ends = "trial", trial_end(user)
    else:
        plan, ends = "expired", (profile.paid_until if profile and profile.paid_until else trial_end(user))
    return {
        "plan": plan,
        "ends": ends,
        "days_left": max((ends - today).days + 1, 0),
        "active": plan != "expired",
        "pending": user.payments.filter(status=PENDING).first(),
    }


def approve(payment, staff):
    """Mark a pending payment approved and extend the user's plan. Paid days start after the trial and after any
    days already paid for, so paying early never loses a day. False if it was already reviewed."""
    with transaction.atomic():
        if not Payment.objects.filter(pk=payment.pk, status=PENDING).update(
                status=APPROVED, reviewed_by=staff, reviewed_at=timezone.now()):
            return False
        profile, _ = Profile.objects.select_for_update().get_or_create(user=payment.user)
        today = timezone.localdate()
        start = max(today, trial_end(payment.user) + dt.timedelta(days=1))
        if profile.paid_until and profile.paid_until >= start:
            start = profile.paid_until + dt.timedelta(days=1)
        profile.plan = payment.plan
        profile.paid_until = start + dt.timedelta(days=settings.PLAN_DAYS[payment.plan] - 1)
        profile.save(update_fields=["plan", "paid_until"])
    return True


def reject(payment, staff, note=""):
    """Mark a pending payment rejected, with a note the user sees. False if it was already reviewed."""
    return bool(Payment.objects.filter(pk=payment.pk, status=PENDING).update(
        status=REJECTED, admin_note=note[:255], reviewed_by=staff, reviewed_at=timezone.now()))
