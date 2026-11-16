from django.urls import path

from apps.platformadmin import views

app_name = "platformadmin"

urlpatterns = [
    path("", views.gym_list, name="gym_list"),
    path("gyms/add/", views.gym_create, name="gym_create"),
    path("gyms/<uuid:public_id>/", views.gym_detail, name="gym_detail"),
    path("gyms/<uuid:public_id>/module/", views.gym_module, name="gym_module"),
    path("gyms/<uuid:public_id>/active/", views.gym_active, name="gym_active"),
]
