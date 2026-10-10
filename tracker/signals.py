from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .services import ensure_default_accounts, ensure_default_categories


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_default_categories(sender, instance, created, **kwargs):
    if created:
        ensure_default_categories(instance)
        ensure_default_accounts(instance)
