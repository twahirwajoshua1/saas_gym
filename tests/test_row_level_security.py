"""
Tenant isolation, lock 2: the database refuses other gyms' rows by itself.

Most of these tests deliberately skip lock 1. They query without any
`for_gym(...)` filter, which is exactly the mistake lock 2 exists to survive.
"""

import pytest
from django.apps import apps
from django.db import ProgrammingError, connection, transaction
from django.urls import reverse

from apps.accounts import services as account_services
from apps.core import tenant_db
from apps.gyms import services
from apps.gyms.models import Branch, GymOwnedModel, GymOwnedQuerySet, Role, StaffAssignment
from apps.people import services as people_services

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-41"


@pytest.fixture
def owner_a():
    return people_services.create_person(full_name="Aline Uwase", phone="0788000001")


@pytest.fixture
def owner_b():
    return people_services.create_person(full_name="Bosco Kamanzi", phone="0788000002")


@pytest.fixture
def gym_a(owner_a):
    gym = services.create_gym(name="Alpha Fitness", owner=owner_a)
    services.add_branch(gym=gym, name="A Downtown")
    return gym


@pytest.fixture
def gym_b(owner_b):
    gym = services.create_gym(name="Bravo Fitness", owner=owner_b)
    services.add_branch(gym=gym, name="B Secret Branch")
    return gym


@pytest.fixture
def app_role():
    """Act like a web request: run as the restricted database role. Use after the data fixtures."""
    tenant_db.enter_app_role(person_id=None)
    yield
    tenant_db.leave_app_role()


def current_user():
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        return cursor.fetchone()[0]


def test_with_no_gym_chosen_nothing_is_visible(gym_a, gym_b, app_role):
    assert current_user() == tenant_db.APP_ROLE
    assert Branch.objects.count() == 0
    assert StaffAssignment.objects.count() == 0


def test_an_unfiltered_query_returns_only_the_current_gyms_rows(gym_a, gym_b, app_role):
    tenant_db.set_current_gym(gym_a.id)

    names = set(Branch.objects.values_list("name", flat=True))  # no for_gym() on purpose

    assert names == {"Main branch", "A Downtown"}


def test_rows_cannot_be_written_into_another_gym(gym_a, gym_b, app_role):
    tenant_db.set_current_gym(gym_a.id)

    Branch.objects.create(gym=gym_a, name="Allowed")  # my own gym: fine
    with pytest.raises(ProgrammingError), transaction.atomic():
        Branch.objects.create(gym=gym_b, name="Planted")


def test_another_gyms_rows_cannot_be_changed_or_deleted(gym_a, gym_b, app_role):
    tenant_db.set_current_gym(gym_a.id)

    assert Branch.objects.filter(gym=gym_b).update(name="Hacked") == 0
    assert Branch.objects.filter(gym=gym_b).delete() == (0, {})
    assert StaffAssignment.objects.filter(gym=gym_b).update(role=Role.TRAINER) == 0


def test_a_person_sees_their_own_assignments_everywhere_but_nobody_elses(
    owner_a, owner_b, gym_a, gym_b
):
    services.assign_staff(gym=gym_b, person=owner_a, role=Role.BRANCH_MANAGER)
    tenant_db.enter_app_role(person_id=owner_a.id)
    try:
        people = set(StaffAssignment.objects.values_list("person_id", flat=True))
        gyms = set(StaffAssignment.objects.values_list("gym_id", flat=True))
    finally:
        tenant_db.leave_app_role()

    assert people == {owner_a.id}  # not Bosco's record at Bravo
    assert gyms == {gym_a.id, gym_b.id}


def test_the_normal_database_user_is_not_restricted(gym_a, gym_b):
    """Migrations, commands and the platform admin rely on this."""
    assert current_user() != tenant_db.APP_ROLE
    assert Branch.objects.count() == 4  # two gyms, a main branch and one more each


def test_every_gym_owned_table_has_row_level_security_and_a_policy():
    tables = [m._meta.db_table for m in apps.get_models() if issubclass(m, GymOwnedModel)]
    tables.append(
        "audit_auditentry"
    )  # gym-scoped, but its gym may be empty so it is not a GymOwnedModel
    assert len(tables) >= 3

    for table in tables:
        with connection.cursor() as cursor:
            cursor.execute("SELECT relrowsecurity FROM pg_class WHERE relname = %s", [table])
            enabled = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM pg_policies WHERE tablename = %s AND %s = ANY(roles)",
                [table, tenant_db.APP_ROLE],
            )
            policies = cursor.fetchone()[0]
        assert enabled, f"{table} has no row-level security. Add a policy in a migration."
        assert policies >= 1, f"{table} has no policy for {tenant_db.APP_ROLE}."


