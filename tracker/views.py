import csv
import logging

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _, ngettext
from django.views.decorators.http import require_POST

from . import llm, services
from .context_processors import THEME_COOKIE, THEMES
from .forms import AccountForm, CategoryForm, TransactionForm, TransferForm, UsernameForm
from .models import BALANCE, EXPENSE, INCOME, TRANSFER, Account, Category, Transaction, Transfer
from .templatetags.money import money

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 2000


# --------------------------------------------------------------------------- settings

def preferences(request):
    """Theme + language (cookies, so they also work on the login page) and, when logged in, the username."""
    username_form = None
    if request.user.is_authenticated:
        posted = request.method == "POST" and "username" in request.POST
        username_form = UsernameForm(request.POST if posted else None, instance=request.user)
        if posted:
            if username_form.is_valid():
                username_form.save()
                messages.success(request, _("Username updated."))
                return redirect("preferences")
            return render(request, "tracker/preferences.html", {"username_form": username_form})
    if request.method == "POST":
        nxt = request.POST.get("next", "")
        if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
            nxt = reverse("preferences")
        response = redirect(nxt)
        year = 60 * 60 * 24 * 365
        theme = request.POST.get("theme")
        if theme in THEMES:
            response.set_cookie(THEME_COOKIE, theme, max_age=year, samesite="Lax")
        lang = request.POST.get("language")
        if lang in dict(django_settings.LANGUAGES):
            response.set_cookie(django_settings.LANGUAGE_COOKIE_NAME, lang, max_age=year, samesite="Lax")
        return response
    return render(request, "tracker/preferences.html", {"username_form": username_form})


# --------------------------------------------------------------------------- dashboard

@login_required
def dashboard(request):
    services.ensure_default_categories(request.user)
    services.ensure_default_accounts(request.user)
    month = services.parse_month(request.GET.get("month"))
    start, end = services.month_bounds(month)
    summary = services.summarize(request.user, start, end)
    period = request.GET.get("recent")
    if period not in services.RECENT_PERIODS:
        period = "week"
    r_start, r_end = services.period_bounds(period)
    context = {
        "month": month,
        "prev_month": services.shift_month(month, -1).strftime("%Y-%m"),
        "next_month": services.shift_month(month, 1).strftime("%Y-%m"),
        "is_current_month": month == timezone.localdate().replace(day=1),
        "summary": summary,
        "chart_data": summary["chart"],
        "budgets": services.budget_status(request.user, start, end),
        "all_time_balance": services.all_time_balance(request.user),
        "accounts": services.account_balances(request.user),
        "ai_quota": services.ai_quota(request.user),
        "recent": services.merge_with_transfers(
            Transaction.objects.filter(user=request.user, date__gte=r_start, date__lte=r_end)
            .select_related("category", "account")[:50],
            Transfer.objects.filter(user=request.user, date__gte=r_start, date__lte=r_end)
            .select_related("from_account", "to_account")[:50],
            limit=50,
        ),
        "recent_period": period,
        "recent_start": r_start,
        "recent_end": r_end,
        "recent_periods": [("day", _("Day")), ("week", _("Week")), ("month", _("Month"))],
        "last_text": request.session.pop("last_ai_text", ""),
    }
    return render(request, "tracker/dashboard.html", context)


# --------------------------------------------------------------------------- AI: add transactions

