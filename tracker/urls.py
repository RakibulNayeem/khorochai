from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("ai/parse/", views.ai_parse, name="ai_parse"),
    path("ai/confirm/", views.ai_confirm, name="ai_confirm"),
    path("ask/", views.ask, name="ask"),
    path("transactions/", views.transaction_list, name="transactions"),
    path("transactions/add/", views.transaction_create, name="transaction_add"),
    path("transactions/<int:pk>/edit/", views.transaction_edit, name="transaction_edit"),
    path("transactions/<int:pk>/delete/", views.transaction_delete, name="transaction_delete"),
    path("transactions/export/", views.export_csv, name="export_csv"),
    path("categories/", views.category_list, name="categories"),
    path("categories/add/", views.category_create, name="category_add"),
    path("categories/<int:pk>/edit/", views.category_edit, name="category_edit"),
    path("categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
    path("accounts/", views.account_list, name="accounts"),
    path("accounts/add/", views.account_create, name="account_add"),
    path("accounts/<int:pk>/edit/", views.account_edit, name="account_edit"),
    path("accounts/<int:pk>/delete/", views.account_delete, name="account_delete"),
    path("transfers/add/", views.transfer_create, name="transfer_add"),
    path("transfers/<int:pk>/edit/", views.transfer_edit, name="transfer_edit"),
    path("transfers/<int:pk>/delete/", views.transfer_delete, name="transfer_delete"),
    path("settings/", views.preferences, name="preferences"),
]
