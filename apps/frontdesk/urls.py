from django.urls import path

from apps.frontdesk import views

app_name = "frontdesk"

urlpatterns = [
    path("gym/desk/", views.desk, name="desk"),
    path("gym/members/", views.people, name="people"),
    path("gym/signups/<uuid:public_id>/pay/", views.signup_accept, name="signup_accept"),
    path("gym/signups/<uuid:public_id>/drop/", views.signup_drop, name="signup_drop"),
    path("gym/desk/problems/<uuid:public_id>/done/", views.problem_done, name="problem_done"),
    path("gym/tablets/", views.tablets, name="tablets"),
    path("gym/tablets/<uuid:public_id>/", views.tablet_action, name="tablet_action"),
    # The entrance tablet (no sign-in)
    path("tablet/", views.kiosk, name="kiosk"),
    path("tablet/connect/", views.kiosk_connect, name="kiosk_connect"),
    path("tablet/scan/", views.kiosk_scan, name="kiosk_scan"),
    path("tablet/pin/", views.kiosk_pin, name="kiosk_pin"),
    path("tablet/enter/<uuid:public_id>/", views.kiosk_enter, name="kiosk_enter"),
    path(
        "tablet/member/<uuid:public_id>/",
        views.kiosk_member_enter,
        name="kiosk_member_enter",
    ),
    path("tablet/idle/", views.kiosk_idle, name="kiosk_idle"),
]
