from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    # login, sign-up, email codes, password reset, Google/Facebook (django-allauth)
    path("auth/", include("allauth.urls")),
    # old addresses
    path("login/", RedirectView.as_view(pattern_name="account_login", query_string=True)),
    path("signup/", RedirectView.as_view(pattern_name="account_signup", query_string=True)),
    path("", include("tracker.urls")),
]
