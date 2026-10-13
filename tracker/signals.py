from allauth.account.signals import email_confirmed, user_signed_up
from django.conf import settings
from django.contrib import messages
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.translation import gettext as _

from .services import ensure_default_accounts, ensure_default_categories


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_default_categories(sender, instance, created, **kwargs):
    if created:
        ensure_default_categories(instance)
        ensure_default_accounts(instance)


def _welcome(request):
    messages.success(request, _("Welcome! Default categories are ready. Try typing your first expense."))


@receiver(user_signed_up)
def first_currency(request, user, **kwargs):
    from . import currency
    from .models import Profile
    Profile.objects.get_or_create(user=user, defaults={"currency": currency.guess_for_language(getattr(request, "LANGUAGE_CODE", ""))})


@receiver(user_signed_up)
def welcome_social(request, user, sociallogin=None, **kwargs):
    if sociallogin:  # Google sign-ups skip the email code, so welcome them now
        _welcome(request)


@receiver(email_confirmed)
def welcome_after_code(request, email_address, **kwargs):
    if not email_address.user.transactions.exists():  # first verification, not a later email change
        _welcome(request)
