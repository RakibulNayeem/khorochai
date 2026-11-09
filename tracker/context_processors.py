from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from . import currency, services

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


def _counts_as_a_visit(request, user):
    """False on the admin panel and on any page a staff member opens: analytics must not run there."""
    if user is not None and user.is_staff:
        return False
    panel = reverse("admin_overview")
    # also the login page a logged-out visitor is sent to from the panel: its URL holds ?next=/admin/...
    return not (request.path.startswith(panel) or request.GET.get("next", "").startswith(panel))


def preferences(request):
    """Theme and language choices for every page (navbar menu + <html> attributes)."""
    user = getattr(request, "user", None)
    counts = _counts_as_a_visit(request, user)
    return {
        "theme": get_theme(request),
        "languages": settings.LANGUAGES,
        "profile": _profile(user) if user is not None and user.is_authenticated else None,
        "developer": settings.DEVELOPER,
        "currency_code": currency.current()[0],
        "currency_symbol": currency.current()[1],
        "contact_email": settings.CONTACT_EMAIL,
        "clarity_id": settings.CLARITY_PROJECT_ID if counts else "",
        "track_activity": settings.TRACK_ACTIVITY and counts,
        **_reminders(request, user),
    }


def _reminders(request, user):
    """Debt reminders for the bell in the navbar; not on the staff panel."""
    if user is None or not user.is_authenticated or request.path.startswith(reverse("admin_overview")):
        return {}
    found = services.debt_reminders(user)
    data = [{"id": f"{d.pk}-{timezone.localdate().isoformat()}", "title": d.title, "body": d.body,
             "url": reverse("debt_detail", args=[d.pk])} for d in found]
    return {"reminders": found, "reminder_data": data, "show_bell": True}
