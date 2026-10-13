import csv
import logging

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _, ngettext
from django.views.decorators.http import require_POST

from . import llm, services
from .context_processors import THEME_COOKIE, THEMES
from .forms import AccountForm, CategoryForm, DebtForm, DebtRecordForm, ProfileForm, TransactionForm, TransferForm
from .models import (
    BALANCE, BORROW, BORROWED, BUDGET, COLLECT, DEBT, DEBT_AI_TYPES, EXPENSE, INCOME, LEND, LENT, REPAY, TRANSFER,
    Account, Category, Debt, DebtRecord, Transaction, Transfer,
)
from .currency import current as current_currency
from .templatetags.money import money

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 2000


# --------------------------------------------------------------------------- settings

def preferences(request):
    """Theme + language (cookies, so they also work on the login page) and, when logged in, the username."""
    username_form = None
    if request.user.is_authenticated:
        posted = request.method == "POST" and "username" in request.POST
        username_form = ProfileForm(request.POST if posted else None, instance=request.user)
        if posted:
            if username_form.is_valid():
                username_form.save()
                messages.success(request, _("Profile updated."))
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


# --------------------------------------------------------------------------- help & contact

def help_page(request):
    guide = [
        {"icon": "type", "title": _("Add spending or income"), "text": _("Type what happened in the box on the dashboard and press Add with AI."),
         "examples": ["আজ বাজারে ৫৫০ টাকা খরচ, রিকশা ৮০", "gotokal lunch 250 ar recharge 100", "Got my salary 35000"]},
        {"icon": "mic", "title": _("Speak instead of typing"), "text": _("Tap the mic in the box, talk, then tap stop. Check the text, then press Add with AI.")},
        {"icon": "accounts", "title": _("Accounts and moving money"), "text": _("Say which account you used, or move money between your own accounts."),
         "examples": ["bkash e lunch 300 dilam", "ATM theke 2000 tullam", "move 500 from cash to bkash"]},
        {"icon": "balance", "title": _("Set an account balance"), "text": _("Tell the AI how much is in an account right now."),
         "examples": ["amar bkash e 5000 ache", "bank balance 50000"]},
        {"icon": "debt", "title": _("Lend and borrow"), "text": _("Keep track of money you lent or borrowed. Account balances change. Paying back money you borrowed counts as an expense (Financial Expenses)."),
         "examples": ["rahim ke 500 dhar dilam", "karim theke 2000 dhar nilam", "রহিম ৩০০ টাকা ফেরত দিল"]},
        {"icon": "budget", "title": _("Monthly budgets"), "text": _("Set a spending limit for a category and watch the progress bar on the dashboard."),
         "examples": ["khabar e mashe 6000 budget", "transport budget 2000"]},
        {"icon": "ask", "title": _("Ask about your money"), "text": _("Use Ask AI for totals, breakdowns and comparisons."),
         "examples": ["এই মাসে খাবারে কত খরচ হলো?", "Where did I spend most last month?"]},
    ]
    return render(request, "tracker/help.html", {"guide": guide})


def about_page(request):
    return render(request, "tracker/about.html")


# --------------------------------------------------------------------------- dashboard

def landing(request):
    """First page for visitors: what KhorochAi does, then Sign up / Log in."""
    demo_rows = [
        {"category": _("Groceries"), "account": _("Cash"), "amount": money(550), "color": "#84cc16"},
        {"category": _("Transport"), "account": _("Cash"), "amount": money(80), "color": "#0ea5e9"},
        {"category": _("Food"), "account": "bKash", "amount": money(300), "color": "#f97316"},
        {"category": _("Tuition"), "account": "bKash", "amount": money(6000), "color": "#22c55e", "income": True},
    ]
    steps = [
        {"title": _("Type or say it"), "text": _("Write or speak what happened, in English, বাংলা or Banglish. Several things in one message is fine.")},
        {"title": _("Check the preview"), "text": _("See exactly what the AI understood — amount, category, account and date. Fix anything with a tap.")},
        {"title": _("Saved and up to date"), "text": _("Your totals, account balances, budgets and charts update instantly.")},
    ]
    features = [
        {"icon": "mic", "title": _("Voice input"), "text": _("Tap the mic and speak. Your words appear in the box for you to check.")},
        {"icon": "language", "title": _("Your language"), "text": _("Bangla, English, Banglish — or any language you speak. The app itself comes in 9 languages.")},
        {"icon": "accounts", "title": _("All your accounts"), "text": _("Cash, bKash, Nagad, Rocket, bank and card, with transfers between them.")},
        {"icon": "budget", "title": _("Monthly budgets"), "text": _("Set limits per category and watch the progress bars as you spend.")},
        {"icon": "ask", "title": _("Ask AI"), "text": _("“এই মাসে খাবারে কত খরচ হলো?” — get the answer from your own data.")},
        {"icon": "chart", "title": _("Clear dashboard"), "text": _("Income, expense, balance, where your money went and day-by-day charts.")},
        {"icon": "moon", "title": _("Light and dark"), "text": _("A calm design that follows your phone's dark mode, or pick your own.")},
        {"icon": "export", "title": _("Export to CSV"), "text": _("Download your transactions for Excel or Google Sheets any time.")},
    ]
    shot_lang = "bn" if request.LANGUAGE_CODE == "bn" else "en"  # screenshots exist in these two
    return render(request, "tracker/landing.html", {"demo_rows": demo_rows, "steps": steps, "features": features,
                                                   "shot_lang": shot_lang})


