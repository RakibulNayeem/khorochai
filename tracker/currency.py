"""Currencies a user can pick, and the one in use for the current request."""
from contextvars import ContextVar

# code: (symbol, decimal places)
CURRENCIES = {
    "BDT": ("৳", 2), "INR": ("₹", 2), "PKR": ("Rs", 2), "NPR": ("Rs", 2), "LKR": ("Rs", 2),
    "USD": ("$", 2), "EUR": ("€", 2), "GBP": ("£", 2), "CAD": ("C$", 2), "AUD": ("A$", 2),
    "SAR": ("SAR", 2), "AED": ("AED", 2), "QAR": ("QAR", 2), "KWD": ("KWD", 3), "OMR": ("OMR", 3),
    "MYR": ("RM", 2), "SGD": ("S$", 2), "IDR": ("Rp", 0), "CNY": ("¥", 2), "JPY": ("¥", 0),
}
CURRENCY_CHOICES = [(code, f"{code} ({symbol})") for code, (symbol, _) in CURRENCIES.items()]
DEFAULT_CURRENCY = "BDT"

# A first guess for new users and visitors, from the language they use
CURRENCY_BY_LANGUAGE = {
    "bn": "BDT", "en": "BDT", "hi": "INR", "ur": "PKR", "ar": "SAR",
    "es": "EUR", "fr": "EUR", "zh-hans": "CNY", "id": "IDR",
}

_current = ContextVar("currency", default=DEFAULT_CURRENCY)


def set_current(code):
    _current.set(code if code in CURRENCIES else DEFAULT_CURRENCY)


def current():
    code = _current.get()
    symbol, decimals = CURRENCIES[code]
    return code, symbol, decimals


def guess_for_language(language):
    return CURRENCY_BY_LANGUAGE.get((language or "").lower(), DEFAULT_CURRENCY)
