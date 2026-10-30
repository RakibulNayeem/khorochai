"""The lists people pick from when adding an account: Bangladeshi banks and mobile payment services.

Picking only fills the account's name, icon and color, so an account never depends on this list:
users can rename it, and a bank leaving the list changes nothing for them.
Names stay in English. The second value is what else people may search for: short names and Bangla.

Logos are looked up by the account's name, so renaming an account to another bank can't leave the old bank's logo.
Names missing from LOGOS have no logo we could confirm, and those accounts show their emoji instead.
"""
import re

from django.templatetags.static import static

# kind -> (icon, color) of an account with nothing more specific
KIND_DEFAULTS = {
    "cash": ("💵", "#16a34a"),
    "mobile": ("📱", "#0ea5e9"),
    "bank": ("🏦", "#2563eb"),
    "card": ("💳", "#7c3aed"),
    "other": ("👛", "#64748b"),
}

BANKS = [
    ("AB Bank", "এবি ব্যাংক"),
    ("Agrani Bank", "অগ্রণী ব্যাংক"),
    ("Al-Arafah Islami Bank", "AIBL আল আরাফাহ ইসলামী ব্যাংক"),
    ("Ansar VDP Unnayan Bank", "আনসার ভিডিপি উন্নয়ন ব্যাংক"),
    ("Bangladesh Commerce Bank", "BCBL বাংলাদেশ কমার্স ব্যাংক"),
    ("Bangladesh Development Bank", "BDBL বাংলাদেশ ডেভেলপমেন্ট ব্যাংক"),
    ("Bangladesh Krishi Bank", "BKB বাংলাদেশ কৃষি ব্যাংক"),
    ("Bangladesh Samabaya Bank", "বাংলাদেশ সমবায় ব্যাংক"),
    ("Bank Al-Falah", "Alfalah ব্যাংক আল ফালাহ"),
    ("Bank Asia", "ব্যাংক এশিয়া"),
    ("BASIC Bank", "বেসিক ব্যাংক"),
    ("Bengal Commercial Bank", "BCB বেঙ্গল কমার্শিয়াল ব্যাংক"),
    ("BRAC Bank", "ব্র্যাক ব্যাংক"),
    ("Citibank N.A.", "Citi সিটিব্যাংক এনএ"),
    ("Citizens Bank", "সিটিজেন্স ব্যাংক"),
    ("Commercial Bank of Ceylon", "কমার্শিয়াল ব্যাংক অব সিলন"),
    ("Community Bank Bangladesh", "কমিউনিটি ব্যাংক"),
    ("Dhaka Bank", "ঢাকা ব্যাংক"),
    ("Dutch-Bangla Bank", "DBBL Dutch Bangla ডাচ বাংলা ব্যাংক"),
    ("Eastern Bank", "EBL ইস্টার্ন ব্যাংক"),
    ("EXIM Bank", "এক্সিম ব্যাংক"),
    ("First Security Islami Bank", "FSIBL ফার্স্ট সিকিউরিটি ইসলামী ব্যাংক"),
    ("Global Islami Bank", "গ্লোবাল ইসলামী ব্যাংক"),
    ("Grameen Bank", "গ্রামীণ ব্যাংক"),
    ("Habib Bank", "HBL হাবিব ব্যাংক"),
    ("HSBC", "এইচএসবিসি"),
    ("ICB Islamic Bank", "আইসিবি ইসলামিক ব্যাংক"),
    ("IFIC Bank", "আইএফআইসি ব্যাংক"),
    ("Islami Bank Bangladesh", "IBBL ইসলামী ব্যাংক বাংলাদেশ"),
    ("Jamuna Bank", "যমুনা ব্যাংক"),
    ("Janata Bank", "জনতা ব্যাংক"),
    ("Karmasangsthan Bank", "কর্মসংস্থান ব্যাংক"),
    ("Meghna Bank", "মেঘনা ব্যাংক"),
    ("Mercantile Bank", "MBL মার্কেন্টাইল ব্যাংক"),
    ("Midland Bank", "মিডল্যান্ড ব্যাংক"),
    ("Modhumoti Bank", "মধুমতি ব্যাংক"),
    ("Mutual Trust Bank", "MTB মিউচুয়াল ট্রাস্ট ব্যাংক"),
    ("National Bank", "NBL ন্যাশনাল ব্যাংক"),
    ("National Bank of Pakistan", "NBP ন্যাশনাল ব্যাংক অব পাকিস্তান"),
    ("NCC Bank", "এনসিসি ব্যাংক"),
    ("NRB Bank", "এনআরবি ব্যাংক"),
    ("NRBC Bank", "NRB Commercial এনআরবিসি ব্যাংক"),
    ("One Bank", "ওয়ান ব্যাংক"),
    ("Padma Bank", "Farmers পদ্মা ব্যাংক"),
    ("Palli Sanchay Bank", "পল্লী সঞ্চয় ব্যাংক"),
    ("Premier Bank", "প্রিমিয়ার ব্যাংক"),
    ("Prime Bank", "প্রাইম ব্যাংক"),
    ("Probashi Kallyan Bank", "প্রবাসী কল্যাণ ব্যাংক"),
    ("Pubali Bank", "পূবালী ব্যাংক"),
    ("Rajshahi Krishi Unnayan Bank", "RAKUB রাজশাহী কৃষি উন্নয়ন ব্যাংক"),
    ("Rupali Bank", "রূপালী ব্যাংক"),
    ("Sammilito Islami Bank", "সম্মিলিত ইসলামী ব্যাংক"),
    ("SBAC Bank", "South Bangla Agriculture সাউথ বাংলা এগ্রিকালচার ব্যাংক"),
    ("Shahjalal Islami Bank", "SJIBL শাহজালাল ইসলামী ব্যাংক"),
    ("Shimanto Bank", "সীমান্ত ব্যাংক"),
    ("Social Islami Bank", "SIBL সোশ্যাল ইসলামী ব্যাংক"),
    ("Sonali Bank", "সোনালী ব্যাংক"),
    ("Southeast Bank", "সাউথইস্ট ব্যাংক"),
    ("Standard Bank", "স্ট্যান্ডার্ড ব্যাংক"),
    ("Standard Chartered", "SCB স্ট্যান্ডার্ড চার্টার্ড"),
    ("State Bank of India", "SBI স্টেট ব্যাংক অব ইন্ডিয়া"),
    ("The City Bank", "City সিটি ব্যাংক"),
    ("Trust Bank", "ট্রাস্ট ব্যাংক"),
    ("Union Bank", "ইউনিয়ন ব্যাংক"),
    ("United Commercial Bank", "UCB ইউসিবি ইউনাইটেড কমার্শিয়াল ব্যাংক"),
    ("Uttara Bank", "উত্তরা ব্যাংক"),
    ("Woori Bank", "উরি ব্যাংক"),
]

