"""Tests for the member's card link, the card page, the API and QR check-in."""

import uuid
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from apps.attendance import services as attendance
from apps.audit.models import AuditEntry
from apps.billing import services as billing
from apps.billing.services import PaymentInput
from apps.gyms.models import Role
from apps.members import services as members
from apps.members.models import Member
from apps.members.services import MemberDetails
from apps.modules import services as modules
from apps.modules.catalog import ATTENDANCE_ACCESS, PAYMENTS_FINANCE

pytestmark = pytest.mark.django_db


def today():
    return timezone.localdate()


@pytest.fixture
def alpha(make_gym):
    staff = make_gym("Alpha Fitness", "0788000001", "Aline Uwase")
    modules.enable_module(gym=staff.gym, code=PAYMENTS_FINANCE)
    modules.enable_module(gym=staff.gym, code=ATTENDANCE_ACCESS)
    return staff


@pytest.fixture
def member(alpha):
    who = members.register_member(
        by=alpha.assignment, phone="0788111222", details=MemberDetails(full_name="Eric Mugisha")
    )
    package = members.create_package(
        by=alpha.assignment,
        name="Monthly",
        price_rwf=30000,
        duration_count=1,
        duration_unit="month",
    )
    billing.sell_membership(
        by=alpha.assignment,
        member=who,
        package=package,
        start_date=today(),
        idempotency_key=uuid.uuid4(),
        payment=PaymentInput(method="mtn_momo", amount_rwf=10000, reference="TX1"),
    )
    return who


@pytest.fixture
def token(alpha, member):
    return members.create_card_link(by=alpha.assignment, member=member)


# --- Card links -------------------------------------------------------------


def test_a_card_link_is_long_random_kept_and_renewable(alpha, member):
    # Registering already gave this member a card; clear it to see one made.
    Member.objects.filter(pk=member.pk).update(card_token=None)
    member.refresh_from_db()
    first = members.create_card_link(by=alpha.assignment, member=member)
    again = members.create_card_link(by=alpha.assignment, member=member)
    renewed = members.create_card_link(by=alpha.assignment, member=member, renew=True)

    assert len(first) >= 40 and first == again and renewed != first
    actions = list(
        AuditEntry.objects.for_gym(alpha.gym)
        .filter(action__startswith="member.card_link")
        .order_by("id")
        .values_list("action", flat=True)
    )
    assert actions == ["member.card_link_created", "member.card_link_renewed"]


def test_a_trainer_cannot_make_card_links(alpha, member, add_staff):
    trainer = add_staff(alpha.gym, "0788000005", Role.TRAINER)

    with pytest.raises(PermissionDenied):
        members.create_card_link(by=trainer.assignment, member=member)


def test_finding_a_member_by_card_code(alpha, member, token, make_gym):
    url = f"https://example.rw/card/{token}/"

    assert members.find_by_card_code(alpha.gym, url) == member
    assert members.find_by_card_code(alpha.gym, token) == member
    assert members.find_by_card_code(alpha.gym, "not a code") is None
    bravo = make_gym("Bravo Fitness", "0788000002")
    assert members.find_by_card_code(bravo.gym, url) is None  # another gym's card


def test_the_database_lookup_returns_only_a_gym_for_an_exact_token(alpha, token):
    assert members.gym_id_for_card(token) == alpha.gym.id
    assert members.gym_id_for_card(token[:-1] + ("A" if token[-1] != "A" else "B")) is None
    assert members.gym_id_for_card("") is None
    assert members.gym_id_for_card("%") is None


# --- The card page (opened without signing in) ------------------------------


def test_the_card_page_shows_status_qr_balance_and_receipts(client, member, token):
    response = client.get(reverse("memberapp:card", args=[token]))

    page = response.content.decode()
    assert response.status_code == 200
    assert "Alpha Fitness" in page and "Eric Mugisha" in page and "Valid until" in page
    assert "<svg" in page  # the QR code
    assert "Still to pay: RWF 20,000." in page
    assert "RCT-000001" in page
    assert "0788 ••• 222" in page and "0788111222" not in page
    assert response["Cache-Control"] == "no-store"
    assert response["Referrer-Policy"] == "no-referrer"
    assert "noindex" in response["X-Robots-Tag"]


def test_an_unknown_or_old_card_link_is_not_found(client, alpha, member, token):
    assert client.get(reverse("memberapp:card", args=["x" * 43])).status_code == 404
    members.create_card_link(by=alpha.assignment, member=member, renew=True)
    assert client.get(reverse("memberapp:card", args=[token])).status_code == 404


def test_the_card_shows_why_a_member_cannot_come_in(client, alpha, member, token):
    membership = members.current_membership(member)
    members.freeze_membership(
        by=alpha.assignment, membership=membership, start=today(), days=5, reason="Travel"
    )

    page = client.get(reverse("memberapp:card", args=[token])).content.decode()

    assert "Not valid" in page and "frozen until" in page


