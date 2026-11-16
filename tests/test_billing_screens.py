"""Tests for the selling, payment and receipt screens."""

import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.billing import services
from apps.billing.models import Invoice, Payment
from apps.billing.services import PaymentInput
from apps.gyms.models import Role
from apps.members import services as member_services
from apps.members.models import Membership
from apps.members.services import MemberDetails
from apps.modules import services as module_services
from apps.modules.catalog import PAYMENTS_FINANCE

pytestmark = pytest.mark.django_db


@pytest.fixture
def alpha(make_gym):
    staff = make_gym("Alpha Fitness", "0788000001", "Aline Uwase")
    module_services.enable_module(gym=staff.gym, code=PAYMENTS_FINANCE)
    return staff


@pytest.fixture
def member(alpha):
    return member_services.register_member(
        by=alpha.assignment, phone="0788111222", details=MemberDetails(full_name="Eric Mugisha")
    )


@pytest.fixture
def monthly(alpha):
    return member_services.create_package(
        by=alpha.assignment,
        name="Monthly",
        price_rwf=30000,
        duration_count=1,
        duration_unit="month",
    )


def sale_form(package, **changes):
    data = {
        "package": str(package.public_id),
        "start_date": timezone.localdate().isoformat(),
        "pay_now": "full",
        "amount_rwf": "",
        "method": "cash",
        "reference": "",
        "idempotency_key": str(uuid.uuid4()),
    }
    return {**data, **changes}


def sell_unpaid(staff, member, package):
    return services.sell_membership(
        by=staff.assignment,
        member=member,
        package=package,
        start_date=timezone.localdate(),
        idempotency_key=uuid.uuid4(),
        payment=None,
    )


# --- Selling ----------------------------------------------------------------


def test_the_sell_form_offers_packages_and_todays_date(alpha, member, monthly, sign_in):
    page = sign_in(alpha).get(reverse("billing:sell", args=[member.public_id])).content.decode()

    assert "Monthly · RWF 30,000 · 1 month" in page
    assert f'value="{timezone.localdate().isoformat()}"' in page
    assert 'name="idempotency_key"' in page


def test_selling_paid_in_full_goes_to_the_receipt(alpha, member, monthly, sign_in):
    client = sign_in(alpha)

    response = client.post(
        reverse("billing:sell", args=[member.public_id]), sale_form(monthly), follow=True
    )

    page = response.content.decode()
    assert "Monthly sold to Eric Mugisha." in page
    assert "RCT-000001" in page and "RWF 30,000" in page
    assert "0788 111 222" in page
    assert "Received by" in page and "Aline Uwase" in page
    assert Payment.objects.get().amount_rwf == 30000


def test_selling_with_a_part_payment_by_mobile_money(alpha, member, monthly, sign_in):
    client = sign_in(alpha)
    url = reverse("billing:sell", args=[member.public_id])

    no_amount = client.post(url, sale_form(monthly, pay_now="part"))
    assert "Enter how much was paid." in no_amount.content.decode()

    no_reference = client.post(
        url, sale_form(monthly, pay_now="part", amount_rwf="10,000", method="mtn_momo")
    )
    assert "Enter the transaction reference" in no_reference.content.decode()
    assert not Invoice.objects.exists()  # nothing was half-saved

    response = client.post(
        url,
        sale_form(
            monthly, pay_now="part", amount_rwf="10,000", method="mtn_momo", reference="TX778899"
        ),
        follow=True,
    )
    page = response.content.decode()
    assert "MTN Mobile Money" in page and "TX778899" in page
    assert "RWF 20,000" in page  # still to pay


def test_selling_to_pay_later_returns_to_the_member_with_a_balance(alpha, member, monthly, sign_in):
    client = sign_in(alpha)

    response = client.post(
        reverse("billing:sell", args=[member.public_id]), sale_form(monthly, pay_now="later")
    )
    assert response.url == reverse("members:member_detail", args=[member.public_id])
    assert not Payment.objects.exists()

    member_page = client.get(response.url).content.decode()
    assert "Active" in member_page and "valid until" in member_page

    # The payments part of the member's page is fetched on its own by HTMX.
    account = client.get(
        reverse("billing:member_account", args=[member.public_id]), headers={"HX-Request": "true"}
    ).content.decode()
    assert "<html" not in account
    assert "Owes" in account and "RWF 30,000" in account
    assert "Record payment" in account


