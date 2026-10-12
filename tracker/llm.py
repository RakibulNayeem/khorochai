"""
LLM layer. The AI ONLY turns free text into structured JSON.
All money math (totals, balances, budgets) is done by Python/SQL, never by the AI.

Providers (both have free tiers):
  - Google Gemini  (best for Bangla / Banglish)  -> GEMINI_API_KEY
  - Groq           (very fast fallback)          -> GROQ_API_KEY
Voice input is transcribed by Gemini (audio in the prompt), with Groq's Whisper as the backup.
"""
import json
import logging
import re
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


class LLMError(Exception):
    """Raised when no provider could return usable JSON."""


def normalize_text(text: str) -> str:
    """Convert Bangla digits to English digits so the model reads numbers reliably."""
    return text.translate(BN_DIGITS)


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _estimate_tokens(*texts) -> int:
    """Rough fallback when a provider does not report usage (~3 characters per token for mixed Bangla/English)."""
    return sum(len(t or "") for t in texts) // 3 + 1


def _call_gemini(system: str, user: str) -> tuple[str, int]:
    if not settings.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set")
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    r = requests.post(
        GEMINI_URL.format(model=settings.GEMINI_MODEL),
        headers={"x-goog-api-key": settings.GEMINI_API_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=settings.LLM_TIMEOUT,
    )
    if r.status_code != 200:
        raise LLMError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
    try:
        body = r.json()
        text = body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, ValueError):
        raise LLMError("Gemini returned an empty response")
    tokens = (body.get("usageMetadata") or {}).get("totalTokenCount") or _estimate_tokens(system, user, text)
    return text, int(tokens)


def _call_groq(system: str, user: str) -> tuple[str, int]:
    if not settings.GROQ_API_KEY:
        raise LLMError("GROQ_API_KEY is not set")
    payload = {
        "model": settings.GROQ_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    r = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
        json=payload,
        timeout=settings.LLM_TIMEOUT,
    )
    if r.status_code != 200:
        raise LLMError(f"Groq HTTP {r.status_code}: {r.text[:300]}")
    try:
        body = r.json()
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        raise LLMError("Groq returned an empty response")
    tokens = (body.get("usage") or {}).get("total_tokens") or _estimate_tokens(system, user, text)
    return text, int(tokens)


PROVIDERS = {"gemini": _call_gemini, "groq": _call_groq}


_pool = ThreadPoolExecutor(max_workers=8)