@login_required
@require_POST
def ai_parse(request):
    text = (request.POST.get("text") or "").strip()
    if not text:
        messages.warning(request, _("Please type what you spent or earned."))
        return redirect("dashboard")
    if len(text) > MAX_INPUT_CHARS:
        messages.warning(request, _("Message is too long (max %(n)s characters).") % {"n": MAX_INPUT_CHARS})
        request.session["last_ai_text"] = text[:MAX_INPUT_CHARS]
        return redirect("dashboard")

    if not services.ai_quota(request.user)["can_use"]:
        messages.warning(request, _limit_message(request.user))
        request.session["last_ai_text"] = text
        return redirect("dashboard")

    exp_names, inc_names = services.category_names(request.user)
    account_names = [a.name for a in Account.objects.filter(user=request.user)]
    usage = {}
    try:
        data = llm.parse_transactions(text, timezone.localdate(), exp_names, inc_names, account_names, usage=usage)
        services.record_ai_usage(request.user, usage.get("tokens", 0))
    except llm.LLMError as exc:
        logger.error("AI parse failed: %s", exc)
        if usage.get("tokens"):  # a reply came back but was unusable: those tokens were still spent
            services.record_ai_usage(request.user, usage["tokens"])
        messages.error(request, _("The AI is busy or not configured right now. Please try again, or add it manually."))
        request.session["last_ai_text"] = text
        return redirect("dashboard")

    rows, ai_message = services.clean_parsed(data, request.user)
    if not rows:
        messages.info(request, ai_message)
        request.session["last_ai_text"] = text
        return redirect("dashboard")

    return render(request, "tracker/preview.html", {
        "rows": rows,
        "raw_text": text,
        "categories": Category.objects.filter(user=request.user),
        "accounts": services.sorted_accounts(request.user),
        "row_count": len(rows),
    })


@login_required
@require_POST
def ai_confirm(request):
    try:
        count = min(int(request.POST.get("row_count", 0)), 30)
    except ValueError:
        count = 0
    raw_text = (request.POST.get("raw_text") or "")[:MAX_INPUT_CHARS]
    user = request.user
    today = timezone.localdate()
    saved, new_cats, skipped, balances = 0, [], 0, []

    with db_transaction.atomic():
        for i in range(count):
            if not request.POST.get(f"include_{i}"):
                continue
            type_ = request.POST.get(f"type_{i}")
            if type_ not in (INCOME, EXPENSE, TRANSFER, BALANCE):
                skipped += 1
                continue
            if type_ == BALANCE:
                account = Account.objects.filter(user=user, pk=request.POST.get(f"account_{i}") or 0).first()
                target = services.parse_balance(request.POST.get(f"amount_{i}"))
                if not account or target is None:
                    skipped += 1
                    continue
                services.set_account_balance(account, target)
                balances.append(f"{account.name} {money(target)}")
                continue
            amount = services.parse_amount(request.POST.get(f"amount_{i}"))
            if amount is None:
                skipped += 1
                continue
            if type_ == TRANSFER:
                src = Account.objects.filter(user=user, pk=request.POST.get(f"account_{i}") or 0).first()
                dst = Account.objects.filter(user=user, pk=request.POST.get(f"to_account_{i}") or 0).first()
                if not src or not dst or src == dst:
                    skipped += 1
                    continue
                Transfer.objects.create(
                    user=user, from_account=src, to_account=dst, amount=amount,
                    date=services.parse_date(request.POST.get(f"date_{i}"), today),
                    note=(request.POST.get(f"note_{i}") or "")[:255],
                )
                saved += 1
                continue
            cat_value = request.POST.get(f"category_{i}", "")
            category = None
            if cat_value == "__new__":
                name = (request.POST.get(f"new_category_{i}") or "").strip()
                if name:
                    category, created = services.get_or_create_category(user, name, type_)
                    if created:
                        new_cats.append(category.name)
            elif cat_value:
                category = Category.objects.filter(user=user, pk=cat_value).first()
            account = Account.objects.filter(user=user, pk=request.POST.get(f"account_{i}") or 0).first()
            if category and category.type != type_:
                # user switched the type; keep the name but use a category of the right type
                category, created = services.get_or_create_category(user, category.name, type_)
                if created:
                    new_cats.append(category.name)
            Transaction.objects.create(
                user=user,
                type=type_,
                amount=amount,
                category=category,
                account=account,
                note=(request.POST.get(f"note_{i}") or "")[:255],
                date=services.parse_date(request.POST.get(f"date_{i}"), today),
                raw_text=raw_text,
                source="ai",
            )
            saved += 1

    if saved:
        msg = ngettext("Saved %(n)s transaction.", "Saved %(n)s transactions.", saved) % {"n": saved}
        if new_cats:
            msg += " " + _("New categories: %(names)s.") % {"names": ", ".join(new_cats)}
        messages.success(request, msg)
    if balances:
        messages.success(request, _("Balance updated: %(list)s.") % {"list": ", ".join(balances)})
    if skipped:
        messages.warning(request, _("%(n)s row(s) skipped because the amount, type or accounts were invalid.") % {"n": skipped})
    if not saved and not skipped and not balances:
        messages.info(request, _("Nothing was saved."))
    return redirect("dashboard")


