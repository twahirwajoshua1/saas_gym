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
from apps.gyms import services as gyms
from apps.gyms.models import Branch, Role, StaffAssignment
from apps.members import services as members
from apps.members.models import Gender
from apps.members.services import MemberDetails
from apps.modules import services as modules
from apps.modules.catalog import ATTENDANCE_ACCESS, PAYMENTS_FINANCE
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


class Generator:
    def __init__(self, *, seed: int, days: int, scale: float, log=print):
        self.rng = random.Random(seed)
        self.days = days
        self.scale = scale
        self.log = log
        self.used_phones = set()
        self.counts = {"members": 0, "sales": 0, "payments": 0, "check_ins": 0, "freezes": 0}
        self.logins = []

    # --- Small helpers ---------------------------------------------------------

    def at(self, day: date, hour: int, minute: int = 0):
        """Move the clock to a moment on a simulated day (Kigali time)."""
        return time_machine.travel(datetime.combine(day, time(hour, minute), KIGALI), tick=False)

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

        for code in (PAYMENTS_FINANCE, ATTENDANCE_ACCESS):
            modules.enable_module(gym=gym, code=code, actor=admin_person)

        packages = {
            name: members.create_package(
                by=owner, name=name, price_rwf=price, duration_count=count, duration_unit=unit
            )
            for name, price, count, unit in spec["packages"]
        }
        sim = SimGym(spec, gym, owner, {}, {}, {}, packages)
        sim.branches = {b.id: b for b in branches}
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
        with self.at(day, hour, minute):
            try:
                attendance.check_in(by=by, member=sm.member)
            except attendance.NotAllowedIn:
                return
        self.counts["check_ins"] += 1

    # --- Everything -------------------------------------------------------------

    def run(self):
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

        day = start
        while day <= today:
            with transaction.atomic():
                for sim in sims:
                    self.simulate_day(sim, day)
            if (day - start).days % 15 == 0:
                self.log(f"  {day:%d %b %Y}: {self.counts}")
            day += timedelta(days=1)
        return self.counts
