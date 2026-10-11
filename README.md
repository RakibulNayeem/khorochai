<img src="static/img/logo.svg" width="64" alt="KhorochAi logo">

# KhorochAi

Type what you spent or earned in **English, বাংলা or Banglish**. The AI reads it, picks the category
(or suggests a new one), you confirm, and the dashboard updates.

> **AI reads your text. Python does the math.** The AI never calculates totals, so numbers are always correct.

## Features
- ✨ AI input: `আজ বাজারে ৫৫০ টাকা খরচ, রিকশা ৮০, টিউশন থেকে ৫০০০ পেলাম` → 3 transactions
- Preview screen before saving (edit amount, type, category, date; untick to skip)
- New category suggested by AI → created only when you save
- Dashboard: income, expense, balance, savings %, category doughnut, day-by-day chart
- Monthly budgets per category with progress bars and warnings
- ✨ Ask AI: "এই মাসে খাবারে কত খরচ হলো?", "Where did I spend most last month?"
- Transactions list with filters, search, edit, delete, CSV export (Bangla-safe for Excel)
- Multi-user: log in with email + password (or Google / Facebook). New emails are verified with a 6-digit code;
  forgotten passwords are reset with a 6-digit code sent by email
- Accounts: Cash, bKash, Nagad, Bank, Card (add your own). The AI picks the account from your text
  ("bkash e 500 dilam"); "Move money" records ATM withdrawals / cash outs between accounts
- Daily AI token allowance per user and for the whole app (stays inside the free tiers), shown as "% used" next to the AI box
- ⚙ Settings (gear icon, top right): Light / Dark / System theme and English / বাংলা interface
- Free AI: Google Gemini (main) + Groq (backup). If the AI fails, you can still add manually.

## Project structure
```
KhorochAi/
├── config/            settings.py, urls.py, wsgi.py
├── tracker/
│   ├── llm.py         AI prompts + Gemini/Groq calls (text -> JSON only)
│   ├── services.py    validation, totals, budgets, query engine (all math here)
│   ├── views.py       pages and AI flows
│   ├── models.py      Category, Transaction
│   └── tests.py       10 tests (AI is mocked)
├── templates/         HTML (Bootstrap)
├── static/css/app.css
├── requirements.txt
└── .env.example
```

---

# PART 1 — Run it on your computer (Windows)

Run these in **VS Code → Terminal** (or Command Prompt), inside the `KhorochAi` folder.

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Get a **free Gemini API key**: open https://aistudio.google.com/apikey → *Create API key* → copy it.
(Optional backup) Groq key: https://console.groq.com/keys

Open `.env` in VS Code and set:
```
DEBUG=True
GEMINI_API_KEY=paste-your-key-here
GROQ_API_KEY=paste-groq-key-here      (optional)
```
Leave `DATABASE_URL=` empty for now (uses a local SQLite file).

Back in the terminal:
```bat
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```
Open http://127.0.0.1:8000 → log in → type `aaj lunch 250 ar rickshaw 60` → **Add with AI**.

Run tests (optional): `python manage.py test tracker`

Changed the Bangla text in `locale/bn/LC_MESSAGES/django.po`? Run `python manage.py compile_po`
(no extra tools needed) and commit both the `.po` and the `.mo` file.

---

# PART 2 — Upload to GitHub

1. On **github.com** → **New repository** → name: `KhorochAi` → Public or Private → **Create** (do NOT add a README).
2. In the **VS Code terminal** (inside the project folder):
```bat
git init
git add .
git commit -m "KhorochAi"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/KhorochAi.git
git push -u origin main
```
Check on GitHub that **`.env` is NOT uploaded** (it is in `.gitignore`). Your keys must stay secret.

---

# PART 3 — Create the database on Neon

1. Go to **https://neon.tech** → sign in with GitHub → **New Project**
   - Name: `khorochai` · Region: **AWS Asia Pacific (Singapore)** (closest to Bangladesh)
2. On the project dashboard click **Connect**.
   - Turn **Connection pooling ON** (the host will contain `-pooler`).
   - Copy the connection string. It looks like:
     `postgresql://neondb_owner:xxxx@ep-xxxx-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`
3. Create the tables **from your computer** (VS Code terminal):
   - In `.env`, set `DATABASE_URL=` to the string you copied.
   ```bat
   python manage.py migrate
   python manage.py createsuperuser
   ```
   This creates all tables in Neon. (After this, your local app also uses Neon. To go back to
   SQLite locally, empty `DATABASE_URL` in `.env` again.)

---

# PART 4 — Deploy on Vercel