def _first_success(names, providers, call, what, hedge_seconds=None):
    """Ask providers in order, but don't wait for a slow one: if the current provider hasn't answered within
    settings.LLM_HEDGE_SECONDS, the next one is asked too and the first good answer wins. A provider that
    fails outright hands over immediately. Returns (name, result)."""
    hedge = settings.LLM_HEDGE_SECONDS if hedge_seconds is None else hedge_seconds
    queue = [n for n in names if n in providers]
    running, errors = {}, []

    def start_next():
        if queue:
            name = queue.pop(0)
            running[_pool.submit(call, providers[name])] = name

    start_next()
    while running:
        done, _ = wait(running, timeout=hedge if queue else settings.LLM_TIMEOUT + 5,
                       return_when=FIRST_COMPLETED)
        if not done:          # still waiting: bring in the next provider alongside (or give up if none is left)
            if not queue:
                errors.append("timed out")
                break
            start_next()
            continue
        for future in done:
            name = running.pop(future)
            try:
                return name, future.result()
            except (LLMError, requests.RequestException, json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning("%s with %s failed: %s", what, name, exc)
                errors.append(f"{name}: {exc}")
        if not running:
            start_next()
    raise LLMError("; ".join(errors) or f"No {what} provider configured")


def call_llm(system: str, user: str, usage: dict | None = None) -> dict:
    """Return the parsed JSON dict from the first provider that answers well (see _first_success).
    If `usage` is given, the tokens spent are added to usage["tokens"]."""
    def call(fn):
        raw, tokens = fn(system, user)
        return _extract_json(raw), tokens

    _, (data, tokens) = _first_success(settings.LLM_PROVIDERS, PROVIDERS, call, "LLM")
    if usage is not None:
        usage["tokens"] = usage.get("tokens", 0) + tokens
    return data


# --------------------------------------------------------------------------- prompts

PARSE_PROMPT = """You extract income, expense, transfer, balance and budget entries for a personal budget app used in Bangladesh.
The user may write in English, Bangla, Banglish (Bangla in English letters) or any other language,
and may mention several transactions in one message.

Return ONLY JSON in exactly this shape:
{"transactions": [{"type": "expense" or "income" or "transfer" or "balance" or "budget", "amount": number, "category": "string",
  "is_new_category": true or false, "account": "string", "to_account": "string",
  "note": "short note", "date": "YYYY-MM-DD"}],
 "message": "short explanation ONLY if no transaction was found, otherwise empty string"}

Rules:
1. Today is __TODAY__ (__WEEKDAY__). Convert relative dates to YYYY-MM-DD:
   আজ / aaj / today = today; গতকাল / gotokal / kal (past) / yesterday = yesterday;
   পরশু / porshu (past) = 2 days ago; "last Friday", "গত শুক্রবার" etc. = the most recent such day.
   If no date is mentioned, use today.
2. amount: positive number in the user's currency (__CURRENCY__). "1.5k" or "১.৫ হাজার" = 1500, "hazar/হাজার" = 1000,
   "lakh/লাখ" = 100000. If another currency is used, keep the number and mention the currency in note.
3. type: spent, paid, bought, gave, bill, khoroch, খরচ, দিলাম, কিনলাম = expense.
   received, earned, got, salary, income, পেলাম, আয়, বেতন = income.
4. category: pick the best match from the EXISTING categories of the same type, using the exact name:
   EXPENSE categories: __EXPENSE__
   INCOME categories: __INCOME__
   Set is_new_category=false when you use one of them.
   Only when nothing fits reasonably, invent a short new English category name (1-2 words, Title Case)
   and set is_new_category=true. Prefer a clear specific new category over dumping things
   into "Other Expense" / "Other Income".
5. account: which of the user's money accounts was used, exact name from: __ACCOUNTS__
   e.g. "bkash e pathalam", "বিকাশে", "bKash theke" = bKash; "card diye", "কার্ডে" = Card;
   "bank theke", "ব্যাংকে" = Bank; "nogod" / "নগদে" = Nagad; "cash", "hate" = Cash.
   If no account is mentioned, use an empty string "".
   TRANSFER: money moved between two of the user's OWN accounts is type "transfer" (not income or expense):
   "move 500 from cash to bkash", "bkash theke 1000 cash out", "ATM theke 2000 tullam" (Bank -> Cash),
   "bkash e 500 cash in korlam" (Cash -> bKash), "card bill 3000 bank theke dilam" (Bank -> Card).
   For a transfer: account = where the money comes FROM, to_account = where it goes TO,
   category = "", is_new_category = false. If a side is not mentioned, use "" for it.
   Sending money to ANOTHER person or paying a shop is NOT a transfer:
   "ma ke bkash e 1000 pathalam" = expense (category Family, account bKash).
   For income and expense, to_account = "".
   BALANCE: a statement of how much money is in an account RIGHT NOW (not money received or spent) is type "balance":
   "amar bkash e 5000 ache", "বিকাশে ৫০০০ টাকা আছে", "bank balance 50000", "hate 2000 taka ache" (Cash),
   "my cash is 1500 now". amount = that balance, account = the account (or "" if not said),
   category = "", is_new_category = false, to_account = "". "ache/আছে/balance/have/now" signal a balance;
   "pelam/পেলাম/got/received" is income, not a balance.
   BUDGET: setting a monthly spending limit for an EXPENSE category is type "budget":
   "khabar e mashe 6000 budget", "transport budget 2000", "বাজারে মাসে ৮০০০ টাকার বেশি খরচ করব না",
   "set my shopping limit to 5000", "rent budget 15000". amount = the monthly limit, category = the existing
   expense category (exact name; or a new short English name with is_new_category=true), account = "",
   to_account = "". "budget/বাজেট/limit/sima/সীমা/er beshi na/বেশি না" signal a budget.
   "remove/delete/bad dao the food budget" = type "budget" with amount 0.
6. note: short description in the user's own language, e.g. "রিকশা ভাড়া", "lunch with friends".
7. Never calculate totals. Never invent transactions. If there is no money amount at all,
   return {"transactions": [], "message": "<explain briefly in the user's language>"}.

Example
Input: "আজ বাজারে 550 টাকা খরচ, রিকশা 80, আর টিউশন থেকে 5000 পেলাম"
Output: {"transactions": [
 {"type":"expense","amount":550,"category":"Groceries","is_new_category":false,"account":"","note":"বাজার","date":"__TODAY__"},
 {"type":"expense","amount":80,"category":"Transport","is_new_category":false,"account":"","note":"রিকশা","date":"__TODAY__"},
 {"type":"income","amount":5000,"category":"Tuition","is_new_category":false,"account":"","note":"টিউশন","date":"__TODAY__"}],
 "message": ""}

Example
Input: "move 500 from cash to bkash, ATM theke 2000 tullam"
Output: {"transactions": [
 {"type":"transfer","amount":500,"category":"","is_new_category":false,"account":"Cash","to_account":"bKash","note":"move to bKash","date":"__TODAY__"},
 {"type":"transfer","amount":2000,"category":"","is_new_category":false,"account":"Bank","to_account":"Cash","note":"ATM","date":"__TODAY__"}],
 "message": ""}

Example
Input: "amar bkash e 5000 ache, bank e 50000"
Output: {"transactions": [
 {"type":"balance","amount":5000,"category":"","is_new_category":false,"account":"bKash","to_account":"","note":"","date":"__TODAY__"},
 {"type":"balance","amount":50000,"category":"","is_new_category":false,"account":"Bank","to_account":"","note":"","date":"__TODAY__"}],
 "message": ""}

Example
Input: "khabar e mashe 6000 budget, transport 2000"
Output: {"transactions": [
 {"type":"budget","amount":6000,"category":"Food","is_new_category":false,"account":"","to_account":"","note":"","date":"__TODAY__"},
 {"type":"budget","amount":2000,"category":"Transport","is_new_category":false,"account":"","to_account":"","note":"","date":"__TODAY__"}],
 "message": ""}

Example
Input: "gotokal gym membership 1500 bkash e dilam"
Output: {"transactions": [
 {"type":"expense","amount":1500,"category":"Fitness","is_new_category":true,"account":"bKash","note":"gym membership","date":"__YESTERDAY__"}],
 "message": ""}
"""


QUERY_PROMPT = """You convert a user's question about their own money into a JSON database query
for a budget app in Bangladesh. The user may ask in English, Bangla, Banglish or another language.

Return ONLY JSON in exactly this shape:
{"type": "expense" or "income" or "both",
 "categories": ["exact existing category names, or empty list for all"],
 "accounts": ["exact account names if the question is about specific accounts, else empty list"],
 "keyword": "word to search in notes, or empty string",
 "start_date": "YYYY-MM-DD",
 "end_date": "YYYY-MM-DD",
 "group_by": "none" or "category" or "day" or "month",
 "title": "the question restated briefly in the user's language"}

Rules:
- Today is __TODAY__ (__WEEKDAY__).
- "this month"/"এই মাসে" = __MONTH_START__ to __TODAY__. "last month"/"গত মাসে" = the full previous month.
  "this year" = Jan 1 of this year to today. "today", "yesterday", "this week", "last 7 days" etc. accordingly.
  If no period is mentioned, use this month.
- Use only these category names (exact spelling):
  EXPENSE: __EXPENSE__
  INCOME: __INCOME__
  ACCOUNTS: __ACCOUNTS__
  Map words like food/khabar/খাবার to the closest category. If the question is about a specific
  thing that is not a category (e.g. "rickshaw", "coffee"), put it in keyword instead.
- "how much did I spend/save/earn", "balance", "income vs expense" questions: choose type sensibly
  ("both" for balance / savings / comparisons).
- "where did I spend most", "breakdown" -> group_by "category". "daily", "per day" -> "day".
  "per month", "monthly trend" -> "month". Otherwise "none".
- Never compute numbers yourself.
"""


def _fill(template: str, today, expense_names, income_names, account_names=(), currency="BDT") -> str:
    import datetime as dt

    yesterday = today - dt.timedelta(days=1)
    return (
        template.replace("__TODAY__", today.isoformat())
        .replace("__YESTERDAY__", yesterday.isoformat())
        .replace("__WEEKDAY__", today.strftime("%A"))
        .replace("__MONTH_START__", today.replace(day=1).isoformat())
        .replace("__EXPENSE__", ", ".join(expense_names) or "(none)")
        .replace("__INCOME__", ", ".join(income_names) or "(none)")
        .replace("__ACCOUNTS__", ", ".join(account_names) or "(none)")
        .replace("__CURRENCY__", currency)
    )


def parse_transactions(text, today, expense_names, income_names, account_names=(), usage=None, currency="BDT") -> dict:
    system = _fill(PARSE_PROMPT, today, expense_names, income_names, account_names, currency)
    return call_llm(system, normalize_text(text), usage=usage)


TITLE_LANGUAGES = {
    "bn": "Bangla (বাংলা script)", "hi": "Hindi", "ur": "Urdu", "ar": "Arabic", "es": "Spanish",
    "fr": "French", "zh-hans": "Simplified Chinese", "id": "Indonesian", "ne": "Nepali", "si": "Sinhala",
    "ta": "Tamil", "ms": "Malay", "th": "Thai", "vi": "Vietnamese", "fil": "Filipino", "ja": "Japanese",
    "ko": "Korean", "my": "Burmese", "fa": "Persian", "tr": "Turkish", "pt": "Portuguese", "ru": "Russian",
    "de": "German", "it": "Italian", "nl": "Dutch", "pl": "Polish", "uk": "Ukrainian", "sw": "Swahili",
}


def build_query(question, today, expense_names, income_names, language="en", account_names=(), usage=None) -> dict:
    system = _fill(QUERY_PROMPT, today, expense_names, income_names, account_names)
    # the app's display language decides the language of the title shown above the answer
    title_lang = TITLE_LANGUAGES.get(language, "English")
    system += f'- Write "title" in {title_lang}.\n'
    return call_llm(system, normalize_text(question), usage=usage)


# --------------------------------------------------------------------------- voice input

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

TRANSCRIBE_PROMPT = """Transcribe this voice note exactly as spoken. The speaker talks about money they spent,
earned or moved. Most users are in Bangladesh and speak Bangla, English or a mix, but any language is possible.
- Write each language in its own script: Bangla in Bangla script, English in English letters, Hindi in Devanagari,
  Arabic/Urdu in Arabic script, and so on. A Bangla-English mix stays mixed, e.g. "আজ lunch এ 250 টাকা দিলাম".
- Write all numbers as digits (0-9), e.g. "পাঁচশো" -> 500, "two thousand" -> 2000.
- Do not translate, summarise or add anything. Return only the transcript, without quotes.
- If there is no speech, return an empty string."""


def _audio_tokens(audio: bytes) -> int:
    """Gemini counts ~32 tokens per second of audio; our WAV is 16 kHz mono 16-bit = 32,000 bytes per second."""
    return max(len(audio) // 1000, 1)


def _transcribe_gemini(audio: bytes, mime: str) -> tuple[str, int]:
    import base64

    if not settings.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set")
    payload = {
        "contents": [{"role": "user", "parts": [
            {"text": TRANSCRIBE_PROMPT},
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio).decode()}},
        ]}],
        "generationConfig": {"temperature": 0},
    }
    r = requests.post(
        GEMINI_URL.format(model=settings.GEMINI_MODEL),
        headers={"x-goog-api-key": settings.GEMINI_API_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=settings.LLM_TIMEOUT,
    )
    if r.status_code != 200:
        raise LLMError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
    try:
        body = r.json()
        parts = body["candidates"][0]["content"].get("parts") or [{"text": ""}]
        text = "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError, ValueError):
        raise LLMError("Gemini returned an empty response")
    tokens = (body.get("usageMetadata") or {}).get("totalTokenCount") or _audio_tokens(audio)
    return text, int(tokens)


def _transcribe_groq(audio: bytes, mime: str) -> tuple[str, int]:
    if not settings.GROQ_API_KEY:
        raise LLMError("GROQ_API_KEY is not set")
    r = requests.post(
        GROQ_TRANSCRIBE_URL,
        headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
        files={"file": ("voice.wav", audio, mime)},
        # the prompt is a spelling hint for local names and words Whisper doesn't know well
        data={"model": settings.GROQ_TRANSCRIBE_MODEL, "response_format": "json", "temperature": "0",
              "prompt": "bKash, Nagad, Rocket."},
        timeout=settings.LLM_TIMEOUT,
    )
    if r.status_code != 200:
        raise LLMError(f"Groq HTTP {r.status_code}: {r.text[:300]}")
    try:
        text = r.json()["text"]
    except (KeyError, ValueError):
        raise LLMError("Groq returned an empty transcription")
    return text, _audio_tokens(audio)  # Whisper bills by audio length; count it like Gemini would


TRANSCRIBERS = {"gemini": _transcribe_gemini, "groq": _transcribe_groq}


def transcribe(audio: bytes, mime: str = "audio/wav", usage: dict | None = None) -> str:
    """Speech -> text: Gemini first (it gets Bangla amounts right); Groq's Whisper only joins if Gemini is very
    slow or fails, because Whisper often mishears Bangla numbers. Tokens spent are added to usage["tokens"]."""
    _, (text, tokens) = _first_success(settings.TRANSCRIBE_PROVIDERS, TRANSCRIBERS, lambda fn: fn(audio, mime), "Transcription",
                                       hedge_seconds=settings.VOICE_HEDGE_SECONDS)
    if usage is not None:
        usage["tokens"] = usage.get("tokens", 0) + tokens
    return " ".join(text.strip().strip('"').split())
