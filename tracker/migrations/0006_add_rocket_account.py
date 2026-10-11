"""Rocket became a default account: give it to existing users who don't have one yet."""
from django.db import migrations


def forwards(apps, schema_editor):
    Account = apps.get_model("tracker", "Account")
    user_ids = Account.objects.values_list("user_id", flat=True).distinct()
    for user_id in user_ids:
        if not Account.objects.filter(user_id=user_id, name__iexact="Rocket").exists():
            Account.objects.create(user_id=user_id, name="Rocket", kind="mobile", icon="🚀", color="#8c3494")


class Migration(migrations.Migration):
    dependencies = [("tracker", "0005_existing_emails_verified")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
