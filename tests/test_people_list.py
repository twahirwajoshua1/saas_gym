"""
The desk's Members page.

At the desk a person is just somebody at the door, so the page shows the gym's
own members and the staff of its companies in one list. Membership tabs
(active, ending soon, ended recently) only apply to members, because company
staff are covered by their company's contract instead.
"""

from datetime import date

import pytest
from django.urls import reverse

from apps.corporate import services as corp
from apps.corporate.services import ContractTerms, EmployeeDetails
from apps.gyms.models import Branch, Role
from apps.members import services as member_services
from apps.members.models import Service
from apps.members.services import MemberDetails
from apps.modules import services as module_services
from apps.modules.catalog import ATTENDANCE_ACCESS, CORPORATE_WELLNESS, PAYMENTS_FINANCE

pytestmark = pytest.mark.django_db


@pytest.fixture
def gym(make_gym):
    staff = make_gym("Umucyo Wellness", "0788000001", "Aline Uwase")
    for code in (PAYMENTS_FINANCE, ATTENDANCE_ACCESS, CORPORATE_WELLNESS):
        module_services.enable_module(gym=staff.gym, code=code)
    member_services.create_service(by=staff.assignment, name="Gym", price_rwf=5000)
    return staff


@pytest.fixture
def desk(gym, add_staff):
    return add_staff(gym.gym, "0788000007", Role.RECEPTIONIST, "Gisele Mugwaneza")


def add_company(gym, name, officer_phone, employees):
    created = corp.create_company(
        name=name,
        tin="",
        officer_name=f"{name} officer",
        officer_phone=officer_phone,
        actor=None,
    )
    branch = Branch.objects.for_gym(gym.gym).first()
    gym_service = Service.objects.for_gym(gym.gym).get(name="Gym")
    contract = corp.propose_contract(
        by=gym.assignment,
        company_code=created.company.code,
        terms=ContractTerms(
            start_date=date(2026, 1, 1),
            end_date=date(2027, 12, 31),
            payment_days=30,
            prices={(gym_service.id, branch.id): 4000},
        ),
    )
    corp.decide_contract(by=created.officer, contract=contract, approve=True)
    for full_name, phone, number, department in employees:
        corp.add_employee(
            by=created.officer,
            details=EmployeeDetails(
                full_name=full_name,
                phone=phone,
                employee_number=number,
                department=department,
            ),
        )
    return created


@pytest.fixture
def people(gym):
    member_services.register_member(
        by=gym.assignment, phone="0788111222", details=MemberDetails(full_name="Grace Kamanzi")
    )
    add_company(
        gym, "EDCL", "0788900001", [("Aline Mukamana", "0788222333", "E-0087", "Customer care")]
    )
    add_company(
        gym, "RTDA", "0788900002", [("Eric Ndayisaba", "0788333444", "R-0203", "Engineering")]
    )


# --- One list ---------------------------------------------------------------


def test_company_staff_appear_beside_the_gyms_own_members(gym, desk, people, sign_in):
    page = sign_in(desk).get(reverse("frontdesk:people")).content.decode()

    assert "Grace Kamanzi" in page
    assert "Aline Mukamana" in page and "EDCL staff" in page
    assert "Eric Ndayisaba" in page and "RTDA staff" in page
    assert "3 people" in page


def test_the_list_can_be_narrowed_to_one_company(gym, desk, people, sign_in):
    client = sign_in(desk)
    edcl = corp.companies_with_contracts(gym.gym).get(name="EDCL")

    page = client.get(
        reverse("frontdesk:people"), {"company": str(edcl.public_id)}
    ).content.decode()

    assert "Aline Mukamana" in page
    assert "Eric Ndayisaba" not in page and "Grace Kamanzi" not in page


def test_the_list_can_be_narrowed_to_the_gyms_own_members(gym, desk, people, sign_in):
    page = (
        sign_in(desk).get(reverse("frontdesk:people"), {"company": "individuals"}).content.decode()
    )

    assert "Grace Kamanzi" in page
    assert "Aline Mukamana" not in page


def test_company_staff_are_left_out_of_the_membership_tabs(gym, desk, people, sign_in):
    """
    "Ending in 7 days" is about memberships. Company staff have none, so
    showing them there would be noise.
    """
    page = sign_in(desk).get(reverse("frontdesk:people"), {"show": "soon"}).content.decode()

    assert "Aline Mukamana" not in page


def test_a_company_staff_row_offers_no_renewal(gym, desk, people, sign_in):
    """Their company's contract renews them; the desk does not sell to them."""
    page = sign_in(desk).get(reverse("frontdesk:people"), {"company": "staff"}).content.decode()

    assert "Aline Mukamana" in page
    assert "Sell membership" not in page and "Renew membership" not in page


def test_searching_finds_a_company_employee_by_name_or_staff_number(gym, desk, people, sign_in):
    client = sign_in(desk)

    by_name = client.get(reverse("frontdesk:people"), {"q": "Ndayisaba"}).content.decode()
    assert "Eric Ndayisaba" in by_name and "Grace Kamanzi" not in by_name

    by_number = client.get(reverse("frontdesk:people"), {"q": "E-0087"}).content.decode()
    assert "Aline Mukamana" in by_number


def test_another_gym_never_sees_these_people(gym, people, make_gym, sign_in):
    other = make_gym("Bravo Fitness", "0788000002", "Bosco Kamanzi")
    for code in (PAYMENTS_FINANCE, ATTENDANCE_ACCESS, CORPORATE_WELLNESS):
        module_services.enable_module(gym=other.gym, code=code)

    page = sign_in(other).get(reverse("frontdesk:people")).content.decode()

    assert "Grace Kamanzi" not in page
    assert "Aline Mukamana" not in page and "EDCL" not in page
