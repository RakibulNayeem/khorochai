"""
Business logic: default categories, validating AI output, and all money calculations.
"""
import calendar
import datetime as dt
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce, Least, TruncMonth
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext, ngettext

from .models import (
    BANK, CARD,
    ACCOUNT_KIND_ORDER, BALANCE, BORROW, BORROWED, BUDGET, DEBT_AI_TYPES, EXPENSE, FINANCIAL_EXPENSES, INCOME, INCREASE, LEND, LENT,
    REPAY, TRANSFER, Account, AIUsage, Category, DailyActivity, Debt, DebtRecord, Transaction, Transfer, category_label,
)

DEFAULT_CATEGORIES = [
    # (name, type, icon, color)
    ("Food", EXPENSE, "🍚", "#f97316"),
    ("Groceries", EXPENSE, "🛒", "#84cc16"),
    ("Transport", EXPENSE, "🚌", "#0ea5e9"),
    ("Rent", EXPENSE, "🏠", "#8b5cf6"),
    ("Utilities", EXPENSE, "💡", "#eab308"),
    ("Mobile & Internet", EXPENSE, "📱", "#06b6d4"),
    ("Health", EXPENSE, "🏥", "#ef4444"),
    ("Education", EXPENSE, "📚", "#6366f1"),
    ("Shopping", EXPENSE, "👕", "#ec4899"),
    ("Entertainment", EXPENSE, "🎉", "#a855f7"),
    ("Family", EXPENSE, "👪", "#14b8a6"),
    ("Other Expense", EXPENSE, "💸", "#64748b"),
    ("Financial Expenses", EXPENSE, "🏦", "#b45309"),
    ("Salary", INCOME, "💼", "#16a34a"),
    ("Tuition", INCOME, "🎓", "#22c55e"),
    ("Freelance", INCOME, "💻", "#10b981"),
    ("Business", INCOME, "🏪", "#059669"),
    ("Gift", INCOME, "🎁", "#65a30d"),
    ("Other Income", INCOME, "💰", "#4d7c0f"),
]

DEFAULT_ACCOUNTS = [
    # (name, kind, icon, color, is_default) - Rocket was added later in migrations/0006_add_rocket_account.py
    ("Cash", "cash", "💵", "#16a34a", True),
    ("bKash", "mobile", "📱", "#e2136e", False),
    ("Nagad", "mobile", "📲", "#f6921e", False),
    ("Rocket", "mobile", "🚀", "#8c3494", False),
    ("Bank", "bank", "🏦", "#2563eb", False),
    ("Card", "card", "💳", "#7c3aed", False),
]

NEW_CATEGORY_COLORS = ["#f43f5e", "#0891b2", "#d97706", "#7c3aed", "#0d9488", "#db2777", "#2563eb", "#ca8a04"]


def ensure_default_categories(user):
    if Category.objects.filter(user=user).exists():
        return
    Category.objects.bulk_create(
        [Category(user=user, name=n, type=t, icon=i, color=c) for n, t, i, c in DEFAULT_CATEGORIES]
    )


def ensure_default_accounts(user):
    if Account.objects.filter(user=user).exists():
        return
    Account.objects.bulk_create(
        [Account(user=user, name=n, kind=k, icon=i, color=c, is_default=d) for n, k, i, c, d in DEFAULT_ACCOUNTS]
    )


def sorted_accounts(user):
    """Cash first, then mobile banking, bank, card, other."""
    return sorted(Account.objects.filter(user=user), key=lambda a: (ACCOUNT_KIND_ORDER.get(a.kind, 9), a.created_at, a.id))


def default_account(user):
    accounts = sorted_accounts(user)
    return next((a for a in accounts if a.is_default), accounts[0] if accounts else None)


MAX_ACTIVITY_SECONDS_PER_PING = 120
MAX_GUEST_ROWS_PER_DAY = 50000  # the counter is open to anyone: a flood of made-up IDs stops here


