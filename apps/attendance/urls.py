from django.urls import path

from apps.attendance import views

app_name = "attendance"

urlpatterns = [
    path("guests/", views.guest_new, name="guest_new"),
    path("guests/rule/", views.guest_rule_set, name="guest_rule_set"),
    path("check-in/", views.home, name="home"),
    path("check-in/branch/", views.set_branch, name="set_branch"),
    path("check-in/<uuid:public_id>/", views.check_in, name="check_in"),
    path("members/<uuid:public_id>/visits/", views.member_visits, name="member_visits"),
]
