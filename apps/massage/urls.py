from django.urls import path

from apps.massage import views

app_name = "massage"

urlpatterns = [
    path("massage/", views.diary, name="diary"),
    path("massage/who/", views.find_person, name="find_person"),
    path("massage/book/", views.book, name="book"),
    path("massage/<uuid:public_id>/answer/", views.answer, name="answer"),
    path("massage/<uuid:public_id>/cancel/", views.cancel, name="cancel"),
    path("massage/<uuid:public_id>/done/", views.mark_done, name="mark_done"),
    path("massage/<uuid:public_id>/note/", views.note, name="note"),
    path("my-day/", views.my_day, name="my_day"),
    # Settings (owner)
    path("massage/types/", views.type_list, name="type_list"),
    path("massage/types/new/", views.type_new, name="type_new"),
    path("massage/types/<uuid:public_id>/", views.type_edit, name="type_edit"),
    path("massage/types/<uuid:public_id>/offered/", views.type_set_active, name="type_set_active"),
    path("massage/rules/", views.rules, name="rules"),
]
