import re
import datetime as dt
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import llm, services
from .models import Account, AIUsage, Category, DailyActivity, Debt, DebtRecord, Transaction, Transfer
from .providers import BANKS, LOGOS, MOBILE_SERVICES, logo_file, logo_for, picker_data


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

    def test_ai_general_word_finds_renamed_account(self):
        Account.objects.filter(user=self.user, name="Bank").update(name="BRAC Bank")
        Account.objects.filter(user=self.user, name="Card").update(name="EBL Card")
        fake = {"transactions": [
            {"type": "expense", "amount": 500, "category": "Food", "account": "Bank", "note": "a"},
            {"type": "expense", "amount": 80, "category": "Food", "account": "card", "note": "b"},
            {"type": "expense", "amount": 90, "category": "Food", "account": "brac bank", "note": "c"},
        ], "message": ""}
        with mock.patch.object(llm, "call_llm", return_value=fake) as call:
            r = self.client.post(reverse("ai_parse"), {"text": "bank theke 500, card e 80"})
        bank = Account.objects.get(user=self.user, name="BRAC Bank")
        card = Account.objects.get(user=self.user, name="EBL Card")
        self.assertEqual([row["account_id"] for row in r.context["rows"]], [bank.id, card.id, bank.id])
        self.assertIn("BRAC Bank = bank", call.call_args[0][0])  # the AI is told the type of a renamed account

    def test_account_form_offers_the_lists(self):
        r = self.client.get(reverse("account_add"))
        self.assertContains(r, "account-picker-data")
        self.assertContains(r, "Dutch-Bangla Bank Card")
        self.assertContains(r, "js/account_picker.js")
        r = self.client.get(reverse("category_add"))
        self.assertNotContains(r, "account-picker")

    def test_add_account_starts_with_the_icon_of_its_type(self):
        r = self.client.get(reverse("account_add") + "?kind=bank")
        self.assertEqual(r.context["form"].initial["icon"], "🏦")
        r = self.client.get(reverse("account_add") + "?kind=nonsense")
        self.assertEqual(r.context["form"].initial["kind"], "mobile")

    def test_default_accounts_can_be_renamed_from_the_list(self):
        bank = Account.objects.get(user=self.user, name="Bank")
        Transaction.objects.create(user=self.user, type="income", amount=700, account=bank)
        r = self.client.post(reverse("account_edit", args=[bank.id]), {
            "kind": "bank", "name": "BRAC Bank", "icon": "🏦", "color": "#2563eb", "opening_balance": "0"})
        self.assertRedirects(r, reverse("accounts"))
        bank.refresh_from_db()
        self.assertEqual(bank.name, "BRAC Bank")
        self.assertEqual(bank.transactions.count(), 1)
        self.client.post(reverse("account_edit", args=[bank.id]), {
            "kind": "bank", "name": "My salary account", "icon": "🏦", "color": "#2563eb", "opening_balance": "0"})
        bank.refresh_from_db()
        self.assertEqual(bank.name, "My salary account")

    def test_logo_follows_the_account_name_and_type(self):
        self.assertEqual(logo_for("bKash", "mobile"), "img/banks/bkash.png")
        self.assertEqual(logo_for("BRAC Bank Card", "card"), "img/banks/brac-bank.png")
        self.assertEqual(logo_for("brac bank savings", "bank"), "img/banks/brac-bank.png")
        self.assertEqual(logo_for("National Bank", "bank"), "img/banks/national-bank.png")
        self.assertEqual(logo_for("National Bank of Pakistan", "bank"), "")  # not National Bank's logo
        self.assertEqual(logo_for("Rocket", "mobile"), "img/banks/rocket.png")
        self.assertEqual(logo_for("mCash", "mobile"), "")  # no confirmed logo yet: shows the emoji
        self.assertEqual(logo_for("BRAC Bank", "cash"), "")
        self.assertEqual(logo_for("Tapan", "mobile"), "")  # a whole-word match, not "Tap"
        self.assertEqual(logo_for("My salary", "bank"), "")

    def test_every_logo_has_a_file(self):
        known = {name for name, _ in BANKS} | {row[0] for row in MOBILE_SERVICES}
        self.assertLessEqual(LOGOS, known)
        for name in LOGOS:
            self.assertIsNotNone(finders.find(logo_file(name)), name)

    def test_account_pages_show_the_logo(self):
        r = self.client.get(reverse("accounts"))
        self.assertContains(r, "img/banks/bkash.png")
        self.assertContains(r, "img/banks/rocket.png")
        self.assertContains(r, "💵")  # Cash has no logo, so it keeps its emoji
        self.assertContains(self.client.get(reverse("dashboard")), "img/banks/bkash.png")
        self.assertContains(self.client.get(reverse("account_add")), "img/banks/brac-bank.png")

    def test_picker_names_fit_an_account(self):
        lists = picker_data()["lists"]
        self.assertEqual(set(lists), {"bank", "card", "mobile"})
        for rows in lists.values():
            names = [row["name"] for row in rows]
            self.assertEqual(len(names), len(set(names)))
            max_len = Account._meta.get_field("name").max_length
            self.assertTrue(all(len(n) <= max_len for n in names), names)
        defaults = {name: (icon, color) for name, _, icon, color, _ in services.DEFAULT_ACCOUNTS}
        for row in lists["mobile"][:3]:
            self.assertEqual((row["icon"], row["color"]), defaults[row["name"]])

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
        r = self.client.get(reverse("dashboard"))  # visitors get the landing page
        self.assertContains(r, "Create free account")
        self.assertContains(r, reverse("account_signup"))
        self.assertRedirects(self.client.get(reverse("transactions")), reverse("account_login") + "?next=/transactions/",
                             fetch_redirect_response=False)
        r = self.client.get(reverse("account_login"))
        self.assertContains(r, "Welcome back")
        self.assertContains(r, 'type="email"')

    def test_change_username_in_settings(self):
        user = self._verified_user()
        self.client.force_login(user)
        r = self.client.post(reverse("preferences"), {"username": "Rakibul", "currency": "BDT"}, follow=True)
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

    def test_help_and_about_pages_are_public(self):
        self.client.logout()
        r = self.client.get(reverse("help"))
        self.assertContains(r, "Still need help?")
        r = self.client.get(reverse("about"))
        self.assertContains(r, "khorochai@gmail.com")
        self.assertContains(r, "rakibul.cse.ruet@gmail.com")
        self.assertContains(r, "linkedin.com/in/rakibulnayeem")
        # every page has the footer credit and the developer pop-up
        r = self.client.get(reverse("account_login"))
        self.assertContains(r, 'data-bs-target="#developerModal"')
        self.assertContains(r, "img/developer.jpg")


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


class CurrencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("rakib", password="pass12345!")
        self.client.login(username="rakib", password="pass12345!")
        food = Category.objects.get(user=self.user, name="Food")
        Transaction.objects.create(user=self.user, type="expense", amount=1500, category=food)

    def test_default_is_taka_and_user_can_change_it(self):
        self.assertContains(self.client.get(reverse("dashboard")), "৳ 1,500.00")
        self.client.post(reverse("preferences"), {"username": "rakib", "currency": "USD"})
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, "$ 1,500.00")
        self.assertNotContains(r, "৳ 1,500.00")
        self.client.post(reverse("preferences"), {"username": "rakib", "currency": "IDR"})
        self.assertContains(self.client.get(reverse("dashboard")), "Rp 1,500<")  # no decimals for rupiah

    def test_language_does_not_change_a_users_currency(self):
        self.client.cookies["django_language"] = "ar"
        self.assertContains(self.client.get(reverse("dashboard")), "৳ 1,500.00")

    def test_ai_is_told_the_currency(self):
        self.client.post(reverse("preferences"), {"username": "rakib", "currency": "INR"})
        seen = {}
        def fake(system, user, usage=None):
            seen["system"] = system
            return {"transactions": [{"type": "expense", "amount": 50, "category": "Food"}], "message": ""}
        with mock.patch.object(llm, "call_llm", side_effect=fake):
            self.client.post(reverse("ai_parse"), {"text": "chai 50"})
        self.assertIn("(INR)", seen["system"])

    def test_signup_currency_follows_language(self):
        from .models import Profile
        self.client.logout()
        self.client.cookies["django_language"] = "hi"
        self.client.post(reverse("account_signup"), {"email": "new@example.com", "password1": "Strong-pass-123",
                                                     "password2": "Strong-pass-123"})
        self.assertEqual(Profile.objects.get(user__email="new@example.com").currency, "INR")

    def test_browser_language_picks_site_language(self):
        self.client.logout()
        r = self.client.get(reverse("account_login"), HTTP_ACCEPT_LANGUAGE="es-ES,es;q=0.9")
        self.assertContains(r, '<html lang="es"')
        r = self.client.get(reverse("account_login"), HTTP_ACCEPT_LANGUAGE="ar")
        self.assertContains(r, 'dir="rtl"')


class HedgingTests(TestCase):
    """A slow AI provider must not make people wait: the next one is asked and the faster answer wins."""

    @override_settings(LLM_PROVIDERS=["gemini", "groq"], LLM_HEDGE_SECONDS=0.2)
    def test_slow_first_provider_is_overtaken(self):
        import time as _time
        def slow(system, user):
            _time.sleep(2)
            return '{"who": "gemini"}', 10
        def fast(system, user):
            return '{"who": "groq"}', 20
        usage = {}
        start = _time.monotonic()
        with mock.patch.dict(llm.PROVIDERS, {"gemini": slow, "groq": fast}):
            self.assertEqual(llm.call_llm("s", "u", usage=usage), {"who": "groq"})
        self.assertLess(_time.monotonic() - start, 1.5)
        self.assertEqual(usage["tokens"], 20)

    @override_settings(LLM_PROVIDERS=["gemini", "groq"], LLM_HEDGE_SECONDS=5)
    def test_fast_first_provider_is_used_alone(self):
        calls = []
        def first(system, user):
            calls.append("gemini")
            return '{"who": "gemini"}', 10
        def second(system, user):
            calls.append("groq")
            return '{"who": "groq"}', 20
        with mock.patch.dict(llm.PROVIDERS, {"gemini": first, "groq": second}):
            self.assertEqual(llm.call_llm("s", "u"), {"who": "gemini"})
        self.assertEqual(calls, ["gemini"])

    @override_settings(LLM_PROVIDERS=["gemini", "groq"], LLM_HEDGE_SECONDS=5)
    def test_failing_provider_hands_over_immediately(self):
        def broken(system, user):
            raise llm.LLMError("503 high demand")
        def ok(system, user):
            return '{"who": "groq"}', 20
        with mock.patch.dict(llm.PROVIDERS, {"gemini": broken, "groq": ok}):
            self.assertEqual(llm.call_llm("s", "u"), {"who": "groq"})

    @override_settings(TRANSCRIBE_PROVIDERS=["gemini", "groq"], VOICE_HEDGE_SECONDS=5)
    def test_voice_uses_gemini_first(self):
        with mock.patch.dict(llm.TRANSCRIBERS, {"gemini": lambda a, m: ("আজ বাজারে 550 টাকা", 9),
                                                "groq": lambda a, m: ("should not be used", 5)}):
            self.assertEqual(llm.transcribe(b"x"), "আজ বাজারে 550 টাকা")

    @override_settings(TRANSCRIBE_PROVIDERS=["gemini", "groq"], VOICE_HEDGE_SECONDS=5)
    def test_voice_falls_back_to_whisper_when_gemini_fails(self):
        def down(a, m):
            raise llm.LLMError("503 high demand")
        with mock.patch.dict(llm.TRANSCRIBERS, {"gemini": down, "groq": lambda a, m: ("hello 250", 5)}):
            self.assertEqual(llm.transcribe(b"x"), "hello 250")