def test_opening_a_card_reveals_nothing_from_other_members_or_gyms(client, alpha, token, make_gym):
    members.register_member(
        by=alpha.assignment, phone="0788999000", details=MemberDetails(full_name="Other Member")
    )
    bravo = make_gym("Bravo Fitness", "0788000002")
    members.register_member(
        by=bravo.assignment, phone="0788555666", details=MemberDetails(full_name="Bravo Member")
    )

    page = client.get(reverse("memberapp:card", args=[token])).content.decode()

    assert "Other Member" not in page and "Bravo" not in page


def test_the_card_request_still_runs_under_tenant_isolation(client, token):
    """After the token is checked, the request may only see the card's gym."""
    from django.db import connection

    from apps.core import tenant_db

    client.get(reverse("memberapp:card", args=[token]))

    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        assert cursor.fetchone()[0] != tenant_db.APP_ROLE  # back to normal afterwards


# --- The API ----------------------------------------------------------------


def test_the_card_api_returns_the_same_facts_as_json(client, member, token):
    response = client.get(reverse("memberapp:card_api", args=[token]))

    data = response.json()
    assert response["Content-Type"] == "application/json"
    assert data["gym"] == "Alpha Fitness"
    assert data["member"] == {"name": "Eric Mugisha", "phone": "0788 ••• 222"}
    assert data["allowed_in_today"] is True and data["reason"] is None
    assert data["membership"]["package"] == "Monthly"
    assert data["owes_rwf"] == 20000
    assert data["receipts"][0] == {
        "number": "RCT-000001",
        "date": today().isoformat(),
        "amount_rwf": 10000,
        "method": "MTN Mobile Money",
        "reversed": False,
    }
    assert client.get(reverse("memberapp:card_api", args=["y" * 43])).status_code == 404


def test_the_api_only_reads(client, token):
    assert client.post(reverse("memberapp:card_api", args=[token])).status_code == 405


# --- Staff panel ------------------------------------------------------------


def test_staff_create_and_share_a_card_link(alpha, member, sign_in):
    client = sign_in(alpha)
    panel_url = reverse("memberapp:card_panel", args=[member.public_id])

    # A member registered at the desk already has a card, ready to hand over.
    panel = client.get(panel_url, headers={"HX-Request": "true"}).content.decode()
    assert "<html" not in panel and member.card_token

    # A member brought in from paper records may have none until staff ask.
    Member.objects.filter(pk=member.pk).update(card_token=None)
    empty = client.get(panel_url, headers={"HX-Request": "true"}).content.decode()
    assert "Create card link" in empty

    client.post(reverse("memberapp:card_link", args=[member.public_id]))
    panel = client.get(panel_url, headers={"HX-Request": "true"}).content.decode()

    member.refresh_from_db()
    assert f"/card/{member.card_token}/" in panel
    assert "https://wa.me/250788111222?text=" in panel and "sms:+250788111222?body=" in panel
    member_page = client.get(reverse("members:member_detail", args=[member.public_id]))
    assert panel_url in member_page.content.decode()


def test_another_gyms_staff_cannot_reach_the_panel(alpha, member, make_gym, sign_in):
    bravo = make_gym("Bravo Fitness", "0788000002")
    client = sign_in(bravo)

    assert client.get(reverse("memberapp:card_panel", args=[member.public_id])).status_code == 404
    assert client.post(reverse("memberapp:card_link", args=[member.public_id])).status_code == 404
    assert Member.objects.get(pk=member.pk).card_token == member.card_token


# --- QR check-in ------------------------------------------------------------


def test_scanning_a_card_at_the_desk_finds_the_member(alpha, member, token, sign_in):
    scanned = f"http://testserver/card/{token}/"

    results = attendance.find_for_check_in(alpha.gym, scanned)
    assert [r.member for r in results] == [member] and results[0].allowed

    page = sign_in(alpha).get(reverse("attendance:home"), {"q": scanned}).content.decode()
    assert "Eric Mugisha" in page
    assert reverse("attendance:check_in", args=[member.public_id]) in page
    assert "Scan card with camera" in page


def test_another_gyms_card_finds_nobody_at_the_desk(alpha, token, make_gym):
    bravo = make_gym("Bravo Fitness", "0788000002")

    assert attendance.find_for_check_in(bravo.gym, f"/card/{token}/") == []


def test_an_expired_card_is_refused_at_the_desk(alpha, member, token):
    from apps.members.models import Membership

    Membership.objects.filter(member=member).update(
        start_date=today() - timedelta(days=40), end_date=today() - timedelta(days=2)
    )

    result = attendance.find_for_check_in(alpha.gym, token)[0]

    assert not result.allowed and result.reason == "expired"


def test_the_qr_drawing_scales_to_fit_the_card():
    from apps.memberapp.services import qr_svg

    svg = qr_svg("https://example.rw/card/abc/")

    assert "viewBox" in svg and 'width="' not in svg.split(">")[0]
