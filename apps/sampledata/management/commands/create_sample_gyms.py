"""
Creates five invented Rwandan gyms (with 1, 2, 3, 4 and 5 branches), their
staff, packages and members, and weeks of history: sales, payments, renewals,
freezes and check-ins.

    python manage.py create_sample_gyms
    python manage.py create_sample_gyms --days 30 --scale 0.5     (smaller and faster)

For demonstrations and testing only. Run it on an empty development database.
Every staff login uses the password printed at the end.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.templatetags.phones import phone
from apps.gyms.models import Gym
from apps.sampledata import rwanda
from apps.sampledata.generator import DEMO_PASSWORD, Generator


class Command(BaseCommand):
    help = "Create five sample Rwandan gyms with weeks of realistic history."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=75, help="Days of history (default 75).")
        parser.add_argument(
            "--scale", type=float, default=1.0, help="Fraction of the usual member numbers."
        )
        parser.add_argument("--seed", type=int, default=2026, help="Same seed, same data.")
        parser.add_argument(
            "--allow-production",
            action="store_true",
            help="Allow running when DEBUG is off. Never use on a real gym's database.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["allow_production"]:
            raise CommandError(
                "DEBUG is off, so this looks like a real installation. Sample data was not created."
            )
        names = [g["name"] for g in rwanda.GYMS]
        if Gym.objects.filter(name__in=names).exists():
            raise CommandError(
                "Sample gyms already exist in this database. Money records cannot be deleted, "
                "so start from an empty database to create them again."
            )
        if not 1 <= options["days"] <= 365:
            raise CommandError("--days must be between 1 and 365.")

        generator = Generator(
            seed=options["seed"],
            days=options["days"],
            scale=options["scale"],
            log=lambda line: self.stdout.write(line),
        )
        counts = generator.run()

        self.stdout.write(self.style.SUCCESS("\nSample data created:"))
        for key, value in counts.items():
            self.stdout.write(f"  {key.replace('_', ' ')}: {value:,}")
        self.stdout.write(f"\nEvery login below uses the password: {DEMO_PASSWORD}\n")
        for gym, role, name, number in generator.logins:
            self.stdout.write(f"  {gym:<26} {role:<32} {name:<28} {phone(number)}")
