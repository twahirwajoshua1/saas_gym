"""Tests for staff management, temporary passwords and branches."""

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from apps.accounts import services as accounts
from apps.accounts.models import User
from apps.audit.models import AuditEntry
from apps.gyms import services as gyms
from apps.gyms.models import Branch, Role, StaffAssignment
from apps.people import services as people
from apps.team import services
from tests.conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture
def alpha(make_gym):
    return make_gym("Alpha Fitness", "0788000001", "Aline Uwase")


def add(alpha, phone="0788200001", role=Role.RECEPTIONIST, name="Claudine Mukamana", branch=None):
    return services.add_staff(
        by=alpha.assignment, full_name=name, phone=phone, role=role, branch=branch
    )


# --- Adding staff -----------------------------------------------------------


def test_adding_a_new_person_gives_them_a_role_and_a_temporary_login(alpha):
    added = add(alpha, phone="0788 200 001")

    assert added.assignment.role == Role.RECEPTIONIST and added.assignment.is_active
    assert added.assignment.person.phone == "+250788200001"
    user = User.objects.get(person=added.assignment.person)
    assert user.check_password(added.temporary_password)
    assert user.must_change_password
    assert len(added.temporary_password) == 14
    actions = set(AuditEntry.objects.for_gym(alpha.gym).values_list("action", flat=True))
    assert {"staff.assigned", "account.created"} <= actions
    assert added.temporary_password not in str(
        list(AuditEntry.objects.values_list("summary", "details"))
    )


def test_someone_with_a_login_keeps_their_own_password(alpha, make_gym):
    bravo = make_gym("Bravo Fitness", "0788000002", "Bosco Kamanzi")

    added = add(alpha, phone="0788000002", role=Role.TRAINER, name="ignored")

    assert added.temporary_password is None
    assert added.assignment.person == bravo.person
    assert not User.objects.get(person=bravo.person).must_change_password


def test_someone_who_left_can_be_added_again(alpha):
    first = add(alpha)
    gyms.remove_staff(assignment=first.assignment)

    again = add(alpha, role=Role.TRAINER)

    assert again.reactivated and again.assignment.pk == first.assignment.pk
    assert again.assignment.role == Role.TRAINER and again.assignment.is_active
    assert StaffAssignment.objects.for_gym(alpha.gym).count() == 2  # owner + this one


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"phone": "123"}, "phone_invalid"),
        ({"name": "  "}, "name_required"),
        ({"role": "boss"}, "role_invalid"),
        ({"phone": "0788000001"}, "already_staff"),  # the owner
    ],
)
def test_wrong_details_are_refused(alpha, kwargs, reason):
    with pytest.raises(services.StaffProblem) as problem:
        add(alpha, **kwargs)

    assert problem.value.reason == reason


def test_owners_are_never_limited_to_a_branch_and_branches_must_be_ours(alpha, make_gym):
    remera = gyms.add_branch(gym=alpha.gym, name="Remera")
    theirs = Branch.objects.for_gym(make_gym("Bravo", "0788000002").gym).get()

    co_owner = add(alpha, role=Role.OWNER, branch=remera)
    assert co_owner.assignment.branch is None

    with pytest.raises(services.StaffProblem):
        add(alpha, phone="0788200002", branch=theirs)
    assert add(alpha, phone="0788200003", branch=remera).assignment.branch == remera


@pytest.mark.parametrize("role", [Role.BRANCH_MANAGER, Role.RECEPTIONIST, Role.TRAINER])
def test_only_owners_manage_staff(alpha, add_staff, role):
    staff = add_staff(alpha.gym, "0788000005", role)

    with pytest.raises(PermissionDenied):
        services.add_staff(by=staff.assignment, full_name="X", phone="0788200009", role=Role.OWNER)


# --- Changing and removing --------------------------------------------------


def test_changing_a_role_and_branch_is_recorded(alpha):
    remera = gyms.add_branch(gym=alpha.gym, name="Remera")
    staff = add(alpha).assignment

    services.change_staff(
        by=alpha.assignment, assignment=staff, role=Role.BRANCH_MANAGER, branch=remera
    )

    staff.refresh_from_db()
    assert (staff.role, staff.branch) == (Role.BRANCH_MANAGER, remera)
    entry = AuditEntry.objects.for_gym(alpha.gym).get(action="staff.changed")
    assert entry.details["role_before"] == Role.RECEPTIONIST


