from decimal import Decimal, InvalidOperation

from django import template
from django.utils.translation import get_language

from ..currency import current

register = template.Library()


@register.filter
def money(value):
    """1234.5 -> '৳ 1,234.50' in the user's currency (negative values get a minus sign)."""
    try:
        d = Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return value
    sign = "-" if d < 0 else ""
    _, symbol, decimals = current()
    text = f"{sign}{symbol} {abs(d):,.{decimals}f}"
    return bangla_digits(text) if (get_language() or "").startswith("bn") else text


BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def bangla_digits(text):
    return str(text).translate(BN_DIGITS)