def test_pressing_the_button_twice_sells_once(alpha, member, monthly, sign_in):
    client = sign_in(alpha)
    form = sale_form(monthly)
    url = reverse("billing:sell", args=[member.public_id])

    first = client.post(url, form)
    second = client.post(url, form)

    assert first.url == second.url  # both land on the same receipt
    assert Membership.objects.count() == 1
    assert Invoice.objects.count() == 1
    assert Payment.objects.count() == 1


def test_an_overlapping_start_date_is_explained(alpha, member, monthly, sign_in):
    sell_unpaid(alpha, member, monthly)
    client = sign_in(alpha)

    response = client.post(reverse("billing:sell", args=[member.public_id]), sale_form(monthly))

    assert "already has a membership covering these dates" in response.content.decode()
    assert Membership.objects.count() == 1


def test_the_form_suggests_the_day_after_the_current_membership_ends(
    alpha, member, monthly, sign_in
):
    sale = sell_unpaid(alpha, member, monthly)
    expected = (sale.membership.end_date + timezone.timedelta(days=1)).isoformat()

    page = sign_in(alpha).get(reverse("billing:sell", args=[member.public_id])).content.decode()

    assert f'value="{expected}"' in page


def test_with_no_packages_the_form_points_to_the_packages_page(alpha, member, sign_in):
    page = sign_in(alpha).get(reverse("billing:sell", args=[member.public_id])).content.decode()

    assert "There are no packages to sell yet" in page


# --- Paying a balance -------------------------------------------------------


def test_recording_a_payment_towards_a_balance(alpha, member, monthly, sign_in):
    sale = sell_unpaid(alpha, member, monthly)
    client = sign_in(alpha)
    url = reverse("billing:payment_record", args=[sale.invoice.public_id])

    form_page = client.get(url).content.decode()
    assert 'value="30000"' in form_page  # the amount owed is filled in

    too_much = client.post(
        url, {"amount_rwf": "40000", "method": "cash", "idempotency_key": str(uuid.uuid4())}
    )
    assert "This is more than what is owed." in too_much.content.decode()

    response = client.post(
        url,
        {"amount_rwf": "12 000", "method": "cash", "idempotency_key": str(uuid.uuid4())},
        follow=True,
    )
    page = response.content.decode()
    assert "Payment recorded." in page
    assert "RWF 12,000" in page and "RWF 18,000" in page  # paid, and still to pay


def test_a_fully_paid_invoice_shows_no_payment_form(alpha, member, monthly, sign_in):
    client = sign_in(alpha)
    client.post(reverse("billing:sell", args=[member.public_id]), sale_form(monthly))
    invoice = Invoice.objects.get()

    page = client.get(reverse("billing:payment_record", args=[invoice.public_id])).content.decode()

    assert "This invoice is fully paid." in page and "<form" not in page.split("<main")[1]


# --- Reversing --------------------------------------------------------------


def test_the_owner_reverses_a_payment_with_a_reason(alpha, member, monthly, sign_in):
    client = sign_in(alpha)
    client.post(reverse("billing:sell", args=[member.public_id]), sale_form(monthly))
    payment = Payment.objects.get()
    url = reverse("billing:payment_reverse", args=[payment.public_id])

    assert (
        "Reverse this payment"
        in client.get(reverse("billing:receipt", args=[payment.public_id])).content.decode()
    )
    assert "Give a reason" not in client.get(url).content.decode()
    assert client.post(url, {"reason": ""}).status_code == 200  # a reason is required

    response = client.post(url, {"reason": "Entered for the wrong member"}, follow=True)

    page = response.content.decode()
    assert "REVERSED" in page and "Entered for the wrong member" in page
    assert "Reverse this payment" not in page
    assert services.member_balance(member) == 30000


