"""Give every existing user the default accounts and put their old transactions in Cash."""
from django.conf import settings
from django.db import migrations

DEFAULT_ACCOUNTS = [
    # (name, kind, icon, color, is_default) - keep in sync with services.DEFAULT_ACCOUNTS
    ("Cash", "cash", "💵", "#16a34a", True),
    ("bKash", "mobile", "📱", "#e2136e", False),
    ("Nagad", "mobile", "📲", "#f6921e", False),
    ("Bank", "bank", "🏦", "#2563eb", False),
    ("Card", "card", "💳", "#7c3aed", False),
]


def forwards(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    Account = apps.get_model("tracker", "Account")
    Transaction = apps.get_model("tracker", "Transaction")
    for user in User.objects.all():
        if Account.objects.filter(user=user).exists():
            continue
        for name, kind, icon, color, is_default in DEFAULT_ACCOUNTS:
            Account.objects.create(user=user, name=name, kind=kind, icon=icon, color=color, is_default=is_default)
        cash = Account.objects.get(user=user, name="Cash")
        Transaction.objects.filter(user=user, account__isnull=True).update(account=cash)


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0002_accounts_transfers_ai_usage"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
