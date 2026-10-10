from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _

INCOME = "income"
EXPENSE = "expense"
TRANSFER = "transfer"  # only used by the AI preview + lists; stored in the Transfer model
TYPE_CHOICES = [(EXPENSE, _("Expense")), (INCOME, _("Income"))]

# Names of the ready-made categories. They are stored in English (the AI matches on them)
# and only shown translated.
DEFAULT_CATEGORY_NAMES = {
    "Food", "Groceries", "Transport", "Rent", "Utilities", "Mobile & Internet", "Health", "Education",
    "Shopping", "Entertainment", "Family", "Other Expense", "Salary", "Tuition", "Freelance", "Business",
    "Gift", "Other Income",
}


def category_label(name):
    """Display name for a category: translated if it is a default one, else as typed."""
    if not name:
        return gettext("Uncategorized")
    return gettext(name) if name in DEFAULT_CATEGORY_NAMES else name


class Category(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=60)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    icon = models.CharField(max_length=8, blank=True, default="🏷️")
    color = models.CharField(max_length=7, default="#6c757d")
    monthly_budget = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text=_("Optional monthly spending limit (expense categories only)."),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["type", "name"]
        verbose_name_plural = "categories"
        constraints = [
            models.UniqueConstraint(fields=["user", "name", "type"], name="unique_category_per_user_type"),
        ]

    def __str__(self):
        return f"{self.icon} {category_label(self.name)}".strip()


# ---------------------------------------------------------------- money accounts (Cash, bKash, bank...)
CASH, MOBILE, BANK, CARD, OTHER = "cash", "mobile", "bank", "card", "other"
ACCOUNT_KIND_CHOICES = [
    (CASH, _("Cash")), (MOBILE, _("Mobile banking")), (BANK, _("Bank account")), (CARD, _("Card")), (OTHER, _("Other")),
]
ACCOUNT_KIND_ORDER = {CASH: 0, MOBILE: 1, BANK: 2, CARD: 3, OTHER: 4}


class Account(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="accounts")
    name = models.CharField(max_length=40)
    kind = models.CharField(max_length=10, choices=ACCOUNT_KIND_CHOICES, default=CASH)
    icon = models.CharField(max_length=8, blank=True, default="💵")
    color = models.CharField(max_length=7, default="#16a34a")
    opening_balance = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text=_("Money already in this account when you started using KhorochAi."),
    )
    is_default = models.BooleanField(
        default=False, help_text=_("Used when you don't say which account (e.g. 'lunch 250')."),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "name"], name="unique_account_per_user"),
        ]

    @property
    def label(self):
        return self.name  # account names are the user's own words: never translated

    def __str__(self):
        return f"{self.icon} {self.label}".strip()


class Transaction(models.Model):
    SOURCE_CHOICES = [("ai", "AI"), ("manual", _("Manual"))]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transactions")
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions")
    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions")
    note = models.CharField(max_length=255, blank=True)
    date = models.DateField(default=timezone.localdate)
    raw_text = models.TextField(blank=True, help_text="Original message typed by the user (AI entries).")
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default="manual")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["user", "date"])]

    is_transfer = False

    def __str__(self):
        return f"{self.get_type_display()} {self.amount} ({self.category or gettext('Uncategorized')})"


class Transfer(models.Model):
    """Money moved between two of the user's accounts (ATM withdrawal, bKash cash-out...).
    Changes account balances only; it is not income or expense."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transfers")
    from_account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="transfers_out")
    to_account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="transfers_in")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(default=timezone.localdate)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    is_transfer = True
    type = TRANSFER

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.from_account} → {self.to_account} {self.amount}"


class AIUsage(models.Model):
    """AI requests and tokens a user used on a day (for the free-tier daily limits)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_usage")
    date = models.DateField()
    count = models.PositiveIntegerField(default=0)
    tokens = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "date"], name="unique_ai_usage_per_day")]
