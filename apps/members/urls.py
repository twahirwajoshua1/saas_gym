from django.urls import path

from apps.members import views

app_name = "members"

urlpatterns = [
    path("members/", views.member_list, name="member_list"),
    path("members/register/", views.member_register, name="member_register"),
    path("members/<uuid:public_id>/", views.member_detail, name="member_detail"),
    path("members/<uuid:public_id>/edit/", views.member_edit, name="member_edit"),
    path("memberships/<uuid:public_id>/adjust/", views.membership_adjust, name="membership_adjust"),
    path("freezes/<uuid:public_id>/end/", views.freeze_end, name="freeze_end"),
    path("packages/", views.package_list, name="package_list"),
    path("packages/add/", views.package_create, name="package_create"),
    path("packages/<uuid:public_id>/edit/", views.package_edit, name="package_edit"),
    path("packages/<uuid:public_id>/active/", views.package_set_active, name="package_set_active"),
]
