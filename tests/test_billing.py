"""Tests for selling memberships, payments, reversals and the ledger."""

import uuid

import pytest
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, ProgrammingError, connection, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.models import AuditEntry
from apps.billing import services
from apps.billing.models import (
    Allocation,
    Invoice,
    InvoiceLine,
    LedgerEntry,
    LedgerKind,
    Payment,
    Reversal,
)
from apps.billing.services import PaymentInput
from apps.core import tenant_db
from apps.gyms.models import Role
from apps.members import services as member_services
from apps.members.models import Membership
from apps.members.services import InvalidDetails, MemberDetails
from apps.modules import services as module_services
from apps.modules.catalog import PAYMENTS_FINANCE

pytestmark = pytest.mark.django_db

CASH = PaymentInput(method="cash")


@pytest.fixture
def alpha(make_gym):
    staff = make_gym("Alpha Fitness", "0788000001", "Aline Uwase")
    module_services.enable_module(gym=staff.gym, code=PAYMENTS_FINANCE)
    return staff


@pytest.fixture
def bravo(make_gym):
    staff = make_gym("Bravo Fitness", "0788000002", "Bosco Kamanzi")
    module_services.enable_module(gym=staff.gym, code=PAYMENTS_FINANCE)
    return staff


def make_member(staff, name="Eric Mugisha", phone="0788111222"):
    return member_services.register_member(
        by=staff.assignment, phone=phone, details=MemberDetails(full_name=name)
    )


def make_package(staff, price=30000):
    return member_services.create_package(
        by=staff.assignment,
        name="Monthly",
        price_rwf=price,
        duration_count=1,
        duration_unit="month",
    )


@pytest.fixture
def member(alpha):
    return make_member(alpha)


@pytest.fixture
def monthly(alpha):
    return make_package(alpha)


def sell(staff, member, package, payment=CASH, key=None, start=None):
    return services.sell_membership(
        by=staff.assignment,
        member=member,
        package=package,
        start_date=start or timezone.localdate(),
        idempotency_key=key or uuid.uuid4(),
        payment=payment,
    )


def pay(staff, invoice, amount, method="cash", reference="", key=None):
    return services.record_payment(
        by=staff.assignment,
        invoice=invoice,
        payment=PaymentInput(method=method, amount_rwf=amount, reference=reference),
        idempotency_key=key or uuid.uuid4(),
    )


def ledger_matches_invoices_and_payments(gym) -> bool:
    """The check an accountant would do: the ledger must equal invoices minus live payments."""
    invoiced = Invoice.objects.for_gym(gym).aggregate(s=Sum("total_rwf"))["s"] or 0
    live_payments = Payment.objects.for_gym(gym).filter(reversal__isnull=True)
    received = live_payments.aggregate(s=Sum("amount_rwf"))["s"] or 0
    allocated = (
        Allocation.objects.for_gym(gym)
        .filter(payment__reversal__isnull=True)
        .aggregate(s=Sum("amount_rwf"))["s"]
        or 0
    )
    return services.total_outstanding(gym) == invoiced - received and received == allocated


# --- Selling ----------------------------------------------------------------


def test_a_sale_paid_in_full(alpha, member, monthly):
    sale = sell(alpha, member, monthly)

    assert sale.membership.member == member
    assert sale.invoice.total_rwf == 30000
    assert sale.invoice.display_number == "INV-000001"
    assert sale.payment.amount_rwf == 30000
    assert sale.payment.display_number == "RCT-000001"
    assert sale.payment.received_by == alpha.person
    assert services.outstanding_on(sale.invoice) == 0
    assert services.member_balance(member) == 0
    assert InvoiceLine.objects.get().membership == sale.membership
    assert ledger_matches_invoices_and_payments(alpha.gym)


def test_a_sale_with_a_part_payment_leaves_a_balance(alpha, member, monthly):
    sale = sell(alpha, member, monthly, payment=PaymentInput(method="cash", amount_rwf=10000))

    assert sale.payment.amount_rwf == 10000
    assert services.outstanding_on(sale.invoice) == 20000
    assert services.member_balance(member) == 20000
    assert sale.membership.status == "active"  # valid even though not fully paid
    assert ledger_matches_invoices_and_payments(alpha.gym)


