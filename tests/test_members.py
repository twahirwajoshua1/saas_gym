"""Tests for registering, finding and editing members."""

from datetime import date, timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEntry
from apps.gyms import services as gym_services
from apps.gyms.models import Branch, Role
from apps.members import services
from apps.members.models import Member
from apps.members.services import MemberDetails
from apps.people.models import Person

pytestmark = pytest.mark.django_db


@pytest.fixture
def alpha(make_gym):
    return make_gym("Alpha Fitness", "0788000001", "Aline Uwase")


@pytest.fixture
def bravo(make_gym):
    return make_gym("Bravo Fitness", "0788000002", "Bosco Kamanzi")


def register(staff, name="Eric Mugisha", phone="0788111222", **details):
    return services.register_member(
        by=staff.assignment, phone=phone, details=MemberDetails(full_name=name, **details)
    )


# --- Registering ------------------------------------------------------------


def test_registering_a_member_creates_the_person_and_the_member(alpha):
    member = register(alpha, name="  Eric   Mugisha ", phone="0788 111 222")

    assert member.full_name == "Eric Mugisha"
    assert member.phone == "+250788111222"
    assert member.branch.name == "Main branch"  # the only branch, chosen automatically
    assert member.joined_on == timezone.localdate()
    assert member.registered_by == alpha.person
    assert Person.objects.filter(phone="+250788111222").count() == 1


def test_registration_is_recorded_without_personal_details(alpha):
    member = register(alpha, emergency_contact_name="Mama Eric", notes="Knee injury")

    entry = AuditEntry.objects.for_gym(alpha.gym).get(action="member.registered")
    assert entry.actor == alpha.person
    assert entry.target_id == str(member.public_id)
    assert "Knee" not in str(entry.details) and "Mama" not in str(entry.details)


def test_the_same_phone_written_differently_is_already_a_member(alpha):
    first = register(alpha, phone="0788111222")

    with pytest.raises(services.AlreadyMember) as problem:
        register(alpha, name="Someone Else", phone="+250 788 111 222")

    assert problem.value.member == first
    assert Member.objects.for_gym(alpha.gym).count() == 1


@pytest.mark.parametrize(
    ("phone", "details", "field"),
    [
        ("0788111222", {"full_name": "   "}, "full_name"),
        ("12345", {"full_name": "Eric"}, "phone"),
        ("0788111222", {"full_name": "Eric", "gender": "other-value"}, "gender"),
        (
            "0788111222",
            {"full_name": "Eric", "date_of_birth": date.today() + timedelta(days=1)},
            "date_of_birth",
        ),
        (
            "0788111222",
            {"full_name": "Eric", "emergency_contact_phone": "12"},
            "emergency_contact_phone",
        ),
    ],
)
def test_wrong_details_are_refused_and_nothing_is_saved(alpha, phone, details, field):
    people_before = Person.objects.count()

    with pytest.raises(services.InvalidDetails) as problem:
        services.register_member(by=alpha.assignment, phone=phone, details=MemberDetails(**details))

    assert problem.value.field == field
    assert not Member.objects.exists()
    assert Person.objects.count() == people_before


def test_with_several_branches_one_must_be_chosen_and_it_must_be_ours(alpha, bravo):
    remera = gym_services.add_branch(gym=alpha.gym, name="Remera")

    with pytest.raises(services.InvalidDetails) as problem:
        register(alpha)
    assert problem.value.field == "branch"

    their_branch = Branch.objects.for_gym(bravo.gym).get()
    with pytest.raises(services.InvalidDetails):
        register(alpha, branch=their_branch)

    assert register(alpha, branch=remera).branch == remera


def test_a_trainer_cannot_register_members(alpha, add_staff):
    trainer = add_staff(alpha.gym, "0788000005", Role.TRAINER)

    with pytest.raises(PermissionDenied):
        register(trainer)


@pytest.mark.parametrize("role", [Role.BRANCH_MANAGER, Role.RECEPTIONIST, Role.SALES_REP])
def test_front_desk_roles_can_register_members(alpha, add_staff, role):
    staff = add_staff(alpha.gym, "0788000005", role)

    assert register(staff).registered_by == staff.person


# --- The same person at two gyms -------------------------------------------


