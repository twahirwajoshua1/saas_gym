"""Tests for services (single sessions such as sauna) and selling them."""

import uuid

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.billing import services as billing
from apps.billing.models import InvoiceLine, LedgerEntry, Payment
from apps.billing.services import PaymentInput
from apps.gyms.models import Role
from apps.members import services as member_services
from apps.members.models import Service
from apps.members.services import InvalidDetails, MemberDetails
from apps.modules import services as module_services
from apps.modules.catalog import PAYMENTS_FINANCE

pytestmark = pytest.mark.django_db


@pytest.fixture
def alpha(make_gym):
    staff = make_gym("Alpha Fitness", "0788000001", "Aline Uwase")
    module_services.enable_module(gym=staff.gym, code=PAYMENTS_FINANCE)
    return staff


@pytest.fixture
def sauna(alpha):
    return member_services.create_service(by=alpha.assignment, name="Sauna", price_rwf=5000)


@pytest.fixture
def member(alpha):
    return member_services.register_member(
        by=alpha.assignment, phone="0788111222", details=MemberDetails(full_name="Eric Mugisha")
    )


def test_owner_adds_renames_and_archives_a_service(alpha, sauna):
    assert sauna.price_rwf == 5000
    member_services.update_service(
        by=alpha.assignment, service=sauna, name="Sauna (30 min)", price_rwf=6000
    )
    sauna.refresh_from_db()
    assert (sauna.name, sauna.price_rwf) == ("Sauna (30 min)", 6000)
    member_services.set_service_active(by=alpha.assignment, service=sauna, active=False)
    assert list(member_services.services_for(alpha.gym)) == []


def test_service_names_are_unique_and_prices_positive(alpha, sauna):
    with pytest.raises(InvalidDetails) as error:
        member_services.create_service(by=alpha.assignment, name="sauna", price_rwf=1000)
    assert error.value.field == "name"
    with pytest.raises(InvalidDetails) as error:
        member_services.create_service(by=alpha.assignment, name="Massage", price_rwf=0)
    assert error.value.field == "price_rwf"


def test_only_the_owner_manages_services(alpha, add_staff):
    desk = add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST)
    with pytest.raises(PermissionDenied):
        member_services.create_service(by=desk.assignment, name="Sauna", price_rwf=5000)


def test_selling_sessions_creates_an_invoice_and_a_payment(alpha, sauna, member):
    sale = billing.sell_sessions(
        by=alpha.assignment,
        member=member,
        service=sauna,
        quantity=2,
        idempotency_key=uuid.uuid4(),
        payment=PaymentInput(method="cash"),
    )
    assert sale.invoice.total_rwf == 10000
    line = InvoiceLine.objects.get(invoice=sale.invoice)
    assert line.service == sauna and line.description.startswith("Sauna x 2")
    assert sale.payment.amount_rwf == 10000
    assert billing.member_balance(member) == 0
    assert LedgerEntry.objects.filter(member=member).count() == 2


def test_selling_sessions_twice_with_the_same_form_records_once(alpha, sauna, member):
    key = uuid.uuid4()
    for _ in range(2):
        billing.sell_sessions(
            by=alpha.assignment, member=member, service=sauna, quantity=1, idempotency_key=key
        )
    assert billing.member_balance(member) == 5000
    assert not Payment.objects.filter(member=member).exists()


def test_archived_services_and_silly_quantities_are_refused(alpha, sauna, member):
    with pytest.raises(InvalidDetails) as error:
        billing.sell_sessions(
            by=alpha.assignment,
            member=member,
            service=sauna,
            quantity=0,
            idempotency_key=uuid.uuid4(),
        )
    assert error.value.field == "quantity"
    member_services.set_service_active(by=alpha.assignment, service=sauna, active=False)
    with pytest.raises(InvalidDetails) as error:
        billing.sell_sessions(
            by=alpha.assignment,
            member=member,
            service=sauna,
            quantity=1,
            idempotency_key=uuid.uuid4(),
        )
    assert error.value.field == "service"


def test_another_gym_cannot_see_or_sell_a_service(alpha, sauna, make_gym):
    bravo = make_gym("Bravo Fitness", "0788000002")
    module_services.enable_module(gym=bravo.gym, code=PAYMENTS_FINANCE)
    assert not Service.objects.for_gym(bravo.gym).exists()
    other = member_services.register_member(
        by=bravo.assignment, phone="0788333444", details=MemberDetails(full_name="Grace")
    )
    with pytest.raises(PermissionDenied):
        billing.sell_sessions(
            by=bravo.assignment,
            member=other,
            service=sauna,
            quantity=1,
            idempotency_key=uuid.uuid4(),
        )


def test_screens_add_a_service_and_sell_a_session(alpha, member, sign_in):
    client = sign_in(alpha)
    response = client.post(
        reverse("members:service_create"), {"name": "Massage", "price_rwf": "15,000"}
    )
    assert response.status_code == 302
    page = client.get(reverse("members:package_list")).content.decode()
    assert "Massage" in page and "15,000" in page

    massage = Service.objects.get(name="Massage")
    form = client.get(reverse("billing:sell_sessions", args=[member.public_id]))
    assert form.status_code == 200
    response = client.post(
        reverse("billing:sell_sessions", args=[member.public_id]),
        {
            "service": str(massage.public_id),
            "quantity": "1",
            "pay_now": "full",
            "method": "cash",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 302
    assert "receipt" in response["Location"]