class DebtTests(TestCase):
    """Lend and borrow: balances move, income/expense don't, and debts close when paid back."""

    def setUp(self):
        self.user = User.objects.create_user("rakib", password="pass12345!")
        self.client.login(username="rakib", password="pass12345!")
        self.cash = Account.objects.get(user=self.user, name="Cash")
        self.bkash = Account.objects.get(user=self.user, name="bKash")
        self.cash.opening_balance = Decimal("5000")
        self.cash.save()
        self.today = timezone.localdate()

    def balances(self):
        return {row["account"].name: row["balance"] for row in self.client.get(reverse("accounts")).context["rows"]}

    def add(self, direction, person, amount, account, **extra):
        data = {"direction": direction, "person": person, "amount": amount, "account": account.id,
                "date": self.today.isoformat(), "due_date": "", "note": ""}
        data.update(extra)
        return self.client.post(reverse("debt_add"), data)

    def test_lend_and_get_paid_back(self):
        r = self.add("lent", "Rahim", "1000", self.cash)
        debt = Debt.objects.get()
        self.assertRedirects(r, reverse("debt_detail", args=[debt.pk]))
        self.assertEqual(self.balances()["Cash"], Decimal("4000"))
        # part paid back into bKash
        r = self.client.post(reverse("debt_detail", args=[debt.pk]), {"kind": "repay", "amount": "300",
                             "account": self.bkash.id, "date": self.today.isoformat(), "note": ""}, follow=True)
        self.assertContains(r, "left")
        self.assertEqual(self.balances()["bKash"], Decimal("300"))
        debt.refresh_from_db()
        self.assertFalse(debt.closed)
        # can't be paid back more than is owed
        r = self.client.post(reverse("debt_detail", args=[debt.pk]), {"kind": "repay", "amount": "800",
                             "account": self.cash.id, "date": self.today.isoformat()})
        self.assertContains(r, "is left to pay back")
        # the rest closes it
        self.client.post(reverse("debt_detail", args=[debt.pk]), {"kind": "repay", "amount": "700",
                         "account": self.cash.id, "date": self.today.isoformat()})
        debt.refresh_from_db()
        self.assertTrue(debt.closed)
        self.assertEqual(self.balances()["Cash"], Decimal("4700"))
        # never income or expense
        dash = self.client.get(reverse("dashboard"))
        self.assertEqual(dash.context["summary"]["income"], Decimal("0"))
        self.assertEqual(dash.context["summary"]["expense"], Decimal("0"))
        # deleting a payment reopens it
        self.client.post(reverse("debt_record_delete", args=[DebtRecord.objects.get(amount=700).pk]))
        debt.refresh_from_db()
        self.assertFalse(debt.closed)

    def test_borrow_increase_and_repay(self):
        self.add("borrowed", "Karim", "2000", self.bkash)
        debt = Debt.objects.get()
        self.assertEqual(self.balances()["bKash"], Decimal("2000"))
        self.client.post(reverse("debt_detail", args=[debt.pk]), {"kind": "increase", "amount": "500",
                         "account": self.bkash.id, "date": self.today.isoformat()})
        self.client.post(reverse("debt_detail", args=[debt.pk]), {"kind": "repay", "amount": "1000",
                         "account": self.cash.id, "date": self.today.isoformat()})
        bal = self.balances()
        self.assertEqual(bal["bKash"], Decimal("2500"))
        self.assertEqual(bal["Cash"], Decimal("4000"))
        summary = self.client.get(reverse("debts")).context["summary"]
        self.assertEqual(summary["i_owe"], Decimal("1500"))
        self.assertEqual(summary["owed_to_me"], Decimal("0"))
        # paying back borrowed money is an expense (Financial Expenses), and the balance only drops once
        dash = self.client.get(reverse("dashboard"))
        self.assertEqual(dash.context["summary"]["expense"], Decimal("1000"))
        self.assertEqual(dash.context["summary"]["income"], Decimal("0"))
        t = Transaction.objects.get()
        self.assertEqual((t.type, t.category.name, t.account, t.amount), ("expense", "Financial Expenses", self.cash, Decimal("1000")))
        self.assertIn("Karim", t.note)
        # the expense is changed on its debt, not on its own
        self.assertRedirects(self.client.get(reverse("transaction_edit", args=[t.pk])), reverse("debt_detail", args=[debt.pk]))
        self.client.post(reverse("transaction_delete", args=[t.pk]))
        self.assertTrue(Transaction.objects.filter(pk=t.pk).exists())
        # editing the payment updates the expense
        record = DebtRecord.objects.get(kind="repay")
        self.client.post(reverse("debt_record_edit", args=[record.pk]), {"kind": "repay", "amount": "1200",
                         "account": self.bkash.id, "date": self.today.isoformat()})
        t.refresh_from_db()
        self.assertEqual((t.amount, t.account), (Decimal("1200"), self.bkash))
        self.assertEqual(self.balances()["bKash"], Decimal("1300"))
        self.assertEqual(self.balances()["Cash"], Decimal("5000"))
        # deleting the payment removes the expense
        self.client.post(reverse("debt_record_delete", args=[record.pk]))
        self.assertFalse(Transaction.objects.exists())
        self.assertEqual(self.balances()["bKash"], Decimal("2500"))

    def test_debts_page_lists_and_closes(self):
        self.add("lent", "Rahim", "500", self.cash, due_date=(self.today + dt.timedelta(days=7)).isoformat())
        self.assertContains(self.add("lent", "Rahim", "500", self.cash,
                                     date=self.today.isoformat(), due_date=(self.today - dt.timedelta(days=1)).isoformat()),
                            "before the date", status_code=200)
        self.add("borrowed", "Karim", "800", self.cash)
        r = self.client.get(reverse("debts"))
        self.assertEqual(len(r.context["lent"]), 1)
        self.assertEqual(len(r.context["borrowed"]), 1)
        self.assertEqual(r.context["summary"]["owed_to_me"], Decimal("500"))
        self.assertEqual(r.context["summary"]["overdue_count"], 0)  # due date before the debt date was refused
        # close without payment: balance doesn't change, it moves to Closed
        karim = Debt.objects.get(person="Karim")
        before = self.balances()["Cash"]
        self.client.post(reverse("debt_settle", args=[karim.pk]))
        self.assertEqual(self.balances()["Cash"], before)
        self.assertEqual(len(self.client.get(reverse("debts")).context["borrowed"]), 0)
        self.assertEqual(len(self.client.get(reverse("debts") + "?show=closed").context["borrowed"]), 1)
        # editing keeps a hand-closed debt closed
        self.client.post(reverse("debt_edit", args=[karim.pk]), {"direction": "borrowed", "person": "Karim", "amount": "900",
                         "account": self.cash.id, "date": self.today.isoformat()})
        karim.refresh_from_db()
        self.assertTrue(karim.closed)
        self.assertEqual(karim.amount, Decimal("900"))

    def test_overdue(self):
        Debt.objects.create(user=self.user, direction="lent", person="Rahim", amount=100, account=self.cash,
                            date=self.today - dt.timedelta(days=10), due_date=self.today - dt.timedelta(days=1))
        r = self.client.get(reverse("debts"))
        self.assertEqual(r.context["summary"]["overdue_count"], 1)
        self.assertContains(r, "past its pay-back date")
        self.assertContains(self.client.get(reverse("dashboard")), "1 overdue")

    def test_ai_lend_borrow_and_collect(self):
        Debt.objects.create(user=self.user, direction="lent", person="Rahim", amount=200, account=self.cash,
                            date=self.today - dt.timedelta(days=5))
        fake = {"transactions": [
            {"type": "lend", "amount": 500, "person": "rahim", "account": "bkash", "due_date": "", "note": "dhar"},
            {"type": "borrow", "amount": 2000, "person": "Karim", "account": "", "note": ""},
            {"type": "collect", "amount": 300, "person": "Rahim", "account": "", "note": "ferot"},
        ], "message": ""}
        with mock.patch.object(llm, "call_llm", return_value=fake) as call:
            r = self.client.post(reverse("ai_parse"), {"text": "rahim ke 500 dhar dilam..."})
        self.assertIn("Rahim", call.call_args[0][0])  # the AI is told the people with open debts
        rows = r.context["rows"]
        self.assertEqual([x["type"] for x in rows], ["lend", "borrow", "collect"])
        self.assertEqual(rows[0]["person"], "Rahim")  # matched to the earlier spelling
        self.assertEqual(rows[0]["account_id"], self.bkash.id)
        self.assertContains(r, "I lent")
        post = {"row_count": 4, "raw_text": "x"}
        for i, row in enumerate(rows):
            post.update({f"include_{i}": "1", f"type_{i}": row["type"], f"amount_{i}": str(row["amount"]),
                         f"account_{i}": row["account_id"], f"person_{i}": row["person"], f"note_{i}": "",
                         f"date_{i}": self.today.isoformat(), f"due_date_{i}": ""})
        post.update({"include_3": "1", "type_3": "repay", "amount_3": "100", "account_3": self.cash.id,
                     "person_3": "Nobody", "date_3": self.today.isoformat()})
        r = self.client.post(reverse("ai_confirm"), post, follow=True)
        self.assertContains(r, "Lent")
        self.assertContains(r, "paid you back")
        self.assertContains(r, "anything")  # nobody to repay: warned, not saved
        self.assertEqual(Debt.objects.count(), 3)
        self.assertFalse(Transaction.objects.exists())  # getting lent money back is not income
        # 300 back: 200 closes the old debt, 100 goes to the newer one
        old = Debt.objects.get(amount=200)
        self.assertTrue(old.closed)
        new = Debt.objects.get(amount=500)
        self.assertEqual(new.records.get().amount, Decimal("100"))
        bal = self.balances()
        self.assertEqual(bal["Cash"], Decimal("5000") - 200 + 2000 + 300)
        self.assertEqual(bal["bKash"], Decimal("-500"))
        # debts show up in the lists
        self.assertContains(self.client.get(reverse("transactions")), "Got paid back")
        self.assertContains(self.client.get(reverse("transactions") + "?type=debt"), "Borrowed")
        self.assertNotContains(self.client.get(reverse("transactions") + "?type=expense"), "Borrowed")

    def test_debts_are_private(self):
        other = User.objects.create_user("other", password="pass12345!")
        d = Debt.objects.create(user=other, direction="lent", person="X", amount=1, date=self.today)
        self.assertEqual(self.client.get(reverse("debt_detail", args=[d.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("debt_delete", args=[d.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("debts")), ">X<")


    def test_ai_repay_is_an_expense(self):
        Debt.objects.create(user=self.user, direction="borrowed", person="Karim", amount=3000, account=self.cash, date=self.today)
        post = {"row_count": 1, "raw_text": "karim ke 1000 ferot dilam", "include_0": "1", "type_0": "repay",
                "amount_0": "1000", "account_0": self.bkash.id, "person_0": "karim", "date_0": self.today.isoformat()}
        r = self.client.post(reverse("ai_confirm"), post, follow=True)
        self.assertContains(r, "You paid karim back")
        self.assertEqual(r.context["summary"]["expense"], Decimal("1000"))
        self.assertEqual(self.balances()["bKash"], Decimal("-1000"))
        self.assertEqual(Transaction.objects.get().category.name, "Financial Expenses")
        self.assertContains(self.client.get(reverse("dashboard")), "Paid back Karim")


class ReminderTests(TestCase):
    """Debt reminders: set on the debt form, shown in the bell from N days before the due date."""

    def setUp(self):
        self.user = User.objects.create_user("rakib", password="pass12345!")
        self.client.login(username="rakib", password="pass12345!")
        self.cash = Account.objects.get(user=self.user, name="Cash")
        self.today = timezone.localdate()

    def add(self, due_in, **extra):
        data = {"direction": "lent", "person": "Rahim", "phone": "01712-345678", "amount": "5000",
                "account": self.cash.id, "date": self.today.isoformat(),
                "due_date": (self.today + dt.timedelta(days=due_in)).isoformat() if due_in is not None else "",
                "remind": "1", "remind_time": "00:00", "remind_daily": "on", "note": ""}
        data.update(extra)
        r = self.client.post(reverse("debt_add"), data)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        return Debt.objects.latest("id")

    def shown(self):
        return [d.pk for d in services.debt_reminders(self.user)]

    def test_form_saves_reminder(self):
        d = self.add(3, remind="custom", remind_custom="5", remind_time="08:30")
        self.assertEqual((d.remind_days, d.remind_time, d.remind_daily, d.phone), (5, dt.time(8, 30), True, "01712-345678"))
        self.assertEqual(self.add(None).remind_days, None)  # no due date, no reminder
        self.assertEqual(self.add(3, remind="off").remind_days, None)

    def test_starts_n_days_before_and_repeats_when_overdue(self):
        later = self.add(3)          # reminds from 1 day before: not yet
        tomorrow = self.add(1)
        overdue = self.add(-2, date=(self.today - dt.timedelta(days=5)).isoformat())
        once = self.add(-2, date=(self.today - dt.timedelta(days=5)).isoformat(), remind_daily="")
        self.assertEqual(self.shown(), [overdue.pk, tomorrow.pk])
        found = services.debt_reminders(self.user)
        self.assertIn("Overdue: Rahim", found[0].title)
        self.assertIn("2 days late", found[0].body)
        self.assertIn("Due tomorrow", found[1].title)
        self.assertNotIn(later.pk, self.shown())
        self.assertNotIn(once.pk, self.shown())

    def test_waits_for_the_reminder_time_on_the_first_day(self):
        d = self.add(1, remind_time="23:59")
        now = timezone.make_aware(dt.datetime.combine(self.today, dt.time(12, 0)))
        self.assertEqual([x.pk for x in services.debt_reminders(self.user, now)], [])
        d.remind_time = dt.time(11, 0)
        d.save()
        self.assertEqual([x.pk for x in services.debt_reminders(self.user, now)], [d.pk])

    def test_snooze_and_paid_back_hide_it(self):
        d = self.add(0)
        self.assertContains(self.client.get(reverse("dashboard")), "Due today")
        self.client.post(reverse("debt_snooze", args=[d.pk]))
        self.assertEqual(self.shown(), [])
        d2 = self.add(0, person="Mitu")
        DebtRecord.objects.create(debt=d2, kind="repay", amount=Decimal("5000"), account=self.cash)
        services.refresh_debt_status(d2)
        self.assertEqual(self.shown(), [])

    def test_others_cannot_snooze(self):
        d = self.add(0)
        User.objects.create_user("other", password="pass12345!")
        self.client.login(username="other", password="pass12345!")
        self.assertEqual(self.client.post(reverse("debt_snooze", args=[d.pk])).status_code, 404)


class AskDebtTests(TestCase):
    """Ask AI answers "who owes whom" from the saved debts."""

    def setUp(self):
        self.user = User.objects.create_user("rakib", password="pass12345!")
        self.client.login(username="rakib", password="pass12345!")
        cash = Account.objects.get(user=self.user, name="Cash")
        today = timezone.localdate()
        rahim = Debt.objects.create(user=self.user, direction="lent", person="Rahim", amount=1000, account=cash, date=today)
        DebtRecord.objects.create(debt=rahim, amount=400, account=cash, date=today)
        Debt.objects.create(user=self.user, direction="lent", person="Sumi", amount=500, account=cash, date=today)
        Debt.objects.create(user=self.user, direction="borrowed", person="Karim", amount=2000, account=cash, date=today)
        Debt.objects.create(user=self.user, direction="lent", person="Old", amount=50, account=cash, date=today, closed=True)

    def ask(self, q):
        q = {"kind": "debts", "title": "t", **q}
        with mock.patch.object(llm, "call_llm", return_value=q) as call:
            r = self.client.post(reverse("ask"), {"question": "x"})
        self.assertIn("Rahim", call.call_args[0][0])  # the AI is told the people's names
        return r

    def test_one_person(self):
        r = self.ask({"debt_direction": "lent", "people": ["rahim"]})
        res = r.context["result"]
        self.assertEqual(res["summary"]["owed_to_me"], Decimal("600"))
        self.assertEqual([d.person for d in res["debts"]], ["Rahim"])
        self.assertContains(r, "Owed to me")
        self.assertNotContains(r, "I owe</div>")

    def test_who_owes_me(self):
        res = self.ask({"debt_direction": "lent", "people": []}).context["result"]
        self.assertEqual(res["summary"]["owed_to_me"], Decimal("1100"))
        self.assertEqual([p["person"] for p in res["people"]], ["Rahim", "Sumi"])  # biggest first, closed left out

    def test_everything(self):
        res = self.ask({"debt_direction": "both", "people": []}).context["result"]
        self.assertEqual((res["summary"]["owed_to_me"], res["summary"]["i_owe"]), (Decimal("1100"), Decimal("2000")))
        self.assertEqual(len(res["debts"]), 3)

    def test_nobody_matches(self):
        r = self.ask({"debt_direction": "borrowed", "people": ["Nobody"]})
        self.assertContains(r, "No matching debts")


class BreachedPasswordTests(TestCase):
    """A password from a data breach is refused at sign-up, before any account or email is created."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()  # allauth rate-limits sign-ups per address; other tests already used some

    def fake_range(self, password, count):
        import hashlib
        digest = hashlib.sha1(password.encode()).hexdigest().upper()
        resp = mock.Mock(status_code=200, text=f"{'0' * 35}:3\r\n{digest[5:]}:{count}\r\n")
        resp.raise_for_status = lambda: None
        return resp

    @override_settings(PASSWORD_BREACH_CHECK=True)
    def test_breached_password_is_refused_and_no_email_sent(self):
        with mock.patch("tracker.password_validation.requests.get", return_value=self.fake_range("Password@123", 1763928)) as get:
            r = self.client.post(reverse("account_signup"), {"email": "new@example.com", "password1": "Password@123",
                                                             "password2": "Password@123"})
        self.assertContains(r, "appeared in a data breach")
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(User.objects.filter(email="new@example.com").exists())
        self.assertEqual(len(get.call_args[0][0].rsplit("/", 1)[1]), 5)  # only a 5-character hash prefix is sent

    @override_settings(PASSWORD_BREACH_CHECK=True)
    def test_safe_password_signs_up(self):
        with mock.patch("tracker.password_validation.requests.get", return_value=self.fake_range("something-else", 9)):
            r = self.client.post(reverse("account_signup"), {"email": "new@example.com", "password1": "kh0r0ch-zebra-river-91",
                                                             "password2": "kh0r0ch-zebra-river-91"}, follow=True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(User.objects.filter(email="new@example.com").exists())

    @override_settings(PASSWORD_BREACH_CHECK=True)
    def test_check_down_does_not_block_sign_up(self):
        import requests as rq
        with mock.patch("tracker.password_validation.requests.get", side_effect=rq.ConnectionError("down")):
            self.client.post(reverse("account_signup"), {"email": "new@example.com", "password1": "kh0r0ch-zebra-river-91",
                                                         "password2": "kh0r0ch-zebra-river-91"})
        self.assertEqual(len(mail.outbox), 1)

    def test_weak_password_sends_nothing(self):
        r = self.client.post(reverse("account_signup"), {"email": "nayeem77@example.com", "password1": "nayeem77",
                                                         "password2": "nayeem77"})
        self.assertContains(r, "too similar to the email")
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(r, "leaked password")  # the rules are shown up front


GOOGLE_ON = {"google": {"APP": {"client_id": "test-id.apps.googleusercontent.com", "secret": "s"},
                        "SCOPE": ["profile", "email"], "AUTH_PARAMS": {"prompt": "select_account"}}}


class SocialLoginTests(TestCase):
    """Log in with Google (Google's side is faked)."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_no_buttons_without_keys(self):
        self.assertNotContains(self.client.get(reverse("account_login")), "Continue with")

    @override_settings(SOCIALACCOUNT_PROVIDERS=GOOGLE_ON)
    def test_buttons_and_redirect_to_google(self):
        r = self.client.get(reverse("account_login"))
        self.assertContains(r, "Continue with Google")
        self.assertNotContains(r, "Continue with Facebook")
        self.assertContains(self.client.get(reverse("account_signup")), "Continue with Google")
        r = self.client.post("/auth/google/login/?process=login")
        self.assertTrue(r["Location"].startswith("https://accounts.google.com/"))
        self.assertIn("redirect_uri=http%3A%2F%2Ftestserver%2Fauth%2Fgoogle%2Flogin%2Fcallback%2F", r["Location"])

    def google_login(self, email, verified=True):
        from urllib.parse import parse_qs, urlparse
        from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
        from allauth.socialaccount.providers.oauth2.client import OAuth2Client
        r = self.client.post("/auth/google/login/?process=login")
        state = parse_qs(urlparse(r["Location"]).query)["state"][0]
        claims = {"sub": "10987", "email": email, "email_verified": verified, "name": "Gina Rahman",
                  "given_name": "Gina", "picture": "https://example.com/p.png"}
        with mock.patch.object(OAuth2Client, "get_access_token", return_value={"access_token": "t", "id_token": "x"}), \
             mock.patch.object(GoogleOAuth2Adapter, "_decode_id_token", return_value=claims):
            return self.client.get(f"/auth/google/login/callback/?code=abc&state={state}", follow=True)

    @override_settings(SOCIALACCOUNT_PROVIDERS=GOOGLE_ON)
    def test_google_sign_up_logs_straight_in(self):
        r = self.google_login("gina@gmail.com")
        user = User.objects.get(email="gina@gmail.com")
        self.assertEqual(r.wsgi_request.user, user)
        self.assertEqual(r.request["PATH_INFO"], "/")
        self.assertEqual(len(mail.outbox), 0)  # Google already verified the email: no code needed
        self.assertTrue(Category.objects.filter(user=user).exists())
        self.assertTrue(Account.objects.filter(user=user, name="Cash").exists())
        self.assertEqual(user.profile.currency, "BDT")

    @override_settings(SOCIALACCOUNT_PROVIDERS=GOOGLE_ON)
    def test_google_with_existing_email_is_the_same_user(self):
        from allauth.account.models import EmailAddress
        user = User.objects.create_user("gina", email="gina@gmail.com", password="kh0r0ch-zebra-river-91")
        EmailAddress.objects.create(user=user, email="gina@gmail.com", verified=True, primary=True)
        r = self.google_login("gina@gmail.com")
        self.assertEqual(r.wsgi_request.user, user)
        self.assertEqual(User.objects.filter(email="gina@gmail.com").count(), 1)

    def test_privacy_page_is_public(self):
        r = self.client.get(reverse("privacy"))
        self.assertContains(r, "Deleting your data")
        self.assertContains(r, "khorochai@gmail.com")


class ActivityTests(TestCase):
    A, B = "a" * 32, "b" * 32

    def setUp(self):
        self.user = User.objects.create_user("rina", email="rina@example.com", password="pass12345!")
        self.boss = User.objects.create_user("boss", email="boss@example.com", password="pass12345!", is_staff=True)

    def ping(self, visitor=None, seconds=0, view=False):
        return self.client.post(reverse("activity_ping"), {"visitor": visitor or self.A, "seconds": seconds, "view": "1" if view else "0"})

    def test_guest_visit_and_time_add_up(self):
        self.assertEqual(self.ping(view=True).status_code, 204)
        self.ping(seconds=60)
        self.ping(seconds=30, view=True)
        row = DailyActivity.objects.get()
        self.assertEqual((row.user, row.visitor_id, row.views, row.seconds, row.date), (None, self.A, 2, 90, timezone.localdate()))

    def test_logged_in_user_has_one_row_a_day_across_devices(self):
        self.client.force_login(self.user)
        self.ping(self.A, 10, True)
        self.ping(self.B, 20, True)
        row = DailyActivity.objects.get()
        self.assertEqual((row.user, row.views, row.seconds), (self.user, 2, 30))

    def test_staff_are_not_counted(self):
        self.client.force_login(self.boss)
        self.assertEqual(self.ping(seconds=10, view=True).status_code, 204)
        self.assertFalse(DailyActivity.objects.exists())

    def test_bad_input_is_refused_or_limited(self):
        self.assertEqual(self.ping("not-an-id").status_code, 400)
        self.assertEqual(self.client.get(reverse("activity_ping")).status_code, 405)
        self.ping(seconds=999999)
        self.assertEqual(DailyActivity.objects.get().seconds, services.MAX_ACTIVITY_SECONDS_PER_PING)
        self.client.post(reverse("activity_ping"), {"visitor": self.A, "seconds": "-5"})
        self.client.post(reverse("activity_ping"), {"visitor": self.A, "seconds": "١٢"})
        self.assertEqual(DailyActivity.objects.get().seconds, services.MAX_ACTIVITY_SECONDS_PER_PING)

    def test_a_day_never_goes_past_24_hours(self):
        DailyActivity.objects.create(date=timezone.localdate(), visitor_id=self.A, seconds=86390)
        self.ping(seconds=60)
        self.assertEqual(DailyActivity.objects.get().seconds, 86400)

    def test_guest_rows_are_capped(self):
        with mock.patch.object(services, "MAX_GUEST_ROWS_PER_DAY", 1):
            self.ping(self.A, view=True)
            self.ping(self.B, view=True)
            self.ping(self.A, view=True)  # someone already counted keeps counting
        self.assertEqual(DailyActivity.objects.get().views, 2)

    @override_settings(TRACK_ACTIVITY=True)
    def test_script_runs_for_visitors_not_staff_or_the_panel(self):
        self.assertContains(self.client.get(reverse("about")), "js/activity.js")
        self.assertNotContains(self.client.get("/auth/login/?next=/admin/"), "js/activity.js")
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("dashboard")), "js/activity.js")
        self.client.force_login(self.boss)
        self.assertNotContains(self.client.get(reverse("dashboard")), "js/activity.js")
        self.assertNotContains(self.client.get(reverse("admin_overview")), "js/activity.js")

    @override_settings(TRACK_ACTIVITY=False)
    def test_script_can_be_switched_off(self):
        self.assertNotContains(self.client.get(reverse("about")), "js/activity.js")

    def test_panel_numbers(self):
        today = timezone.localdate()
        other = User.objects.create_user("omar", email="omar@example.com", password="pass12345!")
        DailyActivity.objects.create(date=today, user=self.user, visitor_id=self.A, views=3, seconds=300)
        DailyActivity.objects.create(date=today, visitor_id=self.A, views=1, seconds=30)  # same browser before logging in
        DailyActivity.objects.create(date=today, visitor_id=self.B, views=2, seconds=30)
        DailyActivity.objects.create(date=today - dt.timedelta(days=5), user=other, visitor_id=self.B, seconds=60)
        DailyActivity.objects.create(date=today - dt.timedelta(days=20), user=other, visitor_id=self.B, seconds=60)
        self.client.force_login(self.boss)
        r = self.client.get(reverse("admin_overview"))
        v = r.context["visits"]
        self.assertEqual((v["visitors_today"], v["users_today"], v["guests_today"], v["views_today"]), (2, 1, 1, 6))
        self.assertEqual((v["active_7"], v["active_30"]), (2, 2))
        self.assertEqual((v["avg_time"], v["avg_user_time"], v["total_time"]), ("3m 00s", "5m 00s", "6m 00s"))
        chart = r.context["chart_data"]
        self.assertEqual((chart["visit_users"][-1], chart["visit_guests"][-1], chart["avg_minutes"][-1]), (1, 1, 3.0))
        self.assertEqual(len(chart["visit_users"]), 30)

    def test_user_page_and_deleting_a_user(self):
        DailyActivity.objects.create(date=timezone.localdate(), user=self.user, visitor_id=self.A, seconds=3700)
        self.client.force_login(self.boss)
        r = self.client.get(reverse("admin_user", args=[self.user.pk]))
        self.assertEqual((r.context["time_today"], r.context["time_30"], r.context["days_30"]), ("1h 01m", "1h 01m", 1))
        self.assertIsNotNone(r.context["last_seen"])
        self.user.delete()
        self.assertFalse(DailyActivity.objects.exists())


class AdminPanelTests(TestCase):
    def setUp(self):
        self.boss = User.objects.create_user("boss", email="boss@example.com", password="pass12345!",
                                             is_staff=True, is_superuser=True)
        self.helper = User.objects.create_user("helper", email="helper@example.com", password="pass12345!", is_staff=True)
        self.user = User.objects.create_user("rina", email="rina@example.com", password="pass12345!")
        Transaction.objects.create(user=self.user, type="expense", amount=Decimal("750"), note="secret note")
        AIUsage.objects.create(user=self.user, date=timezone.localdate(), count=3, tokens=2400)

    def act(self, action, user=None, **extra):
        return self.client.post(reverse("admin_user_action", args=[(user or self.user).pk]),
                                {"action": action, **extra}, follow=True)

    def test_only_staff_can_open_it(self):
        self.assertRedirects(self.client.get(reverse("admin_overview")), "/auth/login/?next=/admin/",
                             fetch_redirect_response=False)
        self.client.login(username="rina", password="pass12345!")
        for name, args in [("admin_overview", []), ("admin_users", []), ("admin_user", [self.user.pk])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 404)
        self.assertEqual(self.act("deactivate", self.boss).status_code, 404)
        self.assertNotContains(self.client.get(reverse("dashboard")), "Admin panel")

    def test_pages_show_counts_not_money_or_text(self):
        self.client.login(username="boss", password="pass12345!")
        self.assertContains(self.client.get(reverse("dashboard")), "Admin panel")
        r = self.client.get(reverse("admin_overview"))
        self.assertContains(r, "AI today (whole app)")
        self.assertContains(r, "rina@example.com")  # top AI user today
        self.assertContains(self.client.get(reverse("admin_users"), {"q": "rina"}), "rina@example.com")
        self.assertNotContains(self.client.get(reverse("admin_users"), {"q": "rina"}), "helper@example.com")
        r = self.client.get(reverse("admin_user", args=[self.user.pk]))
        self.assertContains(r, "2,400 / 30,000 tokens")
        self.assertNotContains(r, "750")
        self.assertNotContains(r, "secret note")

    def test_deactivate_activate_and_reset_ai(self):
        self.client.login(username="boss", password="pass12345!")
        self.act("deactivate")
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertFalse(self.client.login(username="rina", password="pass12345!"))
        self.act("activate")
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.act("reset_ai")
        usage = AIUsage.objects.get(user=self.user)
        self.assertEqual((usage.count, usage.tokens), (0, 0))

    def test_delete_needs_the_email_typed(self):
        self.client.login(username="boss", password="pass12345!")
        r = self.act("delete", confirm_email="wrong@example.com")
        self.assertContains(r, "Type the user&#x27;s email exactly")
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        r = self.act("delete", confirm_email="RINA@example.com")
        self.assertContains(r, "rina@example.com and all their data are deleted.")
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(Transaction.objects.filter(note="secret note").exists())

    def test_cant_act_on_self_or_staff_without_superuser(self):
        self.client.login(username="boss", password="pass12345!")
        self.assertContains(self.act("deactivate", self.boss), "You can&#x27;t do this to your own account.")
        self.client.login(username="helper", password="pass12345!")
        self.assertContains(self.act("deactivate", self.boss), "Only a superuser can change another staff account.")
        self.boss.refresh_from_db()
        self.assertTrue(self.boss.is_active)
        self.act("deactivate")  # a staff member may still manage normal users
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    @override_settings(CLARITY_PROJECT_ID="clar123")
    def test_clarity_never_sees_the_panel_or_staff(self):
        tag = "clarity.ms/tag/"
        self.assertContains(self.client.get(reverse("privacy")), tag)  # visitors: on
        r = self.client.get(reverse("admin_users"), follow=True)  # sent to login with ?next=/admin/users/
        self.assertEqual(r.wsgi_request.path, reverse("account_login"))
        self.assertNotContains(r, tag)
        self.client.login(username="rina", password="pass12345!")
        self.assertContains(self.client.get(reverse("dashboard")), tag)  # normal users: on (text masked)
        self.assertNotContains(self.client.get(reverse("admin_overview")), tag, status_code=404)
        self.client.login(username="boss", password="pass12345!")
        for name, args in [("dashboard", []), ("admin_overview", []), ("admin_users", []), ("admin_user", [self.user.pk])]:
            self.assertNotContains(self.client.get(reverse(name, args=args)), tag)


class PwaTests(TestCase):
    def test_manifest(self):
        r = self.client.get("/manifest.webmanifest")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/manifest+json")
        data = r.json()
        self.assertEqual((data["name"], data["short_name"], data["display"]), ("KhorochAi", "KhorochAi", "standalone"))
        self.assertTrue(any(i["purpose"] == "maskable" and i["sizes"] == "512x512" for i in data["icons"]))

    def test_pages_link_manifest_and_install_button(self):
        html = self.client.get("/").content.decode()
        self.assertIn('rel="manifest"', html)
        self.assertEqual(html.count("data-pwa-install"), 2)  # navbar + landing hero

    def test_service_worker_served_from_root(self):
        r = self.client.get("/sw.js")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Service-Worker-Allowed"], "/")
        self.assertIn("text/javascript", r["Content-Type"])
        body = r.content.decode()
        self.assertIn("/offline/", body)
        self.assertNotIn("{%", body)

    def test_offline_page_is_public_and_has_no_user_data(self):
        r = self.client.get("/offline/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "KhorochAi")


class OfflineSyncTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("rakib", password="pass12345!")
        self.client.force_login(self.user)
        services.ensure_default_categories(self.user)
        services.ensure_default_accounts(self.user)
        self.cat = Category.objects.filter(user=self.user, type="expense").first()
        self.entry = {"client_id": "6f1c2c1e-3a53-4d8e-9a43-0d6a5c1b7e11", "type": "expense", "amount": "120.50",
                      "category": self.cat.pk, "account": services.default_account(self.user).pk,
                      "note": "tea", "date": "2026-11-09"}

    def sync(self, *entries):
        return self.client.post("/transactions/sync/", data={"entries": list(entries)}, content_type="application/json")

    def test_sync_saves_entry_and_is_safe_to_repeat(self):
        for _ in range(2):
            r = self.sync(self.entry)
            self.assertEqual(r.json()["results"], [{"client_id": self.entry["client_id"], "status": "synced"}])
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 1)
        t = Transaction.objects.get(user=self.user)
        self.assertEqual((str(t.amount), t.note, str(t.client_id)), ("120.50", "tea", self.entry["client_id"]))

    def test_invalid_entry_is_reported_not_saved(self):
        bad = dict(self.entry, amount="-5")
        self.assertEqual(self.sync(bad).json()["results"][0]["status"], "invalid")
        self.assertEqual(Transaction.objects.count(), 0)

    def test_cannot_use_another_users_account(self):
        other = User.objects.create_user("other", password="pass12345!")
        services.ensure_default_accounts(other)
        theirs = services.default_account(other)
        self.assertEqual(self.sync(dict(self.entry, account=theirs.pk)).json()["results"][0]["status"], "invalid")

    def test_requires_login_and_post(self):
        self.client.logout()
        self.assertEqual(self.sync(self.entry).status_code, 401)
        self.assertEqual(self.client.get("/transactions/offline-data/").status_code, 401)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/transactions/sync/").status_code, 405)

    def test_offline_data_lists_only_own_items(self):
        other = User.objects.create_user("other", password="pass12345!")
        services.ensure_default_accounts(other)
        data = self.client.get("/transactions/offline-data/").json()
        self.assertEqual(data["user"], self.user.pk)
        own = {a.pk for a in Account.objects.filter(user=self.user)}
        self.assertEqual({a["id"] for a in data["accounts"]}, own)
