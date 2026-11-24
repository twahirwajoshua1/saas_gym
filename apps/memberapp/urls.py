from django.urls import path

from apps.memberapp import views

app_name = "memberapp"

urlpatterns = [
    path("card/<str:token>/", views.card, name="card"),
    path("api/v1/card/<str:token>/", views.card_api, name="card_api"),
    path("card/<str:token>/classes/", views.card_classes, name="card_classes"),
    path("card/<str:token>/classes/<uuid:public_id>/", views.card_book, name="card_book"),
    path(
        "card/<str:token>/classes/<uuid:public_id>/review/", views.card_review, name="card_review"
    ),
    path("card/<str:token>/trainers/", views.card_trainers, name="card_trainers"),
    path(
        "card/<str:token>/trainers/<uuid:public_id>/book/",
        views.card_book_trainer,
        name="card_book_trainer",
    ),
    path(
        "card/<str:token>/trainers/<uuid:public_id>/cancel/",
        views.card_cancel_trainer,
        name="card_cancel_trainer",
    ),
    path("gym/members/<uuid:public_id>/card/", views.card_panel, name="card_panel"),
    path("gym/members/<uuid:public_id>/card/link/", views.card_link, name="card_link"),
]