1. Go to **https://vercel.com** → sign in with GitHub → **Add New… → Project**
2. Find `KhorochAi` → **Import**. Set the Vercel project name to `khorochai` (Vercel only allows lowercase). Vercel detects Django automatically (it finds `manage.py`). Check that **Framework Preset** says **Django** (Settings → Build and Deployment).
3. Open **Environment Variables** and add these (one by one):

| Name | Value |
|---|---|
| `DJANGO_SECRET_KEY` | a long random string (see below) |
| `DEBUG` | `False` |
| `DATABASE_URL` | your Neon pooled connection string |
| `GEMINI_API_KEY` | your Gemini key |
| `GROQ_API_KEY` | your Groq key (optional) |
| `ALLOWED_HOSTS` | `.vercel.app` |
| `CSRF_TRUSTED_ORIGINS` | `https://*.vercel.app` |

   Make a secret key in the VS Code terminal:
   ```bat
   python -c "import secrets; print(secrets.token_urlsafe(50))"
   ```
4. Click **Deploy**. Wait ~1–2 minutes → open the `https://xxxx.vercel.app` link → log in.

### Later updates
- Changed code? → `git add . && git commit -m "update" && git push` → Vercel redeploys automatically.
- Changed `models.py`? → run `python manage.py makemigrations` locally, commit the new migration file,
  push, and run `python manage.py migrate` locally with `DATABASE_URL` pointing at Neon.
- Changed an environment variable on Vercel? → **Deployments → ⋯ → Redeploy**.

### Custom domain (optional)
Vercel → Project → **Settings → Domains** → add your domain. Then add it to
`ALLOWED_HOSTS` (e.g. `.vercel.app,budget.example.com`) and
`CSRF_TRUSTED_ORIGINS` (e.g. `https://*.vercel.app,https://budget.example.com`), and redeploy.

---

## Login emails, Google and Facebook

**Gmail (verification + password-reset codes)** — free, about 500 emails a day:
1. Google Account → **Security** → turn on **2-Step Verification**.
2. Google Account → search **App passwords** → create one named `KhorochAi` → copy the 16-letter password.
3. Set `EMAIL_HOST_USER` (your Gmail address) and `EMAIL_HOST_PASSWORD` (the app password) in `.env` and on Vercel.
Without them (local development) the emails are printed in the terminal.

**Continue with Google** (optional): [Google Cloud Console](https://console.cloud.google.com/apis/credentials) →
Create credentials → **OAuth client ID** → Web application. Authorized redirect URI:
`https://YOUR-APP.vercel.app/auth/google/login/callback/` (and `http://127.0.0.1:8000/auth/google/login/callback/` for local).
Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

**Continue with Facebook** (optional): [developers.facebook.com](https://developers.facebook.com/apps) → Create app →
add **Facebook Login** → Valid OAuth redirect URI: `https://YOUR-APP.vercel.app/auth/facebook/login/callback/`.
Set `FACEBOOK_APP_ID` and `FACEBOOK_APP_SECRET`, and switch the app to **Live** mode.

Each button appears on the login page only when its keys are set.

## Troubleshooting

| Problem | Fix |
|---|---|
| "The AI is busy or not configured" | Check `GEMINI_API_KEY` on Vercel, then redeploy. Free tier has per-minute limits; wait a minute. Add `GROQ_API_KEY` as backup. |
| "AI limit used up" | Daily token allowance reached. Change `AI_DAILY_TOKENS_PER_USER` / `AI_DAILY_TOKENS_TOTAL` / `AI_DAILY_REQUESTS_TOTAL` (keep the totals under your free quota). |
| Gemini says the model is not found | Google retires models over time. Set `GEMINI_MODEL` to a current free model from https://ai.google.dev/gemini-api/docs/models and redeploy. |
| `relation "tracker_category" does not exist` | Tables not created in Neon → run PART 3, step 3. |
| 400 Bad Request | `ALLOWED_HOSTS` is wrong. Use `.vercel.app` (with the dot). |
| 403 CSRF failed on login | `CSRF_TRUSTED_ORIGINS` must be `https://*.vercel.app` (with `https://`). |
| 500 error | Vercel → Project → **Logs** shows the Python error. |
| Page has no styling | Static files: redeploy; check `STATIC_ROOT` is in settings (it is). |

## How the AI part works
1. `llm.parse_transactions()` sends your text + your category list + today's date to Gemini.
2. Gemini returns JSON: type, amount, category, `is_new_category`, note, date.
3. `services.clean_parsed()` validates it (positive amount, real date, matching category).
4. You confirm on the preview page → saved. Totals come from SQL `SUM()`.
5. **Ask AI** works the same way: the AI turns your question into a filter
   (dates, categories, keyword), and `services.run_query()` calculates the answer.

Privacy note: the text you type is sent to Google (or Groq) to be understood. Free-tier
data may be used by the provider to improve their models. Don't type card numbers or passwords.
