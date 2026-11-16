from django.urls import path

from apps.dashboard import views

app_name = "dashboard"

urlpatterns = [
    path("figures/", views.figures, name="figures"),
    path("renewals/", views.renewals, name="renewals"),
]
