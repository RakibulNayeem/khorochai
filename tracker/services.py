"""
Business logic: default categories, validating AI output, and all money calculations.
"""
import calendar
import datetime as dt
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext

from .models import (
    ACCOUNT_KIND_ORDER, BALANCE, EXPENSE, INCOME, TRANSFER, Account, AIUsage, Category, Transaction, Transfer, category_label,
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


def account_balances(user):
    """[{account, balance, income, expense}] using SQL sums. Balance = opening + income - expense ± transfers."""
    accounts = sorted_accounts(user)
    sums = {}
    for r in Transaction.objects.filter(user=user, account__isnull=False).values("account_id", "type").annotate(t=Sum("amount")):
        sums[(r["account_id"], r["type"])] = r["t"]
    t_in = {r["to_account_id"]: r["t"] for r in Transfer.objects.filter(user=user).values("to_account_id").annotate(t=Sum("amount"))}
    t_out = {r["from_account_id"]: r["t"] for r in Transfer.objects.filter(user=user).values("from_account_id").annotate(t=Sum("amount"))}
    rows = []
    for a in accounts:
        inc = sums.get((a.id, INCOME), Decimal("0"))
        exp = sums.get((a.id, EXPENSE), Decimal("0"))
        bal = a.opening_balance + inc - exp + t_in.get(a.id, Decimal("0")) - t_out.get(a.id, Decimal("0"))
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
        if type_ not in (INCOME, EXPENSE, TRANSFER, BALANCE):
            type_ = EXPENSE
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
        cat_name = " ".join(str(item.get("category") or "").split())[:60]
        match = next((c for c in cats if c.type == type_ and c.name.lower() == cat_name.lower()), None)
        acc_name = str(item.get("account") or "").strip().lower()
        account = next((a for a in accounts if a.name.lower() == acc_name), None) or fallback_account
        rows.append({
            "type": type_,
            "amount": amount,
            "category_id": match.id if match else None,
            "new_category": "" if match else (cat_name or ("Other Expense" if type_ == EXPENSE else "Other Income")),
            "account_id": account.id if account else None,
            "to_account_id": None,
            "note": str(item.get("note") or "")[:255],
            "date": date,
        })
    if not rows and not message:
        message = gettext("No income or expense with an amount was found in your message.")
    return rows, message


def _balance_row(item, accounts, fallback_account, today):
    name = str(item.get("account") or "").strip().lower()
    account = next((a for a in accounts if a.name.lower() == name), None) or fallback_account
    return {
        "type": BALANCE,
        "amount": parse_balance(item.get("amount")),
        "category_id": None,
        "new_category": "",
        "account_id": account.id if account else None,
        "to_account_id": None,
        "note": "",
        "date": today,
    }


def _transfer_row(item, amount, date, accounts, fallback_account):
    """Match both sides of a transfer; fill a missing side so the preview always has two different accounts."""
    def find(name):
        name = str(name or "").strip().lower()
        return next((a for a in accounts if a.name.lower() == name), None)

    src, dst = find(item.get("account")), find(item.get("to_account"))
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
    }


def merge_with_transfers(transactions, transfers, limit=None):
    """Transactions and transfers in one list, newest first (for the tables)."""
    items = sorted(list(transactions) + list(transfers), key=lambda t: (t.date, t.created_at), reverse=True)
    return items[:limit] if limit else items


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
