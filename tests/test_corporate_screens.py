"""
The corporate wellness screens, walked through as each person would use them,
with the restricted database role switched on (as in real use).
"""

import io

import pytest
from django.test import Client
from django.urls import reverse
from openpyxl import Workbook

from apps.accounts.models import User
from apps.corporate import services as corp
from apps.corporate.models import Contract, CorporateVisit, Employee
from apps.corporate.services import EmployeeDetails
from apps.gyms.models import Branch, Role
from apps.members import services as member_services
from apps.members.models import Service
from apps.modules import services as module_services
from apps.modules.catalog import ATTENDANCE_ACCESS, CORPORATE_WELLNESS, PAYMENTS_FINANCE
from tests.conftest import TEST_PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture
def gym(make_gym):
    staff = make_gym("Alpha Fitness", "0788000001", "Aline Uwase")
    for code in (PAYMENTS_FINANCE, ATTENDANCE_ACCESS, CORPORATE_WELLNESS):
        module_services.enable_module(gym=staff.gym, code=code)
    member_services.create_service(by=staff.assignment, name="Gym", price_rwf=3000)
    member_services.create_service(by=staff.assignment, name="Sauna", price_rwf=5000)
    return staff


@pytest.fixture
def company():
    created = corp.create_company(
        name="Kigali Bank", tin="", officer_name="Diane", officer_phone="0788900001", actor=None
    )
    user = User.objects.get(person=created.officer.person)
    user.set_password(TEST_PASSWORD)
    user.must_change_password = False
    user.save()
    return created


@pytest.fixture
def officer_client(company):
    client = Client()
    client.force_login(User.objects.get(person=company.officer.person))
    response = client.get(reverse("corporate:start"))
    assert response["Location"] == reverse("corporate:portal_home")
    return client


@pytest.fixture
def gym_client(gym, sign_in):
    return sign_in(gym)


def propose_and_approve(gym_client, officer_client, gym, company):
    services = {s.name: s for s in Service.objects.for_gym(gym.gym)}
    branch = Branch.objects.for_gym(gym.gym).first()
    data = {
        "company_code": company.company.code,
        "start_date": "2026-01-01",
        "end_date": "2027-12-31",
        "payment_days": "30",
        f"price_{services['Gym'].public_id}_{branch.public_id}": "2,500",
        f"price_{services['Sauna'].public_id}_{branch.public_id}": "4000",
        "action": "check",
    }
    page = gym_client.post(reverse("corporate:contract_new"), data).content.decode()
    assert "Kigali Bank" in page  # the gym sees which company the code belongs to
    data["action"] = "propose"
    response = gym_client.post(reverse("corporate:contract_new"), data)
    assert response.status_code == 302
    contract = Contract.objects.get()

    home = officer_client.get(reverse("corporate:portal_home")).content.decode()
    assert "waiting for your approval" in home and "Alpha Fitness" in home
    detail = officer_client.get(
        reverse("corporate:portal_contract_detail", args=[contract.public_id])
    )
    assert "2,500" in detail.content.decode()
    officer_client.post(
        reverse("corporate:portal_contract_decide", args=[contract.public_id]),
        {"decision": "approve"},
    )
    contract.refresh_from_db()
    assert contract.status == "approved"
    return contract, services


def test_the_whole_journey(gym, company, gym_client, officer_client):
    # The officer adds an employee and sends them the card link.
    response = officer_client.post(
        reverse("corporate:employee_add"),
        {
            "full_name": "Eric Mugisha",
            "phone": "0788111222",
            "employee_number": "KB-7",
            "department": "IT",
        },
    )
    assert response.status_code == 302
    employee = Employee.objects.get()
    detail = officer_client.get(response["Location"]).content.decode()
    assert "wa.me/250788111222" in detail

    contract, services = propose_and_approve(gym_client, officer_client, gym, company)

    # The employee opens the card on their phone: the phone is now the card's phone.
    phone = Client()
    card_url = reverse("corporate:card", args=[employee.card_token])
    page = phone.get(card_url)
    assert page.status_code == 200 and "Company gym card" in page.content.decode()
    other_phone = Client()
    assert "set up on another phone" in other_phone.get(card_url).content.decode()

    # Reception scans the card from the phone and records gym + sauna.
    link = f"http://testserver{card_url}?c={corp.card_code(employee)}"
    desk = gym_client.get(reverse("corporate:desk"), {"q": link}).content.decode()
    assert "Eric Mugisha" in desk and "Card checked on their phone" in desk
    response = gym_client.post(
        reverse("corporate:desk_check_in", args=[employee.public_id]),
        {
            "code": corp.card_code(employee),
            "service": [str(services["Gym"].public_id), str(services["Sauna"].public_id)],
        },
    )
    assert response.status_code == 302
    visit = CorporateVisit.objects.get()
    assert visit.activities.count() == 2 and visit.confirmed_at is None

    # The employee confirms on their phone.
    page = phone.get(card_url).content.decode()
    assert "Please confirm your visit" in page
    phone.post(reverse("corporate:card_confirm", args=[employee.card_token, visit.public_id]))
    visit.refresh_from_db()
    assert visit.confirmed_at is not None

    # The company sees the visit with day, hour and services, and can question it.
    visits = officer_client.get(reverse("corporate:portal_visits")).content.decode()
    assert "Eric Mugisha" in visits and "Sauna" in visits and "KB-7" in visits
    officer_client.post(
        reverse("corporate:portal_visit_question", args=[visit.public_id]),
        {"message": "Was on leave"},
    )
    warnings = gym_client.get(reverse("corporate:warning_list")).content.decode()
    assert "Was on leave" in warnings

    # Other pages open for both sides.
    for name in ("gym_home", "visit_list", "bill_list"):
        assert gym_client.get(reverse(f"corporate:{name}")).status_code == 200
    for name in ("employee_list", "portal_contracts", "portal_warnings", "portal_bills"):
        assert officer_client.get(reverse(f"corporate:{name}")).status_code == 200


