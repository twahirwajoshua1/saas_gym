from django.urls import path

from apps.memberapp import views

app_name = "memberapp"

urlpatterns = [
    path("card/<str:token>/", views.card, name="card"),
    path("api/v1/card/<str:token>/", views.card_api, name="card_api"),
    path("gym/members/<uuid:public_id>/card/", views.card_panel, name="card_panel"),
    path("gym/members/<uuid:public_id>/card/link/", views.card_link, name="card_link"),
]
