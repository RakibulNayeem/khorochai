from django import forms
from django.contrib.auth.models import User
from django.utils.translation import gettext, gettext_lazy as _

from .models import EXPENSE, Account, Category, Transaction, Transfer


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
        fields = ("name", "kind", "icon", "color", "opening_balance", "is_default")
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
