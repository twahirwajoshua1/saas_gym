"""
Removes a sign-in lock before its 15 minutes are up.

    python manage.py unlock_sign_in --phone 0788123456
"""

from django.core.management.base import BaseCommand, CommandError

from apps.accounts import services
from apps.people.phones import InvalidPhoneNumber


class Command(BaseCommand):
    help = "Remove the sign-in lock on a phone number."

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True, help="Phone number, e.g. 0788123456.")

    def handle(self, *args, **options):
        try:
            unlocked = services.unlock_sign_in(phone=options["phone"])
        except InvalidPhoneNumber as error:
            raise CommandError(str(error)) from error

        if unlocked:
            self.stdout.write(self.style.SUCCESS("Unlocked. Wrong attempts were also forgotten."))
        else:
            self.stdout.write("That number was not locked and had no wrong attempts.")
