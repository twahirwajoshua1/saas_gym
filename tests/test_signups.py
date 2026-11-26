"""
Signing up on your own phone.

Nothing is a membership until somebody pays. The page puts a person on the
desk's list with a short code; the receptionist takes the money and the
membership starts then. That is the honest order while the system cannot take
money by itself.
"""

import pytest
from django.urls import reverse

from apps.billing.models import Invoice, Payment
from apps.gyms.models import Role
from apps.members import services
from apps.members.models import Member, Membership, SignUp, SignUpStatus
from apps.members.services import MemberDetails
from apps.modules import services as module_services
from apps.modules.catalog import ATTENDANCE_ACCESS, PAYMENTS_FINANCE

pytestmark = pytest.mark.django_db


@pytest.fixture
def gym(make_gym):
    staff = make_gym("Umucyo Wellness", "0788000001", "Aline Uwase")
    for code in (PAYMENTS_FINANCE, ATTENDANCE_ACCESS):
        module_services.enable_module(gym=staff.gym, code=code)
    return staff


@pytest.fixture
def gym_service(gym):
    return services.create_service(by=gym.assignment, name="Gym", price_rwf=5000)


@pytest.fixture
def monthly(gym, gym_service):
    return services.create_package(
        by=gym.assignment,
        name="Gym, monthly",
        price_rwf=50000,
        services=[gym_service],
        duration_count=1,
        duration_unit="month",
    )


@pytest.fixture
def desk(gym, add_staff):
    return add_staff(gym.gym, "0788000007", Role.RECEPTIONIST, "Gisele Mugwaneza")


def join_form(monthly, **extra):
    form = {
        "full_name": "Grace Kamanzi",
        "phone": "0788123456",
        "gender": "female",
        "date_of_birth": "1996-03-14",
        "emergency_contact_name": "Jeanne Kamanzi",
        "emergency_contact_phone": "0788765432",
        "package": str(monthly.public_id),
        "agrees": "on",
    }
    form.update(extra)
    return form


# --- The public page ---------------------------------------------------------


def test_the_sign_up_page_is_open_to_anybody_and_lists_what_is_on_sale(gym, monthly, client):
    page = client.get(reverse("members:join", args=[gym.gym.public_id])).content.decode()

    assert "Join the gym" in page
    assert "Gym, monthly" in page
    assert "only used for my membership" in page
    assert "Pay at the reception" in page


def test_filling_it_in_gives_a_code_and_makes_nobody_a_member(gym, monthly, client):
    response = client.post(
        reverse("members:join", args=[gym.gym.public_id]), join_form(monthly), follow=True
    )

    page = response.content.decode()
    signup = SignUp.objects.get()
    assert signup.status == SignUpStatus.WAITING
    assert signup.code in page and len(signup.code) == 4
    assert signup.phone == "+250788123456"
    assert signup.package_name == "Gym, monthly" and signup.price_rwf == 50000
    assert signup.wants_messages is False  # it was not ticked
    assert not Member.objects.exists()
    assert not Membership.objects.exists()


def test_the_offers_box_is_a_separate_choice(gym, monthly, client):
    client.post(
        reverse("members:join", args=[gym.gym.public_id]),
        join_form(monthly, wants_messages="on"),
    )

    assert SignUp.objects.get().wants_messages is True


def test_the_needed_agreement_cannot_be_skipped(gym, monthly, client):
    form = join_form(monthly)
    del form["agrees"]

    response = client.post(reverse("members:join", args=[gym.gym.public_id]), form)

    assert response.status_code == 200
    assert not SignUp.objects.exists()


def test_filling_it_in_twice_shows_the_first_code_again(gym, monthly, client):
    url = reverse("members:join", args=[gym.gym.public_id])
    client.post(url, join_form(monthly))

    client.post(url, join_form(monthly), follow=True)

    assert SignUp.objects.count() == 1


def test_somebody_who_is_already_a_member_is_sent_to_reception(gym, monthly, client):
    services.register_member(
        by=gym.assignment,
        phone="0788123456",
        details=MemberDetails(full_name="Grace Kamanzi"),
    )

    page = client.post(
        reverse("members:join", args=[gym.gym.public_id]), join_form(monthly)
    ).content.decode()

    assert "already a member" in page
    assert not SignUp.objects.exists()


def test_another_gyms_package_cannot_be_chosen(gym, monthly, make_gym, client):
    other = make_gym("Bravo Fitness", "0788000002", "Bosco Kamanzi")
    their_service = services.create_service(by=other.assignment, name="Gym", price_rwf=5000)
    theirs = services.create_package(
        by=other.assignment,
        name="Theirs",
        price_rwf=1000,
        services=[their_service],
        duration_count=1,
        duration_unit="month",
    )

    response = client.post(
        reverse("members:join", args=[gym.gym.public_id]),
        join_form(monthly, package=str(theirs.public_id)),
    )

    assert response.status_code == 200
    assert not SignUp.objects.exists()


