from django.conf import settings

THEME_COOKIE = "theme"
THEMES = ("light", "dark", "system")


def get_theme(request):
    theme = request.COOKIES.get(THEME_COOKIE, "system")
    return theme if theme in THEMES else "system"


def preferences(request):
    """Theme and language choices for every page (navbar menu + <html> attributes)."""
    return {
        "theme": get_theme(request),
        "languages": settings.LANGUAGES,
    }