def dashboard(request):
    if not request.user.is_authenticated:
        return landing(request)
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
        "debts": services.debt_summary(request.user),
        "ai_quota": services.ai_quota(request.user),
        "greeting": _greeting(request.user),
        "today": timezone.localdate(),
        "recent": services.merge_with_transfers(
            Transaction.objects.filter(user=request.user, date__gte=r_start, date__lte=r_end)
            .select_related("category", "account")[:50],
            Transfer.objects.filter(user=request.user, date__gte=r_start, date__lte=r_end)
            .select_related("from_account", "to_account")[:50],
            services.debt_events(request.user, r_start, r_end),
            limit=50,
        ),
        "recent_period": period,
        "recent_start": r_start,
        "recent_end": r_end,
        "recent_periods": [("day", _("Day")), ("week", _("Week")), ("month", _("Month"))],
        "last_text": request.session.pop("last_ai_text", ""),
    }
    return render(request, "tracker/dashboard.html", context)


def _greeting(user):
    hour = timezone.localtime().hour
    name = user.username
    if 5 <= hour < 12:
        text = _("Good morning, %(name)s")
    elif 12 <= hour < 17:
        text = _("Good afternoon, %(name)s")
    else:
        text = _("Good evening, %(name)s")
    return text % {"name": name}


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
        data = llm.parse_transactions(text, timezone.localdate(), exp_names, inc_names, account_names, usage=usage,
                                      currency=current_currency()[0], people=services.open_debt_people(request.user))
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
        "people": services.all_debt_people(request.user),
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
    saved, new_cats, skipped, balances, budgets, debt_msgs = 0, [], 0, [], [], []

    with db_transaction.atomic():
        for i in range(count):
            if not request.POST.get(f"include_{i}"):
                continue
            type_ = request.POST.get(f"type_{i}")
            if type_ not in (INCOME, EXPENSE, TRANSFER, BALANCE, BUDGET) + DEBT_AI_TYPES:
                skipped += 1
                continue
            if type_ == BUDGET:
                limit = services.parse_balance(request.POST.get(f"amount_{i}"))
                cat_value = request.POST.get(f"category_{i}", "")
                if cat_value == "__new__":
                    name = (request.POST.get(f"new_category_{i}") or "").strip()
                    category, created = services.get_or_create_category(user, name, EXPENSE) if name else (None, False)
                    if created:
                        new_cats.append(category.name)
                else:
                    category = Category.objects.filter(user=user, pk=cat_value or 0).first()
                    if category and category.type != EXPENSE:  # budgets only exist for expense categories
                        category, created = services.get_or_create_category(user, category.name, EXPENSE)
                        if created:
                            new_cats.append(category.name)
                if not category or limit is None or limit < 0:
                    skipped += 1
                    continue
                category.monthly_budget = limit or None  # 0 removes the budget
                category.save(update_fields=["monthly_budget"])
                budgets.append(f"{category} {money(limit)}" if limit else _("%(name)s (removed)") % {"name": category})
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
            if type_ in DEBT_AI_TYPES:
                msg = _save_debt_row(request, i, type_, amount, today)
                if msg is None:
                    skipped += 1
                else:
                    debt_msgs.append(msg)
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
    if budgets:
        messages.success(request, _("Monthly budget set: %(list)s.") % {"list": ", ".join(budgets)})
    for msg in debt_msgs:
        (messages.success if msg[0] else messages.warning)(request, msg[1])
    if skipped:
        messages.warning(request, _("%(n)s row(s) skipped because the amount, type or accounts were invalid.") % {"n": skipped})
    if not saved and not skipped and not balances and not budgets and not debt_msgs:
        messages.info(request, _("Nothing was saved."))
    return redirect("dashboard")


