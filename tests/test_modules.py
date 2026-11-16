"""Tests for switching the nine applications on and off for a gym."""

import io

import pytest
from django.core.exceptions import PermissionDenied
from django.core.management import CommandError, call_command
from django.db import ProgrammingError, transaction
from django.test import RequestFactory
from django.urls import reverse

from apps.accounts import services as account_services
from apps.audit.models import AuditEntry
from apps.core import tenant_db
from apps.gyms import services as gym_services
from apps.gyms.models import Role
from apps.modules import catalog, services
from apps.modules.decorators import module_required
from apps.modules.models import ModuleActivation
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
    return gym_services.create_gym(name="Alpha Fitness", owner=owner_a)


@pytest.fixture
def gym_b(owner_b):
    return gym_services.create_gym(name="Bravo Fitness", owner=owner_b)


# --- The catalogue ----------------------------------------------------------


def test_the_catalogue_has_the_nine_applications_from_the_plan():
    assert len(catalog.CATALOG) == 9
    assert len({m.code for m in catalog.CATALOG}) == 9


def test_every_dependency_exists_and_nothing_depends_on_itself():
    for module in catalog.CATALOG:
        assert module.code not in module.requires
        for needed in module.requires:
            assert needed in catalog.BY_CODE


def test_dependencies_have_no_loops():
    def reaches(start, seen=()):
        for needed in catalog.BY_CODE[start].requires:
            assert needed not in seen, f"loop through {needed}"
            reaches(needed, seen + (start,))

    for module in catalog.CATALOG:
        reaches(module.code)


def test_only_gym_operations_is_always_on_and_everything_else_needs_it():
    assert [m.code for m in catalog.CATALOG if m.always_on] == [catalog.GYM_OPERATIONS]
    for module in catalog.CATALOG:
        if not module.always_on:
            assert catalog.GYM_OPERATIONS in module.requires


# --- Switching on and off ---------------------------------------------------


def test_a_new_gym_has_only_gym_operations(gym_a):
    assert services.enabled_codes(gym_a) == {catalog.GYM_OPERATIONS}
    assert services.is_enabled(gym_a, catalog.GYM_OPERATIONS)
    assert not services.is_enabled(gym_a, catalog.PAYMENTS_FINANCE)


def test_switching_a_module_on_and_off(gym_a):
    assert services.enable_module(gym=gym_a, code=catalog.PAYMENTS_FINANCE) is True
    assert services.is_enabled(gym_a, catalog.PAYMENTS_FINANCE)

    assert services.disable_module(gym=gym_a, code=catalog.PAYMENTS_FINANCE) is True
    assert not services.is_enabled(gym_a, catalog.PAYMENTS_FINANCE)


def test_switching_something_on_twice_changes_nothing_and_records_once(gym_a):
    services.enable_module(gym=gym_a, code=catalog.PAYMENTS_FINANCE)

    assert services.enable_module(gym=gym_a, code=catalog.PAYMENTS_FINANCE) is False
    assert AuditEntry.objects.filter(action="module.enabled").count() == 1


def test_an_application_cannot_be_switched_on_without_what_it_needs(gym_a):
    with pytest.raises(services.MissingDependencies) as problem:
        services.enable_module(gym=gym_a, code=catalog.RETENTION_LOYALTY)

    missing = {m.code for m in problem.value.missing}
    assert missing == {catalog.PAYMENTS_FINANCE, catalog.ATTENDANCE_ACCESS}
    assert not services.is_enabled(gym_a, catalog.RETENTION_LOYALTY)


def test_it_can_be_switched_on_once_its_needs_are_on(gym_a):
    services.enable_module(gym=gym_a, code=catalog.PAYMENTS_FINANCE)
    services.enable_module(gym=gym_a, code=catalog.ATTENDANCE_ACCESS)

    assert services.enable_module(gym=gym_a, code=catalog.RETENTION_LOYALTY) is True


def test_an_application_cannot_be_switched_off_while_another_needs_it(gym_a):
    for code in (catalog.PAYMENTS_FINANCE, catalog.ATTENDANCE_ACCESS, catalog.RETENTION_LOYALTY):
        services.enable_module(gym=gym_a, code=code)

    with pytest.raises(services.HasActiveDependents) as problem:
        services.disable_module(gym=gym_a, code=catalog.PAYMENTS_FINANCE)

    assert [m.code for m in problem.value.dependents] == [catalog.RETENTION_LOYALTY]
    assert services.is_enabled(gym_a, catalog.PAYMENTS_FINANCE)


def test_gym_operations_cannot_be_switched_off(gym_a):
    with pytest.raises(services.CannotDisable):
        services.disable_module(gym=gym_a, code=catalog.GYM_OPERATIONS)


def test_an_unknown_code_is_refused(gym_a):
    with pytest.raises(services.UnknownModule):
        services.enable_module(gym=gym_a, code="free_money")


def test_switching_off_keeps_the_history_and_switching_on_again_reuses_the_row(gym_a):
    services.enable_module(gym=gym_a, code=catalog.PAYMENTS_FINANCE)
    services.enable_module(gym=gym_a, code=catalog.SALES_GROWTH)
    services.disable_module(gym=gym_a, code=catalog.SALES_GROWTH)

    row = ModuleActivation.objects.for_gym(gym_a).get(module=catalog.SALES_GROWTH)
    assert row.is_active is False and row.deactivated_at is not None

    services.enable_module(gym=gym_a, code=catalog.SALES_GROWTH)
    row.refresh_from_db()
    assert row.is_active is True and row.deactivated_at is None
    assert ModuleActivation.objects.for_gym(gym_a).filter(module=catalog.SALES_GROWTH).count() == 1


