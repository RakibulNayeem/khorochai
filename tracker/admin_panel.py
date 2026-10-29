"""Our own admin panel at /admin/: users, AI usage, app activity and user actions. Staff only.

It shows counts, never users' amounts, the people they owe or what they typed.
"""
import datetime as dt
from functools import wraps

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.db.models import Count, Exists, F, OuterRef, Q, Subquery, Sum
from django.db.models.functions import TruncDate
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Account, AIUsage, Category, Debt, Profile, Transaction, Transfer

CHART_DAYS = 30
USER_SORTS = {"joined": F("date_joined").desc(), "login": F("last_login").desc(nulls_last=True), "email": F("email").asc()}
USER_FILTERS = ("all", "active", "inactive", "unverified", "staff")


def staff_required(view):
    """Logged-out visitors go to the login page; logged-in non-staff get a plain 404."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_staff:
            raise Http404
        return view(request, *args, **kwargs)
    return wrapper


def _per_day(qs, start, field="created_at"):
    """{date: row count} for rows whose timestamp `field` falls on or after `start` (Bangladesh days)."""
    rows = (qs.filter(**{f"{field}__date__gte": start}).annotate(day=TruncDate(field))
            .values("day").annotate(n=Count("id")))
    return {r["day"]: r["n"] for r in rows}


def _series(by_day, days):
    return [by_day.get(d, 0) for d in days]


def _pct(part, whole):
    return min(round(part / whole * 100), 100) if whole else 0


def _verified():
    return Exists(EmailAddress.objects.filter(user=OuterRef("pk"), verified=True))


@staff_required
def overview(request):
    today = timezone.localdate()
    start = today - dt.timedelta(days=CHART_DAYS - 1)
    days = [start + dt.timedelta(days=i) for i in range(CHART_DAYS)]
    week_ago, month_ago = today - dt.timedelta(days=6), today - dt.timedelta(days=29)

    users = User.objects.all()
    total_users = users.count()
    verified = users.filter(_verified()).count()

    ai_today = AIUsage.objects.filter(date=today).aggregate(n=Sum("count"), t=Sum("tokens"))
    ai_requests, ai_tokens = ai_today["n"] or 0, ai_today["t"] or 0
    ai_days = {r["date"]: r for r in AIUsage.objects.filter(date__gte=start).values("date")
               .annotate(n=Sum("count"), t=Sum("tokens"))}
    per_user_limit = settings.AI_DAILY_TOKENS_PER_USER
    top_ai = [
        {"user": r.user, "requests": r.count, "tokens": r.tokens, "pct": _pct(r.tokens, per_user_limit)}
        for r in AIUsage.objects.filter(date=today, count__gt=0).select_related("user").order_by("-tokens")[:10]
    ]

    tx = Transaction.objects.all()
    ai_entries = _per_day(tx.filter(source="ai"), start)
    manual_entries = _per_day(tx.exclude(source="ai"), start)
    transfers = _per_day(Transfer.objects.all(), start)
    debts = _per_day(Debt.objects.all(), start)
    signups = _per_day(users, start, "date_joined")
    active_7 = (users.filter(Q(transactions__created_at__date__gte=week_ago) | Q(ai_usage__date__gte=week_ago))
                .distinct().count())

    month_tx = tx.filter(created_at__date__gte=month_ago)
    features = [
        ("Entries added with AI", month_tx.filter(source="ai").count()),
        ("Entries added by hand", month_tx.exclude(source="ai").count()),
        ("Transfers", Transfer.objects.filter(created_at__date__gte=month_ago).count()),
        ("Debts added", Debt.objects.filter(created_at__date__gte=month_ago).count()),
        ("AI requests (add, voice, Ask AI)", AIUsage.objects.filter(date__gte=month_ago).aggregate(n=Sum("count"))["n"] or 0),
    ]
    feature_max = max((n for _, n in features), default=0)

    return render(request, "tracker/admin/overview.html", {
        "tab": "overview",
        "today": today,
        "stats": {
            "users": total_users,
            "new_today": users.filter(date_joined__date=today).count(),
            "new_7": users.filter(date_joined__date__gte=week_ago).count(),
            "new_30": users.filter(date_joined__date__gte=month_ago).count(),
            "active_7": active_7,
            "verified": verified,
            "verified_pct": _pct(verified, total_users),
            "google": SocialAccount.objects.filter(provider="google").count(),
            "inactive": users.filter(is_active=False).count(),
        },
        "ai": {
            "requests": ai_requests,
            "requests_limit": settings.AI_DAILY_REQUESTS_TOTAL,
            "requests_pct": _pct(ai_requests, settings.AI_DAILY_REQUESTS_TOTAL),
            "tokens": ai_tokens,
            "tokens_limit": settings.AI_DAILY_TOKENS_TOTAL,
            "tokens_pct": _pct(ai_tokens, settings.AI_DAILY_TOKENS_TOTAL),
            "per_user_limit": per_user_limit,
            "users_today": len(top_ai),
            "top": top_ai,
        },
        "features": [{"label": label, "n": n, "bar": _pct(n, feature_max)} for label, n in features],
        "totals": {
            "transactions": tx.count(),
            "transfers": Transfer.objects.count(),
            "debts": Debt.objects.count(),
            "accounts": Account.objects.count(),
            "budgets": Category.objects.filter(monthly_budget__isnull=False).count(),
            "other_currency": Profile.objects.exclude(currency="BDT").count(),
        },
        "chart_data": {
            "days": [d.strftime("%d %b") for d in days],
            "signups": _series(signups, days),
            "ai_requests": [ai_days.get(d, {}).get("n") or 0 for d in days],
            "ai_tokens": [ai_days.get(d, {}).get("t") or 0 for d in days],
            "ai_entries": _series(ai_entries, days),
            "manual_entries": _series(manual_entries, days),
            "transfers": _series(transfers, days),
            "debts": _series(debts, days),
        },
    })


@staff_required
def user_list(request):
    today = timezone.localdate()
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status") if request.GET.get("status") in USER_FILTERS else "all"
    sort = request.GET.get("sort") if request.GET.get("sort") in USER_SORTS else "joined"

    users = User.objects.annotate(
        is_verified=_verified(),
        entries=Count("transactions", distinct=True),
        ai_today=Subquery(AIUsage.objects.filter(user=OuterRef("pk"), date=today).values("count")[:1]),
    )
    if q:
        users = users.filter(Q(email__icontains=q) | Q(username__icontains=q))
    if status == "active":
        users = users.filter(is_active=True)
    elif status == "inactive":
        users = users.filter(is_active=False)
    elif status == "unverified":
        users = users.filter(is_verified=False)
    elif status == "staff":
        users = users.filter(is_staff=True)
    users = users.order_by(USER_SORTS[sort], "-pk")

    page = Paginator(users, 25).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "tracker/admin/users.html", {
        "tab": "users",
        "page": page,
        "q": q,
        "status": status,
        "sort": sort,
        "statuses": USER_FILTERS,
        "sorts": [("joined", "Newest"), ("login", "Last login"), ("email", "Email")],
        "querystring": params.urlencode(),
    })


def _manage_block_reason(actor, target):
    """Why `actor` may not deactivate, reset or delete `target`, or "" when they may."""
    if actor.pk == target.pk:
        return "You can't do this to your own account."
    if (target.is_staff or target.is_superuser) and not actor.is_superuser:
        return "Only a superuser can change another staff account."
    return ""


@staff_required
def user_detail(request, pk):
    target = get_object_or_404(User, pk=pk)
    today = timezone.localdate()
    start = today - dt.timedelta(days=13)
    usage = {r.date: r for r in AIUsage.objects.filter(user=target, date__gte=start)}
    days = [start + dt.timedelta(days=i) for i in range(14)]
    ai_today = usage.get(today)
    return render(request, "tracker/admin/user_detail.html", {
        "tab": "users",
        "target": target,
        "emails": EmailAddress.objects.filter(user=target),
        "google": SocialAccount.objects.filter(user=target, provider="google").exists(),
        "currency": Profile.objects.filter(user=target).values_list("currency", flat=True).first() or "BDT",
        "counts": [
            ("Transactions", target.transactions.count()),
            ("…added with AI", target.transactions.filter(source="ai").count()),
            ("Transfers", target.transfers.count()),
            ("Accounts", target.accounts.count()),
            ("Categories", target.categories.count()),
            ("Budgets", target.categories.filter(monthly_budget__isnull=False).count()),
            ("Debts", target.debts.count()),
            ("Open debts", target.debts.filter(closed=False).count()),
        ],
        "last_entry": target.transactions.order_by("-created_at").values_list("created_at", flat=True).first(),
        "ai_today": ai_today,
        "ai_pct": _pct(ai_today.tokens if ai_today else 0, settings.AI_DAILY_TOKENS_PER_USER),
        "ai_limit": settings.AI_DAILY_TOKENS_PER_USER,
        "ai_days": [{"day": d, "requests": usage[d].count if d in usage else 0,
                     "tokens": usage[d].tokens if d in usage else 0} for d in reversed(days)],
        "block_reason": _manage_block_reason(request.user, target),
    })


@staff_required
@require_POST
def user_action(request, pk):
    target = get_object_or_404(User, pk=pk)
    back = redirect("admin_user", pk=target.pk)
    reason = _manage_block_reason(request.user, target)
    if reason:
        messages.error(request, reason)
        return back
    action = request.POST.get("action")
    if action == "deactivate":
        # Django refuses logins and existing sessions of inactive users, so this logs them out everywhere.
        User.objects.filter(pk=target.pk).update(is_active=False)
        messages.success(request, f"{target.email} is deactivated and can no longer log in.")
    elif action == "activate":
        User.objects.filter(pk=target.pk).update(is_active=True)
        messages.success(request, f"{target.email} can log in again.")
    elif action == "reset_ai":
        AIUsage.objects.filter(user=target, date=timezone.localdate()).update(count=0, tokens=0)
        messages.success(request, f"Today's AI usage for {target.email} is reset to 0.")
    elif action == "delete":
        if request.POST.get("confirm_email", "").strip().lower() != target.email.lower():
            messages.error(request, "Type the user's email exactly to delete the account.")
            return back
        email = target.email
        target.delete()  # every model of theirs cascades: entries, accounts, debts, usage, profile, emails
        messages.success(request, f"{email} and all their data are deleted.")
        return redirect("admin_users")
    else:
        messages.error(request, "Unknown action.")
    return back