def _save_debt_row(request, i, type_, amount, today):
    """Save one lend/borrow/collect/repay row from the AI preview. Returns (ok, message), or None if invalid."""
    user = request.user
    person = " ".join((request.POST.get(f"person_{i}") or "").split())[:60]
    account = Account.objects.filter(user=user, pk=request.POST.get(f"account_{i}") or 0).first()
    if not person or not account:
        return None
    date = services.parse_date(request.POST.get(f"date_{i}"), today)
    note = (request.POST.get(f"note_{i}") or "")[:255]
    if type_ in (LEND, BORROW):
        due = services.parse_date(request.POST.get(f"due_date_{i}"))
        Debt.objects.create(user=user, direction=LENT if type_ == LEND else BORROWED, person=person, amount=amount,
                            account=account, date=date, due_date=due if due and due >= date else None, note=note)
        text = (_("Lent %(amount)s to %(person)s.") if type_ == LEND else _("Borrowed %(amount)s from %(person)s."))
        return True, text % {"amount": money(amount), "person": person}
    direction = LENT if type_ == COLLECT else BORROWED
    if not services.record_debt_payment(user, person, direction, amount, account, date, note):
        if type_ == COLLECT:
            text = _("%(person)s doesn't owe you anything, so the %(amount)s payment was not saved.")
        else:
            text = _("You don't owe %(person)s anything, so the %(amount)s payment was not saved.")
        return False, text % {"amount": money(amount), "person": person}
    text = (_("%(person)s paid you back %(amount)s.") if type_ == COLLECT else _("You paid %(person)s back %(amount)s."))
    return True, text % {"amount": money(amount), "person": person}


# --------------------------------------------------------------------------- AI: voice input

@login_required
@require_POST
def ai_transcribe(request):
    """Mic button: a short WAV recording -> text that goes into the input box for the user to check."""
    audio = request.FILES.get("audio")
    if not audio:
        return JsonResponse({"error": _("No recording was received.")}, status=400)
    if audio.size > django_settings.VOICE_MAX_BYTES:
        return JsonResponse({"error": _("The recording is too long. Please keep it under a minute.")}, status=400)
    if not services.ai_quota(request.user)["can_use"]:
        return JsonResponse({"error": _limit_message(request.user)}, status=429)
    usage = {}
    try:
        text = llm.transcribe(audio.read(), "audio/wav", usage=usage)
    except llm.LLMError as exc:
        logger.error("Transcription failed: %s", exc)
        return JsonResponse({"error": _("Couldn't turn the recording into text right now. Please try again or type it.")}, status=502)
    services.record_ai_usage(request.user, usage.get("tokens", 0))
    if not text:
        return JsonResponse({"error": _("I couldn't hear anything. Please try again a little closer to the mic.")}, status=422)
    return JsonResponse({"text": text, "quota": services.ai_quota(request.user)})


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


def _filtered_debt_events(request):
    """Debt rows matching the same filters (they have no category, so a category filter hides them)."""
    type_ = request.GET.get("type", "")
    if type_ in (INCOME, EXPENSE, TRANSFER) or request.GET.get("category") or request.GET.get("account") == "none":
        return []
    start = end = None
    month = request.GET.get("month", "")
    if month:
        start, end = services.month_bounds(services.parse_month(month))
    start = services.parse_date(request.GET.get("from")) or start
    end = services.parse_date(request.GET.get("to")) or end
    acc = request.GET.get("account", "")
    return services.debt_events(request.user, start, end, int(acc) if acc.isdigit() else None,
                                request.GET.get("q", "").strip())


@login_required
def transaction_list(request):
    qs = _filtered_transactions(request)
    type_ = request.GET.get("type")
    rows = qs.none() if type_ in (TRANSFER, DEBT) else qs
    transfers = Transfer.objects.none() if type_ == DEBT else _filtered_transfers(request)
    items = services.merge_with_transfers(rows, transfers, _filtered_debt_events(request))
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
    if t.debt_record_id:
        messages.info(request, _("This expense is a debt repayment. Change it here."))
        return redirect("debt_detail", pk=t.debt_record.debt_id)
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
    if t.debt_record_id:
        messages.info(request, _("This expense is a debt repayment. Change it here."))
        return redirect("debt_detail", pk=t.debt_record.debt_id)
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
    for t in _filtered_debt_events(request):
        kind = (f"debt {t.kind}" if t.is_record else f"debt {t.direction}")
        writer.writerow([t.date, kind, t.amount, t.person, t.account.name if t.account else "", t.note, "", ""])
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


# --------------------------------------------------------------------------- debts (lend & borrow)

