"""
Builds the sample gyms day by day, as if they had used the system for weeks.

Every record is made through the real business rules (the same services the
screens use), with the clock moved back to each simulated day and time. So
invoices, receipts, the ledger, the audit log and check-ins are as consistent
as real use would make them, and the figures on the dashboard add up.
"""

import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import time_machine
from django.db import transaction
from django.utils import timezone

from apps.accounts import services as accounts
from apps.attendance import services as attendance
from apps.billing import services as billing
from apps.billing.services import PaymentInput
from apps.classes import services as classes
from apps.corporate import services as corporate
from apps.corporate.models import CorporateBill
from apps.corporate.services import ContractTerms, EmployeeDetails
from apps.frontdesk import services as frontdesk
from apps.frontdesk.models import ProblemKind, ScanProblem
from apps.gyms import services as gyms
from apps.gyms.models import Branch, Role, StaffAssignment
from apps.massage import services as massage
from apps.members import services as members
from apps.members.models import Gender
from apps.members.services import InvalidDetails, MemberDetails
from apps.modules import services as modules
from apps.modules.catalog import (
    ATTENDANCE_ACCESS,
    CORPORATE_WELLNESS,
    PAYMENTS_FINANCE,
    TRAINING_CLASSES,
)
from apps.people import services as people
from apps.sampledata import rwanda

DEMO_PASSWORD = "Kigali-demo-2026"
KIGALI = ZoneInfo("Africa/Kigali")

# How members behave. Weights are rough guesses at a typical Kigali gym.
KINDS = [("regular", 50), ("dropin", 18), ("long", 20), ("student", 12)]
RENEW_CHANCE = {"regular": 0.75, "student": 0.65, "long": 0.6, "dropin": 0.0}
DAY_FACTOR = [1.0, 1.0, 1.0, 1.0, 0.9, 0.7, 0.35]  # Monday to Sunday


@dataclass
class SimMember:
    member: object
    kind: str
    habit: float
    branch_id: int
    end: date | None = None  # last day of the latest membership
    start: date | None = None
    renews: bool = False
    renew_on: date | None = None
    frozen: list = field(default_factory=list)  # (start, end) pairs


@dataclass
class SimGym:
    spec: dict
    gym: object
    owner: StaffAssignment
    desk: dict  # branch id -> receptionist assignment
    trainers: dict  # branch id -> trainer assignment
    managers: dict  # branch id -> manager assignment
    packages: dict
    people: list = field(default_factory=list)
    arrivals_left: float = 0.0
    arrivals_per_day: float = 0.0
    arrival_carry: float = 0.0
    unpaid: list = field(default_factory=list)  # (due day, invoice, staff)
    branches: dict = field(default_factory=dict)  # branch id -> branch
    services: dict = field(default_factory=dict)  # service name -> Service
    class_types: dict = field(default_factory=dict)  # class name -> ClassType
    trainer_pages: dict = field(default_factory=dict)  # branch id -> TrainerProfile
    massage_types: dict = field(default_factory=dict)  # massage name -> MassageType
    masseuses: dict = field(default_factory=dict)  # branch id -> TrainerProfile


@dataclass
class SimEmployee:
    employee: object
    officer: object
    sim: SimGym
    branch_id: int
    habit: float
    pin: bool


