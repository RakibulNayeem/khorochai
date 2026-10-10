from decimal import Decimal, InvalidOperation

from django import template
from django.conf import settings

register = template.Library()


@register.filter
def money(value):
    """1234.5 -> '৳ 1,234.50' (negative values get a minus sign)."""
    try:
        d = Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return value
    sign = "-" if d < 0 else ""
    return f"{sign}{settings.CURRENCY_SYMBOL} {abs(d):,.2f}"
