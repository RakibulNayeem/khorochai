import datetime as dt

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _

from .providers import logo_for

INCOME = "income"
EXPENSE = "expense"
TRANSFER = "transfer"  # only used by the AI preview + lists; stored in the Transfer model
BALANCE = "balance"    # AI preview only: "bkash e 5000 ache" sets an account's current balance
BUDGET = "budget"      # AI preview only: "khabar e mashe 6000 budget" sets a category's monthly budget
TYPE_CHOICES = [(EXPENSE, _("Expense")), (INCOME, _("Income"))]

# Names of the ready-made categories. They are stored in English (the AI matches on them)
# and only shown translated.
DEFAULT_CATEGORY_NAMES = {
    "Food", "Groceries", "Transport", "Rent", "Utilities", "Mobile & Internet", "Health", "Education",
    "Shopping", "Entertainment", "Family", "Other Expense", "Salary", "Tuition", "Freelance", "Business",
    "Gift", "Other Income", "Financial Expenses",
}
FINANCIAL_EXPENSES = "Financial Expenses"  # paying back borrowed money is counted here


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

    @property
    def logo(self):
        return logo_for(self.name, self.kind)

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
    # Made by the browser for entries typed offline, so a retried sync can't create the same entry twice.
    client_id = models.UUIDField(null=True, blank=True, editable=False)
    # Set when this expense is the repayment of a borrowed debt; it is kept in step with that record.
    debt_record = models.OneToOneField("DebtRecord", on_delete=models.CASCADE, null=True, blank=True, related_name="expense")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["user", "date"])]
        constraints = [models.UniqueConstraint(fields=["user", "client_id"], name="unique_transaction_client_id")]

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


class DailyActivity(models.Model):
    """Visits and time on the site for one person on one day, for the admin panel. Only these numbers, nothing they did.
    A row has a user when logged in; a visitor who isn't logged in is known only by a random ID from their browser."""
    date = models.DateField(db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="activity")
    visitor_id = models.CharField(max_length=32)
    views = models.PositiveIntegerField(default=0)
    seconds = models.PositiveIntegerField(default=0)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["date", "user"], condition=models.Q(user__isnull=False),
                                    name="unique_activity_per_user_day"),
            models.UniqueConstraint(fields=["date", "visitor_id"], condition=models.Q(user__isnull=True),
                                    name="unique_activity_per_visitor_day"),
        ]


class Profile(models.Model):
    """Per-user settings that must follow the user to every device."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    currency = models.CharField(max_length=3, default="BDT")

    def __str__(self):
        return f"{self.user} ({self.currency})"


# ---------------------------------------------------------------- debts (money lent to / borrowed from people)
LENT, BORROWED = "lent", "borrowed"
DEBT_DIRECTION_CHOICES = [(LENT, _("I lent")), (BORROWED, _("I borrowed"))]
REPAY, INCREASE = "repay", "increase"
DEBT_RECORD_CHOICES = [(REPAY, _("Paid back")), (INCREASE, _("Added to debt"))]
DEBT = "debt"          # used by the lists for debt rows
# AI preview only: the four kinds of debt sentence
LEND, BORROW, COLLECT, REPAY_DEBT = "lend", "borrow", "collect", "repay"
DEBT_AI_TYPES = (LEND, BORROW, COLLECT, REPAY_DEBT)


class Debt(models.Model):
    """Money lent to someone or borrowed from someone. Not income or expense: it only moves money in or
    out of an account until it is paid back. Remaining = amount + increases - repayments."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="debts")
    direction = models.CharField(max_length=10, choices=DEBT_DIRECTION_CHOICES)
    person = models.CharField(max_length=60)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name="debts",
                                help_text=_("The money left or came into this account."))
    date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    # db_default too, so older code still running during a deploy can insert debts without these columns
    phone = models.CharField(max_length=20, blank=True, default="", db_default="")
    # reminder: None = off; otherwise how many days before the due date it starts (0 = on the due date)
    remind_days = models.PositiveSmallIntegerField(null=True, blank=True)
    remind_time = models.TimeField(default=dt.time(9, 0), db_default=dt.time(9, 0))
    remind_daily = models.BooleanField(default=True, db_default=True,
                                       help_text=_("Keep reminding every day while it is overdue."))
    snoozed_until = models.DateField(null=True, blank=True)
    closed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    is_transfer = False
    is_debt = True
    is_record = False
    type = DEBT

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [models.Index(fields=["user", "closed"])]

    def __str__(self):
        return f"{self.get_direction_display()} {self.person} {self.amount}"

    @property
    def is_lent(self):
        return self.direction == LENT

    @property
    def flow(self):
        """+1 when this row brought money into the account, -1 when money left it."""
        return -1 if self.is_lent else 1

    @property
    def debt(self):
        return self


class DebtRecord(models.Model):
    """A later event on a debt: (part of) it paid back, or more lent/borrowed."""
    debt = models.ForeignKey(Debt, on_delete=models.CASCADE, related_name="records")
    kind = models.CharField(max_length=10, choices=DEBT_RECORD_CHOICES, default=REPAY)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name="debt_records")
    date = models.DateField(default=timezone.localdate)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    is_transfer = False
    is_debt = True
    is_record = True
    type = DEBT

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount} ({self.debt.person})"

    @property
    def person(self):
        return self.debt.person

    @property
    def is_expense(self):
        """Paying back money I borrowed is counted as an expense (Financial Expenses)."""
        return self.kind == REPAY and not self.debt.is_lent

    @property
    def flow(self):
        # a repayment on money I lent comes in; on money I borrowed it goes out. An increase is the opposite.
        sign = 1 if self.debt.is_lent else -1
        return sign if self.kind == REPAY else -sign
