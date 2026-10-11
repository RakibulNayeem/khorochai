from decimal import Decimal, InvalidOperation

from django import template
from django.conf import settings
from django.utils.translation import get_language

register = template.Library()


@register.filter
def money(value):
    """1234.5 -> '৳ 1,234.50' (negative values get a minus sign)."""
    try:
        d = Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return value
    sign = "-" if d < 0 else ""
    text = f"{sign}{settings.CURRENCY_SYMBOL} {abs(d):,.2f}"
    return bangla_digits(text) if (get_language() or "").startswith("bn") else text


BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def bangla_digits(text):
    return str(text).translate(BN_DIGITS)