def test_one_person_two_gyms_each_gym_keeps_its_own_details(alpha, bravo):
    at_alpha = register(alpha, name="Eric Mugisha", phone="0788111222", notes="Alpha's note")
    at_bravo = register(bravo, name="E. Mugisha (Bravo)", phone="0788 111 222")

    assert at_alpha.person == at_bravo.person  # one identity on the platform
    assert Person.objects.filter(phone="+250788111222").count() == 1
    assert at_alpha.person.full_name == "Eric Mugisha"  # the shared record was not overwritten

    assert Member.objects.for_gym(alpha.gym).get().full_name == "Eric Mugisha"
    assert Member.objects.for_gym(bravo.gym).get().full_name == "E. Mugisha (Bravo)"
    assert Member.objects.for_gym(bravo.gym).get().notes == ""


def test_staff_of_one_gym_can_be_a_member_of_another(alpha, bravo):
    member = register(bravo, name="Aline U.", phone="0788000001")  # Alpha's owner

    assert member.person == alpha.person


# --- Editing ----------------------------------------------------------------


def test_editing_changes_the_gyms_details_and_records_which_fields(alpha):
    member = register(alpha)

    services.update_member(
        by=alpha.assignment,
        member=member,
        details=MemberDetails(
            full_name="Eric M. Mugisha", gender="male", emergency_contact_phone="0788 999 000"
        ),
    )

    member.refresh_from_db()
    assert member.full_name == "Eric M. Mugisha"
    assert member.emergency_contact_phone == "+250788999000"
    assert member.person.full_name == "Eric Mugisha"  # platform identity untouched
    entry = AuditEntry.objects.for_gym(alpha.gym).get(action="member.updated")
    assert sorted(entry.details["changed"]) == ["emergency_contact_phone", "full_name", "gender"]
    assert "+250788999000" not in str(entry.details)


def test_saving_a_member_without_changes_records_nothing(alpha):
    member = register(alpha)

    services.update_member(
        by=alpha.assignment, member=member, details=MemberDetails(full_name="Eric Mugisha")
    )

    assert not AuditEntry.objects.filter(action="member.updated").exists()


def test_staff_cannot_edit_another_gyms_member(alpha, bravo):
    theirs = register(bravo)

    with pytest.raises(PermissionDenied):
        services.update_member(
            by=alpha.assignment, member=theirs, details=MemberDetails(full_name="Changed")
        )


# --- Searching --------------------------------------------------------------


@pytest.fixture
def three_members(alpha):
    register(alpha, name="Eric Mugisha", phone="0788111222")
    register(alpha, name="Claudine Mukamana", phone="0722333444")
    register(alpha, name="Patrick Niyonzima", phone="0733555666")
    return alpha


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", ["Claudine Mukamana", "Eric Mugisha", "Patrick Niyonzima"]),
        ("mu", ["Claudine Mukamana", "Eric Mugisha"]),
        ("ERIC", ["Eric Mugisha"]),
        ("0788", ["Eric Mugisha"]),
        ("0788 111", ["Eric Mugisha"]),
        ("+250722", ["Claudine Mukamana"]),
        ("555666", ["Patrick Niyonzima"]),
        ("nobody", []),
    ],
)
def test_search_by_part_of_a_name_or_phone(three_members, query, expected):
    found = services.search_members(three_members.gym, query)

    assert [m.full_name for m in found] == expected


# --- Screens ----------------------------------------------------------------


def test_register_through_the_form_then_see_the_member_page(alpha, sign_in):
    client = sign_in(alpha)

    form_page = client.get(reverse("members:member_register")).content.decode()
    assert 'name="phone"' in form_page
    assert 'name="branch"' not in form_page  # one branch, so the question is not asked

    response = client.post(
        reverse("members:member_register"),
        {"full_name": "Eric Mugisha", "phone": "0788 111 222", "gender": "male"},
        follow=True,
    )

    page = response.content.decode()
    assert "Eric Mugisha was registered." in page
    assert "0788 111 222" in page  # shown the local way; stored as +250788111222
    assert "Main branch" in page


def test_the_form_asks_for_a_branch_when_there_are_several(alpha, sign_in):
    remera = gym_services.add_branch(gym=alpha.gym, name="Remera")
    client = sign_in(alpha)

    response = client.post(
        reverse("members:member_register"),
        {"full_name": "Eric Mugisha", "phone": "0788111222", "branch": str(remera.public_id)},
    )

    assert response.status_code == 302
    assert Member.objects.for_gym(alpha.gym).get().branch == remera


