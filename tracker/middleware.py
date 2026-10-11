from . import currency


class CurrencyMiddleware:
    """Use the logged-in user's currency (or a guess from the language for visitors) for every amount shown."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            # a user's currency never changes with the language: their saved choice, else taka
            from .models import Profile
            code = Profile.objects.filter(user=user).values_list("currency", flat=True).first()
            currency.set_current(code or currency.DEFAULT_CURRENCY)
        else:
            currency.set_current(currency.guess_for_language(getattr(request, "LANGUAGE_CODE", "")))
        return self.get_response(request)