def test_a_sale_to_pay_later_then_paid_in_two_parts(alpha, member, monthly):
    sale = sell(alpha, member, monthly, payment=None)
    assert sale.payment is None
    assert services.member_balance(member) == 30000

    pay(alpha, sale.invoice, 12000)
    second = pay(alpha, sale.invoice, 18000, method="mtn_momo", reference="TX123456")

    assert second.display_number == "RCT-000002"
    assert services.outstanding_on(sale.invoice) == 0
    assert services.member_balance(member) == 0
    kinds = list(LedgerEntry.objects.for_gym(alpha.gym).values_list("kind", "amount_rwf"))
    assert kinds == [
        (LedgerKind.INVOICE_ISSUED, 30000),
        (LedgerKind.PAYMENT_RECEIVED, -12000),
        (LedgerKind.PAYMENT_RECEIVED, -18000),
    ]
    assert ledger_matches_invoices_and_payments(alpha.gym)


def test_the_invoice_uses_the_price_on_the_day_even_if_the_package_changes_later(
    alpha, member, monthly
):
    sale = sell(alpha, member, monthly, payment=None)
    member_services.update_package(
        by=alpha.assignment,
        package=monthly,
        name="Monthly",
        price_rwf=50000,
        duration_count=1,
        duration_unit="month",
    )

    assert Invoice.objects.for_gym(alpha.gym).get().total_rwf == 30000
    assert services.outstanding_on(sale.invoice) == 30000


def test_everything_is_recorded_in_the_audit_log(alpha, member, monthly):
    sell(alpha, member, monthly)

    actions = set(AuditEntry.objects.for_gym(alpha.gym).values_list("action", flat=True))
    assert {"membership.created", "invoice.issued", "payment.recorded"} <= actions
    entry = AuditEntry.objects.for_gym(alpha.gym).get(action="payment.recorded")
    assert entry.actor == alpha.person and entry.details["amount_rwf"] == 30000


# --- Wrong values -----------------------------------------------------------


@pytest.mark.parametrize(
    ("payment", "field", "reason"),
    [
        (PaymentInput(method="cash", amount_rwf=0), "amount_rwf", "out_of_range"),
        (PaymentInput(method="cash", amount_rwf=-500), "amount_rwf", "out_of_range"),
        (PaymentInput(method="cash", amount_rwf=30001), "amount_rwf", "more_than_owed"),
        (PaymentInput(method="cash", amount_rwf=100.5), "amount_rwf", "out_of_range"),
        (PaymentInput(method="bitcoin"), "method", "invalid"),
    ],
)
def test_a_wrong_payment_cancels_the_whole_sale(alpha, member, monthly, payment, field, reason):
    with pytest.raises(InvalidDetails) as problem:
        sell(alpha, member, monthly, payment=payment)

    assert (problem.value.field, problem.value.reason) == (field, reason)
    # Nothing at all was saved: no membership, invoice, ledger line or audit entry.
    assert not Membership.objects.exists()
    assert not Invoice.objects.exists()
    assert not LedgerEntry.objects.exists()
    assert not AuditEntry.objects.filter(action__in=["invoice.issued", "membership.created"])


def test_paying_more_than_is_owed_is_refused(alpha, member, monthly):
    sale = sell(alpha, member, monthly, payment=PaymentInput(method="cash", amount_rwf=25000))

    with pytest.raises(InvalidDetails) as problem:
        pay(alpha, sale.invoice, 5001)

    assert problem.value.reason == "more_than_owed"
    assert Payment.objects.count() == 1


def test_a_fully_paid_invoice_takes_no_more_payments(alpha, member, monthly):
    sale = sell(alpha, member, monthly)

    with pytest.raises(InvalidDetails):
        pay(alpha, sale.invoice, 1)


# --- The same form arriving twice ------------------------------------------


def test_submitting_the_same_sale_twice_records_it_once(alpha, member, monthly):
    key = uuid.uuid4()

    first = sell(alpha, member, monthly, key=key)
    second = sell(alpha, member, monthly, key=key)

    assert second.invoice == first.invoice
    assert second.membership == first.membership
    assert second.payment == first.payment
    assert Invoice.objects.count() == 1
    assert Payment.objects.count() == 1
    assert Membership.objects.count() == 1
    assert LedgerEntry.objects.count() == 2


