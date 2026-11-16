from django.urls import path

from apps.spreadsheets import views

app_name = "spreadsheets"

urlpatterns = [
    path("", views.home, name="home"),
    path("template/", views.template, name="template"),
    path("import/", views.upload, name="upload"),
    path("import/confirm/", views.confirm, name="confirm"),
    path("import/cancel/", views.cancel, name="cancel"),
    path("export/members/", views.export_members, name="export_members"),
    path("export/payments/", views.export_payments, name="export_payments"),
    path("export/visits/", views.export_visits, name="export_visits"),
]