def record_activity(user, visitor_id, seconds, view):
    """Add one browser ping to today's counts. Staff aren't counted, so the numbers show real users."""
    if user is not None and user.is_staff:
        return
    today = timezone.localdate()
    seconds = max(0, min(seconds, MAX_ACTIVITY_SECONDS_PER_PING))
    row = {"date": today, "user": user}
    if user is None:
        row["visitor_id"] = visitor_id
    changes = {"views": F("views") + (1 if view else 0), "last_seen": timezone.now(),
               "seconds": Least(F("seconds") + seconds, Value(86400))}
    if user is not None:
        changes["visitor_id"] = visitor_id
    if DailyActivity.objects.filter(**row).update(**changes):
        return
    if user is None and DailyActivity.objects.filter(date=today, user__isnull=True).count() >= MAX_GUEST_ROWS_PER_DAY:
        return
    try:
        with transaction.atomic():
            DailyActivity.objects.create(date=today, user=user, visitor_id=visitor_id, views=1 if view else 0, seconds=seconds)
    except IntegrityError:  # two pings at once: the other one made the row
        DailyActivity.objects.filter(**row).update(**changes)


def find_account(accounts, name):
    """The account the AI named. "Bank" or "Card" with no account of that name means the first of that type,
    because users rename those two to their own bank's name."""
    name = str(name or "").strip().lower()
    exact = next((a for a in accounts if a.name.lower() == name), None)
    if exact or name not in (BANK, CARD):
        return exact
    return next((a for a in accounts if a.kind == name), None)


def account_balances(user):
    """[{account, balance, income, expense}] using SQL sums.
    Balance = opening + income - expense ± transfers ± debts (lending takes money out, borrowing brings it in,
    and paying back reverses that)."""
    accounts = sorted_accounts(user)
    sums = {}
    for r in Transaction.objects.filter(user=user, account__isnull=False).values("account_id", "type").annotate(t=Sum("amount")):
        sums[(r["account_id"], r["type"])] = r["t"]
    t_in = {r["to_account_id"]: r["t"] for r in Transfer.objects.filter(user=user).values("to_account_id").annotate(t=Sum("amount"))}
    t_out = {r["from_account_id"]: r["t"] for r in Transfer.objects.filter(user=user).values("from_account_id").annotate(t=Sum("amount"))}
    debt_flow = {}
    for r in Debt.objects.filter(user=user, account__isnull=False).values("account_id", "direction").annotate(t=Sum("amount")):
        sign = -1 if r["direction"] == LENT else 1
        debt_flow[r["account_id"]] = debt_flow.get(r["account_id"], Decimal("0")) + sign * r["t"]
    for r in (DebtRecord.objects.filter(debt__user=user, account__isnull=False, expense__isnull=True)
              .values("account_id", "debt__direction", "kind").annotate(t=Sum("amount"))):
        sign = 1 if r["debt__direction"] == LENT else -1
        if r["kind"] != REPAY:
            sign = -sign
        debt_flow[r["account_id"]] = debt_flow.get(r["account_id"], Decimal("0")) + sign * r["t"]
    rows = []
    for a in accounts:
        inc = sums.get((a.id, INCOME), Decimal("0"))
        exp = sums.get((a.id, EXPENSE), Decimal("0"))
        bal = (a.opening_balance + inc - exp + t_in.get(a.id, Decimal("0")) - t_out.get(a.id, Decimal("0"))
               + debt_flow.get(a.id, Decimal("0")))
        rows.append({"account": a, "balance": bal, "income": inc, "expense": exp})
    return rows


def category_names(user):
    cats = Category.objects.filter(user=user)
    return (
        [c.name for c in cats if c.type == EXPENSE],
        [c.name for c in cats if c.type == INCOME],
    )


def get_or_create_category(user, name, type_):
    name = " ".join(name.split())[:60]
    existing = Category.objects.filter(user=user, type=type_, name__iexact=name).first()
    if existing:
        return existing, False
    n = Category.objects.filter(user=user).count()
    cat = Category.objects.create(
        user=user, name=name, type=type_, icon="🏷️",
        color=NEW_CATEGORY_COLORS[n % len(NEW_CATEGORY_COLORS)],
    )
    return cat, True


# --------------------------------------------------------------------------- dates

def parse_month(month_str):
    """'2026-10' -> date(2026,10,1). Falls back to the current month."""
    today = timezone.localdate()
    try:
        y, m = month_str.split("-")
        return dt.date(int(y), int(m), 1)
    except (AttributeError, ValueError):
        return today.replace(day=1)


