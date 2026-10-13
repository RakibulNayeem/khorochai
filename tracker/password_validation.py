"""Refuse passwords that appear in known data breaches (the same lists browsers warn about), so sign-up asks for a
different password *before* anything is created or emailed.

Uses the free Have I Been Pwned "range" API with k-anonymity: only the first 5 characters of the password's SHA-1
hash leave the server, never the password. If the service can't be reached, the password is allowed (the other
validators still apply), so sign-up never breaks because of it."""
import hashlib
import logging

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

RANGE_URL = "https://api.pwnedpasswords.com/range/{}"


def breach_count(password, timeout=3):
    """How many times this password appears in known breaches (0 if none), or None if the check couldn't run."""
    digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    try:
        r = requests.get(RANGE_URL.format(prefix), headers={"Add-Padding": "true", "User-Agent": "KhorochAi"}, timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Breached-password check unavailable: %s", exc)
        return None
    for line in r.text.splitlines():
        hash_suffix, _sep, count = line.partition(":")
        if hash_suffix.strip() == suffix:
            return int(count.strip() or 0)
    return 0


class BreachedPasswordValidator:
    def validate(self, password, user=None):
        if not getattr(settings, "PASSWORD_BREACH_CHECK", True):
            return
        if breach_count(password):
            raise ValidationError(
                _("This password has appeared in a data breach, so it isn't safe. Please choose a different one."),
                code="password_breached",
            )

    def get_help_text(self):
        return _("Your password can't be one that has appeared in a data breach.")