def test_owners_cannot_change_or_remove_themselves(alpha):
    for call in (
        lambda: services.change_staff(
            by=alpha.assignment, assignment=alpha.assignment, role=Role.TRAINER, branch=None
        ),
        lambda: services.remove_staff(by=alpha.assignment, assignment=alpha.assignment),
    ):
        with pytest.raises(services.StaffProblem) as problem:
            call()
        assert problem.value.reason == "yourself"


def test_the_gym_always_keeps_an_owner(alpha):
    co_owner = add(alpha, role=Role.OWNER).assignment

    services.remove_staff(by=alpha.assignment, assignment=co_owner)  # fine: Aline remains

    # Bring the co-owner back and let them try to remove Aline, the other owner.
    back = add(alpha, role=Role.OWNER).assignment
    services.remove_staff(by=back, assignment=alpha.assignment)
    alpha.assignment.refresh_from_db()
    assert not alpha.assignment.is_active
    with pytest.raises(services.StaffProblem) as problem:
        services.change_staff(by=back, assignment=back, role=Role.TRAINER, branch=None)
    assert problem.value.reason == "yourself"


def test_the_last_owner_cannot_be_demoted_by_another_gym_role(alpha):
    co_owner = add(alpha, role=Role.OWNER).assignment
    services.change_staff(by=co_owner, assignment=alpha.assignment, role=Role.TRAINER, branch=None)

    # Now co_owner is the only owner. Aline (a trainer) cannot act; co_owner cannot demote self.
    with pytest.raises(services.StaffProblem):
        services.change_staff(by=co_owner, assignment=co_owner, role=Role.TRAINER, branch=None)
    assert gyms.other_active_owners(alpha.assignment) == 1


def test_removed_staff_lose_access_at_once(alpha, client):
    added = add(alpha)
    user = User.objects.get(person=added.assignment.person)
    client.force_login(user)
    user.must_change_password = False
    user.save()
    client.get(reverse("gyms:enter"))
    assert client.get(reverse("gyms:home"), follow=True).status_code == 200

    services.remove_staff(by=alpha.assignment, assignment=added.assignment)

    assert client.get(reverse("gyms:home")).status_code == 302


def test_staff_of_another_gym_cannot_be_touched(alpha, make_gym):
    bravo = make_gym("Bravo Fitness", "0788000002")
    theirs = services.add_staff(
        by=bravo.assignment, full_name="Bravo staff", phone="0788300001", role=Role.TRAINER
    ).assignment

    with pytest.raises(PermissionDenied):
        services.remove_staff(by=alpha.assignment, assignment=theirs)
    with pytest.raises(PermissionDenied):
        services.reset_password(by=alpha.assignment, assignment=theirs)


# --- Passwords --------------------------------------------------------------


def test_resetting_a_password_gives_a_new_temporary_one_and_unlocks(alpha):
    added = add(alpha)
    for _ in range(5):
        try:
            accounts.authenticate_by_phone(None, phone="0788200001", password="wrong")
        except accounts.TooManyAttempts:
            pass

    password = services.reset_password(by=alpha.assignment, assignment=added.assignment)

    assert password != added.temporary_password
    user = accounts.authenticate_by_phone(None, phone="0788200001", password=password)
    assert user is not None and user.must_change_password
    assert AuditEntry.objects.for_gym(alpha.gym).filter(action="account.password_reset").exists()


def test_an_owner_cannot_reset_the_password_of_someone_who_works_elsewhere(alpha, make_gym):
    bravo = make_gym("Bravo Fitness", "0788000002", "Bosco Kamanzi")
    bosco_here = add(alpha, phone="0788000002", role=Role.TRAINER).assignment

    assert not services.can_reset_password(alpha.assignment, bosco_here)
    with pytest.raises(services.StaffProblem) as problem:
        services.reset_password(by=alpha.assignment, assignment=bosco_here)
    assert problem.value.reason == "cannot_reset"
    assert User.objects.get(person=bravo.person).check_password(TEST_PASSWORD)


