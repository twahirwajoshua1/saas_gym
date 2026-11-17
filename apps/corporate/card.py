"""
The employee's card, opened on their own phone without signing in.

The card link is the key. The first phone that opens it keeps a secret
(a cookie) and becomes the card's phone; on any other phone the card does not
show. The QR code carries a 6-digit code that changes every 30 seconds, so a
screenshot sent to someone else stops working after about two minutes. The
employee also confirms each visit here: this replaces signing the paper list.
"""

from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.core import tenant_db
from apps.corporate import services
from apps.corporate.models import CorporateVisit, Employee
from apps.corporate.portal import qr_svg

COOKIE = "gymos_card"
COOKIE_AGE = 5 * 365 * 24 * 3600
PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}


def _employee_for(token) -> Employee:
    company_id = services.company_id_for_card(token)
    if company_id is None:
        raise Http404("No card with this link.")
    tenant_db.set_current_company(company_id)
    return get_object_or_404(Employee.objects.select_related("person", "company"), card_token=token)


def _private(response):
    for header, value in PRIVATE_HEADERS.items():
        response[header] = value
    return response


def _cookie_name(employee) -> str:
    return f"{COOKIE}_{employee.pk}"


@require_GET
def card(request, token):
    employee = _employee_for(token)
    secret = request.COOKIES.get(_cookie_name(employee))
    status = services.card_phone_status(employee, secret)
    new_secret = None
    if status == services.CardPhone.NEW:
        new_secret = services.claim_card(employee)
        status = services.CardPhone.THIS

    context = {"employee": employee, "status": status}
    if status == services.CardPhone.THIS and employee.is_active:
        code = services.card_code(employee)
        link = request.build_absolute_uri(reverse("corporate:card", args=[token])) + f"?c={code}"
        context |= {
            "qr": qr_svg(link),
            "code": code,
            "to_confirm": services.visits_to_confirm(employee),
            "recent": services.recent_visits(employee),
        }
    response = _private(render(request, "corporate/card.html", context))
    if new_secret:
        response.set_cookie(
            _cookie_name(employee),
            new_secret,
            max_age=COOKIE_AGE,
            httponly=True,
            samesite="Lax",
            secure=request.is_secure(),
        )
    return response


@require_POST
def confirm(request, token, public_id):
    employee = _employee_for(token)
    secret = request.COOKIES.get(_cookie_name(employee))
    if services.card_phone_status(employee, secret) != services.CardPhone.THIS:
        raise Http404("This card is set up on another phone.")
    visit = get_object_or_404(CorporateVisit.objects.filter(employee=employee), public_id=public_id)
    services.confirm_visit(employee=employee, visit=visit)
    return redirect("corporate:card", token=token)