@login_required
def debt_list(request):
    show_closed = request.GET.get("show") == "closed"
    debts = services.debts_with_totals(request.user)
    summary = services.debt_summary(request.user, debts)

    def order(d):  # overdue first, then by pay-back date (none last), then newest
        return (not d.overdue, d.due_date is None, d.due_date or d.date, -d.date.toordinal())

    shown = sorted((d for d in debts if d.closed == show_closed), key=order)
    if show_closed:
        shown.sort(key=lambda d: d.date, reverse=True)
    return render(request, "tracker/debts.html", {
        "summary": summary,
        "show_closed": show_closed,
        "lent": [d for d in shown if d.direction == LENT],
        "borrowed": [d for d in shown if d.direction == BORROWED],
        "closed_count": sum(1 for d in debts if d.closed),
        "open_count": sum(1 for d in debts if not d.closed),
    })


@login_required
def debt_create(request):
    direction = request.GET.get("type") if request.GET.get("type") in (LENT, BORROWED) else LENT
    initial = {"date": timezone.localdate(), "direction": direction, "account": services.default_account(request.user),
               "person": request.GET.get("person", "")[:60]}
    form = DebtForm(request.POST or None, user=request.user, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.save(commit=False)
        d.user = request.user
        d.save()
        messages.success(request, _("Debt saved."))
        return redirect("debt_detail", pk=d.pk)
    return render(request, "tracker/form.html", {"form": form, "title": _("Add debt"),
                                                "people": services.all_debt_people(request.user)})


@login_required
def debt_detail(request, pk):
    debt = get_object_or_404(Debt.objects.select_related("account"), pk=pk, user=request.user)
    services.debt_totals(debt)
    initial = {"kind": REPAY, "date": timezone.localdate(), "account": debt.account or services.default_account(request.user),
               "amount": debt.remaining if debt.remaining > 0 else None}
    form = DebtRecordForm(request.POST or None, user=request.user, debt=debt, initial=initial)
    if request.method == "POST" and form.is_valid():
        r = form.save(commit=False)
        r.debt = debt
        r.save()
        services.sync_debt_expense(r)
        services.refresh_debt_status(debt)
        if debt.closed:
            messages.success(request, _("Fully paid back. The debt is closed."))
        else:
            messages.success(request, _("Saved. %(left)s left.") % {"left": money(debt.remaining)})
        return redirect("debt_detail", pk=debt.pk)
    history = [{"r": r, "flow": r.flow} for r in debt.records.select_related("account")]
    return render(request, "tracker/debt_detail.html", {"debt": debt, "form": form, "history": history,
                                                       "show_form": request.method == "POST"})


@login_required
def debt_edit(request, pk):
    debt = get_object_or_404(Debt, pk=pk, user=request.user)
    settled_by_hand = debt.closed and services.debt_totals(debt).remaining > 0  # keep it closed
    form = DebtForm(request.POST or None, instance=debt, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        services.sync_debt_expenses(debt)  # a change of person/direction updates the repayment expenses
        if not settled_by_hand:
            services.refresh_debt_status(debt)
        messages.success(request, _("Debt updated."))
        return redirect("debt_detail", pk=debt.pk)
    return render(request, "tracker/form.html", {"form": form, "title": _("Edit debt"),
                                                "people": services.all_debt_people(request.user),
                                                "delete_url": reverse("debt_delete", args=[debt.pk])})


@login_required
@require_POST
def debt_delete(request, pk):
    get_object_or_404(Debt, pk=pk, user=request.user).delete()
    messages.success(request, _("Debt deleted. Its payments were removed too."))
    return redirect("debts")


@login_required
@require_POST
def debt_settle(request, pk):
    """Close a debt that won't be paid in full (forgiven or settled some other way), or reopen it.
    No money moves, so account balances don't change."""
    debt = get_object_or_404(Debt, pk=pk, user=request.user)
    debt.closed = not debt.closed
    debt.save(update_fields=["closed"])
    messages.success(request, _("Debt closed.") if debt.closed else _("Debt reopened."))
    return redirect("debt_detail", pk=debt.pk)


@login_required
def debt_record_edit(request, pk):
    record = get_object_or_404(DebtRecord.objects.select_related("debt"), pk=pk, debt__user=request.user)
    form = DebtRecordForm(request.POST or None, instance=record, user=request.user, debt=record.debt)
    if request.method == "POST" and form.is_valid():
        form.save()
        services.sync_debt_expense(record)
        services.refresh_debt_status(record.debt)
        messages.success(request, _("Payment updated."))
        return redirect("debt_detail", pk=record.debt.pk)
    return render(request, "tracker/form.html", {"form": form, "title": _("Edit payment"),
                                                "delete_url": reverse("debt_record_delete", args=[record.pk])})


@login_required
@require_POST
def debt_record_delete(request, pk):
    record = get_object_or_404(DebtRecord.objects.select_related("debt"), pk=pk, debt__user=request.user)
    debt = record.debt
    record.delete()
    services.refresh_debt_status(debt)
    messages.success(request, _("Payment deleted."))
    return redirect("debt_detail", pk=debt.pk)
