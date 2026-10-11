"""Switching to email login: existing users who already gave an email can log in with it right away."""
from django.conf import settings
from django.db import migrations


def forwards(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    EmailAddress = apps.get_model("account", "EmailAddress")
    taken = set()
    for user in User.objects.exclude(email="").order_by("date_joined"):
        email = user.email.strip().lower()
        if email in taken or EmailAddress.objects.filter(email__iexact=email).exists():
            continue  # the same email on two old accounts: only the oldest gets it
        EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
        taken.add(email)


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0004_ai_usage_tokens"),
        ("account", "0009_emailaddress_unique_primary_email"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
