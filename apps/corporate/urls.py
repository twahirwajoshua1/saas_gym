from django.urls import path

from apps.corporate import card, portal, views

app_name = "corporate"

urlpatterns = [
    path("start/", views.start, name="start"),
    # The employee's own card (no sign-in)
    path("e/<str:token>/", card.card, name="card"),
    path("e/<str:token>/confirm/<uuid:public_id>/", card.confirm, name="card_confirm"),
    # Gym screens
    path("gym/companies/", views.gym_home, name="gym_home"),
    path("gym/companies/contracts/new/", views.contract_new, name="contract_new"),
    path(
        "gym/companies/contracts/<uuid:public_id>/", views.contract_detail, name="contract_detail"
    ),
    path(
        "gym/companies/contracts/<uuid:public_id>/edit/", views.contract_edit, name="contract_edit"
    ),
    path("gym/companies/contracts/<uuid:public_id>/end/", views.contract_end, name="contract_end"),
    path("gym/companies/contracts/<uuid:public_id>/bill/", views.bill_issue, name="bill_issue"),
    path("gym/companies/desk/", views.desk, name="desk"),
    path("gym/companies/desk/<uuid:public_id>/", views.desk_check_in, name="desk_check_in"),
    path("gym/companies/visits/", views.visit_list, name="visit_list"),
    path(
        "gym/companies/activities/<uuid:public_id>/cancel/",
        views.activity_cancel,
        name="activity_cancel",
    ),
    path("gym/companies/warnings/", views.warning_list, name="warning_list"),
    path(
        "gym/companies/warnings/<uuid:public_id>/settle/",
        views.warning_settle,
        name="warning_settle",
    ),
    path("gym/companies/bills/", views.bill_list, name="bill_list"),
    path("gym/companies/bills/<uuid:public_id>/", views.bill_detail, name="bill_detail"),
    path("gym/companies/bills/<uuid:public_id>/pay/", views.bill_payment, name="bill_payment"),
    path(
        "gym/companies/payments/<uuid:public_id>/reverse/",
        views.bill_payment_reverse,
        name="bill_payment_reverse",
    ),
    path("gym/companies/lines/<int:pk>/credit/", views.bill_line_credit, name="bill_line_credit"),
    # Company screens
    path("company/", portal.portal_home, name="portal_home"),
    path("company/employees/", portal.employee_list, name="employee_list"),
    path("company/employees/add/", portal.employee_add, name="employee_add"),
    path("company/employees/import/", portal.employee_import, name="employee_import"),
    path(
        "company/employees/import/review/",
        portal.employee_import_review,
        name="employee_import_review",
    ),
    path("company/employees/<uuid:public_id>/", portal.employee_detail, name="employee_detail"),
    path(
        "company/employees/<uuid:public_id>/action/",
        portal.employee_action,
        name="employee_action",
    ),
    path("company/contracts/", portal.contract_list, name="portal_contracts"),
    path(
        "company/contracts/<uuid:public_id>/",
        portal.contract_detail,
        name="portal_contract_detail",
    ),
    path(
        "company/contracts/<uuid:public_id>/decide/",
        portal.contract_decide,
        name="portal_contract_decide",
    ),
    path(
        "company/contracts/<uuid:public_id>/end/",
        portal.contract_end,
        name="portal_contract_end",
    ),
    path("company/visits/", portal.visit_list, name="portal_visits"),
    path(
        "company/visits/<uuid:public_id>/question/",
        portal.visit_question,
        name="portal_visit_question",
    ),
    path("company/warnings/", portal.warning_list, name="portal_warnings"),
    path(
        "company/warnings/<uuid:public_id>/accept/",
        portal.warning_accept,
        name="portal_warning_accept",
    ),
    path("company/bills/", portal.bill_list, name="portal_bills"),
    path("company/league/", portal.league, name="portal_league"),
    path("company/bills/<uuid:public_id>/", portal.bill_detail, name="portal_bill_detail"),
]
