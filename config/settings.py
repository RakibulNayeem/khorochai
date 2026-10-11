"""
Django settings for the AI Budget Tracker.

Works locally (SQLite) and on Vercel + Neon (PostgreSQL) with no code changes.
All secrets come from environment variables (.env locally, Vercel dashboard in production).
"""
import os
import sys
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_list(name, default=""):
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DEBUG", "False").lower() in ("1", "true", "yes")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,.vercel.app")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "https://*.vercel.app")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "tracker",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "tracker.context_processors.preferences",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------- Database
# Local: SQLite. Production: set DATABASE_URL to your Neon *pooled* connection string.
DATABASE_URL = os.environ.get("DATABASE_URL")
RUNNING_TESTS = len(sys.argv) > 1 and sys.argv[1] == "test"
if DATABASE_URL and not RUNNING_TESTS:  # tests always use local SQLite, never the live database
    DATABASES = {
        # Keep connections open between requests (a new TLS + login handshake costs several round trips);
        # health checks drop a connection that Neon closed while it was idle.
        "default": dj_database_url.parse(DATABASE_URL, conn_max_age=300, conn_health_checks=True, ssl_require=True)
    }
    # Neon's pooler (PgBouncer, transaction mode) does not support server-side cursors
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Interface language: chosen in Settings and stored in the "django_language" cookie.
# Translations live in locale/bn/LC_MESSAGES/django.po -> run `python manage.py compile_po` after editing.
LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("bn", "বাংলা")]
LOCALE_PATHS = [BASE_DIR / "locale"]
LANGUAGE_COOKIE_AGE = 60 * 60 * 24 * 365
TIME_ZONE = "Asia/Dhaka"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------- Static files
# WhiteNoise serves static files straight from the apps (no collectstatic step needed on Vercel).
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
WHITENOISE_USE_FINDERS = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------- Accounts & login (django-allauth)
# Users log in with email + password, or with Google / Facebook. The username still exists
# (made from the email, changeable in Settings) but is never needed to log in.
# New emails must be verified with a 6-digit code; forgotten passwords are reset with a code.
LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "dashboard"
ACCOUNT_LOGOUT_REDIRECT_URL = "account_login"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True
ACCOUNT_PASSWORD_RESET_BY_CODE_ENABLED = True
ALLAUTH_USER_CODE_FORMAT = {"numeric": True, "length": 6, "dashed": False}  # e.g. 482913
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_LOGIN_ON_PASSWORD_RESET = True
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_EMAIL_SUBJECT_PREFIX = "KhorochAi: "
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http" if DEBUG else "https"

# Google / Facebook login: each one switches on only when its keys are set.
SOCIALACCOUNT_PROVIDERS = {}
if os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"):
    INSTALLED_APPS.append("allauth.socialaccount.providers.google")
    SOCIALACCOUNT_PROVIDERS["google"] = {
        "APP": {"client_id": os.environ["GOOGLE_CLIENT_ID"], "secret": os.environ["GOOGLE_CLIENT_SECRET"]},
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"prompt": "select_account"},
    }
if os.environ.get("FACEBOOK_APP_ID") and os.environ.get("FACEBOOK_APP_SECRET"):
    INSTALLED_APPS.append("allauth.socialaccount.providers.facebook")
    SOCIALACCOUNT_PROVIDERS["facebook"] = {
        "APP": {"client_id": os.environ["FACEBOOK_APP_ID"], "secret": os.environ["FACEBOOK_APP_SECRET"]},
        "METHOD": "oauth2",
        "SCOPE": ["email", "public_profile"],
        "FIELDS": ["id", "email", "name", "first_name", "last_name"],
    }
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True           # Google login with the same (verified) email = same user
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True

# ---------------------------------------------------------------- Email (verification + reset codes)
# Gmail: EMAIL_HOST_USER = your Gmail address, EMAIL_HOST_PASSWORD = a Google "App password".
# Without them (local development) emails are printed in the terminal instead of sent.
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "").replace(" ", "")
if EMAIL_HOST_USER and EMAIL_HOST_PASSWORD:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_USE_TLS = True
    EMAIL_TIMEOUT = 20
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows terminals can't print Bangla emails otherwise
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL") or f"KhorochAi <{EMAIL_HOST_USER or 'noreply@localhost'}>"

# ---------------------------------------------------------------- Security (production)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# ---------------------------------------------------------------- AI settings
# Provider order: the first one is tried first, the next is the fallback.
LLM_PROVIDERS = env_list("LLM_PROVIDERS", "gemini,groq")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_TRANSCRIBE_MODEL = os.environ.get("GROQ_TRANSCRIBE_MODEL", "whisper-large-v3-turbo")  # voice input backup
VOICE_MAX_BYTES = 2_000_000  # ~60 s of 16 kHz mono WAV
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "25"))
# Daily AI limits. Free tiers: Gemini Flash-Lite ~500 requests/day; Groq gpt-oss-120b 200,000 tokens/day.
# Measured: one "Add with AI" request uses ~1,600 tokens, one "Ask AI" ~700 (so 30,000 = ~18 adds a day).
AI_DAILY_TOKENS_PER_USER = int(os.environ.get("AI_DAILY_TOKENS_PER_USER", "30000"))
AI_DAILY_TOKENS_TOTAL = int(os.environ.get("AI_DAILY_TOKENS_TOTAL", "1000000"))
AI_DAILY_REQUESTS_TOTAL = int(os.environ.get("AI_DAILY_REQUESTS_TOTAL", "400"))

CURRENCY_SYMBOL = os.environ.get("CURRENCY_SYMBOL", "৳")

# ---------------------------------------------------------------- Help & contact page
CONTACT_EMAIL = "khorochai@gmail.com"
DEVELOPER = {
    "name": "Rakibul Nayeem",
    "email": "rakibul.cse.ruet@gmail.com",
    "facebook": "https://www.facebook.com/raki.nayeem",   # full URLs; a button is hidden while its link is empty
    "linkedin": "https://www.linkedin.com/in/rakibulnayeem/",
    "portfolio": "https://rakibulnayeem.vercel.app/",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