def test_a_closed_gym_has_no_sign_up_page(gym, monthly, client):
    gym.gym.is_active = False
    gym.gym.save()

    assert client.get(reverse("members:join", args=[gym.gym.public_id])).status_code == 404


# --- The desk ----------------------------------------------------------------


def waiting_signup(gym, monthly):
    return services.create_signup(
        gym.gym,
        full_name="Grace Kamanzi",
        phone="0788123456",
        package=monthly,
        date_of_birth=None,
        wants_messages=True,
    )


def test_the_desk_sees_who_is_waiting_to_pay(gym, monthly, desk, sign_in):
    signup = waiting_signup(gym, monthly)

    page = sign_in(desk).get(reverse("frontdesk:desk")).content.decode()

    assert "Waiting for payment" in page
    assert "Grace Kamanzi" in page and signup.code in page
    assert "Record payment" in page


def test_taking_the_money_registers_the_member_and_starts_the_membership(
    gym, monthly, desk, sign_in
):
    signup = waiting_signup(gym, monthly)
    client = sign_in(desk)

    response = client.post(
        reverse("frontdesk:signup_accept", args=[signup.public_id]),
        {
            "pay_now": "full",
            "method": "cash",
            "idempotency_key": "11111111-1111-4111-8111-111111111111",
        },
        follow=True,
    )

    assert response.status_code == 200
    member = Member.objects.get()
    assert member.full_name == "Grace Kamanzi"
    assert Membership.objects.get().member_id == member.pk
    assert Payment.objects.get().amount_rwf == 50000
    assert Invoice.objects.count() == 1
    signup.refresh_from_db()
    assert signup.status == SignUpStatus.JOINED
    assert signup.member_id == member.pk


def test_the_same_sign_up_cannot_be_paid_for_twice(gym, monthly, desk, sign_in):
    signup = waiting_signup(gym, monthly)
    client = sign_in(desk)
    form = {
        "pay_now": "full",
        "method": "cash",
        "idempotency_key": "22222222-2222-4222-8222-222222222222",
    }
    client.post(reverse("frontdesk:signup_accept", args=[signup.public_id]), form)

    client.post(reverse("frontdesk:signup_accept", args=[signup.public_id]), form, follow=True)

    assert Member.objects.count() == 1
    assert Membership.objects.count() == 1
    assert Payment.objects.count() == 1


def test_somebody_who_never_came_is_taken_off_the_list(gym, monthly, desk, sign_in):
    signup = waiting_signup(gym, monthly)

    sign_in(desk).post(reverse("frontdesk:signup_drop", args=[signup.public_id]))

    signup.refresh_from_db()
    assert signup.status == SignUpStatus.DROPPED
    assert not Member.objects.exists()


def test_another_gym_never_sees_the_sign_up(gym, monthly, make_gym, sign_in):
    waiting_signup(gym, monthly)
    other = make_gym("Bravo Fitness", "0788000002", "Bosco Kamanzi")
    module_services.enable_module(gym=other.gym, code=PAYMENTS_FINANCE)
    module_services.enable_module(gym=other.gym, code=ATTENDANCE_ACCESS)

    page = sign_in(other).get(reverse("frontdesk:desk")).content.decode()

    assert "Grace Kamanzi" not in page


def test_a_gym_with_several_branches_asks_which_one(gym, monthly, client):
    """One place to train is not worth asking about; a choice of three is."""
    from apps.gyms import services as gym_services

    kacyiru = gym_services.create_branch(by=gym.assignment, name="Kacyiru")
    url = reverse("members:join", args=[gym.gym.public_id])

    page = client.get(url).content.decode()
    assert "Which one will you use?" in page

    client.post(url, join_form(monthly, branch=str(kacyiru.public_id)))

    assert SignUp.objects.get().branch_id == kacyiru.pk


def test_the_branch_they_picked_is_where_they_are_registered(gym, monthly, desk, sign_in):
    from apps.gyms import services as gym_services

    kacyiru = gym_services.create_branch(by=gym.assignment, name="Kacyiru")
    signup = services.create_signup(
        gym.gym,
        full_name="Grace Kamanzi",
        phone="0788123456",
        package=monthly,
        branch=kacyiru,
    )

    sign_in(desk).post(
        reverse("frontdesk:signup_accept", args=[signup.public_id]),
        {
            "pay_now": "full",
            "method": "cash",
            "idempotency_key": "33333333-3333-4333-8333-333333333333",
        },
    )

    assert Member.objects.get().branch_id == kacyiru.pk
