"""
Creates a login for someone who runs the platform.

    python manage.py create_platform_admin --name "Your Name" --phone 0788123456
"""

import getpass

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts import services
from apps.people.phones import InvalidPhoneNumber
from apps.people.services import InvalidPersonDetails


class Command(BaseCommand):
    help = "Create a platform administrator who signs in with a phone number."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Full name, in quotes.")
        parser.add_argument("--phone", required=True, help="Phone number, e.g. 0788123456.")

    def handle(self, *args, **options):
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Password (again): "):
            raise CommandError("The two passwords are not the same.")

        try:
            user = services.create_platform_admin(
                full_name=options["name"], phone=options["phone"], password=password
            )
        except ValidationError as error:
            raise CommandError(" ".join(error.messages)) from error
        except (InvalidPhoneNumber, InvalidPersonDetails, services.AccountAlreadyExists) as error:
            raise CommandError(str(error)) from error

        self.stdout.write(self.style.SUCCESS(f"Created platform administrator {user.phone}."))