def test_an_owner_cannot_reset_a_platform_administrators_password(alpha):
    admin = accounts.create_platform_admin(
        full_name="Platform", phone="0788999999", password=TEST_PASSWORD
    )
    here = add(alpha, phone="0788999999", role=Role.TRAINER).assignment

    assert not services.can_reset_password(alpha.assignment, here)
    assert admin.check_password(TEST_PASSWORD)


def test_changing_your_own_password(alpha):
    user = alpha.user
    with pytest.raises(accounts.WrongPassword):
        accounts.change_own_password(user=user, current_password="nope", new_password="x")

    accounts.change_own_password(
        user=user, current_password=TEST_PASSWORD, new_password="a-much-better-one-77"
    )

    assert user.check_password("a-much-better-one-77")


# --- Screens ----------------------------------------------------------------


def test_the_owner_adds_staff_and_sees_the_password_once(alpha, sign_in):
    client = sign_in(alpha)

    response = client.post(
        reverse("team:staff_add"),
        {"full_name": "Claudine Mukamana", "phone": "0788 200 001", "role": "receptionist"},
    )

    page = response.content.decode()
    assert "Claudine Mukamana is now Receptionist" in page
    assert "shown only once" in page
    staff_page = client.get(reverse("team:staff_list")).content.decode()
    assert "Claudine Mukamana" in staff_page and "has not signed in yet" in staff_page


def test_a_new_staff_member_must_choose_a_password_first(alpha, client):
    added = add(alpha)
    temporary = added.temporary_password

    client.post(reverse("accounts:login"), {"phone": "0788200001", "password": temporary})
    assert client.get(reverse("gyms:home")).url == reverse("accounts:password_change")
    assert client.get(reverse("frontdesk:people")).url == reverse("accounts:password_change")

    mismatch = client.post(
        reverse("accounts:password_change"),
        {
            "current_password": temporary,
            "new_password": "my-own-secret-41",
            "new_password_again": "different",
        },
    )
    assert "not the same" in mismatch.content.decode()

    client.post(
        reverse("accounts:password_change"),
        {
            "current_password": temporary,
            "new_password": "my-own-secret-41",
            "new_password_again": "my-own-secret-41",
        },
    )
    client.get(reverse("gyms:enter"))
    assert (
        client.get(reverse("gyms:home"), follow=True).status_code == 200
    )  # still signed in, now free to work


def test_staff_screens_change_reset_and_remove(alpha, sign_in):
    added = add(alpha)
    client = sign_in(alpha)
    key = added.assignment.person.public_id

    client.post(reverse("team:staff_edit", args=[key]), {"role": "trainer"})
    reset = client.post(reverse("team:staff_reset_password", args=[key]))
    removed = client.post(reverse("team:staff_remove", args=[key]), follow=True)

    added.assignment.refresh_from_db()
    assert added.assignment.role == Role.TRAINER
    assert "New temporary password for Claudine Mukamana" in reset.content.decode()
    assert "no longer has access" in removed.content.decode()
    assert not added.assignment.is_active


def test_the_owner_has_no_buttons_against_themselves(alpha, sign_in):
    page = sign_in(alpha).get(reverse("team:staff_list")).content.decode()

    me = alpha.person.public_id
    assert reverse("team:staff_remove", args=[me]) not in page
    assert "(you)" in page


@pytest.mark.parametrize("role", [Role.BRANCH_MANAGER, Role.RECEPTIONIST])
def test_other_roles_cannot_open_staff_screens(alpha, add_staff, sign_in, role):
    client = sign_in(add_staff(alpha.gym, "0788000005", role))

    assert client.get(reverse("team:staff_list")).status_code == 403
    assert client.get(reverse("gyms:branch_add")).status_code == 403


def test_another_gyms_staff_cannot_be_reached_by_address(alpha, make_gym, sign_in):
    bravo = make_gym("Bravo Fitness", "0788000002")
    theirs = services.add_staff(
        by=bravo.assignment, full_name="Bravo staff", phone="0788300001", role=Role.TRAINER
    ).assignment
    client = sign_in(alpha)
    key = theirs.person.public_id

    assert client.get(reverse("team:staff_edit", args=[key])).status_code == 404
    assert client.post(reverse("team:staff_remove", args=[key])).status_code == 404
    assert client.post(reverse("team:staff_reset_password", args=[key])).status_code == 404
    theirs.refresh_from_db()
    assert theirs.is_active


# --- Branches ---------------------------------------------------------------