# --------------------------------------------------------------------------- AI: ask questions

@login_required
def ask(request):
    question = (request.POST.get("question") or "").strip() if request.method == "POST" else ""
    result = None
    if question and not services.ai_quota(request.user)["can_use"]:
        messages.warning(request, _limit_message(request.user))
    elif question:
        exp_names, inc_names = services.category_names(request.user)
        account_names = [a.name for a in Account.objects.filter(user=request.user)]
        usage = {}
        try:
            q = llm.build_query(question[:500], timezone.localdate(), exp_names, inc_names,
                                language=request.LANGUAGE_CODE, account_names=account_names, usage=usage)
            services.record_ai_usage(request.user, usage.get("tokens", 0))
            result = services.run_query(request.user, q)
        except llm.LLMError as exc:
            logger.error("AI query failed: %s", exc)
            if usage.get("tokens"):
                services.record_ai_usage(request.user, usage["tokens"])
            messages.error(request, _("The AI is busy or not configured right now. Please try again."))
    return render(request, "tracker/ask.html", {"question": question, "result": result,
                                               "ai_quota": services.ai_quota(request.user)})


def _limit_message(user):
    if services.ai_quota(user)["app_full"]:
        return _("The AI has reached today's limit for everyone. It resets at midnight. "
                 "You can still add transactions manually.")
    return _("You've used 100% of today's AI allowance. It resets at midnight. "
             "You can still add transactions manually.")


# --------------------------------------------------------------------------- transactions

def _filtered_transactions(request):
    qs = Transaction.objects.filter(user=request.user).select_related("category", "account")
    month = request.GET.get("month", "")
    if month:
        start, end = services.month_bounds(services.parse_month(month))
        qs = qs.filter(date__gte=start, date__lte=end)
    date_from = services.parse_date(request.GET.get("from"))
    date_to = services.parse_date(request.GET.get("to"))
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    type_ = request.GET.get("type", "")
    if type_ in (INCOME, EXPENSE):
        qs = qs.filter(type=type_)
    acc = request.GET.get("account", "")
    if acc == "none":
        qs = qs.filter(account__isnull=True)
    elif acc.isdigit():
        qs = qs.filter(account_id=int(acc))
    cat = request.GET.get("category", "")
    if cat == "none":
        qs = qs.filter(category__isnull=True)
    elif cat.isdigit():
        qs = qs.filter(category_id=int(cat))
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(note__icontains=q) | Q(raw_text__icontains=q) | Q(category__name__icontains=q))
    return qs


def _filtered_transfers(request):
    """Transfers matching the same filters (they have no category, so a category filter hides them)."""
    type_ = request.GET.get("type", "")
    if type_ in (INCOME, EXPENSE) or request.GET.get("category"):
        return Transfer.objects.none()
    qs = Transfer.objects.filter(user=request.user).select_related("from_account", "to_account")
    month = request.GET.get("month", "")
    if month:
        start, end = services.month_bounds(services.parse_month(month))
        qs = qs.filter(date__gte=start, date__lte=end)
    date_from = services.parse_date(request.GET.get("from"))
    date_to = services.parse_date(request.GET.get("to"))
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    acc = request.GET.get("account", "")
    if acc.isdigit():
        qs = qs.filter(Q(from_account_id=int(acc)) | Q(to_account_id=int(acc)))
    elif acc == "none":
        return Transfer.objects.none()
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(note__icontains=q)
    return qs


