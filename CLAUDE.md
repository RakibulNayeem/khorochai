# KhorochAi

An AI expense and income tracker for Bangladesh. Users type or speak, e.g. "আজ বাজারে ৫৫০ টাকা খরচ, tuition theke 6000 pelam". The AI turns that into rows the user checks before saving.

- Live at https://khorochai.vercel.app (Vercel, region `sin1`, Neon Postgres).
- Repo: `RakibulNayeem/khorochai`. Every push to `main` deploys.

## What's built
- **Adding entries:** with AI (`ai_parse` → preview → `ai_confirm`) or by hand. The AI understands these types:
  - expense, income;
  - transfer between your own accounts;
  - set balance, set monthly budget;
  - lend, borrow, collect, repay.
- **Voice input:** the mic records a 16 kHz WAV (`static/js/voice.js`). Gemini transcribes it, with Groq Whisper as the backup.
- **Accounts:** Cash, bKash, Nagad, Rocket, Bank and Card are created per user. Balances are worked out in SQL by `services.account_balances`.
- **Categories and budgets:** default categories are created per user by a signal. Budgets are monthly and per expense category.
- **Debts** (`Debt`, `DebtRecord`):
  - a list of "Owed to me" and "I owe", active and closed, with overdue dates;
  - a detail page to record payments or add more;
  - a debt closes by itself when fully paid back.
- **Ask AI:**
  - money questions → `run_query`;
  - debt questions → `run_debt_query`.
- **Dashboard:** monthly stats, account strip, debt summary, charts, budgets, and recent transactions (Day/Week/Month).
- **Other features:**
  - CSV export;
  - light, dark and system theme;
  - 29 interface languages;
  - 32 currencies, saved per user;
  - Help and About pages;
  - landing page for visitors.
- **Admin panel** (`/admin/`, `tracker/admin_panel.py`): our own, for staff only; Django's admin is removed.
  - Overview: users and sign-ups, AI usage today against the limits, charts for the last 30 days, feature use.
  - Users: search and filter; each user's page lets staff deactivate or activate them, reset today's AI usage, or delete them with all their data (type the email to confirm).
  - It shows counts only, never users' amounts or what they typed. Only a superuser can act on staff accounts, and nobody on their own.
  - Staff-only, so its text is English and not translated. Non-staff get a 404.
- **Analytics:** Microsoft Clarity (project `z57gryr8d2`), off when `DEBUG` is on. Logged-in pages mask all text.
- **Auth:** django-allauth, mounted at `/auth/` because `/accounts/` is the money-accounts page.
  - Email-only login.
  - Email verification and password reset use 6-digit codes, sent through Gmail SMTP.
  - Passwords found in data breaches are refused (`tracker/password_validation.py`, HIBP range API). If the service is down, the password is allowed.

## Key decisions
- **The AI only parses; Python and SQL do all the maths.** Gemini `gemini-flash-lite-latest` goes first and Groq second. `_first_success` asks the next provider when the first is slow: after 3 s for text and 8 s for voice.
- **Daily AI limits** live in `AIUsage`, for each user and for the whole app. Failed requests cost nothing.
- **Account balance** = opening balance + income − expense ± transfers ± debts. "Set balance" changes the opening balance, so no fake income or expense is created.
- **Transfers, lending, borrowing and getting paid back are not income or expense.** Paying back *borrowed* money **is** an expense, by the user's choice:
  - it creates a linked `Transaction` (`debt_record`) in the "Financial Expenses" category, kept in step by `services.sync_debt_expense`;
  - that record is left out of the balance sum, so money isn't subtracted twice;
  - the linked transaction can only be changed from its debt page.
- **Accounts and categories are per user,** stored in English and shown translated. Names users type are never translated.
- **Translations:** Bangla `.po` is written by hand; the other 28 locales are machine-translated. Compile with `python manage.py compile_po`, a pure-Python command.
- **Digits:** Bangla digits are applied client-side. Mark user-typed text with `data-keep-digits` to exempt it.
- **Currency:** a logged-in user's currency comes from `Profile` and defaults to BDT. It is only guessed from the language for visitors.
- **Local database:** `.env` points at the **live** Neon database; leave it that way. For local trials with fake data, set `DATABASE_URL=""` to use SQLite.
- **Tests** always use SQLite: `python manage.py test tracker`, about 72 tests.
- **Migrations:** run them on Neon (`python manage.py migrate`) *before* pushing a change that needs them.

## Pending / ideas
- **Social login:** Google and Facebook are deferred. They switch on once the `GOOGLE_*` and `FACEBOOK_*` env keys are set.
- **Landing page:** the "Your language" feature card still says "9 languages"; it should say 29.
- **Debt ideas offered to the user, not built:**
  - show this month's debt movement on the dashboard;
  - offer to count the unpaid part of a forgiven lent debt as an expense;
  - count extra paid on a loan (interest) as an expense;
  - optionally count borrowing as income.
- **Swahili:** one message ("… doesn't owe you anything …") is still in English.
