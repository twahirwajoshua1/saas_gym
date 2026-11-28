from django.urls import path

from apps.members import views

app_name = "members"

urlpatterns = [
    # The gym's own sign-up page, reached by a QR poster. No sign-in.
    path("join/<uuid:public_id>/", views.join, name="join"),
    path("joined/<uuid:gym_id>/<uuid:public_id>/", views.joined, name="joined"),
    path("members/register/", views.member_register, name="member_register"),
    path("members/<uuid:public_id>/", views.member_detail, name="member_detail"),
    path("members/<uuid:public_id>/edit/", views.member_edit, name="member_edit"),
    path("memberships/<uuid:public_id>/adjust/", views.membership_adjust, name="membership_adjust"),
    path("freezes/<uuid:public_id>/end/", views.freeze_end, name="freeze_end"),
    path("join-link/", views.join_link, name="join_link"),
    path("packages/", views.package_list, name="package_list"),
    path("packages/add/", views.package_create, name="package_create"),
    path("packages/prices/", views.price_grid_save, name="price_grid_save"),
    path(
        "packages/tickets/<uuid:public_id>/add/",
        views.ticket_bundle_add,
        name="ticket_bundle_add",
    ),
    path("packages/<uuid:public_id>/edit/", views.package_edit, name="package_edit"),
    path("packages/<uuid:public_id>/active/", views.package_set_active, name="package_set_active"),
    path("services/add/", views.service_create, name="service_create"),
    path("services/<uuid:public_id>/edit/", views.service_edit, name="service_edit"),
    path("services/<uuid:public_id>/active/", views.service_set_active, name="service_set_active"),
]