MOBILE_SERVICES = [
    # (name, search words, icon, color) - the first three match DEFAULT_ACCOUNTS in services.py
    ("bKash", "বিকাশ", "📱", "#e2136e"),
    ("Nagad", "Nogod নগদ", "📲", "#f6921e"),
    ("Rocket", "DBBL রকেট", "🚀", "#8c3494"),
    ("Upay", "উপায়", *KIND_DEFAULTS["mobile"]),
    ("mCash", "Islami Bank এমক্যাশ", *KIND_DEFAULTS["mobile"]),
    ("Cellfin", "Islami Bank সেলফিন", *KIND_DEFAULTS["mobile"]),
    ("MyCash", "Mercantile মাইক্যাশ", *KIND_DEFAULTS["mobile"]),
    ("OK Wallet", "One Bank ওকে ওয়ালেট", *KIND_DEFAULTS["mobile"]),
    ("Pathao Pay", "পাঠাও পে", *KIND_DEFAULTS["mobile"]),
    ("SureCash", "শিওরক্যাশ", *KIND_DEFAULTS["mobile"]),
    ("Tap", "Trust Axiata Pay ট্যাপ", *KIND_DEFAULTS["mobile"]),
]

LOGOS = frozenset({
    "AB Bank", "Agrani Bank", "Bangladesh Commerce Bank", "Bank Asia", "BASIC Bank", "BRAC Bank", "Citibank N.A.",
    "Citizens Bank", "Commercial Bank of Ceylon", "Community Bank Bangladesh", "Dhaka Bank", "Eastern Bank", "EXIM Bank",
    "Grameen Bank", "Habib Bank", "HSBC", "ICB Islamic Bank", "IFIC Bank", "Jamuna Bank", "Meghna Bank", "Mercantile Bank",
    "Midland Bank", "Mutual Trust Bank", "National Bank", "NRBC Bank", "One Bank", "Padma Bank", "Premier Bank", "Prime Bank",
    "Pubali Bank", "Rajshahi Krishi Unnayan Bank", "Rupali Bank", "SBAC Bank", "Shahjalal Islami Bank", "Shimanto Bank",
    "Social Islami Bank", "Sonali Bank", "Southeast Bank", "Standard Chartered", "The City Bank", "Union Bank",
    "United Commercial Bank", "Uttara Bank",
    "bKash", "Nagad", "OK Wallet", "Pathao Pay", "Tap", "Upay",
})
BANK_NAMES = sorted((name for name, _ in BANKS), key=len, reverse=True)
MOBILE_NAMES = sorted((row[0] for row in MOBILE_SERVICES), key=len, reverse=True)


def logo_file(name):
    return "img/banks/" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") + ".png"


def logo_for(account_name, kind):
    """The logo for an account, or "". Longest name first: "National Bank of Pakistan" is not "National Bank"."""
    names = MOBILE_NAMES if kind == "mobile" else BANK_NAMES if kind in ("bank", "card") else ()
    typed = account_name.lower()
    for name in names:
        if typed == name.lower() or typed.startswith(name.lower() + " "):
            return logo_file(name) if name in LOGOS else ""
    return ""


def picker_data():
    """What the account form's search list needs, by account kind. A card is listed by the bank that issued it."""
    def bank_rows(kind, suffix=""):
        icon, color = KIND_DEFAULTS[kind]
        return [{"name": f"{name}{suffix}", "search": f"{name} {words}".lower(), "icon": icon, "color": color,
                 "logo": static(logo_file(name)) if name in LOGOS else ""}
                for name, words in BANKS]

    return {
        "lists": {
            "bank": bank_rows("bank"),
            "card": bank_rows("card", " Card"),
            "mobile": [{"name": name, "search": f"{name} {words}".lower(), "icon": icon, "color": color,
                        "logo": static(logo_file(name)) if name in LOGOS else ""}
                       for name, words, icon, color in MOBILE_SERVICES],
        },
        "defaults": {kind: {"icon": icon, "color": color} for kind, (icon, color) in KIND_DEFAULTS.items()},
    }
