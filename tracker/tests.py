import re
import datetime as dt
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import llm
from .models import Account, AIUsage, Category, Transaction, Transfer


class FlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("rakib", password="pass12345!")
        self.client.login(username="rakib", password="pass12345!")
        self.today = timezone.localdate()

    def test_default_categories_created(self):
        self.assertTrue(Category.objects.filter(user=self.user, name="Food", type="expense").exists())
        self.assertTrue(Category.objects.filter(user=self.user, name="Salary", type="income").exists())

    def test_dashboard_loads(self):
        r = self.client.get(reverse("dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Add with AI")

    def test_ai_parse_preview_and_confirm(self):
        fake = {"transactions": [
            {"type": "expense", "amount": "৫৫০", "category": "groceries", "is_new_category": False,
             "note": "বাজার", "date": self.today.isoformat()},
            {"type": "expense", "amount": 1500, "category": "Fitness", "is_new_category": True,
             "note": "gym", "date": (self.today - dt.timedelta(days=1)).isoformat()},
            {"type": "income", "amount": 5000, "category": "Tuition", "is_new_category": False,
             "note": "টিউশন", "date": "not-a-date"},
            {"type": "expense", "amount": -5, "category": "Food"},
        ], "message": ""}
        with mock.patch.object(llm, "call_llm", return_value=fake):
            r = self.client.post(reverse("ai_parse"), {"text": "আজ বাজারে ৫৫০ টাকা"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "New category: Fitness")
        rows = r.context["rows"]
        self.assertEqual(len(rows), 3)  # negative amount dropped
        self.assertEqual(rows[0]["amount"], Decimal("550.00"))
        self.assertEqual(rows[2]["date"], self.today)

        groceries = Category.objects.get(user=self.user, name="Groceries")
        tuition = Category.objects.get(user=self.user, name="Tuition")
        post = {"row_count": 3, "raw_text": "আজ বাজারে ৫৫০ টাকা",
                "include_0": "1", "type_0": "expense", "amount_0": "550", "category_0": groceries.id,
                "note_0": "বাজার", "date_0": self.today.isoformat(),
                "include_1": "1", "type_1": "expense", "amount_1": "1500", "category_1": "__new__",
                "new_category_1": "Fitness", "note_1": "gym", "date_1": self.today.isoformat(),
                "type_2": "income", "amount_2": "5000", "category_2": tuition.id}  # row 2 not included
        r = self.client.post(reverse("ai_confirm"), post, follow=True)
        self.assertContains(r, "Saved 2 transactions")
        self.assertContains(r, "New categories: Fitness")
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 2)
        self.assertTrue(Category.objects.filter(user=self.user, name="Fitness").exists())
        self.assertEqual(r.context["summary"]["expense"], Decimal("2050.00"))

    def test_ai_failure_falls_back(self):
        with mock.patch.object(llm, "call_llm", side_effect=llm.LLMError("no key")):
            r = self.client.post(reverse("ai_parse"), {"text": "lunch 200"}, follow=True)
        self.assertContains(r, "add it manually")
        self.assertContains(r, "lunch 200")  # text kept in the box

    def test_no_amount_message(self):
        with mock.patch.object(llm, "call_llm", return_value={"transactions": [], "message": "কোনো টাকার পরিমাণ পাইনি"}):
            r = self.client.post(reverse("ai_parse"), {"text": "hello"}, follow=True)
        self.assertContains(r, "কোনো টাকার পরিমাণ পাইনি")

    def test_ask(self):
        food = Category.objects.get(user=self.user, name="Food")
        Transaction.objects.create(user=self.user, type="expense", amount=300, category=food, note="lunch", date=self.today)
        Transaction.objects.create(user=self.user, type="expense", amount=200, category=food, note="dinner", date=self.today)
        Transaction.objects.create(user=self.user, type="income", amount=1000, note="x", date=self.today)
        q = {"type": "expense", "categories": ["food"], "keyword": "", "start_date": self.today.replace(day=1).isoformat(),
             "end_date": self.today.isoformat(), "group_by": "category", "title": "এই মাসে খাবারে কত খরচ?"}
        with mock.patch.object(llm, "call_llm", return_value=q):
            r = self.client.post(reverse("ask"), {"question": "এই মাসে খাবারে কত খরচ?"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["result"]["expense"], Decimal("500"))
        self.assertContains(r, "৳ 500.00")

    def test_manual_crud_and_isolation(self):
        food = Category.objects.get(user=self.user, name="Food")
        r = self.client.post(reverse("transaction_add"), {"type": "expense", "amount": "120", "category": food.id,
                                                           "note": "tea", "date": self.today.isoformat()})
        self.assertEqual(r.status_code, 302)
        t = Transaction.objects.get(note="tea")
        salary = Category.objects.get(user=self.user, name="Salary")
        r = self.client.post(reverse("transaction_edit", args=[t.pk]), {"type": "expense", "amount": "120",
                             "category": salary.id, "note": "tea", "date": self.today.isoformat()})
        self.assertContains(r, "income category")  # type mismatch rejected
        other = User.objects.create_user("other", password="pass12345!")
        self.client.logout()
        self.client.login(username="other", password="pass12345!")
        self.assertEqual(self.client.get(reverse("transaction_edit", args=[t.pk])).status_code, 404)
        r = self.client.get(reverse("transactions"))
        self.assertNotContains(r, "tea")
        self.assertTrue(Category.objects.filter(user=other).exists())

    def test_budget_and_pages(self):
        food = Category.objects.get(user=self.user, name="Food")
        r = self.client.post(reverse("category_edit", args=[food.pk]), {"name": "Food", "type": "expense",
                             "icon": "🍚", "color": "#f97316", "monthly_budget": "1000"})
        self.assertEqual(r.status_code, 302)
        Transaction.objects.create(user=self.user, type="expense", amount=900, category=food, date=self.today)
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, "90.0% used")
        for name in ("transactions", "categories", "category_add", "transaction_add", "ask", "export_csv"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        r = self.client.post(reverse("category_add"), {"name": "food", "type": "expense", "icon": "x", "color": "#000000"})
        self.assertContains(r, "already have")

    def test_extract_json(self):
        self.assertEqual(llm._extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(llm._extract_json('Sure! {"a": 2} hope this helps'), {"a": 2})
        self.assertEqual(llm.normalize_text("৫৫০"), "550")

    def test_theme_and_language_settings(self):
        r = self.client.post(reverse("preferences"), {"theme": "dark", "language": "bn", "next": reverse("dashboard")})
        self.assertRedirects(r, reverse("dashboard"))
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, 'data-bs-theme="dark"')
        self.assertContains(r, "AI দিয়ে যোগ করুন")
        self.assertContains(r, 'lang="bn"')
        # unknown values are ignored; an outside "next" falls back to the settings page
        r = self.client.post(reverse("preferences"), {"theme": "neon", "next": "https://evil.example"})
        self.assertRedirects(r, reverse("preferences"))
        self.assertEqual(self.client.cookies["theme"].value, "dark")


class AccountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("rakib", password="pass12345!")
        self.client.login(username="rakib", password="pass12345!")
        self.cash = Account.objects.get(user=self.user, name="Cash")
        self.bkash = Account.objects.get(user=self.user, name="bKash")

    def test_default_accounts(self):
        names = set(Account.objects.filter(user=self.user).values_list("name", flat=True))
        self.assertEqual(names, {"Cash", "bKash", "Nagad", "Rocket", "Bank", "Card"})
        self.assertTrue(self.cash.is_default)

    def test_balances_with_transfer(self):
        self.cash.opening_balance = Decimal("1000")
        self.cash.save()
        food = Category.objects.get(user=self.user, name="Food")
        Transaction.objects.create(user=self.user, type="expense", amount=200, category=food, account=self.cash)
        Transaction.objects.create(user=self.user, type="income", amount=5000, account=self.bkash)
        r = self.client.post(reverse("transfer_add"), {"from_account": self.bkash.id, "to_account": self.cash.id,
                                                       "amount": "1500", "date": timezone.localdate().isoformat()})
        self.assertRedirects(r, reverse("accounts"))
        r = self.client.get(reverse("accounts"))
        bal = {row["account"].name: row["balance"] for row in r.context["rows"]}
        self.assertEqual(bal["Cash"], Decimal("2300"))   # 1000 - 200 + 1500
        self.assertEqual(bal["bKash"], Decimal("3500"))  # 5000 - 1500
        self.assertEqual(r.context["total"], Decimal("5800"))
        # transfers are not income or expense
        dash = self.client.get(reverse("dashboard"))
        self.assertEqual(dash.context["summary"]["income"], Decimal("5000"))
        self.assertEqual(dash.context["summary"]["expense"], Decimal("200"))

    def test_transfer_needs_two_accounts(self):
        r = self.client.post(reverse("transfer_add"), {"from_account": self.cash.id, "to_account": self.cash.id,
                                                       "amount": "10", "date": timezone.localdate().isoformat()})
        self.assertContains(r, "two different accounts")
        self.assertFalse(Transfer.objects.exists())

    def test_ai_picks_account(self):
        fake = {"transactions": [
            {"type": "expense", "amount": 500, "category": "Food", "account": "bkash", "note": "lunch"},
            {"type": "expense", "amount": 80, "category": "Transport", "account": "", "note": "rickshaw"},
        ], "message": ""}
        with mock.patch.object(llm, "call_llm", return_value=fake):
            r = self.client.post(reverse("ai_parse"), {"text": "bkash e lunch 500, rickshaw 80"})
        rows = r.context["rows"]
        self.assertEqual(rows[0]["account_id"], self.bkash.id)
        self.assertEqual(rows[1]["account_id"], self.cash.id)  # default account

    def test_ai_transfer(self):
        fake = {"transactions": [
            {"type": "transfer", "amount": 500, "account": "Cash", "to_account": "bkash", "note": "move"},
            {"type": "transfer", "amount": 2000, "account": "", "to_account": "Cash", "note": "ATM"},
        ], "message": ""}
        with mock.patch.object(llm, "call_llm", return_value=fake):
            r = self.client.post(reverse("ai_parse"), {"text": "move 500 from cash to bkash, ATM 2000"})
        rows = r.context["rows"]
        self.assertEqual((rows[0]["type"], rows[0]["account_id"], rows[0]["to_account_id"]),
                         ("transfer", self.cash.id, self.bkash.id))
        self.assertNotEqual(rows[1]["account_id"], rows[1]["to_account_id"])  # missing side filled, never Cash->Cash
        self.assertEqual(rows[1]["to_account_id"], self.cash.id)
        post = {"row_count": 1, "raw_text": "move", "include_0": "1", "type_0": "transfer", "amount_0": "500",
                "account_0": self.cash.id, "to_account_0": self.bkash.id, "note_0": "move",
                "date_0": timezone.localdate().isoformat()}
        r = self.client.post(reverse("ai_confirm"), post, follow=True)
        self.assertContains(r, "Saved 1 transaction")
        self.assertEqual(Transfer.objects.get().amount, Decimal("500"))
        self.assertFalse(Transaction.objects.exists())  # not income or expense
        self.assertEqual(r.context["summary"]["expense"], Decimal("0"))
        self.assertContains(r, "Transfer")  # shown in recent transactions
        lst = self.client.get(reverse("transactions"))
        self.assertContains(lst, "Transfer")
        self.assertNotContains(self.client.get(reverse("transactions") + "?type=expense"), "⇄ Transfer</span>")

    def test_ai_sets_balance(self):
        food = Category.objects.get(user=self.user, name="Food")
        Transaction.objects.create(user=self.user, type="expense", amount=300, category=food, account=self.bkash)
        fake = {"transactions": [
            {"type": "balance", "amount": "৫০০০", "account": "bkash"},
            {"type": "balance", "amount": 0, "account": "Nagad"},
        ], "message": ""}
        with mock.patch.object(llm, "call_llm", return_value=fake):
            r = self.client.post(reverse("ai_parse"), {"text": "amar bkash e 5000 ache, nagad e kichu nai"})
        rows = r.context["rows"]
        self.assertEqual([(x["type"], x["amount"]) for x in rows], [("balance", Decimal("5000.00")), ("balance", Decimal("0.00"))])
        self.assertContains(r, "Set balance")
        nagad = Account.objects.get(user=self.user, name="Nagad")
        post = {"row_count": 2, "raw_text": "x",
                "include_0": "1", "type_0": "balance", "amount_0": "5000", "account_0": self.bkash.id,
                "include_1": "1", "type_1": "balance", "amount_1": "0", "account_1": nagad.id}
        r = self.client.post(reverse("ai_confirm"), post, follow=True)
        self.assertContains(r, "Balance updated")
        bal = {row["account"].name: row["balance"] for row in self.client.get(reverse("accounts")).context["rows"]}
        self.assertEqual(bal["bKash"], Decimal("5000"))  # even after the ৳300 already spent from it
        self.assertEqual(bal["Nagad"], Decimal("0"))
        self.bkash.refresh_from_db()
        self.assertEqual(self.bkash.opening_balance, Decimal("5300"))
        self.assertEqual(Transaction.objects.count(), 1)  # no fake income/expense was created
        self.assertEqual(r.context["summary"]["income"], Decimal("0"))

    def test_ai_sets_budgets(self):
        food = Category.objects.get(user=self.user, name="Food")
        fake = {"transactions": [
            {"type": "budget", "amount": 6000, "category": "food"},
            {"type": "budget", "amount": 1500, "category": "Gym", "is_new_category": True},
        ], "message": ""}
        with mock.patch.object(llm, "call_llm", return_value=fake):
            r = self.client.post(reverse("ai_parse"), {"text": "khabar e mashe 6000 budget, gym 1500"})
        rows = r.context["rows"]
        self.assertEqual([(x["type"], x["category_id"], x["new_category"]) for x in rows],
                         [("budget", food.id, ""), ("budget", None, "Gym")])
        post = {"row_count": 2, "raw_text": "x",
                "include_0": "1", "type_0": "budget", "amount_0": "6000", "category_0": food.id,
                "include_1": "1", "type_1": "budget", "amount_1": "1500", "category_1": "__new__", "new_category_1": "Gym"}
        r = self.client.post(reverse("ai_confirm"), post, follow=True)
        self.assertContains(r, "Monthly budget set")
        food.refresh_from_db()
        self.assertEqual(food.monthly_budget, Decimal("6000"))
        self.assertEqual(Category.objects.get(user=self.user, name="Gym", type="expense").monthly_budget, Decimal("1500"))
        self.assertEqual([b["category"].name for b in r.context["budgets"]], ["Food", "Gym"])
        self.assertFalse(Transaction.objects.exists())  # a budget is not a transaction
        # "budget 0" removes it
        self.client.post(reverse("ai_confirm"), {"row_count": 1, "include_0": "1", "type_0": "budget",
                                                 "amount_0": "0", "category_0": food.id})
        food.refresh_from_db()
        self.assertIsNone(food.monthly_budget)

    def test_only_one_default_account(self):
        self.client.post(reverse("account_edit", args=[self.bkash.id]), {
            "name": "bKash", "kind": "mobile", "icon": "📱", "color": "#e2136e", "opening_balance": "0", "is_default": "on"})
        self.assertEqual(list(Account.objects.filter(user=self.user, is_default=True)), [self.bkash])


class AILimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("rakib", password="pass12345!")
        self.client.login(username="rakib", password="pass12345!")

    @staticmethod
    def fake_llm(tokens):
        """Stand-in for call_llm that reports `tokens` used, like the real providers do."""
        def fake(system, user, usage=None):
            if usage is not None:
                usage["tokens"] = usage.get("tokens", 0) + tokens
            return {"transactions": [{"type": "expense", "amount": 50, "category": "Food"}], "message": ""}
        return fake

    @override_settings(AI_DAILY_TOKENS_PER_USER=5000)
    def test_tokens_counted_and_shown_as_percent(self):
        with mock.patch.object(llm, "call_llm", side_effect=self.fake_llm(1250)):
            self.client.post(reverse("ai_parse"), {"text": "tea 50"})
        usage = AIUsage.objects.get(user=self.user)
        self.assertEqual((usage.count, usage.tokens), (1, 1250))
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, "AI today: 25% used")
        self.assertContains(r, "1,250 of 5,000 AI tokens used today")

    @override_settings(AI_DAILY_TOKENS_PER_USER=3000)
    def test_user_token_limit(self):
        with mock.patch.object(llm, "call_llm", side_effect=self.fake_llm(1600)) as m:
            for _ in range(3):
                r = self.client.post(reverse("ai_parse"), {"text": "tea 50"}, follow=True)
        self.assertEqual(m.call_count, 2)  # 1600 + 1600 >= 3000, so the third never reached the AI
        self.assertContains(r, "used 100% of today")
        self.assertContains(r, "AI limit used up for today")
        self.assertContains(r, 'id="ai-btn" disabled')

    def test_failed_request_costs_nothing(self):
        with mock.patch.object(llm, "call_llm", side_effect=llm.LLMError("down")):
            self.client.post(reverse("ai_parse"), {"text": "tea 50"})
        self.assertFalse(AIUsage.objects.exists())
        self.assertContains(self.client.get(reverse("dashboard")), "AI today: 0% used")

    @override_settings(AI_DAILY_TOKENS_TOTAL=10000)
    def test_app_wide_token_limit(self):
        other = User.objects.create_user("other", password="x")
        AIUsage.objects.create(user=other, date=timezone.localdate(), count=4, tokens=10000)
        with mock.patch.object(llm, "call_llm") as m:
            r = self.client.post(reverse("ask"), {"question": "food this month?"})
        m.assert_not_called()
        self.assertContains(r, "limit for everyone")

    def test_provider_reports_tokens(self):
        class Resp:
            status_code = 200
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}],
                        "usageMetadata": {"totalTokenCount": 2345}}
        usage = {}
        with override_settings(GEMINI_API_KEY="k", LLM_PROVIDERS=["gemini"]),                 mock.patch.object(llm.requests, "post", return_value=Resp()):
            self.assertEqual(llm.call_llm("sys", "hi", usage=usage), {"ok": True})
        self.assertEqual(usage["tokens"], 2345)


class RecentPeriodTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("rakib", password="pass12345!")
        self.client.login(username="rakib", password="pass12345!")

    def test_week_bounds_start_saturday(self):
        from . import services
        fri = dt.date(2026, 10, 16)  # a Friday
        self.assertEqual(services.period_bounds("week", fri), (dt.date(2026, 10, 10), fri))
        sat = dt.date(2026, 10, 10)
        self.assertEqual(services.period_bounds("week", sat), (sat, sat))
        self.assertEqual(services.period_bounds("day", fri), (fri, fri))
        self.assertEqual(services.period_bounds("month", fri), (dt.date(2026, 10, 1), fri))

    def test_dashboard_periods(self):
        today = timezone.localdate()
        old = today - dt.timedelta(days=40)
        Transaction.objects.create(user=self.user, type="expense", amount=10, note="today-tea", date=today)
        Transaction.objects.create(user=self.user, type="expense", amount=99, note="old-shoes", date=old)
        r = self.client.get(reverse("dashboard"))
        self.assertEqual(r.context["recent_period"], "week")  # default
        self.assertContains(r, "today-tea")
        self.assertNotContains(r, "old-shoes")
        r = self.client.get(reverse("dashboard") + "?recent=day")
        self.assertEqual([t.note for t in r.context["recent"]], ["today-tea"])
        lst = self.client.get(reverse("transactions") + f"?from={old.isoformat()}&to={old.isoformat()}")
        self.assertContains(lst, "old-shoes")
        self.assertNotContains(lst, "today-tea")