def test_a_printed_card_needs_the_pin_at_the_desk(gym, company, gym_client, officer_client):
    employee = corp.add_employee(
        by=company.officer,
        details=EmployeeDetails(full_name="Grace", phone="0788111555", employee_number="9"),
    )
    _, services = propose_and_approve(gym_client, officer_client, gym, company)
    officer_client.post(
        reverse("corporate:employee_action", args=[employee.public_id]),
        {"action": "pin", "pin": "2468"},
    )
    url = reverse("corporate:desk_check_in", args=[employee.public_id])
    gym_client.post(url, {"service": [str(services["Gym"].public_id)]})
    assert not CorporateVisit.objects.exists()  # no proof
    gym_client.post(url, {"service": [str(services["Gym"].public_id)], "pin": "2468"})
    assert CorporateVisit.objects.get().confirmed_at is not None


def test_uploading_the_excel_list(company, officer_client):
    book = Workbook()
    sheet = book.active
    sheet.append(["Name", "Phone", "Employee number", "Department"])
    sheet.append(["Eric Mugisha", "0788111222", 101, "IT"])
    sheet.append(["Grace Uwera", "0788111555", 102, "Finance"])
    data = io.BytesIO()
    book.save(data)
    data.seek(0)
    data.name = "staff.xlsx"
    response = officer_client.post(reverse("corporate:employee_import"), {"file": data})
    assert response["Location"] == reverse("corporate:employee_import_review")
    review = officer_client.get(response["Location"]).content.decode()
    assert "Grace Uwera" in review
    officer_client.post(reverse("corporate:employee_import_review"))
    assert sorted(Employee.objects.values_list("employee_number", flat=True)) == ["101", "102"]


def test_a_gym_cannot_open_another_companys_pages_and_desk_staff_cannot_make_contracts(
    gym, company, gym_client, add_staff, sign_in
):
    assert gym_client.get(reverse("corporate:portal_home"))["Location"] == reverse(
        "corporate:start"
    )
    desk = add_staff(gym.gym, "0788000007", Role.RECEPTIONIST)
    client = sign_in(desk)
    assert client.get(reverse("corporate:contract_new")).status_code == 403
    assert client.get(reverse("corporate:desk")).status_code == 200


def test_platform_staff_register_a_company(db):
    from apps.accounts import services as account_services

    admin = account_services.create_platform_admin(
        full_name="Platform", phone="0788100000", password=TEST_PASSWORD
    )
    client = Client()
    client.force_login(admin)
    response = client.post(
        reverse("platformadmin:company_create"),
        {"name": "MTN Test", "tin": "", "officer_name": "Jean", "officer_phone": "0788900002"},
    )
    page = response.content.decode()
    assert "CO-" in page and "MTN Test is registered" in page


def test_contract_prices_can_be_filled_with_a_reduction_on_member_prices(
    gym, company, gym_client, officer_client
):
    page = gym_client.post(
        reverse("corporate:contract_new"),
        {
            "company_code": company.company.code,
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "payment_days": "30",
            "reduction": "20",
            "action": "fill",
        },
    ).content.decode()
    assert 'value="2400"' in page and 'value="4000"' in page  # Gym 3,000 and Sauna 5,000, -20%

    contract, _ = propose_and_approve(gym_client, officer_client, gym, company)
    detail = officer_client.get(
        reverse("corporate:portal_contract_detail", args=[contract.public_id])
    ).content.decode()
    assert "Members pay" in detail and "−17%" in detail  # Gym: 2,500 instead of 3,000
