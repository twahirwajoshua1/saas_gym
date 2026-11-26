"""
Packages counted in visits: "15 swimming tickets".

A package is what is included, a rule, and a price. The rule is either a length
of time (a membership) or a number of visits (a ticketbook). These tests cover
the ticketbook side: buying one, spending visits, running out, expiring, and
putting a visit back when it was taken by mistake.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.gyms.models import Role
from apps.members import services
from apps.members.durations import end_date_for
from apps.members.models import PackageKind

pytestmark = pytest.mark.django_db


@pytest.fixture
def alpha(make_gym):
    return make_gym("Alpha Fitness", "0788000001", "Aline Uwase")


@pytest.fixture
def bravo(make_gym):
    return make_gym("Bravo Fitness", "0788000002", "Bosco Kamanzi")


@pytest.fixture
def swimming(alpha):
    return services.create_service(by=alpha.assignment, name="Swimming", price_rwf=5000)


@pytest.fixture
def gym_floor(alpha):
    return services.create_service(by=alpha.assignment, name="Gym", price_rwf=4000)


@pytest.fixture
def member(alpha):
    return services.register_member(
        by=alpha.assignment,
        phone="0788111222",
        details=services.MemberDetails(full_name="Grace Kamanzi"),
    )


def tickets(alpha, swimming, *, visits=15, months=3, name="15 swimming tickets"):
    return services.create_package(
        by=alpha.assignment,
        name=name,
        price_rwf=60000,
        kind=PackageKind.COUNT,
        services=[swimming],
        visit_count=visits,
        duration_count=months,
        duration_unit="month" if months else "",
    )


# --- The package itself -----------------------------------------------------


def test_a_package_can_count_visits_instead_of_time(alpha, swimming):
    package = tickets(alpha, swimming)

    assert package.is_count
    assert package.visit_count == 15
    assert package.rule_label == "15 visits, expires in 3 months"
    assert [service.name for service in package.included_services.all()] == ["Swimming"]


def test_visits_can_be_set_never_to_run_out(alpha, swimming):
    package = tickets(alpha, swimming, months=None)

    assert package.expires is False
    assert package.rule_label == "15 visits, never expires"


def test_a_package_for_a_length_of_time_says_so(alpha, swimming):
    package = services.create_package(
        by=alpha.assignment,
        name="Swimming only, monthly",
        price_rwf=35000,
        services=[swimming],
        duration_count=1,
        duration_unit="month",
    )

    assert package.is_count is False
    assert package.rule_label == "1 month, as often as you like"


def test_a_package_must_include_at_least_one_service(alpha, swimming):
    with pytest.raises(services.InvalidDetails) as raised:
        services.create_package(
            by=alpha.assignment,
            name="Nothing at all",
            price_rwf=1000,
            services=[],
            duration_count=1,
            duration_unit="month",
        )

    assert (raised.value.field, raised.value.reason) == ("services", "required")


def test_a_package_cannot_include_another_gyms_service(alpha, bravo, swimming):
    theirs = services.create_service(by=bravo.assignment, name="Swimming", price_rwf=5000)

    with pytest.raises(services.InvalidDetails) as raised:
        services.create_package(
            by=alpha.assignment,
            name="Theirs",
            price_rwf=1000,
            services=[theirs],
            duration_count=1,
            duration_unit="month",
        )

    assert raised.value.field == "services"


def test_counting_visits_needs_a_number_of_visits(alpha, swimming):
    with pytest.raises(services.InvalidDetails) as raised:
        services.create_package(
            by=alpha.assignment,
            name="Tickets",
            price_rwf=60000,
            kind=PackageKind.COUNT,
            services=[swimming],
            visit_count=None,
        )

    assert raised.value.field == "visit_count"


# --- Buying and spending ----------------------------------------------------


def test_buying_a_book_copies_the_rule_and_sets_the_expiry_date(alpha, swimming, member):
    package = tickets(alpha, swimming)

    book = services.create_ticketbook(by=alpha.assignment, member=member, package=package)

    assert book.package_name == "15 swimming tickets"
    assert book.price_rwf == 60000
    assert book.visits_allowed == 15
    assert book.visits_left == 15
    assert book.expires_on == end_date_for(book.bought_on, 3, "month")
    assert [service.name for service in book.included_services.all()] == ["Swimming"]


def test_later_changes_to_the_package_do_not_touch_a_book_already_sold(alpha, swimming, member):
    package = tickets(alpha, swimming)
    book = services.create_ticketbook(by=alpha.assignment, member=member, package=package)

    services.update_package(
        by=alpha.assignment,
        package=package,
        name="15 swimming tickets",
        price_rwf=99000,
        services=[swimming],
        kind=PackageKind.COUNT,
        visit_count=5,
        duration_count=3,
        duration_unit="month",
    )

    book.refresh_from_db()
    assert (book.price_rwf, book.visits_allowed) == (60000, 15)


def test_spending_visits_counts_down_and_stops_at_zero(alpha, swimming, member):
    package = tickets(alpha, swimming, visits=2)
    book = services.create_ticketbook(by=alpha.assignment, member=member, package=package)

    services.spend_ticket(by=alpha.assignment, ticketbook=book, service=swimming)
    services.spend_ticket(by=alpha.assignment, ticketbook=book, service=swimming)

    assert book.visits_left == 0
    with pytest.raises(services.InvalidDetails) as raised:
        services.spend_ticket(by=alpha.assignment, ticketbook=book, service=swimming)
    assert (raised.value.field, raised.value.reason) == ("ticketbook", "used_up")


def test_a_visit_cannot_be_spent_on_a_service_the_book_does_not_include(
    alpha, swimming, gym_floor, member
):
    package = tickets(alpha, swimming)
    book = services.create_ticketbook(by=alpha.assignment, member=member, package=package)

    with pytest.raises(services.InvalidDetails) as raised:
        services.spend_ticket(by=alpha.assignment, ticketbook=book, service=gym_floor)

    assert (raised.value.field, raised.value.reason) == ("service", "not_included")


def test_visits_cannot_be_spent_after_the_book_has_run_out(alpha, swimming, member):
    package = tickets(alpha, swimming)
    book = services.create_ticketbook(by=alpha.assignment, member=member, package=package)
    gone = book.expires_on + timedelta(days=1)

    with pytest.raises(services.InvalidDetails):
        services.spend_ticket(by=alpha.assignment, ticketbook=book, service=swimming, used_on=gone)


def test_a_visit_taken_by_mistake_is_put_back_and_stays_on_record(alpha, swimming, member):
    package = tickets(alpha, swimming, visits=3)
    book = services.create_ticketbook(by=alpha.assignment, member=member, package=package)
    use = services.spend_ticket(by=alpha.assignment, ticketbook=book, service=swimming)
    assert book.visits_left == 2

    services.cancel_ticket_use(by=alpha.assignment, use=use, reason="Rang the wrong member")

    assert book.visits_left == 3
    use.refresh_from_db()
    assert use.cancelled_at is not None
    assert use.cancel_reason == "Rang the wrong member"
    assert book.uses.count() == 1  # nothing was deleted


def test_putting_a_visit_back_needs_a_reason(alpha, swimming, member):
    package = tickets(alpha, swimming)
    book = services.create_ticketbook(by=alpha.assignment, member=member, package=package)
    use = services.spend_ticket(by=alpha.assignment, ticketbook=book, service=swimming)

    with pytest.raises(services.InvalidDetails) as raised:
        services.cancel_ticket_use(by=alpha.assignment, use=use, reason="  ")

    assert raised.value.field == "reason"


# --- The two kinds do not mix -----------------------------------------------


def test_a_package_that_counts_visits_is_not_sold_as_a_membership(alpha, swimming, member):
    package = tickets(alpha, swimming)

    with pytest.raises(services.InvalidDetails) as raised:
        services.create_membership(
            by=alpha.assignment,
            member=member,
            package=package,
            start_date=timezone.localdate(),
        )

    assert (raised.value.field, raised.value.reason) == ("package", "counts_visits")


def test_a_package_for_a_length_of_time_is_not_sold_as_a_book(alpha, swimming, member):
    package = services.create_package(
        by=alpha.assignment,
        name="Swimming only, monthly",
        price_rwf=35000,
        services=[swimming],
        duration_count=1,
        duration_unit="month",
    )

    with pytest.raises(services.InvalidDetails) as raised:
        services.create_ticketbook(by=alpha.assignment, member=member, package=package)

    assert (raised.value.field, raised.value.reason) == ("package", "counts_time")


# --- What a member may use today --------------------------------------------


def test_a_membership_only_covers_the_services_it_includes(alpha, swimming, gym_floor, member):
    swim_only = services.create_package(
        by=alpha.assignment,
        name="Swimming only, monthly",
        price_rwf=35000,
        services=[swimming],
        duration_count=1,
        duration_unit="month",
    )
    services.create_membership(
        by=alpha.assignment, member=member, package=swim_only, start_date=timezone.localdate()
    )

    allowed = services.allowed_services(member)

    assert [item.service.name for item in allowed] == ["Swimming"]
    assert allowed[0].by_ticket is False


def test_a_ticketbook_adds_to_what_a_membership_covers(alpha, swimming, gym_floor, member):
    gym_only = services.create_package(
        by=alpha.assignment,
        name="Gym, monthly",
        price_rwf=50000,
        services=[gym_floor],
        duration_count=1,
        duration_unit="month",
    )
    services.create_membership(
        by=alpha.assignment, member=member, package=gym_only, start_date=timezone.localdate()
    )
    services.create_ticketbook(by=alpha.assignment, member=member, package=tickets(alpha, swimming))

    allowed = {item.service.name: item for item in services.allowed_services(member)}

    assert sorted(allowed) == ["Gym", "Swimming"]
    assert allowed["Gym"].by_ticket is False
    assert allowed["Swimming"].by_ticket is True


def test_a_book_with_no_visits_left_covers_nothing(alpha, swimming, member):
    book = services.create_ticketbook(
        by=alpha.assignment, member=member, package=tickets(alpha, swimming, visits=1)
    )
    services.spend_ticket(by=alpha.assignment, ticketbook=book, service=swimming)

    assert services.allowed_services(member) == []


# --- Who may do what --------------------------------------------------------


def test_a_trainer_cannot_sell_a_book_or_spend_a_visit(alpha, add_staff, swimming, member):
    trainer = add_staff(alpha.gym, "0788000009", Role.TRAINER)
    package = tickets(alpha, swimming)
    book = services.create_ticketbook(by=alpha.assignment, member=member, package=package)

    with pytest.raises(PermissionDenied):
        services.create_ticketbook(by=trainer.assignment, member=member, package=package)
    with pytest.raises(PermissionDenied):
        services.spend_ticket(by=trainer.assignment, ticketbook=book, service=swimming)


def test_another_gym_cannot_touch_a_book(alpha, bravo, swimming, member):
    book = services.create_ticketbook(
        by=alpha.assignment, member=member, package=tickets(alpha, swimming)
    )

    with pytest.raises(PermissionDenied):
        services.spend_ticket(by=bravo.assignment, ticketbook=book, service=swimming)