def test_submitting_the_same_payment_twice_records_it_once(alpha, member, monthly):
    sale = sell(alpha, member, monthly, payment=None)
    key = uuid.uuid4()

    first = pay(alpha, sale.invoice, 10000, key=key)
    second = pay(alpha, sale.invoice, 10000, key=key)

    assert second == first
    assert services.outstanding_on(sale.invoice) == 20000


def test_the_database_itself_refuses_a_duplicate_key(alpha, member, monthly):
    sale = sell(alpha, member, monthly, payment=None)
    payment = pay(alpha, sale.invoice, 1000)

    with pytest.raises(DatabaseError), transaction.atomic():
        Payment.objects.create(
            gym=alpha.gym,
            number=999,
            member=member,
            amount_rwf=1000,
            method="cash",
            idempotency_key=payment.idempotency_key,
        )


# --- Numbering --------------------------------------------------------------


def test_each_gym_counts_its_own_invoice_and_receipt_numbers(alpha, bravo, member, monthly):
    other = make_member(alpha, "Claudine Mukamana", "0788333444")
    theirs = make_member(bravo, "Bravo Member", "0788555666")

    numbers = [
        sell(alpha, member, monthly).invoice.number,
        sell(alpha, other, monthly).invoice.number,
        sell(bravo, theirs, make_package(bravo)).invoice.number,
    ]

    assert numbers == [1, 2, 1]
    assert list(
        Payment.objects.for_gym(alpha.gym).order_by("number").values_list("number", flat=True)
    ) == [1, 2]


# --- Reversals --------------------------------------------------------------


def test_reversing_a_payment_puts_the_amount_back_and_keeps_the_payment(alpha, member, monthly):
    sale = sell(alpha, member, monthly)

    services.reverse_payment(by=alpha.assignment, payment=sale.payment, reason="Entered twice")

    payment = Payment.objects.get()
    assert payment.is_reversed and payment.amount_rwf == 30000  # still there, unchanged
    assert payment.reversal.reason == "Entered twice"
    assert services.outstanding_on(sale.invoice) == 30000
    assert services.member_balance(member) == 30000
    assert services.collected_on(alpha.gym, timezone.localdate()) == 0
    assert LedgerEntry.objects.last().kind == LedgerKind.PAYMENT_REVERSED
    assert AuditEntry.objects.for_gym(alpha.gym).filter(action="payment.reversed").count() == 1
    assert ledger_matches_invoices_and_payments(alpha.gym)


def test_after_a_reversal_the_invoice_can_be_paid_again(alpha, member, monthly):
    sale = sell(alpha, member, monthly)
    services.reverse_payment(by=alpha.assignment, payment=sale.payment, reason="Wrong method")

    pay(alpha, sale.invoice, 30000, method="mtn_momo", reference="TX999")

    assert services.member_balance(member) == 0
    assert ledger_matches_invoices_and_payments(alpha.gym)


def test_a_payment_cannot_be_reversed_twice_or_without_a_reason(alpha, member, monthly):
    sale = sell(alpha, member, monthly)

    with pytest.raises(InvalidDetails):
        services.reverse_payment(by=alpha.assignment, payment=sale.payment, reason="   ")
    services.reverse_payment(by=alpha.assignment, payment=sale.payment, reason="Mistake")
    with pytest.raises(services.AlreadyReversed):
        services.reverse_payment(by=alpha.assignment, payment=sale.payment, reason="Again")

    assert Reversal.objects.count() == 1


@pytest.mark.parametrize("role", [Role.BRANCH_MANAGER, Role.RECEPTIONIST, Role.SALES_REP])
def test_only_the_owner_can_reverse_but_front_desk_can_sell_and_take_payments(
    alpha, member, monthly, add_staff, role
):
    staff = add_staff(alpha.gym, "0788000005", role)

    sale = sell(staff, member, monthly, payment=PaymentInput(method="cash", amount_rwf=5000))
    pay(staff, sale.invoice, 5000)

    with pytest.raises(PermissionDenied):
        services.reverse_payment(by=staff.assignment, payment=sale.payment, reason="No")


