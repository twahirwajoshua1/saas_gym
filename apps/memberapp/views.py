"""
The member's card page and the card API, opened without signing in, and the
staff panel for sharing a member's card link.

Opening a card: the token in the link is checked with the database's narrow
card lookup (see members.services.gym_id_for_card). Only then is the database
told which gym this request acts for, exactly as signing in does for staff.
"""

from urllib.parse import quote

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from apps.core import tenant_db
from apps.gyms.permissions import FRONT_DESK_ROLES
from apps.gyms.tenancy import gym_required
from apps.memberapp import services
from apps.members import services as members
from apps.members.models import Member

PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}


def _member_for_token(token: str) -> Member:
    gym_id = members.gym_id_for_card(token)
    if gym_id is None:
        raise Http404("No card with this link.")
    tenant_db.set_current_gym(gym_id)
    queryset = Member.objects.filter(gym_id=gym_id, card_token=token)
    return get_object_or_404(queryset.select_related("person", "gym"))


def _private(response):
    for header, value in PRIVATE_HEADERS.items():
        response[header] = value
    return response


@require_GET
def card(request, token):
    member = _member_for_token(token)
    summary = services.card_summary(member)
    url = request.build_absolute_uri(reverse("memberapp:card", args=[token]))
    return _private(
        render(request, "memberapp/card.html", {"card": summary, "qr": services.qr_svg(url)})
    )


@require_GET
def card_api(request, token):
    """GET /api/v1/card/<token>/ : the card as JSON. The first API endpoint."""
    member = _member_for_token(token)
    data = services.card_as_data(services.card_summary(member))
    return _private(JsonResponse(data))


# --- For staff ---------------------------------------------------------------


def _staff_member(request, public_id) -> Member:
    return get_object_or_404(
        Member.objects.for_gym(request.gym).select_related("person"), public_id=public_id
    )


@gym_required(*FRONT_DESK_ROLES)
def card_panel(request, public_id):
    member = _staff_member(request, public_id)
    context = {"member": member}
    if member.card_token:
        url = request.build_absolute_uri(reverse("memberapp:card", args=[member.card_token]))
        message = _("Your %(gym)s membership card: %(url)s") % {"gym": request.gym.name, "url": url}
        digits = member.person.phone.lstrip("+")
        context |= {
            "card_url": url,
            "qr": services.qr_svg(url, scale=4),
            "whatsapp_url": f"https://wa.me/{digits}?text={quote(message)}",
        }
    if request.headers.get("HX-Request"):
        return render(request, "memberapp/_card_panel.html", context)
    return render(request, "memberapp/card_panel.html", context)


@gym_required(*FRONT_DESK_ROLES)
@require_POST
def card_link(request, public_id):
    member = _staff_member(request, public_id)
    members.create_card_link(
        by=request.assignment, member=member, renew=request.POST.get("renew") == "yes"
    )
    return redirect("members:member_detail", public_id=member.public_id)
