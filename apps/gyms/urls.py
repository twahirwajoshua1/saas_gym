from django.urls import path

from apps.gyms import views

app_name = "gyms"

urlpatterns = [
    path("enter/", views.enter, name="enter"),
    path("choose/", views.choose, name="choose"),
    path("", views.home, name="home"),
    path("activity/", views.activity, name="activity"),
    path("settings/", views.settings_home, name="settings"),
    path("branches/", views.branch_list, name="branch_list"),
    path("branches/add/", views.branch_add, name="branch_add"),
    path("branches/<uuid:public_id>/", views.branch_detail, name="branch_detail"),
    path("branches/<uuid:public_id>/edit/", views.branch_edit, name="branch_edit"),
]
