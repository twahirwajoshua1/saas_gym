from django.urls import path

from apps.modules import views

app_name = "modules"

urlpatterns = [
    path("", views.module_list, name="list"),
    path("<str:code>/ask/", views.ask, name="ask"),
]
