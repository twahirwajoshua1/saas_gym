"""
python manage.py create_demo_logins

Short demo names for the sample data, so a demonstration does not need phone
numbers. Only on a developer's own computer: it needs DEBUG=True and
DEMO_LOGINS=True in .env, and create_sample_gyms must have run first.

    gymowner / gymower          owner of Umucyo Wellness Centre
    receptionist / receptionist receptionist at Umucyo, Nyarutarama
    member / member             opens a sample member's card (members have no login)
    company / Company           WASAC's logistics officer

The names are not case-sensitive; the passwords are.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.urls import reverse

from apps.accounts import services as accounts
from apps.core import tenant_db
from apps.corporate.models import Company
from apps.gyms.models import Gym, StaffAssignment
from apps.members import services as members
from apps.members.models import Member

GYM = "Umucyo Wellness Centre"
COMPANY = "WASAC"


class Command(BaseCommand):
    help = "Short demo names (gymowner, receptionist, member, company) for the sample data."

    def handle(self, *args, **options):
        if not settings.DEMO_LOGINS:
            raise CommandError(
                "Demo names are off. Add DEMO_LOGINS=True to .env (with DEBUG=True) first."
            )
        gym = Gym.objects.filter(name=GYM).first()
        company = Company.objects.filter(name=COMPANY).first()
        if gym is None or company is None:
            raise CommandError("Run python manage.py create_sample_gyms first.")
        tenant_db.set_current_gym(gym.id)

        staff = StaffAssignment.objects.filter(gym=gym, is_active=True).select_related(
            "person__user_account", "branch"
        )
        owner = staff.filter(role="owner").first()
        receptionists = staff.filter(role="receptionist")
        desk = receptionists.filter(branch__name="Nyarutarama").first() or receptionists.first()
        officer = company.officers.select_related("person__user_account").first()
        member = (
            Member.objects.for_gym(gym)
            .annotate(visits=Count("check_ins"))
            .order_by("-visits")
            .first()
        )
        token = members.create_card_link(by=owner, member=member)

        rows = [
            ("gymowner", "gymower", owner.person.user_account, "", f"Owner, {GYM}"),
            (
                "receptionist",
                "receptionist",
                desk.person.user_account,
                "",
                f"Receptionist, {GYM} {desk.branch.name}",
            ),
            (
                "member",
                "member",
                None,
                reverse("memberapp:card", args=[token]),
                f"Member card of {member.full_name}, {GYM}",
            ),
            ("company", "Company", officer.person.user_account, "", f"Officer, {COMPANY}"),
        ]
        self.stdout.write("Demo names (sign-in page, in the phone number box):\n")
        for name, password, user, url, label in rows:
            accounts.set_demo_login(name=name, password=password, user=user, url=url, label=label)
            self.stdout.write(f"  {name:<14} {password:<14} {label}")
        self.stdout.write(self.style.SUCCESS("Done."))
