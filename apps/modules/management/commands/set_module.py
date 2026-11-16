"""
Shows, switches on or switches off an application for a gym.

    python manage.py set_module --gym "Kigali Fit Club"                          (shows status)
    python manage.py set_module --gym "Kigali Fit Club" --module payments_finance
    python manage.py set_module --gym "Kigali Fit Club" --module payments_finance --off
"""

from django.core.management.base import BaseCommand, CommandError

from apps.gyms import services as gym_services
from apps.modules import services


class Command(BaseCommand):
    help = "Show, switch on or switch off an application for a gym."

    def add_arguments(self, parser):
        parser.add_argument("--gym", required=True, help="The gym's name (in quotes) or its id.")
        parser.add_argument(
            "--module", help="The application's code, for example payments_finance."
        )
        parser.add_argument("--off", action="store_true", help="Switch it off instead of on.")

    def handle(self, *args, **options):
        try:
            gym = gym_services.find_gym(options["gym"])
            if options["module"]:
                self._change(gym, options["module"], switch_off=options["off"])
        except (gym_services.GymLookupError, services.ModuleError) as error:
            raise CommandError(str(error)) from error

        self.stdout.write(f"\nApplications for {gym.name}:")
        for status in services.module_status(gym):
            state = "ON " if status["enabled"] else "off"
            self.stdout.write(f"  [{state}] {status['module'].code:<24} {status['module'].name}")

    def _change(self, gym, code, *, switch_off):
        if switch_off:
            changed = services.disable_module(gym=gym, code=code)
            message = "Switched off." if changed else "It was already off."
        else:
            changed = services.enable_module(gym=gym, code=code)
            message = "Switched on." if changed else "It was already on."
        self.stdout.write(self.style.SUCCESS(message))