def month_bounds(first_day):
    last = calendar.monthrange(first_day.year, first_day.month)[1]
    return first_day, first_day.replace(day=last)


def shift_month(first_day, delta):
    m = first_day.month - 1 + delta
    return dt.date(first_day.year + m // 12, m % 12 + 1, 1)


RECENT_PERIODS = ("day", "week", "month")


def period_bounds(period, today=None):
    """'day' = today, 'week' = this week (Saturday to Friday, as in Bangladesh), 'month' = this month so far."""
    today = today or timezone.localdate()
    if period == "day":
        return today, today
    if period == "month":
        return today.replace(day=1), today
    start = today - dt.timedelta(days=(today.weekday() - 5) % 7)  # weekday(): Saturday = 5
    return start, today


def parse_date(value, default=None):
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return default


def parse_balance(value):
    """Like parse_amount, but a balance may also be 0 or negative (e.g. a card you owe money on)."""
    if value is None or str(value).strip() == "":
        return None
    s = str(value).translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789"))
    s = s.replace(",", "").replace("৳", "").replace("tk", "").replace("Tk", "").strip()
    try:
        d = Decimal(s).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    return d if abs(d) < Decimal("10000000000") else None


def set_account_balance(account, target):
    """Make `account` show `target` as its balance today by adjusting its opening balance
    (so no income or expense is invented). Returns the change made."""
    current = next(r["balance"] for r in account_balances(account.user) if r["account"].pk == account.pk)
    diff = target - current
    if diff:
        account.opening_balance += diff
        account.save(update_fields=["opening_balance"])
    return diff


def parse_amount(value):
    """Return a positive Decimal with 2 places, or None."""
    if value is None:
        return None
    s = str(value).translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789"))
    s = s.replace(",", "").replace("৳", "").replace("tk", "").replace("Tk", "").strip()
    try:
        d = Decimal(s).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    if d <= 0 or d >= Decimal("10000000000"):
        return None
    return d


# --------------------------------------------------------------------------- AI output validation

def clean_parsed(data, user):
    """
    Validate the LLM JSON and match categories against the user's real categories.
    Returns (rows, message). Each row is a dict ready for the preview form.
    """
    today = timezone.localdate()
    message = ""
    if not isinstance(data, dict):
        return [], gettext("The AI response was not understood. Please try again.")
    message = str(data.get("message") or "")
    items = data.get("transactions") or []
    if not isinstance(items, list):
        items = []

    cats = list(Category.objects.filter(user=user))
    accounts = list(Account.objects.filter(user=user))
    fallback_account = default_account(user)
    rows = []
    for item in items[:30]:
        if not isinstance(item, dict):
            continue
        type_ = str(item.get("type", "")).lower().strip()
        if type_ not in (INCOME, EXPENSE, TRANSFER, BALANCE, BUDGET) + DEBT_AI_TYPES:
            type_ = EXPENSE
        if type_ == BUDGET:
            row = _budget_row(item, cats, today)
            if row["amount"] is not None:
                rows.append(row)
            continue
        if type_ == BALANCE:
            rows.append(_balance_row(item, accounts, fallback_account, today))
            if rows[-1]["amount"] is None:
                rows.pop()
            continue
        amount = parse_amount(item.get("amount"))
        if amount is None:
            continue
        date = parse_date(item.get("date"), today)
        if date > today + dt.timedelta(days=366) or date.year < 2000:
            date = today
        if type_ == TRANSFER:
            rows.append(_transfer_row(item, amount, date, accounts, fallback_account))
            continue
        if type_ in DEBT_AI_TYPES:
            rows.append(_debt_row(item, type_, amount, date, accounts, fallback_account, user))
            continue
        cat_name = " ".join(str(item.get("category") or "").split())[:60]
        match = next((c for c in cats if c.type == type_ and c.name.lower() == cat_name.lower()), None)
        account = find_account(accounts, item.get("account")) or fallback_account
        rows.append({
            "type": type_,
            "amount": amount,
            "category_id": match.id if match else None,
            "new_category": "" if match else (cat_name or ("Other Expense" if type_ == EXPENSE else "Other Income")),
            "account_id": account.id if account else None,
            "to_account_id": None,
            "note": str(item.get("note") or "")[:255],
            "date": date,
            "person": "",
        })
    if not rows and not message:
        message = gettext("No income or expense with an amount was found in your message.")
    return rows, message


def _debt_row(item, type_, amount, date, accounts, fallback_account, user):
    """Lending, borrowing, or a debt being paid back. A name that differs from an earlier debt's only in
    upper/lower case is written the earlier way, so payments find the right debt."""
    account = find_account(accounts, item.get("account")) or fallback_account
    person = " ".join(str(item.get("person") or "").split())[:60]
    if person:
        known = Debt.objects.filter(user=user, person__iexact=person).order_by("-date").values_list("person", flat=True).first()
        person = known or person
    due = parse_date(item.get("due_date")) if type_ in (LEND, BORROW) else None
    return {
        "type": type_,
        "amount": amount,
        "category_id": None,
        "new_category": "",
        "account_id": account.id if account else None,
        "to_account_id": None,
        "note": str(item.get("note") or "")[:255],
        "date": date,
        "person": person,
        "due_date": due if due and due >= date else None,
    }


def _budget_row(item, cats, today):
    """A monthly budget for an expense category. 0 means "remove the budget"."""
    cat_name = " ".join(str(item.get("category") or "").split())[:60]
    match = next((c for c in cats if c.type == EXPENSE and c.name.lower() == cat_name.lower()), None)
    amount = parse_balance(item.get("amount"))
    return {
        "type": BUDGET,
        "amount": amount if amount is not None and amount >= 0 else None,
        "category_id": match.id if match else None,
        "new_category": "" if match else (cat_name or "Other Expense"),
        "account_id": None,
        "to_account_id": None,
        "note": "",
        "date": today,
        "person": "",
    }


def _balance_row(item, accounts, fallback_account, today):
    account = find_account(accounts, item.get("account")) or fallback_account
    return {
        "type": BALANCE,
        "amount": parse_balance(item.get("amount")),
        "category_id": None,
        "new_category": "",
        "account_id": account.id if account else None,
        "to_account_id": None,
        "note": "",
        "date": today,
        "person": "",
    }


def _transfer_row(item, amount, date, accounts, fallback_account):
    """Match both sides of a transfer; fill a missing side so the preview always has two different accounts."""
    src, dst = find_account(accounts, item.get("account")), find_account(accounts, item.get("to_account"))
    if src is None:
        src = fallback_account if fallback_account != dst else None
    if src is None or src == dst:
        src = next((a for a in accounts if a != dst), src)
    if dst is None or dst == src:
        dst = next((a for a in accounts if a != src), None)
    return {
        "type": TRANSFER,
        "amount": amount,
        "category_id": None,
        "new_category": "",
        "account_id": src.id if src else None,
        "to_account_id": dst.id if dst else None,
        "note": str(item.get("note") or "")[:255],
        "date": date,
        "person": "",
    }


def merge_with_transfers(transactions, transfers, debt_items=(), limit=None):
    """Transactions, transfers and debt events in one list, newest first (for the tables)."""
    items = sorted(list(transactions) + list(transfers) + list(debt_items), key=lambda t: (t.date, t.created_at), reverse=True)
    return items[:limit] if limit else items


# --------------------------------------------------------------------------- debts

ZERO = Value(Decimal("0"), output_field=DecimalField(max_digits=12, decimal_places=2))


def debts_with_totals(user):
    """The user's debts, each with .added, .repaid, .total, .remaining, .pct_paid and .overdue (SQL sums)."""
    today = timezone.localdate()
    qs = (Debt.objects.filter(user=user).select_related("account")
          .annotate(repaid=Coalesce(Sum("records__amount", filter=Q(records__kind=REPAY)), ZERO),
                    added=Coalesce(Sum("records__amount", filter=Q(records__kind=INCREASE)), ZERO)))
    return [_add_totals(d, today) for d in qs]


def _add_totals(d, today=None):
    today = today or timezone.localdate()
    d.total = d.amount + d.added
    d.remaining = d.total - d.repaid
    d.pct_paid = min(round(float(d.repaid / d.total * 100)), 100) if d.total > 0 else 100
    open_ = not d.closed and d.remaining > 0
    d.overdue = bool(open_ and d.due_date and d.due_date < today)
    d.due_soon = bool(open_ and not d.overdue and d.due_date and (d.due_date - today).days <= 3)
    return d


def debt_totals(debt):
    """Totals for one debt (same attributes as debts_with_totals)."""
    sums = {r["kind"]: r["t"] for r in debt.records.values("kind").annotate(t=Sum("amount"))}
    debt.repaid = sums.get(REPAY, Decimal("0"))
    debt.added = sums.get(INCREASE, Decimal("0"))
    return _add_totals(debt)


def debt_summary(user, debts=None):
    """What others owe me and what I owe, over open debts."""
    debts = debts if debts is not None else debts_with_totals(user)
    open_ = [d for d in debts if not d.closed]
    lent = [d for d in open_ if d.direction == LENT]
    borrowed = [d for d in open_ if d.direction == BORROWED]
    owed_to_me = sum((max(d.remaining, 0) for d in lent), Decimal("0"))
    i_owe = sum((max(d.remaining, 0) for d in borrowed), Decimal("0"))
    return {
        "owed_to_me": owed_to_me, "i_owe": i_owe, "net": owed_to_me - i_owe,
        "lent_count": len(lent), "borrowed_count": len(borrowed),
        "overdue_count": sum(1 for d in open_ if d.overdue),
        "has_open": bool(open_),
    }


MAX_REMIND_DAYS = 60  # same limit as the form


def debt_reminders(user, now=None):
    """Open debts whose reminder is on now, overdue first. Each gets .days_left plus .title and .body,
    the text shown in the bell menu and in phone notifications.

    A reminder starts `remind_days` before the due date, at `remind_time` on that first day, and runs to the due
    date; after that only if `remind_daily`. A snooze hides it until `remind_time` on `snoozed_until`."""
    now = timezone.localtime(now)
    today = now.date()
    qs = (Debt.objects.filter(user=user, closed=False, remind_days__isnull=False, due_date__isnull=False,
                              due_date__lte=today + dt.timedelta(days=MAX_REMIND_DAYS))
          .annotate(repaid=Coalesce(Sum("records__amount", filter=Q(records__kind=REPAY)), ZERO),
                    added=Coalesce(Sum("records__amount", filter=Q(records__kind=INCREASE)), ZERO)))
    found = []
    for d in qs:
        _add_totals(d, today)
        start = d.due_date - dt.timedelta(days=d.remind_days)
        if d.remaining <= 0 or today < start or (d.overdue and not d.remind_daily):
            continue
        if d.snoozed_until and today < d.snoozed_until:
            continue
        if today in (start, d.snoozed_until) and now.time() < d.remind_time:
            continue
        d.days_left = (d.due_date - today).days
        d.title, d.body = _reminder_text(d)
        found.append(d)
    found.sort(key=lambda d: d.days_left)
    return found


def _reminder_text(d):
    from .templatetags.money import money
    amount, day = money(d.remaining), date_format(d.due_date, "j M")
    if d.overdue:
        late = -d.days_left
        title = gettext("Overdue: %(person)s") % {"person": d.person}
        body = ngettext("%(amount)s was due %(day)s · %(n)d day late", "%(amount)s was due %(day)s · %(n)d days late",
                        late) % {"amount": amount, "day": day, "n": late}
        return title, body
    if d.days_left == 0:
        title = gettext("Due today: %(amount)s") % {"amount": amount}
    elif d.days_left == 1:
        title = gettext("Due tomorrow: %(amount)s") % {"amount": amount}
    else:
        title = ngettext("Due in %(n)d day: %(amount)s", "Due in %(n)d days: %(amount)s",
                         d.days_left) % {"n": d.days_left, "amount": amount}
    if d.is_lent:
        body = gettext("%(person)s will pay you back on %(day)s.") % {"person": d.person, "day": day}
    else:
        body = gettext("Pay %(person)s back by %(day)s.") % {"person": d.person, "day": day}
    return title, body


def refresh_debt_status(debt):
    """A debt closes by itself when it is fully paid back, and reopens if a payment is removed or more is added."""
    debt_totals(debt)
    closed = debt.remaining <= 0
    if closed != debt.closed:
        debt.closed = closed
        debt.save(update_fields=["closed"])
        _add_totals(debt)
    return debt


def open_debt_people(user):
    """Names of people with an open debt (told to the AI so it spells them the same way)."""
    return sorted(set(Debt.objects.filter(user=user, closed=False).values_list("person", flat=True)), key=str.lower)


def all_debt_people(user):
    return sorted(set(Debt.objects.filter(user=user).values_list("person", flat=True)), key=str.lower)


def record_debt_payment(user, person, direction, amount, account, date, note=""):
    """A payment from/to `person` without a particular debt chosen (the AI's "rahim 300 ferot dilo"):
    it pays off that person's open debts of `direction`, oldest first; anything paid beyond what is owed
    goes on the newest one. Returns the records created ([] when there is no open debt with them)."""
    debts = [d for d in debts_with_totals(user)
             if not d.closed and d.direction == direction and d.person.lower() == person.lower()]
    debts.sort(key=lambda d: (d.date, d.created_at))
    records, left = [], amount
    for i, d in enumerate(debts):
        part = left if i == len(debts) - 1 else min(left, max(d.remaining, Decimal("0")))
        if part <= 0:
            continue
        record = DebtRecord.objects.create(debt=d, kind=REPAY, amount=part, account=account, date=date, note=note)
        sync_debt_expense(record)
        records.append(record)
        refresh_debt_status(d)
        left -= part
        if left <= 0:
            break
    return records


def financial_category(user):
    """The "Financial Expenses" category (made again if the user deleted it)."""
    cat = Category.objects.filter(user=user, type=EXPENSE, name__iexact=FINANCIAL_EXPENSES).first()
    return cat or Category.objects.create(user=user, name=FINANCIAL_EXPENSES, type=EXPENSE, icon="🏦", color="#b45309")


def sync_debt_expense(record):
    """Paying back borrowed money is an expense: keep one Transaction (Financial Expenses) in step with the record.
    Any other record (money I lent coming back, or more borrowed) has none."""
    debt = record.debt
    existing = Transaction.objects.filter(debt_record=record).first()
    if not record.is_expense:
        if existing:
            existing.delete()
        return None
    t = existing or Transaction(user=debt.user, type=EXPENSE, debt_record=record, source="manual")
    t.amount, t.account, t.date = record.amount, record.account, record.date
    if not t.category_id:
        t.category = financial_category(debt.user)
    t.note = (gettext("Paid back %(person)s") % {"person": debt.person} + (f" · {record.note}" if record.note else ""))[:255]
    t.save()
    return t


def sync_debt_expenses(debt):
    for record in debt.records.all():
        sync_debt_expense(record)


def debt_events(user, start=None, end=None, account_id=None, q=""):
    """Debt rows for the transaction lists: each new debt and each later payment (both have .flow, +1 in / -1 out).
    Repayments of borrowed money are left out here because they are listed as expenses."""
    debts = Debt.objects.filter(user=user).select_related("account")
    records = DebtRecord.objects.filter(debt__user=user, expense__isnull=True).select_related("debt", "account")
    if start:
        debts, records = debts.filter(date__gte=start), records.filter(date__gte=start)
    if end:
        debts, records = debts.filter(date__lte=end), records.filter(date__lte=end)
    if account_id:
        debts, records = debts.filter(account_id=account_id), records.filter(account_id=account_id)
    if q:
        debts = debts.filter(Q(person__icontains=q) | Q(note__icontains=q))
        records = records.filter(Q(debt__person__icontains=q) | Q(note__icontains=q))
    return list(debts[:200]) + list(records[:200])


# --------------------------------------------------------------------------- summaries

def _sum(qs):
    return qs.aggregate(t=Sum("amount"))["t"] or Decimal("0")


def summarize(user, start, end):
    qs = Transaction.objects.filter(user=user, date__gte=start, date__lte=end)
    income = _sum(qs.filter(type=INCOME))
    expense = _sum(qs.filter(type=EXPENSE))

    by_cat = (
        qs.filter(type=EXPENSE)
        .values("category__name", "category__icon", "category__color")
        .annotate(total=Sum("amount"), n=Count("id"))
        .order_by("-total")
    )
    expense_by_category = [
        {
            "name": category_label(r["category__name"]),
            "icon": r["category__icon"] or "❔",
            "color": r["category__color"] or "#94a3b8",
            "total": r["total"],
            "n": r["n"],
            "pct": round(float(r["total"] / expense * 100), 1) if expense else 0,
        }
        for r in by_cat
    ]

    income_by_category = [
        {"name": category_label(r["category__name"]), "icon": r["category__icon"] or "❔", "total": r["total"]}
        for r in qs.filter(type=INCOME).values("category__name", "category__icon")
        .annotate(total=Sum("amount")).order_by("-total")
    ]

    # daily series for charts
    days = (end - start).days + 1
    labels = [(start + dt.timedelta(days=i)) for i in range(days)]
    daily_exp = {d: Decimal("0") for d in labels}
    daily_inc = {d: Decimal("0") for d in labels}
    for r in qs.values("date", "type").annotate(t=Sum("amount")):
        target = daily_exp if r["type"] == EXPENSE else daily_inc
        if r["date"] in target:
            target[r["date"]] = r["t"]

    return {
        "income": income,
        "expense": expense,
        "balance": income - expense,
        "savings_rate": round(float((income - expense) / income * 100), 1) if income else None,
        "expense_by_category": expense_by_category,
        "income_by_category": income_by_category,
        "chart": {
            "days": [d.strftime("%d") for d in labels],
            "daily_expense": [float(daily_exp[d]) for d in labels],
            "daily_income": [float(daily_inc[d]) for d in labels],
            "cat_labels": [f'{c["icon"]} {c["name"]}' for c in expense_by_category],
            "cat_values": [float(c["total"]) for c in expense_by_category],
            "cat_colors": [c["color"] for c in expense_by_category],
        },
    }


def all_time_balance(user):
    qs = Transaction.objects.filter(user=user)
    return _sum(qs.filter(type=INCOME)) - _sum(qs.filter(type=EXPENSE))


def budget_status(user, start, end):
    result = []
    cats = Category.objects.filter(user=user, type=EXPENSE, monthly_budget__isnull=False, monthly_budget__gt=0)
    for c in cats:
        spent = _sum(Transaction.objects.filter(user=user, category=c, date__gte=start, date__lte=end))
        pct = float(spent / c.monthly_budget * 100)
        result.append({
            "category": c,
            "spent": spent,
            "limit": c.monthly_budget,
            "left": c.monthly_budget - spent,
            "pct": round(pct, 1),
            "bar": min(pct, 100),
            "level": "danger" if pct >= 100 else "warning" if pct >= 80 else "brand",
        })
    return sorted(result, key=lambda b: -b["pct"])


# --------------------------------------------------------------------------- natural-language questions

def run_query(user, q):
    """Execute the structured query the AI produced. All numbers come from the database."""
    today = timezone.localdate()
    start = parse_date(q.get("start_date"), today.replace(day=1))
    end = parse_date(q.get("end_date"), today)
    if start > end:
        start, end = end, start

    qs = Transaction.objects.filter(user=user, date__gte=start, date__lte=end).select_related("category")
    type_ = str(q.get("type") or "both").lower()
    if type_ in (INCOME, EXPENSE):
        qs = qs.filter(type=type_)

    names = [n for n in (q.get("categories") or []) if isinstance(n, str) and n.strip()]
    if names:
        cond = Q()
        for n in names:
            cond |= Q(category__name__iexact=n.strip())
        qs = qs.filter(cond)

    account_names = [n for n in (q.get("accounts") or []) if isinstance(n, str) and n.strip()]
    if account_names:
        cond = Q()
        for n in account_names:
            cond |= Q(account__name__iexact=n.strip())
        qs = qs.filter(cond)

    keyword = str(q.get("keyword") or "").strip()
    if keyword:
        qs = qs.filter(Q(note__icontains=keyword) | Q(raw_text__icontains=keyword))

    income = _sum(qs.filter(type=INCOME))
    expense = _sum(qs.filter(type=EXPENSE))

    group_by = q.get("group_by") or "none"
    groups = []
    if group_by == "category":
        groups = [
            {"label": f'{r["category__icon"] or ""} {category_label(r["category__name"])}'.strip(),
             "type": r["type"], "total": r["total"], "n": r["n"]}
            for r in qs.values("category__name", "category__icon", "type")
            .annotate(total=Sum("amount"), n=Count("id")).order_by("type", "-total")
        ]
    elif group_by == "day":
        groups = [
            {"label": date_format(r["date"], "d M Y"), "type": r["type"], "total": r["total"], "n": r["n"]}
            for r in qs.values("date", "type").annotate(total=Sum("amount"), n=Count("id")).order_by("date", "type")
        ]
    elif group_by == "month":
        groups = [
            {"label": date_format(r["m"], "F Y"), "type": r["type"], "total": r["total"], "n": r["n"]}
            for r in qs.annotate(m=TruncMonth("date")).values("m", "type")
            .annotate(total=Sum("amount"), n=Count("id")).order_by("m", "type")
        ]

    return {
        "title": str(q.get("title") or gettext("Your question")),
        "start": start,
        "end": end,
        "type": type_,
        "categories": names,
        "accounts": account_names,
        "keyword": keyword,
        "income": income,
        "expense": expense,
        "net": income - expense,
        "count": qs.count(),
        "groups": groups,
        "items": list(qs[:50]),
    }


def run_debt_query(user, q):
    """Answer a debts question ("Rahim er kache koto pabo?") from the saved debts. All numbers come from the database."""
    direction = str(q.get("debt_direction") or "both").lower()
    names = [" ".join(n.split()).lower() for n in (q.get("people") or []) if isinstance(n, str) and n.strip()]
    debts = debts_with_totals(user)
    if direction in (LENT, BORROWED):
        debts = [d for d in debts if d.direction == direction]
    if names:
        exact = [d for d in debts if d.person.lower() in names]
        debts = exact or [d for d in debts if any(n in d.person.lower() or d.person.lower() in n for n in names)]
    if not q.get("include_closed") and not (names and not any(not d.closed for d in debts)):
        debts = [d for d in debts if not d.closed]   # a named person with only closed debts still gets an answer
    by_person = {}
    for d in debts:
        p = by_person.setdefault(d.person.lower(), {"person": d.person, "owed_to_me": Decimal("0"), "i_owe": Decimal("0"), "n": 0})
        p["n"] += 1
        if not d.closed:
            p["owed_to_me" if d.direction == LENT else "i_owe"] += max(d.remaining, Decimal("0"))
    people = sorted(by_person.values(), key=lambda p: -(p["owed_to_me"] + p["i_owe"]))
    for p in people:
        p["net"] = p["owed_to_me"] - p["i_owe"]
    summary = debt_summary(user, debts)
    debts.sort(key=lambda d: (d.closed, not d.overdue, d.due_date or d.date))
    return {
        "kind": "debts",
        "title": str(q.get("title") or gettext("Your question")),
        "direction": direction if direction in (LENT, BORROWED) else "both",
        "names": [p["person"] for p in people] if names else [],
        "summary": summary,
        "people": people if len(people) > 1 else [],
        "debts": debts,
    }


# --------------------------------------------------------------------------- AI daily limits
# The free Gemini/Groq tiers only allow so many tokens/requests per day for the whole app.
# Every user gets a daily token allowance (AI_DAILY_TOKENS_PER_USER), shown as "% used".
# The app as a whole stops at AI_DAILY_TOKENS_TOTAL tokens or AI_DAILY_REQUESTS_TOTAL requests,
# whichever comes first, so one busy day can't use up the free quota. Days follow Bangladesh time.

def ai_quota(user):
    today = timezone.localdate()
    mine = AIUsage.objects.filter(user=user, date=today).first()
    used = mine.tokens if mine else 0
    totals = AIUsage.objects.filter(date=today).aggregate(t=Sum("tokens"), n=Sum("count"))
    app_full = (
        (totals["t"] or 0) >= settings.AI_DAILY_TOKENS_TOTAL
        or (totals["n"] or 0) >= settings.AI_DAILY_REQUESTS_TOTAL
    )
    limit = settings.AI_DAILY_TOKENS_PER_USER
    pct = min(round(used / limit * 100), 100) if limit else 100
    return {
        "tokens_used": used,
        "tokens_limit": limit,
        "pct_used": pct,
        "pct_left": 100 - pct,
        "can_use": used < limit and not app_full,
        "app_full": app_full,
    }


def record_ai_usage(user, tokens):
    """Add one successful AI request and the tokens it used to today's total."""
    row, _ = AIUsage.objects.get_or_create(user=user, date=timezone.localdate())
    AIUsage.objects.filter(pk=row.pk).update(count=F("count") + 1, tokens=F("tokens") + max(int(tokens), 0))