def test_a_trainer_cannot_sell_or_take_payments(alpha, member, monthly, add_staff):
    trainer = add_staff(alpha.gym, "0788000005", Role.TRAINER)
    sale = sell(alpha, member, monthly, payment=None)

    with pytest.raises(PermissionDenied):
        sell(trainer, member, monthly)
    with pytest.raises(PermissionDenied):
        pay(trainer, sale.invoice, 1000)


# --- Module switch and other gyms -------------------------------------------


def test_nothing_works_without_the_payments_application(make_gym):
    staff = make_gym("No Payments Gym", "0788000009")
    member, package = make_member(staff), make_package(staff)

    with pytest.raises(PermissionDenied, match="not switched on"):
        sell(staff, member, package)

    assert not Invoice.objects.exists()


def test_staff_cannot_touch_another_gyms_money(alpha, bravo, member, monthly):
    sale = sell(alpha, member, monthly, payment=PaymentInput(method="cash", amount_rwf=1000))

    with pytest.raises(PermissionDenied):
        pay(bravo, sale.invoice, 1000)
    with pytest.raises(PermissionDenied):
        services.reverse_payment(by=bravo.assignment, payment=sale.payment, reason="x")
    with pytest.raises(PermissionDenied):
        sell(bravo, member, make_package(bravo))


# --- Figures ----------------------------------------------------------------


def test_collected_today_and_unpaid_invoices(alpha, member, monthly):
    other = make_member(alpha, "Claudine Mukamana", "0788333444")
    sell(alpha, member, monthly)
    part = sell(alpha, other, monthly, payment=PaymentInput(method="cash", amount_rwf=10000))

    assert services.collected_on(alpha.gym, timezone.localdate()) == 40000
    assert services.total_outstanding(alpha.gym) == 20000
    unpaid = services.unpaid_invoices(alpha.gym)
    assert [(i.display_number, i.owed_rwf) for i in unpaid] == [
        (part.invoice.display_number, 20000)
    ]


# --- Permanent records ------------------------------------------------------

PERMANENT = [Invoice, InvoiceLine, Payment, Allocation, LedgerEntry, Reversal]


@pytest.fixture
def reversed_sale(alpha, member, monthly):
    sale = sell(alpha, member, monthly)
    services.reverse_payment(by=alpha.assignment, payment=sale.payment, reason="Test")
    return sale


@pytest.mark.parametrize("model", PERMANENT, ids=lambda m: m.__name__)
def test_money_records_cannot_be_changed_or_deleted(reversed_sale, model):
    assert model.objects.exists()

    with pytest.raises(DatabaseError, match="permanent"), transaction.atomic():
        model.objects.all().update(gym_id=model.objects.first().gym_id)
    # Straight to the database, past Django's own safety checks.
    with pytest.raises(DatabaseError, match="permanent"), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {model._meta.db_table}")
    with pytest.raises(DatabaseError, match="permanent"), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(f"TRUNCATE {model._meta.db_table} CASCADE")


def test_a_payment_amount_cannot_be_edited_even_by_the_administrator(reversed_sale):
    payment = Payment.objects.get()
    payment.amount_rwf = 1

    with pytest.raises(DatabaseError, match="permanent"), transaction.atomic():
        payment.save()

    payment.refresh_from_db()
    assert payment.amount_rwf == 30000


def test_the_web_role_sees_only_its_gyms_money_and_cannot_edit_it(alpha, bravo, member, monthly):
    sell(alpha, member, monthly)
    sell(bravo, make_member(bravo, "Bravo Member", "0788555666"), make_package(bravo, price=77000))

    tenant_db.enter_app_role(person_id=None)
    try:
        assert Invoice.objects.count() == 0 and Payment.objects.count() == 0  # no gym chosen
        tenant_db.set_current_gym(alpha.gym.id)
        totals = list(Invoice.objects.values_list("total_rwf", flat=True))  # unfiltered
        ledger = LedgerEntry.objects.aggregate(s=Sum("amount_rwf"))["s"]
        with pytest.raises(ProgrammingError, match="permission denied"), transaction.atomic():
            Payment.objects.all().update(amount_rwf=1)
    finally:
        tenant_db.leave_app_role()

    assert totals == [30000]
    assert ledger == 0
