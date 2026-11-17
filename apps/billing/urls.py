from django.urls import path

from apps.billing import views

app_name = "billing"

urlpatterns = [
    path("payments/", views.payments_home, name="payments_home"),
    path("payments/<uuid:public_id>/receipt/", views.receipt, name="receipt"),
    path("payments/<uuid:public_id>/reverse/", views.reverse_payment, name="payment_reverse"),
    path("invoices/<uuid:public_id>/pay/", views.record_payment, name="payment_record"),
    path("members/<uuid:public_id>/sell/", views.sell_membership, name="sell"),
    path("members/<uuid:public_id>/sessions/", views.sell_sessions, name="sell_sessions"),
    path("members/<uuid:public_id>/account/", views.member_account, name="member_account"),
]
