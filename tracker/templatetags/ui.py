from django import template

register = template.Library()


@register.filter
def bs_widget(field, extra=""):
    """Render a form field's widget with Bootstrap classes (used by the allauth templates)."""
    widget = field.field.widget
    input_type = getattr(widget, "input_type", "")
    if input_type in ("checkbox", "radio"):
        css = "form-check-input"
    elif input_type == "select" or widget.__class__.__name__.endswith("Select"):
        css = "form-select"
    else:
        css = "form-control"
    if field.errors:
        css += " is-invalid"
    attrs = {"class": f"{css} {extra}".strip()}
    if field.name == "code":  # 6-digit email codes: number keypad + phones offer the code from the email
        attrs.update(inputmode="numeric", autocomplete="one-time-code", placeholder="000000", maxlength="6")
    return field.as_widget(attrs=attrs)