def test_another_gyms_branch_cannot_be_submitted(alpha, bravo, sign_in):
    gym_services.add_branch(gym=alpha.gym, name="Remera")
    their_branch = Branch.objects.for_gym(bravo.gym).get()
    client = sign_in(alpha)

    response = client.post(
        reverse("members:member_register"),
        {"full_name": "Eric", "phone": "0788111222", "branch": str(their_branch.public_id)},
    )

    assert response.status_code == 200  # the form again, with an error
    assert not Member.objects.exists()


def test_a_duplicate_phone_shows_a_warning_with_a_link_to_the_member(alpha, sign_in):
    existing = register(alpha)
    client = sign_in(alpha)

    response = client.post(
        reverse("members:member_register"), {"full_name": "Another Name", "phone": "0788111222"}
    )

    page = response.content.decode()
    assert "This phone number already belongs to Eric Mugisha." in page
    assert reverse("members:member_detail", args=[existing.public_id]) in page
    assert Member.objects.for_gym(alpha.gym).count() == 1


def test_an_invalid_phone_is_explained_on_the_form(alpha, sign_in):
    client = sign_in(alpha)

    response = client.post(
        reverse("members:member_register"), {"full_name": "Eric", "phone": "12345"}
    )

    assert "Enter a valid phone number, for example 0788 123 456." in response.content.decode()


def test_edit_through_the_form(alpha, sign_in):
    member = register(alpha)
    client = sign_in(alpha)
    url = reverse("members:member_edit", args=[member.public_id])

    edit_page = client.get(url).content.decode()
    assert 'value="Eric Mugisha"' in edit_page
    assert 'name="phone"' not in edit_page  # the phone cannot be changed here

    response = client.post(
        url, {"full_name": "Eric M. Mugisha", "notes": "Prefers mornings"}, follow=True
    )

    page = response.content.decode()
    assert "Changes saved." in page
    assert "Eric M. Mugisha" in page and "Prefers mornings" in page


def test_the_list_shows_members_and_an_empty_state(alpha, sign_in):
    client = sign_in(alpha)
    assert "No members yet" in client.get(reverse("frontdesk:people")).content.decode()

    register(alpha)
    page = client.get(reverse("frontdesk:people")).content.decode()
    assert "Eric Mugisha" in page and "1 person" in page


def test_searching_from_the_page_and_from_the_live_search_box(three_members, sign_in):
    client = sign_in(three_members)
    url = reverse("frontdesk:people")

    full_page = client.get(url, {"q": "0722"}).content.decode()
    assert "Claudine Mukamana" in full_page and "Eric Mugisha" not in full_page
    assert "<html" in full_page

    # The search box (HTMX) receives only the results, not a whole page.
    part = client.get(url, {"q": "eric"}, headers={"HX-Request": "true"}).content.decode()
    assert "Eric Mugisha" in part and "Claudine" not in part
    assert "<html" not in part

    nothing = client.get(url, {"q": "zzz"}).content.decode()
    assert "Nobody matches" in nothing


def test_long_lists_are_split_into_pages(alpha, sign_in):
    for number in range(30):
        register(alpha, name=f"Member {number:02d}", phone=f"07881000{number:02d}")
    client = sign_in(alpha)

    first = client.get(reverse("frontdesk:people")).content.decode()
    second = client.get(reverse("frontdesk:people"), {"page": 2}).content.decode()

    assert "Member 00" in first and "Member 29" not in first
    assert "Page 1 of 2" in first
    assert "Member 29" in second


def test_text_typed_by_staff_is_shown_as_text_not_run_as_code(alpha, sign_in):
    member = register(alpha, name="<script>alert(1)</script>", notes="<b>bold</b>")
    client = sign_in(alpha)

    page = client.get(reverse("members:member_detail", args=[member.public_id])).content.decode()

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert "<b>bold</b>" not in page


def test_phone_numbers_are_shown_the_local_way():
    from apps.core.templatetags.phones import phone

    assert phone("+250788123456") == "0788 123 456"
    assert phone("+254712345678") == "+254712345678"  # another country: left as stored
    assert phone("") == "" and phone(None) == ""


def test_money_is_shown_with_thousands_separators():
    from apps.core.templatetags.money import rwf

    assert rwf(30000) == "RWF 30,000"
    assert rwf(280000) == "RWF 280,000"
    assert rwf(None) == ""