class Generator:
    def __init__(self, *, seed: int, days: int, scale: float, log=print):
        self.rng = random.Random(seed)
        self.days = days
        self.scale = scale
        self.real_now = timezone.localtime().astimezone(KIGALI) - timedelta(minutes=2)
        self.log = log
        self.used_phones = set()
        self.counts = {
            "members": 0,
            "sales": 0,
            "payments": 0,
            "check_ins": 0,
            "freezes": 0,
            "company_visits": 0,
            "company_bills": 0,
        }
        self.employees = []  # SimEmployee
        self.paydays = {}  # bill id -> day the company pays it
        self.logins = []

    # --- Small helpers ---------------------------------------------------------

    def at(self, day: date, hour: int, minute: int = 0):
        """
        Move the clock to a moment on a simulated day (Kigali time). Today only
        runs until now: today's day (05:00-22:00) is squeezed into the hours that
        have really passed, so nothing appears to happen in the future.
        """
        moment = datetime.combine(day, time(hour, minute), KIGALI)
        now = self.real_now
        if moment > now:
            opening = datetime.combine(now.date(), time(5, 0), KIGALI)
            share = max(0.0, (moment - opening) / timedelta(hours=17))
            moment = min(now, opening + (now - opening) * min(share, 1.0))
            moment = max(moment, opening) if now > opening else now
        return time_machine.travel(moment, tick=False)

    def phone(self) -> str:
        while True:
            number = (
                "0" + self.rng.choice(rwanda.PHONE_PREFIXES) + f"{self.rng.randrange(10**7):07d}"
            )
            if number in self.used_phones or people.find_person_by_phone(number):
                continue
            self.used_phones.add(number)
            return number

    def name(self) -> tuple[str, str]:
        if self.rng.random() < 0.5:
            first, gender = self.rng.choice(rwanda.FEMALE_FIRST_NAMES), Gender.FEMALE
        else:
            first, gender = self.rng.choice(rwanda.MALE_FIRST_NAMES), Gender.MALE
        surname = self.rng.choice(rwanda.SURNAMES)
        # Names beginning Nyira- or Muka- are women's names in Kinyarwanda.
        while surname == first or (
            gender == Gender.MALE and surname.startswith(rwanda.FEMALE_ONLY_PREFIXES)
        ):
            surname = self.rng.choice(rwanda.SURNAMES)
        return f"{first} {surname}", gender

    def visit_time(self) -> tuple[int, int]:
        roll = self.rng.random()
        if roll < 0.4:
            minutes = self.rng.randint(5 * 60 + 30, 8 * 60 + 30)
        elif roll < 0.55:
            minutes = self.rng.randint(11 * 60 + 30, 14 * 60)
        else:
            minutes = self.rng.randint(16 * 60 + 30, 20 * 60 + 30)
        return divmod(minutes, 60)

    def payment_for(self, price: int) -> PaymentInput | None:
        roll = self.rng.random()
        method = self.rng.choices(
            ["mtn_momo", "cash", "airtel_money", "bank_transfer"], [60, 28, 9, 3]
        )[0]
        if method in ("mtn_momo", "airtel_money"):
            reference = f"{self.rng.randrange(10**10, 10**11)}"
        elif method == "bank_transfer":
            reference = f"FT{self.rng.randrange(10**9, 10**10)}"
        else:
            reference = ""
        if roll < 0.80:
            return PaymentInput(method=method, reference=reference)
        if roll < 0.92:
            part = max(1000, round(price * self.rng.uniform(0.3, 0.7) / 1000) * 1000)
            return PaymentInput(
                method=method, amount_rwf=min(part, price - 1000), reference=reference
            )
        return None

    def login(self, person, role_label: str, gym_name: str):
        accounts.create_user_account(person=person, password=DEMO_PASSWORD)
        self.logins.append((gym_name, role_label, person.full_name, person.phone))

    # --- Setting up a gym -------------------------------------------------------

    def staff(self, sim_gym, role, branch, label) -> StaffAssignment:
        full_name, _ = self.name()
        person = people.create_person(full_name=full_name, phone=self.phone())
        self.login(person, label, sim_gym.gym.name)
        return gyms.assign_staff(
            gym=sim_gym.gym, person=person, role=role, branch=branch, actor=sim_gym.owner.person
        )

    def create_gym(self, spec: dict, day: date, admin_person) -> SimGym:
        first, surname, _gender = spec["owner"]
        owner_person = people.find_person_by_phone(spec["owner_phone"]) or people.create_person(
            full_name=f"{first} {surname}", phone=spec["owner_phone"]
        )
        self.used_phones.add(spec["owner_phone"])
        gym = gyms.create_gym(name=spec["name"], owner=owner_person, actor=admin_person)
        owner = StaffAssignment.objects.for_gym(gym).select_related("gym", "person").get()
        self.login(owner_person, "Owner", gym.name)

        main = Branch.objects.for_gym(gym).get()
        gyms.rename_branch(by=owner, branch=main, name=spec["branches"][0])
        branches = [main] + [gyms.create_branch(by=owner, name=n) for n in spec["branches"][1:]]

        for code in (PAYMENTS_FINANCE, ATTENDANCE_ACCESS, TRAINING_CLASSES):
            modules.enable_module(gym=gym, code=code, actor=admin_person)

        sim_services = {
            name: members.create_service(by=owner, name=name, price_rwf=price)
            for name, price in rwanda.SERVICES.get(spec["name"], [])
        }
        packages = {
            name: members.create_package(
                by=owner, name=name, price_rwf=price, duration_count=count, duration_unit=unit
            )
            for name, price, count, unit in spec["packages"]
        }
        self.add_shaped_packages(owner, sim_services, packages)
        sim = SimGym(spec, gym, owner, {}, {}, {}, packages)
        sim.branches = {b.id: b for b in branches}
        sim.services = sim_services
        many = len(branches) > 1
        for branch in branches:
            if many:
                sim.managers[branch.id] = self.staff(
                    sim, Role.BRANCH_MANAGER, branch, f"Branch manager, {branch.name}"
                )
            sim.desk[branch.id] = self.staff(
                sim, Role.RECEPTIONIST, branch, f"Receptionist, {branch.name}"
            )
            sim.trainers[branch.id] = self.staff(
                sim, Role.TRAINER, branch, f"Trainer, {branch.name}"
            )
        if len(branches) >= 3:
            self.staff(sim, Role.SALES_REP, None, "Sales representative")
        self.set_up_classes(sim)

        total = max(3, round(spec["members"] * self.scale))
        existing = round(total * 0.35)
        sim.arrivals_left = total - existing
        sim.arrivals_per_day = sim.arrivals_left / max(1, self.days)
        for _ in range(existing):
            self.bring_in_existing(sim, branches, day)
        return sim

    # --- Members ----------------------------------------------------------------

    def new_member(self, sim: SimGym, by: StaffAssignment, branch_id: int):
        full_name, gender = self.name()
        contact_name, contact_phone = "", ""
        if self.rng.random() < 0.4:
            contact_name, contact_phone = self.name()[0], self.phone()
        details = MemberDetails(
            full_name=full_name,
            gender=gender,
            branch=sim.branches[branch_id],
            emergency_contact_name=contact_name,
            emergency_contact_phone=contact_phone,
        )
        member = members.register_member(by=by, phone=self.phone(), details=details)
        kind = self.rng.choices([k for k, _ in KINDS], [w for _, w in KINDS])[0]
        sim_member = SimMember(member, kind, self.rng.uniform(0.2, 0.7), branch_id)
        sim.people.append(sim_member)
        self.counts["members"] += 1
        if self.rng.random() < 0.35:
            members.create_card_link(by=by, member=member)
        return sim_member

    def package_for(self, sim: SimGym, kind: str):
        p = sim.packages
        if kind == "dropin":
            return p["Daily pass"]
        if kind == "student":
            return p.get("Student monthly", p["Monthly"])
        if kind == "long":
            longer = [n for n in ("Quarterly", "Annual") if n in p]
            return p[self.rng.choice(longer)] if longer else p["Monthly"]
        if "Weekly" in p and self.rng.random() < 0.15:
            return p["Weekly"]
        return p["Monthly"]

    def bring_in_existing(self, sim: SimGym, branches, day: date):
        branch = self.rng.choice(branches)
        with self.at(day, 9, self.rng.randint(0, 59)):
            sm = self.new_member(sim, sim.owner, branch.id)
            if sm.kind == "dropin":
                sm.kind = "regular"
            package = self.package_for(sim, sm.kind)
            valid_until = day + timedelta(days=self.rng.randint(1, 45))
            membership = members.record_existing_membership(
                by=sim.owner, member=sm.member, package=package, valid_until=valid_until
            )
        self.after_membership(sm, membership.start_date, membership.end_date)

    def after_membership(self, sm: SimMember, start: date, end: date):
        sm.start, sm.end = start, end
        sm.renews = self.rng.random() < RENEW_CHANCE[sm.kind]
        sm.renew_on = end + timedelta(days=self.rng.randint(-3, 4)) if sm.renews else None

    def sell(self, sim: SimGym, sm: SimMember, by: StaffAssignment, day: date, hour: int):
        package = self.package_for(sim, sm.kind)
        payment = self.payment_for(package.price_rwf)
        with self.at(day, hour, self.rng.randint(0, 59)):
            sale = billing.sell_membership(
                by=by,
                member=sm.member,
                package=package,
                start_date=members.next_start_date(sm.member),
                idempotency_key=uuid.uuid4(),
                payment=payment,
            )
        self.counts["sales"] += 1
        if sale.payment:
            self.counts["payments"] += 1
            self.maybe_fix_a_mistake(sim, sale, by, day)
        if billing.outstanding_on(sale.invoice) > 0 and self.rng.random() < 0.75:
            sim.unpaid.append((day + timedelta(days=self.rng.randint(2, 20)), sale.invoice, by))
        self.after_membership(sm, sale.membership.start_date, sale.membership.end_date)

    def maybe_fix_a_mistake(self, sim, sale, by, day):
        """About 1 payment in 100 is recorded wrongly and corrected by the owner."""
        if self.rng.random() > 0.01 or sale.payment.method != "cash":
            return
        with self.at(day, 20, 45):
            billing.reverse_payment(
                by=sim.owner,
                payment=sale.payment,
                reason="Recorded as cash, but it was paid by MTN MoMo",
            )
            billing.record_payment(
                by=by,
                invoice=sale.invoice,
                payment=PaymentInput(
                    method="mtn_momo",
                    amount_rwf=sale.payment.amount_rwf,
                    reference=f"{self.rng.randrange(10**10, 10**11)}",
                ),
                idempotency_key=uuid.uuid4(),
            )
        self.counts["payments"] += 1

    # --- One simulated day ------------------------------------------------------

    def is_valid(self, sm: SimMember, day: date) -> bool:
        if sm.end is None or not (sm.start <= day <= sm.end):
            return False
        return not any(a <= day <= b for a, b in sm.frozen)

    def simulate_day(self, sim: SimGym, day: date):
        branches = list(sim.desk)

        # New members arrive spread over the whole period: each day adds a share
        # (more on some days, fewer on others) and whole people walk in.
        sim.arrival_carry += sim.arrivals_per_day * self.rng.uniform(0.3, 1.7)
        while sim.arrivals_left >= 1 and sim.arrival_carry >= 1:
            sim.arrivals_left -= 1
            sim.arrival_carry -= 1
            branch_id = self.rng.choice(branches)
            hour = self.rng.randint(8, 19)
            with self.at(day, hour, self.rng.randint(0, 59)):
                sm = self.new_member(sim, sim.desk[branch_id], branch_id)
            if sm.kind != "dropin":
                self.sell(sim, sm, sim.desk[branch_id], day, hour)

        for sm in sim.people:
            if sm.renews and sm.renew_on == day:
                self.sell(sim, sm, sim.desk[sm.branch_id], day, self.rng.randint(7, 19))

        still_unpaid = []
        for due, invoice, by in sim.unpaid:
            if due != day:
                still_unpaid.append((due, invoice, by))
                continue
            owed = billing.outstanding_on(invoice)
            if owed > 0:
                with self.at(day, self.rng.randint(8, 19), self.rng.randint(0, 59)):
                    billing.record_payment(
                        by=by,
                        invoice=invoice,
                        payment=PaymentInput(
                            method="mtn_momo",
                            amount_rwf=owed,
                            reference=f"{self.rng.randrange(10**10, 10**11)}",
                        ),
                        idempotency_key=uuid.uuid4(),
                    )
                self.counts["payments"] += 1
        sim.unpaid = still_unpaid

        for sm in sim.people:
            self.maybe_freeze(sim, sm, day)
            self.maybe_visit(sim, sm, day)

    def maybe_freeze(self, sim: SimGym, sm: SimMember, day: date):
        if sm.kind not in ("long", "regular") or not self.is_valid(sm, day) or sm.frozen:
            return
        if (sm.end - day).days < 20 or self.rng.random() > 0.002:
            return
        length = self.rng.randint(7, 21)
        by = sim.managers.get(sm.branch_id, sim.owner)
        membership = members.current_membership(sm.member, on=day)
        if membership is None:
            return
        with self.at(day, 10, 15):
            freeze = members.freeze_membership(
                by=by,
                membership=membership,
                start=day,
                days=length,
                reason=self.rng.choice(rwanda.FREEZE_REASONS),
            )
        sm.frozen.append((freeze.start_date, freeze.end_date))
        membership.refresh_from_db()
        sm.end = membership.end_date
        if sm.renew_on:
            sm.renew_on += timedelta(days=length)
        self.counts["freezes"] += 1

    def maybe_visit(self, sim: SimGym, sm: SimMember, day: date):
        chance = sm.habit * DAY_FACTOR[day.weekday()]
        if sm.kind == "dropin":
            chance = 0.22 * DAY_FACTOR[day.weekday()]
        elif sm.end and not sm.renews and (sm.end - day).days < 20:
            chance *= 0.15  # members who will not renew stop coming first
        if self.rng.random() > chance:
            return
        hour, minute = self.visit_time()
        desk = sim.desk[sm.branch_id]
        if sm.kind == "dropin" and not self.is_valid(sm, day):
            self.sell(sim, sm, desk, day, max(6, min(hour, 20)))
        if not self.is_valid(sm, day):
            return
        by = desk if self.rng.random() < 0.8 else sim.trainers[sm.branch_id]
        # Most people scan themselves in at the entrance tablet; the rest at the desk.
        use_tablet = day >= self.tablets_from and self.rng.random() < 0.75
        with self.at(day, hour, minute):
            try:
                if use_tablet:
                    record, _new = attendance.self_check_in(
                        gym=sim.gym, branch=sim.branches[sm.branch_id], member=sm.member
                    )
                else:
                    record, _new = attendance.check_in(by=by, member=sm.member)
            except attendance.NotAllowedIn:
                return
            self.record_what_they_did(sim, by, record, use_tablet)
        self.counts["check_ins"] += 1

    def record_what_they_did(self, sim, by, record, use_tablet):
        """
        What the member said they were doing. Most people tick one thing; some
        tick two. Only what their package covers can be ticked at all.
        """
        choices = attendance.choices_for(record.member, record.checked_in_on)
        if not choices:
            return
        wanted = [choices[0].service]
        if len(choices) > 1 and self.rng.random() < 0.35:
            wanted.append(self.rng.choice(choices[1:]).service)
        try:
            if use_tablet:
                attendance.record_self_activities(
                    gym=sim.gym, check_in=record, services_chosen=wanted
                )
            else:
                attendance.record_activities(by=by, check_in=record, services_chosen=wanted)
        except InvalidDetails:
            return

    # --- Companies (corporate wellness) -------------------------------------------

    def create_company(self, spec: dict, sims: dict, start: date, admin):
        first, surname = spec["officer"]
        created = corporate.create_company(
            name=spec["name"],
            tin=spec["tin"],
            officer_name=f"{first} {surname}",
            officer_phone=spec["officer_phone"],
            actor=admin,
        )
        self.used_phones.add(spec["officer_phone"])
        user = created.officer.person.user_account
        user.set_password(DEMO_PASSWORD)
        user.must_change_password = False
        user.save()
        self.logins.append(
            (spec["name"], "Logistics officer (company)", user.person.full_name, user.person.phone)
        )
        officer = created.officer

        contracts = []
        for gym_name in spec["gyms"]:
            sim = sims[gym_name]
            if not modules.is_enabled(sim.gym, CORPORATE_WELLNESS):
                modules.enable_module(gym=sim.gym, code=CORPORATE_WELLNESS, actor=admin)
            prices = {}
            for service in sim.services.values():
                for branch in sim.branches.values():
                    share = spec["discount"] * (0.85 if branch.name in rwanda.OUTSIDE_KIGALI else 1)
                    prices[(service.id, branch.id)] = int(round(service.price_rwf * share, -2))
            contract = corporate.propose_contract(
                by=sim.owner,
                company_code=created.company.code,
                terms=ContractTerms(
                    start_date=start,
                    end_date=start + timedelta(days=365),
                    payment_days=self.rng.choice([15, 30, 45]),
                    prices=prices,
                ),
            )
            corporate.decide_contract(by=officer, contract=contract, approve=True)
            contracts.append((sim, contract))

        for number in range(max(3, round(spec["employees"] * self.scale))):
            full_name, _gender = self.name()
            employee = corporate.add_employee(
                by=officer,
                details=EmployeeDetails(
                    full_name=full_name,
                    phone=self.phone(),
                    employee_number=f"{spec['name'][:3].upper()}-{1001 + number}",
                    department=self.rng.choice(rwanda.DEPARTMENTS),
                ),
            )
            uses_pin = self.rng.random() < 0.15
            if uses_pin:
                corporate.set_pin(by=officer, employee=employee, pin="1234")
            else:
                employee.device_hash = uuid.uuid4().hex  # opened the card on their phone
                employee.save(update_fields=["device_hash"])
            sim, contract = self.rng.choice(contracts)
            self.employees.append(
                SimEmployee(
                    employee=employee,
                    officer=officer,
                    sim=sim,
                    branch_id=self.rng.choice(list(sim.branches)),
                    habit=self.rng.choice([0.05, 0.12, 0.25, 0.4]),
                    pin=uses_pin,
                )
            )

    def corporate_day(self, day: date):
        for se in self.employees:
            if not se.employee.is_active:
                continue
            if self.rng.random() > se.habit * DAY_FACTOR[day.weekday()]:
                continue
            sim = se.sim
            wanted = [s.id for name, s in sim.services.items() if name == "Gym"]
            for name, service in sim.services.items():
                if name != "Gym" and self.rng.random() < 0.18:
                    wanted.append(service.id)
            if not wanted:
                continue
            hour, minute = self.visit_time()
            use_tablet = day >= self.tablets_from and self.rng.random() < 0.8
            with self.at(day, hour, minute):
                try:
                    if use_tablet:
                        result = corporate.self_check_in_employee(
                            gym=sim.gym,
                            branch=sim.branches[se.branch_id],
                            employee=se.employee,
                            service_ids=wanted,
                            phone_code_ok=not se.pin,
                            pin="1234" if se.pin else "",
                        )
                    else:
                        result = corporate.check_in_employee(
                            by=sim.desk[se.branch_id],
                            employee=se.employee,
                            service_ids=wanted,
                            phone_code_ok=not se.pin,
                            pin="1234" if se.pin else "",
                        )
                except InvalidDetails:  # for example a service not covered at that branch
                    continue
            self.counts["company_visits"] += 1
            if self.rng.random() < 0.004:
                with self.at(day, 21, 0):
                    corporate.question_visit(
                        by=se.officer,
                        visit=result.visit,
                        message="This employee was on leave that day.",
                    )

        # The company's leavers come off the list now and then.
        if self.employees and self.rng.random() < 0.03:
            se = self.rng.choice(self.employees)
            if se.employee.is_active:
                with self.at(day, 10, 0):
                    corporate.remove_employee(by=se.officer, employee=se.employee)

        # Bills come on the last evening of the month; companies pay later.
        with self.at(day, 23, 30):
            for bill in corporate.issue_due_bills(day):
                self.counts["company_bills"] += 1
                if self.rng.random() < 0.85:
                    self.paydays[bill.id] = day + timedelta(days=self.rng.randint(10, 40))
        for bill_id, payday in list(self.paydays.items()):
            if payday != day:
                continue
            bill = CorporateBill.objects.select_related("gym").get(pk=bill_id)
            owner = StaffAssignment.objects.for_gym(bill.gym).filter(role=Role.OWNER).first()
            with self.at(day, 11, 0):
                corporate.record_bill_payment(
                    by=owner,
                    bill=bill,
                    amount_rwf=corporate.bill_state(bill).owed_rwf,
                    reference=f"BK{self.rng.randrange(10**8, 10**9)}",
                    received_on=day,
                    idempotency_key=uuid.uuid4(),
                )
            del self.paydays[bill_id]

    # --- Classes and trainers -----------------------------------------------------

    def add_shaped_packages(self, owner, sim_services, packages):
        """
        A few packages that show the shapes a gym can sell: one service only, a
        combination, and a book of prepaid visits. The plain ones above include
        everything the gym sells; these say exactly what is in them.
        """
        swimming = sim_services.get("Swimming")
        gym_floor = sim_services.get("Gym")
        sauna = sim_services.get("Sauna")
        massage = sim_services.get("Massage")

        if swimming is not None:
            packages["Swimming only, monthly"] = members.create_package(
                by=owner,
                name="Swimming only, monthly",
                price_rwf=swimming.price_rwf * 7,
                services=[swimming],
                duration_count=1,
                duration_unit="month",
            )
            packages["15 swimming tickets"] = members.create_package(
                by=owner,
                name="15 swimming tickets",
                price_rwf=int(swimming.price_rwf * 0.8) * 15,
                kind=members.PackageKind.COUNT,
                services=[swimming],
                visit_count=15,
                duration_count=3,
                duration_unit="month",
            )
        if swimming is not None and gym_floor is not None and sauna is not None:
            packages["Gym + sauna + swimming, monthly"] = members.create_package(
                by=owner,
                name="Gym + sauna + swimming, monthly",
                price_rwf=(gym_floor.price_rwf + sauna.price_rwf + swimming.price_rwf) * 5,
                services=[gym_floor, sauna, swimming],
                duration_count=1,
                duration_unit="month",
            )
        if massage is not None:
            packages["5 massages"] = members.create_package(
                by=owner,
                name="5 massages",
                price_rwf=int(massage.price_rwf * 0.8) * 5,
                kind=members.PackageKind.COUNT,
                services=[massage],
                visit_count=5,
            )

    def set_up_classes(self, sim):
        """
        What each gym teaches, who teaches it, and the coming four weeks of the
        timetable. Members book into it later, as the days are simulated.
        """
        owner = sim.owner
        for name, expect, minutes, places in rwanda.CLASSES:
            sim.class_types[name] = classes.save_class_type(
                by=owner, name=name, what_to_expect=expect, minutes=minutes, places=places
            )
        for branch_id, staff in sim.trainers.items():
            branch = sim.branches[branch_id]
            headline, about = self.rng.choice(rwanda.TRAINER_WORDS)
            profile = classes.save_trainer(by=owner, staff=staff, headline=headline, about=about)
            sim.trainer_pages[branch_id] = profile
            # Free hours for one-to-one sessions, two afternoons a week.
            classes.set_trainer_hours(
                by=owner,
                trainer=profile,
                windows=[
                    (day, time(14, 0), time(17, 0), branch)
                    for day in self.rng.sample(range(0, 5), 2)
                ],
            )
        self.set_up_massage(sim)

    def set_up_massage(self, sim):
        """
        The kinds of massage a gym offers, and who gives them.

        Only gyms that sell massage at all get this. Each kind is also a
        service, so a company can agree its own price for it in a contract.
        """
        if "Massage" not in sim.services:
            return
        owner = sim.owner
        base = sim.services["Massage"].price_rwf
        for name, minutes, share, expect in rwanda.MASSAGE_TYPES:
            made = massage.create_type(
                by=owner,
                name=name,
                minutes=minutes,
                price_rwf=int(round(base * share / 1000 / 500)) * 500,
                what_to_expect=expect,
            )
            sim.massage_types[name] = made
            # Each kind is a service too, so company contracts can price it.
            sim.services[name] = made.service
        massage.set_rules(by=owner, cut_off_minutes=120, gap_minutes=10, reminder_minutes=10)
        # One masseuse per branch, working mornings and late afternoons.
        for branch_id in sim.desk:
            branch = sim.branches[branch_id]
            person = self.staff(sim, Role.TRAINER, branch, f"Masseuse, {branch.name}")
            profile = classes.save_trainer(
                by=owner,
                staff=person,
                headline="Massage therapist",
                about="Sports and deep tissue massage.",
                takes_personal_sessions=False,
                gives_massages=True,
            )
            classes.set_trainer_hours(
                by=owner,
                trainer=profile,
                windows=[(day, time(8, 0), time(18, 0), branch) for day in range(0, 6)],
            )
            sim.masseuses[branch_id] = profile

    def schedule_classes(self, sims):
        """
        Put the timetable up for the coming four weeks. This runs on the real
        clock, after the simulated days, so the classes are genuinely ahead.
        """
        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday())
        for sim in sims:
            for branch_id, profile in sim.trainer_pages.items():
                branch = sim.branches[branch_id]
                for name, weekday, hour in rwanda.TIMETABLE:
                    class_type = sim.class_types.get(name)
                    if class_type is None:
                        continue
                    first = monday + timedelta(days=weekday)
                    if first < today:
                        first += timedelta(weeks=1)
                    classes.add_to_timetable(
                        by=sim.owner,
                        class_type=class_type,
                        branch=branch,
                        first_day=first,
                        at=time(hour, 0),
                        trainer=profile,
                        repeat_weeks=4,
                    )

    def fill_classes(self, sims):
        """Members take places in the classes coming up, and review past ones."""
        today = timezone.localdate()
        for sim in sims:
            upcoming = classes.timetable(sim.gym, today, days=10)
            active = [sm for sm in sim.people if sm.end and sm.end >= today]
            if not active:
                continue
            for session in upcoming:
                wanted = self.rng.randrange(0, session.places + 1)
                for sm in self.rng.sample(active, min(wanted, len(active))):
                    try:
                        classes.book(member=sm.member, session=session)
                    except (classes.ClassFull, classes.TooLate, InvalidDetails):
                        break

    def book_massages(self, sims):
        """
        Massages in the coming days: most accepted, a couple still waiting for
        the desk to answer, so the demonstration shows both.
        """
        today = timezone.localdate()
        for sim in sims:
            if not sim.massage_types:
                continue
            kinds = list(sim.massage_types.values())
            for branch_id, masseuse in sim.masseuses.items():
                branch = sim.branches[branch_id]
                people_here = [
                    sm
                    for sm in sim.people
                    if sm.branch_id == branch_id and self.is_valid(sm, today)
                ]
                if not people_here:
                    continue
                for step in range(5):
                    kind = self.rng.choice(kinds)
                    day = today + timedelta(days=self.rng.randint(0, 4))
                    free = massage.free_slots(
                        sim.gym, branch=branch, massage_type=kind, day=day, masseuse=masseuse
                    )
                    if not free:
                        continue
                    slot = self.rng.choice(free)
                    sm = self.rng.choice(people_here)
                    try:
                        massage.book(
                            by=sim.desk[branch_id],
                            massage_type=kind,
                            masseuse=masseuse,
                            branch=branch,
                            day=day,
                            at=slot.at,
                            person=sm.member.person,
                            member=sm.member,
                            accepted=step >= 2,
                        )
                    except InvalidDetails:
                        continue
                    self.counts["massages"] = self.counts.get("massages", 0) + 1

    def sell_ticketbooks(self, sims):
        """
        A few members buy a book of prepaid visits instead of, or as well as,
        a membership: "15 swimming tickets", "5 massages".
        """
        today = timezone.localdate()
        for sim in sims:
            books = [p for p in sim.packages.values() if p.is_count]
            if not books or not sim.people:
                continue
            for _step in range(6):
                sm = self.rng.choice(sim.people)
                package = self.rng.choice(books)
                by = sim.desk.get(sm.branch_id) or sim.owner
                try:
                    with self.at(today - timedelta(days=self.rng.randint(0, 20)), 10):
                        sale = billing.sell_package(
                            by=by,
                            member=sm.member,
                            package=package,
                            start_date=today,
                            idempotency_key=uuid.uuid4(),
                            payment=self.payment_for(package.price_rwf),
                        )
                except (InvalidDetails, billing.AlreadyReversed):
                    continue
                self.counts["ticketbooks"] = self.counts.get("ticketbooks", 0) + 1
                # Some of the visits have already been used.
                book = sale.ticketbook
                service = book.included_services.first()
                for _used in range(self.rng.randint(0, 4)):
                    try:
                        members.spend_ticket(by=by, ticketbook=book, service=service)
                    except InvalidDetails:
                        break

    def waiting_signups(self, sims):
        """
        A couple of people per gym who filled in the sign-up page on their own
        phone and have not paid yet, so the desk has something waiting.
        """
        for sim in sims:
            on_sale = list(members.packages_on_sale(sim.gym))
            if not on_sale:
                continue
            for _step in range(2):
                full_name, gender = self.name()
                try:
                    members.create_signup(
                        sim.gym,
                        full_name=full_name,
                        phone=self.phone(),
                        package=self.rng.choice(on_sale),
                        gender=gender,
                        wants_messages=self.rng.random() < 0.6,
                    )
                except (InvalidDetails, members.AlreadyMember, members.AlreadySignedUp):
                    continue
                self.counts["signups"] = self.counts.get("signups", 0) + 1

    def bring_friends(self, sims, today):
        """A few members brought a friend in during the last week."""
        for sim in sims:
            attendance.set_guest_rule(by=sim.owner, free_per_month=1)
            active = [sm for sm in sim.people if sm.end and sm.end >= today]
            for sm in self.rng.sample(active, min(4, len(active))):
                guest_name, _gender = self.name()
                when = today - timedelta(days=self.rng.randrange(0, 7))
                with self.at(when, 17, 30):
                    try:
                        attendance.bring_a_friend(
                            by=sim.desk[sm.branch_id],
                            member=sm.member,
                            guest_name=guest_name,
                            branch=sim.branches[sm.branch_id],
                            today=when,
                        )
                    except (attendance.NotAllowedIn, InvalidDetails):
                        continue

    # --- Shifts and the desk ------------------------------------------------------

    def close_shifts(self, sims, day: date, today: date):
        """Receptionists close their shift at 14:00 and 21:00 (not yet today evening)."""
        for sim in sims:
            for desk in sim.desk.values():
                for hour in (14, 21):
                    if day == today and hour == 21:
                        continue
                    if day == today and self.real_now.hour < hour:
                        continue
                    with self.at(day, hour, 2):
                        billing.close_shift(by=desk)
        if day == today:
            # A few things the tablet could not let through, for the desk to see.
            for sim in sims:
                lapsed = [sm for sm in sim.people if sm.end and sm.end < day][:2]
                for sm in lapsed:
                    with self.at(day, max(6, self.real_now.hour - 1), 10):
                        ScanProblem.objects.create(
                            gym=sim.gym,
                            branch=sim.branches[sm.branch_id],
                            kind=ProblemKind.MEMBERSHIP_ENDED,
                            member=sm.member,
                        )

    # --- Everything -------------------------------------------------------------

    def run(self):
        self.real_now = timezone.localtime().astimezone(KIGALI) - timedelta(minutes=2)
        today = timezone.localdate()
        start = today - timedelta(days=self.days)

        with self.at(start, 7):
            admin = people.find_person_by_phone(rwanda.PLATFORM_ADMIN_PHONE)
            if admin is None:
                admin_user = accounts.create_platform_admin(
                    full_name="Platform Demo",
                    phone=rwanda.PLATFORM_ADMIN_PHONE,
                    password=DEMO_PASSWORD,
                )
                admin = admin_user.person
                self.logins.append(("Platform", "Platform admin", admin.full_name, admin.phone))

        sims = []
        for spec in rwanda.GYMS:
            with transaction.atomic(), self.at(start, 8):
                sims.append(self.create_gym(spec, start, admin))
            self.log(f"Set up {spec['name']} ({len(spec['branches'])} branches)")

        # The gyms put entrance tablets in during the last month.
        self.tablets_from = today - timedelta(days=30)
        for sim in sims:
            owner = sim.owner
            with transaction.atomic(), self.at(self.tablets_from, 8):
                for branch in sim.branches.values():
                    tablet = frontdesk.add_tablet(by=owner, branch=branch, name="Front entrance")
                    tablet.secret_hash = uuid.uuid4().hex + uuid.uuid4().hex  # connected
                    tablet.paired_at, tablet.pairing_code = timezone.now(), ""
                    tablet.last_seen_at = timezone.now()
                    tablet.save()

        by_name = {sim.spec["name"]: sim for sim in sims}
        for spec in rwanda.COMPANIES:
            with transaction.atomic(), self.at(start, 9):
                self.create_company(spec, by_name, start, admin)
            self.log(f"Set up {spec['name']} (company)")

        day = start
        while day <= today:
            with transaction.atomic():
                for sim in sims:
                    self.simulate_day(sim, day)
                self.corporate_day(day)
            if day >= today - timedelta(days=6):
                with transaction.atomic():
                    self.close_shifts(sims, day, today)
            if (day - start).days % 15 == 0:
                self.log(f"  {day:%d %b %Y}: {self.counts}")
            day += timedelta(days=1)
        with transaction.atomic():
            self.schedule_classes(sims)
            self.fill_classes(sims)
            self.book_massages(sims)
            self.sell_ticketbooks(sims)
            self.waiting_signups(sims)
            self.bring_friends(sims, today)
        return self.counts
