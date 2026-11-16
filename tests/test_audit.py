"""Tests for the permanent audit log."""

import pytest
from django.db import DatabaseError, ProgrammingError, connection, transaction
from django.urls import reverse

from apps.accounts import services as account_services
from apps.audit import services as audit
from apps.audit.models import AuditEntry
from apps.core import tenant_db
from apps.gyms import services
from apps.gyms.models import Role, StaffAssignment
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
    return services.create_gym(name="Alpha Fitness", owner=owner_a)


@pytest.fixture
def gym_b(owner_b):
    return services.create_gym(name="Bravo Fitness", owner=owner_b)


def actions(gym):
    return [e.action for e in AuditEntry.objects.for_gym(gym).order_by("id")]


# --- Recording --------------------------------------------------------------


def test_record_stores_what_who_and_about_what(gym_a, owner_a):
    entry = audit.record(
        action="demo.happened",
        summary="Something happened",
        gym=gym_a,
        actor=owner_a,
        target=gym_a,
        details={"owner": owner_a.public_id},  # a UUID: must become plain text
    )

    entry.refresh_from_db()
    assert entry.actor == owner_a
    assert entry.target_type == "gyms.gym"
    assert entry.target_id == str(gym_a.public_id)
    assert entry.details == {"owner": str(owner_a.public_id)}


def test_creating_a_gym_is_recorded_with_who_did_it(owner_a):
    gym = services.create_gym(name="Alpha Fitness", owner=owner_a, actor=owner_a)

    entry = AuditEntry.objects.for_gym(gym).get()
    assert entry.action == "gym.created"
    assert entry.actor == owner_a
    assert "Alpha Fitness" in entry.summary


def test_branches_and_staff_changes_are_recorded(gym_a, owner_a):
    branch = services.add_branch(gym=gym_a, name="Remera", actor=owner_a)
    trainer = people_services.create_person(full_name="Eric Mugisha", phone="0788000009")
    assignment = services.assign_staff(
        gym=gym_a, person=trainer, role=Role.TRAINER, branch=branch, actor=owner_a
    )
    services.remove_staff(assignment=assignment, actor=owner_a)

    assert actions(gym_a) == ["gym.created", "branch.added", "staff.assigned", "staff.removed"]
    assigned = AuditEntry.objects.for_gym(gym_a).get(action="staff.assigned")
    assert assigned.details["role"] == Role.TRAINER
    assert assigned.details["branch"] == str(branch.public_id)
    assert assigned.actor == owner_a


def test_account_creation_is_recorded_without_the_password(owner_a):
    account_services.create_user_account(person=owner_a, password=PASSWORD)

    entry = AuditEntry.objects.get(action="account.created")
    assert entry.gym is None
    assert PASSWORD not in str(entry.details) + entry.summary


def test_an_action_and_its_audit_entry_succeed_or_fail_together(owner_a, monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("the audit log could not be written")

    monkeypatch.setattr(audit, "record", broken)

    with pytest.raises(RuntimeError):
        services.create_gym(name="Ghost Gym", owner=owner_a)

    from apps.gyms.models import Branch, Gym

    assert Gym.objects.count() == 0
    assert Branch.objects.count() == 0
    assert StaffAssignment.objects.count() == 0


# --- Permanent --------------------------------------------------------------


def test_entries_cannot_be_changed(gym_a):
    entry = AuditEntry.objects.for_gym(gym_a).get()
    entry.summary = "Nothing to see here"

    with pytest.raises(DatabaseError, match="permanent"), transaction.atomic():
        entry.save()


def test_entries_cannot_be_deleted(gym_a):
    with pytest.raises(DatabaseError, match="permanent"), transaction.atomic():
        AuditEntry.objects.for_gym(gym_a).delete()

    assert AuditEntry.objects.for_gym(gym_a).count() == 1


def test_the_table_cannot_be_emptied(gym_a):
    with pytest.raises(DatabaseError, match="permanent"), transaction.atomic():
        with connection.cursor() as cursor:
            # Settle this test's own pending inserts first, so it is our rule
            # that refuses the TRUNCATE and not a PostgreSQL technicality.
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute("TRUNCATE audit_auditentry")


def test_the_web_role_cannot_even_attempt_to_change_entries(gym_a):
    tenant_db.enter_app_role(person_id=None)
    try:
        tenant_db.set_current_gym(gym_a.id)
        with pytest.raises(ProgrammingError, match="permission denied"), transaction.atomic():
            AuditEntry.objects.for_gym(gym_a).update(summary="changed")
        with pytest.raises(ProgrammingError, match="permission denied"), transaction.atomic():
            AuditEntry.objects.for_gym(gym_a).delete()
    finally:
        tenant_db.leave_app_role()


# --- Private ----------------------------------------------------------------


def test_each_gym_sees_only_its_own_entries_even_without_a_filter(gym_a, gym_b, owner_a):
    account_services.create_user_account(person=owner_a, password=PASSWORD)  # platform-level entry
    tenant_db.enter_app_role(person_id=None)
    try:
        assert AuditEntry.objects.count() == 0  # no gym chosen: nothing visible
        tenant_db.set_current_gym(gym_a.id)
        visible = list(AuditEntry.objects.all())  # deliberately unfiltered
    finally:
        tenant_db.leave_app_role()

    assert [e.gym_id for e in visible] == [gym_a.id]  # not Bravo's, not the platform's


def test_the_web_role_can_only_write_entries_for_its_own_gym(gym_a, gym_b):
    tenant_db.enter_app_role(person_id=None)
    try:
        tenant_db.set_current_gym(gym_a.id)
        audit.record(action="demo", summary="mine", gym=gym_a)  # fine
        with pytest.raises(ProgrammingError), transaction.atomic():
            audit.record(action="demo", summary="forged", gym=gym_b)
    finally:
        tenant_db.leave_app_role()


# --- The Activity page ------------------------------------------------------


def make_login(person):
    return account_services.create_user_account(person=person, password=PASSWORD)


def test_the_owner_sees_their_gyms_activity_and_nobody_elses(client, owner_a, gym_a, gym_b):
    client.force_login(make_login(owner_a))
    client.get(reverse("gyms:enter"))

    page = client.get(reverse("gyms:activity")).content.decode()

    assert "Alpha Fitness was registered" in page
    assert "Bravo Fitness" not in page


def test_only_the_owner_may_open_the_activity_page(client, gym_a):
    receptionist = people_services.create_person(full_name="Claudine", phone="0788000003")
    services.assign_staff(gym=gym_a, person=receptionist, role=Role.RECEPTIONIST)
    client.force_login(make_login(receptionist))
    client.get(reverse("gyms:enter"))

    assert client.get(reverse("gyms:activity")).status_code == 403