def test_the_restricted_role_cannot_skip_the_rules():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = %s",
            [tenant_db.APP_ROLE],
        )
        assert cursor.fetchone() == (False, False, False)


# --- Through the real pages -------------------------------------------------


def make_login(person):
    return account_services.create_user_account(person=person, password=PASSWORD)


def test_a_page_that_forgets_the_gym_filter_still_shows_only_my_gym(
    client, monkeypatch, owner_a, gym_a, gym_b
):
    """Simulates a developer's mistake: for_gym() that does not filter at all."""
    monkeypatch.setattr(GymOwnedQuerySet, "for_gym", lambda self, gym: self)
    client.force_login(make_login(owner_a))
    client.get(reverse("gyms:enter"))

    page = client.get(reverse("gyms:branch_list")).content.decode()

    assert "A Downtown" in page
    assert "B Secret Branch" not in page


def test_the_connection_goes_back_to_normal_after_every_request(client, owner_a, gym_a):
    client.force_login(make_login(owner_a))
    client.get(reverse("gyms:enter"))
    client.get(reverse("gyms:home"))

    assert current_user() != tenant_db.APP_ROLE
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.current_gym_id', true)")
        assert cursor.fetchone()[0] in (None, "")


def test_platform_admin_sees_every_gym(client, gym_a, gym_b):
    admin = account_services.create_platform_admin(
        full_name="Platform Owner", phone="0788555555", password=PASSWORD
    )
    client.force_login(admin)

    page = client.get(reverse("admin:gyms_branch_changelist")).content.decode()

    assert "A Downtown" in page
    assert "B Secret Branch" in page


def test_a_gym_owner_cannot_use_the_platform_admin(client, owner_a, gym_a):
    client.force_login(make_login(owner_a))

    response = client.get(reverse("admin:gyms_branch_changelist"))

    assert response.status_code == 302  # sent to the admin sign-in page
    assert "Alpha Fitness" not in response.content.decode()


def test_a_branch_limited_role_keeps_its_branch(client, owner_a, gym_a):
    branch = Branch.objects.for_gym(gym_a).get(name="A Downtown")
    receptionist = people_services.create_person(full_name="Claudine", phone="0788000003")
    services.assign_staff(gym=gym_a, person=receptionist, role=Role.RECEPTIONIST, branch=branch)
    client.force_login(make_login(receptionist))
    client.get(reverse("gyms:enter"))

    from apps.gyms.tenancy import current_assignment

    request = client.get(reverse("gyms:home")).wsgi_request
    assert current_assignment(request) is not None
    assert request.assignment.branch_id == branch.id


def test_members_and_packages_are_hidden_by_the_database_too(make_gym):
    """An unfiltered query, as the web role, returns only the current gym's rows."""
    from apps.members import services as member_services
    from apps.members.models import Member, Package
    from apps.members.services import MemberDetails

    alpha = make_gym("Alpha Fitness", "0788100001")
    bravo = make_gym("Bravo Fitness", "0788100002")
    for staff, phone in ((alpha, "0788300001"), (bravo, "0788300002")):
        member_services.register_member(
            by=staff.assignment,
            phone=phone,
            details=MemberDetails(full_name=f"Of {staff.gym.name}"),
        )
        member_services.create_package(
            by=staff.assignment,
            name="Monthly",
            price_rwf=30000,
            duration_count=1,
            duration_unit="month",
        )

    tenant_db.enter_app_role(person_id=None)
    try:
        assert Member.objects.count() == 0 and Package.objects.count() == 0  # no gym chosen
        tenant_db.set_current_gym(alpha.gym.id)
        members = list(Member.objects.values_list("full_name", flat=True))  # unfiltered
        packages = list(Package.objects.values_list("gym_id", flat=True))
        with pytest.raises(ProgrammingError), transaction.atomic():
            Member.objects.filter(gym=alpha.gym).update(gym=bravo.gym)  # cannot move a row out
    finally:
        tenant_db.leave_app_role()

    assert members == ["Of Alpha Fitness"]
    assert packages == [alpha.gym.id]
