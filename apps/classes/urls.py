from django.urls import path

from apps.classes import views

app_name = "classes"

urlpatterns = [
    path("classes/", views.timetable, name="timetable"),
    path("classes/schedule/", views.schedule, name="schedule"),
    path("classes/session/<uuid:public_id>/", views.session_detail, name="session_detail"),
    path("classes/session/<uuid:public_id>/cancel/", views.session_cancel, name="session_cancel"),
    path("classes/session/<uuid:public_id>/came/", views.mark_came, name="mark_came"),
    path("classes/kinds/", views.class_types, name="class_types"),
    path("classes/kinds/new/", views.class_type_edit, name="class_type_new"),
    path("classes/kinds/<uuid:public_id>/", views.class_type_edit, name="class_type_edit"),
    path(
        "classes/kinds/<uuid:public_id>/archive/",
        views.class_type_archive,
        name="class_type_archive",
    ),
    path("trainers/", views.trainers, name="trainers"),
    path("trainers/<uuid:public_id>/", views.trainer_detail, name="trainer_detail"),
    path("trainers/<uuid:public_id>/edit/", views.trainer_edit, name="trainer_edit"),
    path("trainers/<uuid:public_id>/hours/", views.trainer_hours, name="trainer_hours"),
    path("trainers/new/<uuid:staff_id>/", views.trainer_edit, name="trainer_new"),
    path("reviews/<uuid:public_id>/hide/", views.review_hide, name="review_hide"),
]
