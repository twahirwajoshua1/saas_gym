from django.urls import path

from apps.gyms import views

app_name = "gyms"

urlpatterns = [
    path("enter/", views.enter, name="enter"),
    path("choose/", views.choose, name="choose"),
    path("", views.home, name="home"),
    path("activity/", views.activity, name="activity"),
    path("branches/", views.branch_list, name="branch_list"),
    path("branches/<uuid:public_id>/", views.branch_detail, name="branch_detail"),
]
