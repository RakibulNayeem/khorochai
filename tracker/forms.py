from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.utils.translation import gettext, gettext_lazy as _

from .billing import image_type
from .models import DEBT_DIRECTION_CHOICES, EXPENSE, PAY_METHOD_CHOICES, Account, Category, Debt, DebtRecord, Payment, Transaction, Transfer


class BootstrapMixin:
    def _style(self):
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs.setdefault("class", "form-select")
            elif isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault("class", "form-check-input")
            else:
                w.attrs.setdefault("class", "form-control")


class ProfileForm(BootstrapMixin, forms.ModelForm):
    """Username (greeting + menu; people log in with their email) and the currency for every amount."""
    currency = forms.ChoiceField(label=_("Currency"), help_text=_("Used for every amount in the app."))

    class Meta:
        model = User
        fields = ("username",)
        labels = {"username": _("Username")}
        help_texts = {"username": _("Shown in the app. You log in with your email, not this.")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .currency import CURRENCY_CHOICES, DEFAULT_CURRENCY
        from .models import Profile
        self.fields["currency"].choices = CURRENCY_CHOICES
        profile = Profile.objects.filter(user=self.instance).first() if self.instance.pk else None
        self.fields["currency"].initial = profile.currency if profile else DEFAULT_CURRENCY
        self._style()

    def save(self, commit=True):
        from .models import Profile
        user = super().save(commit)
        Profile.objects.update_or_create(user=user, defaults={"currency": self.cleaned_data["currency"]})
        return user

    def clean_username(self):
        name = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_("This username is already taken."))
        return name


class TransactionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ("type", "amount", "category", "account", "note", "date")
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
        }
        labels = {"type": _("Type"), "amount": _("Amount"), "category": _("Category"),
                  "account": _("Account"), "note": _("Note"), "date": _("Date")}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["category"].queryset = Category.objects.filter(user=user)
        self.fields["account"].queryset = Account.objects.filter(user=user)
        self._style()

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount is None or amount <= 0:
            raise forms.ValidationError(_("Amount must be greater than zero."))
        return amount

    def clean(self):
        data = super().clean()
        cat, type_ = data.get("category"), data.get("type")
        if cat and type_ and cat.type != type_:
            if type_ == "income":
                msg = gettext("'%(name)s' is an expense category; pick an income category.")
            else:
                msg = gettext("'%(name)s' is an income category; pick an expense category.")
            self.add_error("category", msg % {"name": cat})
        return data


class CategoryForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ("name", "type", "icon", "color", "monthly_budget")
        widgets = {
            "color": forms.TextInput(attrs={"type": "color"}),
            "icon": forms.TextInput(attrs={"placeholder": _("e.g. 🍔")}),
            "monthly_budget": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }
        labels = {"name": _("Name"), "type": _("Type"), "icon": _("Icon"), "color": _("Color"),
                  "monthly_budget": _("Monthly budget")}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self._style()
        self.fields["color"].widget.attrs["class"] = "form-control form-control-color"

    def clean(self):
        data = super().clean()
        name, type_ = data.get("name"), data.get("type")
        if name and type_:
            qs = Category.objects.filter(user=self.user, type=type_, name__iexact=name.strip())
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("name", _("You already have a category with this name."))
        if type_ != EXPENSE and data.get("monthly_budget"):
            data["monthly_budget"] = None
        return data


class AccountForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Account
        fields = ("kind", "name", "icon", "color", "opening_balance", "is_default")
        widgets = {
            "color": forms.TextInput(attrs={"type": "color"}),
            "icon": forms.TextInput(attrs={"placeholder": _("e.g. 💵")}),
            "opening_balance": forms.NumberInput(attrs={"step": "0.01"}),
        }
        labels = {"name": _("Name"), "kind": _("Type"), "icon": _("Icon"), "color": _("Color"),
                  "opening_balance": _("Opening balance"), "is_default": _("Default account")}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self._style()
        self.fields["color"].widget.attrs["class"] = "form-control form-control-color"

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        qs = Account.objects.filter(user=self.user, name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(_("You already have an account with this name."))
        return name


class TransferForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Transfer
        fields = ("from_account", "to_account", "amount", "date", "note")
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "note": forms.TextInput(attrs={"placeholder": _("e.g. ATM withdrawal, bKash cash out")}),
        }
        labels = {"from_account": _("From"), "to_account": _("To"), "amount": _("Amount"),
                  "date": _("Date"), "note": _("Note")}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        accounts = Account.objects.filter(user=user)
        self.fields["from_account"].queryset = accounts
        self.fields["to_account"].queryset = accounts
        self._style()

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount is None or amount <= 0:
            raise forms.ValidationError(_("Amount must be greater than zero."))
        return amount

    def clean(self):
        data = super().clean()
        if data.get("from_account") and data.get("from_account") == data.get("to_account"):
            self.add_error("to_account", _("Pick two different accounts."))
        return data


REMIND_CHOICES = [("0", _("On due date")), ("1", _("1 day before")), ("3", _("3 days before")), ("custom", _("Custom")), ("off", _("No reminder"))]
MAX_REMIND_DAYS = 60


class DebtForm(BootstrapMixin, forms.ModelForm):
    """A debt and, when it has a pay-back date, when to be reminded about it."""
    remind = forms.ChoiceField(label=_("Remind me"), choices=REMIND_CHOICES, widget=forms.RadioSelect, required=False)
    remind_custom = forms.IntegerField(label=_("Days before"), min_value=1, max_value=MAX_REMIND_DAYS, required=False,
                                       widget=forms.NumberInput(attrs={"min": 1, "max": MAX_REMIND_DAYS}))

    class Meta:
        model = Debt
        fields = ("direction", "person", "phone", "amount", "date", "due_date", "remind_time", "remind_daily",
                  "account", "note")
        widgets = {
            "direction": forms.RadioSelect,
            "person": forms.TextInput(attrs={"list": "people", "autocomplete": "off", "placeholder": _("e.g. Rahim")}),
            "phone": forms.TextInput(attrs={"type": "tel", "autocomplete": "off", "placeholder": "01XXX-XXXXXX",
                                            "inputmode": "tel"}),
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "due_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "remind_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "note": forms.TextInput(attrs={"placeholder": _("e.g. for his phone, till salary day")}),
        }
        labels = {"direction": _("Type"), "person": _("Person"), "phone": _("Phone"), "amount": _("Amount"),
                  "account": _("Account"), "date": _("Date"), "due_date": _("Pay back by"),
                  "remind_time": _("Reminder time"), "remind_daily": _("Repeat daily if overdue"), "note": _("Note")}
        help_texts = {"account": _("Where the money went out of (lent) or came into (borrowed). Its balance changes."),
                      "remind_daily": _("Every day until it is paid back.")}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(user=user)
        self.fields["account"].required = True
        self.fields["remind_time"].required = False
        days = self.instance.remind_days if self.instance.pk else 1  # new debts: a day before, if a date is set
        if days is None:
            self.initial.setdefault("remind", "off")
        elif str(days) in ("0", "1", "3"):
            self.initial.setdefault("remind", str(days))
        else:
            self.initial.setdefault("remind", "custom")
            self.initial.setdefault("remind_custom", days)
        self._style()
        self.fields["direction"].widget.attrs.pop("class", None)  # drawn as two toggle buttons
        self.fields["remind"].widget.attrs.pop("class", None)     # drawn as chips
        self.fields["direction"].choices = DEBT_DIRECTION_CHOICES  # no empty "---------" choice

    def clean_person(self):
        return " ".join(self.cleaned_data["person"].split())

    def clean_phone(self):
        phone = "".join(self.cleaned_data["phone"].split())
        if phone and not all(c.isdigit() or c in "+-" for c in phone):
            raise forms.ValidationError(_("Use digits only, e.g. 01712-345678."))
        return phone

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount is None or amount <= 0:
            raise forms.ValidationError(_("Amount must be greater than zero."))
        return amount

    def clean(self):
        data = super().clean()
        if data.get("due_date") and data.get("date") and data["due_date"] < data["date"]:
            self.add_error("due_date", _("The pay-back date can't be before the date of the debt."))
        remind = data.get("remind")
        if remind == "custom" and not data.get("remind_custom"):
            self.add_error("remind_custom", _("How many days before the due date?"))
        if not data.get("remind_time"):
            data["remind_time"] = self.instance.remind_time
        return data

    def save(self, commit=True):
        debt = super().save(commit=False)
        remind = self.cleaned_data.get("remind")
        if not debt.due_date or remind in (None, "", "off"):
            debt.remind_days = None
        else:
            debt.remind_days = self.cleaned_data["remind_custom"] if remind == "custom" else int(remind)
        if "due_date" in self.changed_data or "remind" in self.changed_data:
            debt.snoozed_until = None  # a new date or reminder starts fresh
        if commit:
            debt.save()
        return debt


class DebtRecordForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = DebtRecord
        fields = ("kind", "amount", "account", "date", "note")
        widgets = {
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        labels = {"kind": _("Type"), "amount": _("Amount"), "account": _("Account"), "date": _("Date"), "note": _("Note")}

    def __init__(self, *args, user=None, debt=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.debt = debt
        self.fields["account"].queryset = Account.objects.filter(user=user)
        self.fields["account"].required = True
        self._style()

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount is None or amount <= 0:
            raise forms.ValidationError(_("Amount must be greater than zero."))
        return amount

    def clean(self):
        data = super().clean()
        if self.debt and data.get("kind") == "repay" and data.get("amount"):
            from .services import debt_totals
            debt_totals(self.debt)
            left = self.debt.remaining + (self.instance.amount if self.instance.pk and self.instance.kind == "repay" else 0)
            if data["amount"] > left:
                from .templatetags.money import money
                self.add_error("amount", gettext("Only %(left)s is left to pay back.") % {"left": money(max(left, 0))})
        return data


class PaymentForm(BootstrapMixin, forms.ModelForm):
    """What a user sends after paying for a plan. The screenshot is checked to be a real PNG, JPEG or WebP image."""
    screenshot_file = forms.FileField(label=_("Payment screenshot"), required=False,
                                      help_text=_("Optional, but it helps us confirm your payment faster."))

    class Meta:
        model = Payment
        fields = ("method", "name", "phone", "paid_from", "transaction_id", "note")
        labels = {"method": _("Paid with"), "name": _("Your name"), "phone": _("Phone number"),
                  "paid_from": _("Paid from (number or account)"), "transaction_id": _("Transaction ID"),
                  "note": _("Note (optional)")}
        widgets = {"method": forms.RadioSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()
        self.fields["method"].choices = PAY_METHOD_CHOICES  # no empty "---------" radio
        self.fields["method"].widget.attrs["class"] = "form-check-input"
        self.fields["screenshot_file"].widget.attrs["accept"] = "image/png,image/jpeg,image/webp"
        for name in ("phone", "paid_from"):
            self.fields[name].widget.attrs.update({"inputmode": "tel", "autocomplete": "tel"})

    def clean_transaction_id(self):
        return self.cleaned_data["transaction_id"].strip().upper()

    def clean_screenshot_file(self):
        upload = self.cleaned_data.get("screenshot_file")
        if not upload:
            return None
        if upload.size > settings.PAYMENT_SCREENSHOT_MAX_BYTES:
            raise forms.ValidationError(_("The screenshot is too large. Please use one under 3 MB."))
        data = upload.read()
        mime = image_type(data)
        if not mime:
            raise forms.ValidationError(_("Please upload a PNG, JPG or WebP image."))
        return data, mime

    def save(self, commit=True):
        payment = super().save(commit=False)
        shot = self.cleaned_data.get("screenshot_file")
        if shot:
            payment.screenshot, payment.screenshot_type = shot
        if commit:
            payment.save()
        return payment
