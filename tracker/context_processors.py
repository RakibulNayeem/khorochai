from django.conf import settings

from . import currency

THEME_COOKIE = "theme"
THEMES = ("light", "dark", "system")


def get_theme(request):
    theme = request.COOKIES.get(THEME_COOKIE, "system")
    return theme if theme in THEMES else "system"


AVATAR_COLORS = ["#2457a6", "#1a7f37", "#b3261e", "#7b3fbf", "#c26a00", "#0b7a75", "#a8325e", "#4a5568"]


def _profile(user):
    """Name, initials and a stable avatar colour for the profile menu."""
    name = (user.username or "").strip()
    words = name.split()
    initials = "".join(w[0] for w in words[:2]).upper() if words else "?"
    color = AVATAR_COLORS[sum(map(ord, user.username or "x")) % len(AVATAR_COLORS)]
    return {"name": name, "initials": initials, "color": color}


def preferences(request):
    """Theme and language choices for every page (navbar menu + <html> attributes)."""
    user = getattr(request, "user", None)
    return {
        "theme": get_theme(request),
        "languages": settings.LANGUAGES,
        "profile": _profile(user) if user is not None and user.is_authenticated else None,
        "developer": settings.DEVELOPER,
        "currency_code": currency.current()[0],
        "currency_symbol": currency.current()[1],
        "contact_email": settings.CONTACT_EMAIL,
        "clarity_id": settings.CLARITY_PROJECT_ID,
    }