@login_required
def transaction_list(request):
    qs = _filtered_transactions(request)
    rows = qs.none() if request.GET.get("type") == TRANSFER else qs
    items = services.merge_with_transfers(rows, _filtered_transfers(request))
    page = Paginator(items, 50).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    income = services._sum(qs.filter(type=INCOME))
    expense = services._sum(qs.filter(type=EXPENSE))
    return render(request, "tracker/transactions.html", {
        "page": page,
        "income": income,
        "expense": expense,
        "net": income - expense,
        "categories": Category.objects.filter(user=request.user),
        "accounts": services.sorted_accounts(request.user),
        "f": request.GET,
        "querystring": params.urlencode(),
    })


@login_required
def transaction_create(request):
    form = TransactionForm(request.POST or None, user=request.user,
                           initial={"date": timezone.localdate(), "type": EXPENSE,
                                    "account": services.default_account(request.user)})
    if request.method == "POST" and form.is_valid():
        t = form.save(commit=False)
        t.user = request.user
        t.source = "manual"
        t.save()
        messages.success(request, _("Transaction added."))
        return redirect("dashboard")
    return render(request, "tracker/form.html", {"form": form, "title": _("Add transaction")})


@login_required
def transaction_edit(request, pk):
    t = get_object_or_404(Transaction, pk=pk, user=request.user)
    form = TransactionForm(request.POST or None, instance=t, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Transaction updated."))
        return redirect("transactions")
    return render(request, "tracker/form.html", {"form": form, "title": _("Edit transaction"),
                                                "delete_url": reverse("transaction_delete", args=[t.pk])})


@login_required
@require_POST
def transaction_delete(request, pk):
    t = get_object_or_404(Transaction, pk=pk, user=request.user)
    t.delete()
    messages.success(request, _("Transaction deleted."))
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect("transactions")


@login_required
def export_csv(request):
    qs = _filtered_transactions(request)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="transactions.csv"'
    response.write("﻿")  # BOM so Excel shows Bangla correctly
    writer = csv.writer(response)
    writer.writerow(["Date", "Type", "Amount", "Category", "Account", "Note", "Source", "Original text"])
    for t in qs:
        writer.writerow([t.date, t.type, t.amount, t.category.name if t.category else "",
                         t.account.name if t.account else "", t.note, t.source, t.raw_text])
    for t in _filtered_transfers(request):
        writer.writerow([t.date, "transfer", t.amount, "", f"{t.from_account.name} -> {t.to_account.name}",
                         t.note, "", ""])
    return response


# --------------------------------------------------------------------------- categories

@login_required
def category_list(request):
    month = timezone.localdate().replace(day=1)
    start, end = services.month_bounds(month)
    cats = Category.objects.filter(user=request.user)
    spent = {
        r["category_id"]: r["t"]
        for r in Transaction.objects.filter(user=request.user, date__gte=start, date__lte=end)
        .values("category_id").annotate(t=services.Sum("amount"))
    }
    rows = [{"c": c, "this_month": spent.get(c.id, 0)} for c in cats]
    return render(request, "tracker/categories.html", {
        "expense_rows": [r for r in rows if r["c"].type == EXPENSE],
        "income_rows": [r for r in rows if r["c"].type == INCOME],
    })


@login_required
def category_create(request):
    form = CategoryForm(request.POST or None, user=request.user,
                        initial={"type": request.GET.get("type", EXPENSE), "icon": "🏷️"})
    if request.method == "POST" and form.is_valid():
        c = form.save(commit=False)
        c.user = request.user
        c.save()
        messages.success(request, _("Category '%(name)s' added.") % {"name": c.name})
        return redirect("categories")
    return render(request, "tracker/form.html", {"form": form, "title": _("Add category")})


@login_required
def category_edit(request, pk):
    c = get_object_or_404(Category, pk=pk, user=request.user)
    form = CategoryForm(request.POST or None, instance=c, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Category updated."))
        return redirect("categories")
    return render(request, "tracker/form.html", {"form": form, "title": _("Edit category"),
                                                "delete_url": reverse("category_delete", args=[c.pk])})


@login_required
@require_POST
def category_delete(request, pk):
    c = get_object_or_404(Category, pk=pk, user=request.user)
    n = c.transactions.count()
    c.delete()
    msg = _("Category '%(name)s' deleted.") % {"name": c.name}
    if n:
        msg += " " + _("%(n)s transaction(s) are now Uncategorized.") % {"n": n}
    messages.success(request, msg)
    return redirect("categories")


# --------------------------------------------------------------------------- accounts & transfers

def _save_account(form, user):
    a = form.save(commit=False)
    a.user = user
    a.save()
    if a.is_default:
        Account.objects.filter(user=user).exclude(pk=a.pk).update(is_default=False)
    return a


@login_required
def account_list(request):
    services.ensure_default_accounts(request.user)
    rows = services.account_balances(request.user)
    return render(request, "tracker/accounts.html", {
        "rows": rows,
        "total": sum((r["balance"] for r in rows), 0),
        "transfers": Transfer.objects.filter(user=request.user).select_related("from_account", "to_account")[:30],
    })


@login_required
def account_create(request):
    form = AccountForm(request.POST or None, user=request.user,
                       initial={"kind": request.GET.get("kind", "mobile"), "icon": "📱", "color": "#0ea5e9"})
    if request.method == "POST" and form.is_valid():
        a = _save_account(form, request.user)
        messages.success(request, _("Account '%(name)s' added.") % {"name": a.label})
        return redirect("accounts")
    return render(request, "tracker/form.html", {"form": form, "title": _("Add account")})


@login_required
def account_edit(request, pk):
    a = get_object_or_404(Account, pk=pk, user=request.user)
    form = AccountForm(request.POST or None, instance=a, user=request.user)
    if request.method == "POST" and form.is_valid():
        _save_account(form, request.user)
        messages.success(request, _("Account updated."))
        return redirect("accounts")
    return render(request, "tracker/form.html", {"form": form, "title": _("Edit account"),
                                                "delete_url": reverse("account_delete", args=[a.pk])})


@login_required
@require_POST
def account_delete(request, pk):
    a = get_object_or_404(Account, pk=pk, user=request.user)
    n = a.transactions.count()
    a.delete()
    msg = _("Account '%(name)s' deleted.") % {"name": a.label}
    if n:
        msg += " " + _("%(n)s transaction(s) no longer have an account.") % {"n": n}
    messages.success(request, msg)
    return redirect("accounts")


@login_required
def transfer_create(request):
    initial = {"date": timezone.localdate()}
    if request.GET.get("from", "").isdigit():
        initial["from_account"] = request.GET["from"]
    form = TransferForm(request.POST or None, user=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        t = form.save(commit=False)
        t.user = request.user
        t.save()
        messages.success(request, _("Transfer saved."))
        return redirect("accounts")
    return render(request, "tracker/form.html", {"form": form, "title": _("Move money between accounts")})


@login_required
def transfer_edit(request, pk):
    t = get_object_or_404(Transfer, pk=pk, user=request.user)
    form = TransferForm(request.POST or None, instance=t, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Transfer updated."))
        return redirect("accounts")
    return render(request, "tracker/form.html", {"form": form, "title": _("Edit transfer"),
                                                "delete_url": reverse("transfer_delete", args=[t.pk])})


@login_required
@require_POST
def transfer_delete(request, pk):
    get_object_or_404(Transfer, pk=pk, user=request.user).delete()
    messages.success(request, _("Transfer deleted."))
    return redirect("accounts")
