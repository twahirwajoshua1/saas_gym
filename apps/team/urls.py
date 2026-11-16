from django.urls import path

from apps.team import views

app_name = "team"

urlpatterns = [
    path("", views.staff_list, name="staff_list"),
    path("add/", views.staff_add, name="staff_add"),
    path("<uuid:public_id>/edit/", views.staff_edit, name="staff_edit"),
    path("<uuid:public_id>/remove/", views.staff_remove, name="staff_remove"),
    path("<uuid:public_id>/password/", views.staff_reset_password, name="staff_reset_password"),
]