def test_the_owner_adds_and_renames_branches(alpha, sign_in):
    client = sign_in(alpha)

    client.post(reverse("gyms:branch_add"), {"name": "Remera"})
    duplicate = client.post(reverse("gyms:branch_add"), {"name": "remera"})
    branch = Branch.objects.for_gym(alpha.gym).get(name="Remera")
    client.post(reverse("gyms:branch_edit", args=[branch.public_id]), {"name": "Remera Giporoso"})

    assert "already has a branch called" in duplicate.content.decode()
    branch.refresh_from_db()
    assert branch.name == "Remera Giporoso"
    actions = set(AuditEntry.objects.for_gym(alpha.gym).values_list("action", flat=True))
    assert {"branch.added", "branch.renamed"} <= actions


def test_branch_rules_in_the_services(alpha, add_staff, make_gym):
    manager = add_staff(alpha.gym, "0788000005", Role.BRANCH_MANAGER)
    theirs = Branch.objects.for_gym(make_gym("Bravo", "0788000002").gym).get()

    with pytest.raises(PermissionDenied):
        gyms.create_branch(by=manager.assignment, name="Kicukiro")
    with pytest.raises(gyms.InvalidGymDetails):
        gyms.rename_branch(by=alpha.assignment, branch=theirs, name="Mine now")


def test_person_helper_still_finds_people(alpha):
    assert people.find_person_by_phone("0788000001") == alpha.person


# --- Finding somebody on a long staff list ------------------------------------


def test_the_staff_list_can_be_cut_to_one_branch(alpha, add_staff):
    """
    Somebody with no branch of their own works at every branch, so they show
    whichever branch is asked for.
    """
    north = Branch.objects.for_gym(alpha.gym).first()
    south = gyms.add_branch(gym=alpha.gym, name="Kicukiro")
    add_staff(alpha.gym, "0788000021", Role.RECEPTIONIST, "North Desk", branch=north)
    add_staff(alpha.gym, "0788000022", Role.RECEPTIONIST, "South Desk", branch=south)
    add_staff(alpha.gym, "0788000023", Role.TRAINER, "Roaming Trainer")

    names = [row.assignment.person.full_name for row in services.staff_of(alpha.gym, branch=north)]

    assert "North Desk" in names
    assert "Roaming Trainer" in names
    assert "South Desk" not in names


def test_the_staff_list_can_be_cut_to_one_role(alpha, add_staff):
    add_staff(alpha.gym, "0788000024", Role.RECEPTIONIST, "Gisele Mugwaneza")
    add_staff(alpha.gym, "0788000025", Role.TRAINER, "Kevin Bizimana")

    names = [
        row.assignment.person.full_name for row in services.staff_of(alpha.gym, role=Role.TRAINER)
    ]

    assert names == ["Kevin Bizimana"]


def test_branch_and_role_narrow_together(alpha, add_staff):
    north = Branch.objects.for_gym(alpha.gym).first()
    south = gyms.add_branch(gym=alpha.gym, name="Kicukiro")
    add_staff(alpha.gym, "0788000026", Role.RECEPTIONIST, "North Desk", branch=north)
    add_staff(alpha.gym, "0788000027", Role.TRAINER, "North Trainer", branch=north)
    add_staff(alpha.gym, "0788000028", Role.TRAINER, "South Trainer", branch=south)

    rows = services.staff_of(alpha.gym, branch=north, role=Role.TRAINER)

    assert [row.assignment.person.full_name for row in rows] == ["North Trainer"]


def test_no_filter_still_shows_everybody(alpha, add_staff):
    add_staff(alpha.gym, "0788000029", Role.RECEPTIONIST, "Gisele Mugwaneza")

    assert len(services.staff_of(alpha.gym)) == 2  # the owner and the receptionist


def test_the_filters_work_from_the_page(alpha, add_staff, sign_in):
    add_staff(alpha.gym, "0788000030", Role.TRAINER, "Kevin Bizimana")
    add_staff(alpha.gym, "0788000031", Role.RECEPTIONIST, "Gisele Mugwaneza")

    page = sign_in(alpha).get(reverse("team:staff_list"), {"role": Role.TRAINER}).content.decode()

    assert "Kevin Bizimana" in page
    assert "Gisele Mugwaneza" not in page
