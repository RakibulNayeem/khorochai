from django.contrib import admin

from .models import Category, Transaction


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "icon", "monthly_budget", "user")
    list_filter = ("type", "user")
    search_fields = ("name",)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "type", "amount", "category", "note", "source", "user")
    list_filter = ("type", "source", "user", "date")
    search_fields = ("note", "raw_text")

admin.site.site_header = "KhorochAi Admin"
admin.site.site_title = "KhorochAi Admin"
admin.site.index_title = "KhorochAi"