def test_every_switch_is_recorded_in_the_audit_log(gym_a, owner_a):
    services.enable_module(gym=gym_a, code=catalog.PAYMENTS_FINANCE, actor=owner_a)
    services.disable_module(gym=gym_a, code=catalog.PAYMENTS_FINANCE, actor=owner_a)

    entries = AuditEntry.objects.for_gym(gym_a).filter(action__startswith="module.").order_by("id")
    assert [e.action for e in entries] == ["module.enabled", "module.disabled"]
    assert entries[0].details == {"module": catalog.PAYMENTS_FINANCE}
    assert entries[0].actor == owner_a


def test_one_gyms_modules_do_not_affect_another(gym_a, gym_b):
    services.enable_module(gym=gym_a, code=catalog.PAYMENTS_FINANCE)

    assert services.is_enabled(gym_a, catalog.PAYMENTS_FINANCE)
    assert not services.is_enabled(gym_b, catalog.PAYMENTS_FINANCE)


# --- Protecting screens -----------------------------------------------------


def test_module_required_blocks_gyms_without_the_module_and_allows_those_with_it(gym_a):
    @module_required(catalog.PAYMENTS_FINANCE)
    def view(request):
        return "shown"

    request = RequestFactory().get("/")
    request.gym = gym_a

    with pytest.raises(PermissionDenied):
        view(request)

    services.enable_module(gym=gym_a, code=catalog.PAYMENTS_FINANCE)
    assert view(request) == "shown"


def test_module_required_refuses_a_made_up_module_when_the_screen_is_defined():
    with pytest.raises(services.UnknownModule):
        module_required("nonsense")


# --- The database enforces who can change switches --------------------------


def test_gym_staff_can_read_their_gyms_switches_but_not_others(gym_a, gym_b):
    services.enable_module(gym=gym_a, code=catalog.PAYMENTS_FINANCE)
    services.enable_module(gym=gym_b, code=catalog.ATTENDANCE_ACCESS)

    tenant_db.enter_app_role(person_id=None)
    try:
        assert ModuleActivation.objects.count() == 0  # no gym chosen
        tenant_db.set_current_gym(gym_a.id)
        seen = list(ModuleActivation.objects.values_list("module", flat=True))  # unfiltered
    finally:
        tenant_db.leave_app_role()

    assert seen == [catalog.PAYMENTS_FINANCE]


def test_the_web_role_cannot_switch_modules_on_or_off_even_for_its_own_gym(gym_a):
    services.enable_module(gym=gym_a, code=catalog.PAYMENTS_FINANCE)
    tenant_db.enter_app_role(person_id=None)
    try:
        tenant_db.set_current_gym(gym_a.id)
        with pytest.raises(ProgrammingError, match="permission denied"), transaction.atomic():
            ModuleActivation.objects.create(gym=gym_a, module=catalog.SALES_GROWTH)
        with pytest.raises(ProgrammingError, match="permission denied"), transaction.atomic():
            ModuleActivation.objects.for_gym(gym_a).update(is_active=False)
    finally:
        tenant_db.leave_app_role()


# --- The Applications page --------------------------------------------------


def make_login(person):
    return account_services.create_user_account(person=person, password=PASSWORD)


def test_the_owner_sees_which_applications_the_gym_has(client, owner_a, gym_a):
    services.enable_module(gym=gym_a, code=catalog.PAYMENTS_FINANCE)
    client.force_login(make_login(owner_a))
    client.get(reverse("gyms:enter"))

    page = client.get(reverse("modules:list")).content.decode()

    assert "Payments and Finance</strong>:\n        on" in page
    assert "Retention and Loyalty</strong>:\n        off" in page

    retention = page.split("Retention and Loyalty")[1].split("<strong>")[0]
    assert "Needs: Attendance and Access." in retention
    assert "Payments and Finance" not in retention  # already on, so no longer "needed"


def test_a_receptionist_cannot_open_the_applications_page(client, gym_a):
    receptionist = people_services.create_person(full_name="Claudine", phone="0788000003")
    gym_services.assign_staff(gym=gym_a, person=receptionist, role=Role.RECEPTIONIST)
    client.force_login(make_login(receptionist))
    client.get(reverse("gyms:enter"))

    assert client.get(reverse("modules:list")).status_code == 403


# --- The platform command ---------------------------------------------------


def run_command(*args):
    out = io.StringIO()
    call_command("set_module", *args, stdout=out)
    return out.getvalue()


def test_command_switches_a_module_on_by_gym_name_and_shows_status(gym_a):
    output = run_command("--gym", "alpha fitness", "--module", "payments_finance")

    assert "Switched on." in output
    assert "[ON ] payments_finance" in output
    assert "[off] sales_growth" in output
    assert services.is_enabled(gym_a, catalog.PAYMENTS_FINANCE)


def test_command_reports_problems_in_plain_words(gym_a):
    with pytest.raises(CommandError, match="needs Payments and Finance"):
        run_command("--gym", "Alpha Fitness", "--module", "sales_growth")
    with pytest.raises(CommandError, match="No gym is called"):
        run_command("--gym", "Nobody Gym")
    with pytest.raises(CommandError, match="is not an application"):
        run_command("--gym", "Alpha Fitness", "--module", "nonsense")


def test_find_gym_by_id_and_when_names_clash(gym_a, owner_b):
    assert gym_services.find_gym(str(gym_a.public_id)) == gym_a

    gym_services.create_gym(name="Alpha Fitness", owner=owner_b)
    with pytest.raises(gym_services.AmbiguousGym):
        gym_services.find_gym("Alpha Fitness")