def test_a_receptionist_can_sell_and_print_but_not_reverse(
    alpha, member, monthly, add_staff, sign_in
):
    client = sign_in(add_staff(alpha.gym, "0788000005", Role.RECEPTIONIST))
    client.post(reverse("billing:sell", args=[member.public_id]), sale_form(monthly))
    payment = Payment.objects.get()

    receipt = client.get(reverse("billing:receipt", args=[payment.public_id])).content.decode()
    assert "RCT-000001" in receipt and "Reverse this payment" not in receipt

    reverse_url = reverse("billing:payment_reverse", args=[payment.public_id])
    assert client.get(reverse_url).status_code == 403
    assert client.post(reverse_url, {"reason": "x"}).status_code == 403
    assert not Payment.objects.get().is_reversed


# --- The Payments page ------------------------------------------------------


def test_the_payments_page_shows_todays_money_and_who_owes(alpha, member, monthly, sign_in):
    other = member_services.register_member(
        by=alpha.assignment, phone="0788333444", details=MemberDetails(full_name="Claudine M")
    )
    services.sell_membership(
        by=alpha.assignment,
        member=member,
        package=monthly,
        start_date=timezone.localdate(),
        idempotency_key=uuid.uuid4(),
        payment=PaymentInput(method="cash"),
    )
    sell_unpaid(alpha, other, monthly)

    page = sign_in(alpha).get(reverse("billing:payments_home")).content.decode()

    assert "Collected today" in page and "Owed by members" in page
    assert page.count("RWF 30,000") >= 3  # collected, owed, and in the lists
    assert "Claudine M" in page and "Unpaid invoices" in page
    assert reverse("billing:payments_home") in page  # the menu has a Payments item


# --- Without the application, and other gyms --------------------------------


def test_without_the_payments_application_the_screens_are_closed(make_gym, sign_in):
    staff = make_gym("No Payments Gym", "0788000009")
    member = member_services.register_member(
        by=staff.assignment, phone="0788111222", details=MemberDetails(full_name="Eric")
    )
    client = sign_in(staff)

    assert client.get(reverse("billing:sell", args=[member.public_id])).status_code == 403
    assert client.get(reverse("billing:payments_home")).status_code == 403
    assert client.get(reverse("billing:member_account", args=[member.public_id])).status_code == 403

    member_page = client.get(reverse("members:member_detail", args=[member.public_id]))
    text = member_page.content.decode()
    assert "Selling memberships needs the Payments and Finance application." in text
    assert reverse("billing:payments_home") not in text  # no Payments item in the menu


def test_another_gyms_money_screens_are_not_found(alpha, member, monthly, make_gym, sign_in):
    sign_in(alpha).post(reverse("billing:sell", args=[member.public_id]), sale_form(monthly))
    invoice, payment = Invoice.objects.get(), Payment.objects.get()

    bravo = make_gym("Bravo Fitness", "0788000002")
    module_services.enable_module(gym=bravo.gym, code=PAYMENTS_FINANCE)
    client = sign_in(bravo)

    for name, record in [
        ("billing:sell", member),
        ("billing:member_account", member),
        ("billing:payment_record", invoice),
        ("billing:receipt", payment),
        ("billing:payment_reverse", payment),
    ]:
        assert client.get(reverse(name, args=[record.public_id])).status_code == 404, name

    blocked = client.post(
        reverse("billing:payment_reverse", args=[payment.public_id]), {"reason": "theft"}
    )
    assert blocked.status_code == 404
    page = client.get(reverse("billing:payments_home")).content.decode()
    assert "Eric Mugisha" not in page and "RCT-000001" not in page
    assert not Payment.objects.get().is_reversed


def test_a_waiting_notice_is_not_shown_to_the_next_person_on_the_same_computer(
    alpha, member, monthly, add_staff, sign_in
):
    """Found by the test above: reception computers are shared between staff."""
    client = sign_in(alpha)
    # The sale succeeds, but the browser never loads the next page, so the
    # notice "Monthly sold to Eric Mugisha." is still waiting.
    client.post(reverse("billing:sell", args=[member.public_id]), sale_form(monthly))

    client.post(reverse("accounts:logout"))
    trainer = add_staff(alpha.gym, "0788000007", Role.TRAINER)
    page = sign_in(trainer).get(reverse("gyms:home")).content.decode()

    assert "sold to Eric Mugisha" not in page