def _code_from(message):
    """The 6-digit code, on its own indented line in the email."""
    return re.search(r"^\s+(\d{6})\s*$", message.body, re.M).group(1)


class AuthTests(TestCase):
    """Email + password login, email verification and password reset by code (django-allauth)."""
    PW = "Strong-pass-123"

    def test_signup_needs_email_code(self):
        r = self.client.post(reverse("account_signup"), {"email": "new@example.com", "password1": self.PW,
                                                         "password2": self.PW})
        self.assertRedirects(r, reverse("account_email_verification_sent"))
        user = User.objects.get(email="new@example.com")
        self.assertTrue(user.username)  # made automatically from the email
        self.assertNotIn("_auth_user_id", self.client.session)  # not logged in before the code
        self.assertEqual(len(mail.outbox), 1)
        r = self.client.post(reverse("account_email_verification_sent"), {"code": "000000"})
        self.assertNotIn("_auth_user_id", self.client.session)
        r = self.client.post(reverse("account_email_verification_sent"), {"code": _code_from(mail.outbox[0])}, follow=True)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertContains(r, "Default categories are ready")
        self.assertTrue(Account.objects.filter(user=user, name="bKash").exists())

    def _verified_user(self, email="me@example.com"):
        from allauth.account.models import EmailAddress
        user = User.objects.create_user("me", email=email, password=self.PW)
        EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
        return user

    def test_login_with_email_not_username(self):
        user = self._verified_user()
        r = self.client.post(reverse("account_login"), {"login": "me", "password": self.PW})
        self.assertNotIn("_auth_user_id", self.client.session)
        r = self.client.post(reverse("account_login"), {"login": "ME@example.com", "password": self.PW})
        self.assertRedirects(r, reverse("dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_password_reset_by_code(self):
        self._verified_user()
        r = self.client.post(reverse("account_reset_password"), {"email": "me@example.com"})
        self.assertRedirects(r, reverse("account_confirm_password_reset_code"))
        r = self.client.post(reverse("account_confirm_password_reset_code"), {"code": _code_from(mail.outbox[-1])})
        self.assertRedirects(r, reverse("account_complete_password_reset"))
        self.client.post(reverse("account_complete_password_reset"), {"password1": "New-pass-456", "password2": "New-pass-456"})
        self.client.logout()
        self.client.post(reverse("account_login"), {"login": "me@example.com", "password": "New-pass-456"})
        self.assertIn("_auth_user_id", self.client.session)

    def test_old_urls_and_login_page(self):
        self.assertRedirects(self.client.get("/login/"), reverse("account_login"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("dashboard")), reverse("account_login") + "?next=/",
                             fetch_redirect_response=False)
        r = self.client.get(reverse("account_login"))
        self.assertContains(r, "Welcome back")
        self.assertContains(r, 'type="email"')

    def test_change_username_in_settings(self):
        user = self._verified_user()
        self.client.force_login(user)
        r = self.client.post(reverse("preferences"), {"username": "Rakibul"}, follow=True)
        self.assertContains(r, "Profile updated")
        user.refresh_from_db()
        self.assertEqual(user.username, "Rakibul")
        # the username is used in the menu (avatar initial) and the greeting
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, ">Rakibul<")
        self.assertContains(r, '>R</span>')
        self.assertIn("Rakibul", r.context["greeting"])

    def test_bangla_digits_for_money(self):
        from django.utils import translation
        from .templatetags.money import money
        with translation.override("bn"):
            self.assertEqual(money(43810), "৳ ৪৩,৮১০.০০")
        with translation.override("en"):
            self.assertEqual(money(-1500.5), "-৳ 1,500.50")

    def test_help_page_is_public(self):
        r = self.client.get(reverse("help"))
        self.assertContains(r, "khorochai@gmail.com")
        self.assertContains(r, "rakibul.cse.ruet@gmail.com")


class VoiceInputTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("rakib", password="pass12345!")
        self.client.login(username="rakib", password="pass12345!")

    def _wav(self, size=32000):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile("voice.wav", bytes(4) + bytes(size), content_type="audio/wav")

    def test_transcribe_returns_text_and_counts_tokens(self):
        def fake(audio, mime, usage=None):
            usage["tokens"] = 300
            return "আজ lunch এ 250 টাকা দিলাম"
        with mock.patch.object(llm, "transcribe", side_effect=fake):
            r = self.client.post(reverse("ai_transcribe"), {"audio": self._wav()})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["text"], "আজ lunch এ 250 টাকা দিলাম")
        self.assertEqual(AIUsage.objects.get(user=self.user).tokens, 300)

    def test_transcribe_errors(self):
        self.assertEqual(self.client.post(reverse("ai_transcribe")).status_code, 400)  # no file
        with override_settings(VOICE_MAX_BYTES=10):
            self.assertEqual(self.client.post(reverse("ai_transcribe"), {"audio": self._wav()}).status_code, 400)
        with mock.patch.object(llm, "transcribe", side_effect=llm.LLMError("down")):
            r = self.client.post(reverse("ai_transcribe"), {"audio": self._wav()})
        self.assertEqual(r.status_code, 502)
        self.assertFalse(AIUsage.objects.exists())  # failed attempts cost nothing
        with mock.patch.object(llm, "transcribe", return_value=""):
            self.assertEqual(self.client.post(reverse("ai_transcribe"), {"audio": self._wav()}).status_code, 422)

    @override_settings(AI_DAILY_TOKENS_PER_USER=100)
    def test_transcribe_respects_daily_limit(self):
        AIUsage.objects.create(user=self.user, date=timezone.localdate(), count=1, tokens=100)
        with mock.patch.object(llm, "transcribe") as m:
            r = self.client.post(reverse("ai_transcribe"), {"audio": self._wav()})
        self.assertEqual(r.status_code, 429)
        m.assert_not_called()

    def test_mic_button_on_pages(self):
        for name in ("dashboard", "ask"):
            self.assertContains(self.client.get(reverse(name)), 'class="voice-btn"')

    def test_transcribe_requires_login(self):
        self.client.logout()
        r = self.client.post(reverse("ai_transcribe"), {"audio": self._wav()})
        self.assertEqual(r.status_code, 302)
