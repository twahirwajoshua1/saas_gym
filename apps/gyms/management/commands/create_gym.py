"""
Registers a gym with its first branch and its owner.

    python manage.py create_gym --name "Kigali Fit Club" --owner-phone 0788123456

If the owner is not registered yet, also give --owner-name "Their Name".
"""

from django.core.management.base import BaseCommand, CommandError

from apps.gyms import services
from apps.people import services as people_services
from apps.people.phones import InvalidPhoneNumber


class Command(BaseCommand):
    help = "Register a gym with a main branch and an owner."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="The gym's name, in quotes.")
        parser.add_argument("--owner-phone", required=True, help="The owner's phone number.")
        parser.add_argument("--owner-name", help="Needed only if the owner is not registered yet.")

    def handle(self, *args, **options):
        try:
            owner = people_services.find_person_by_phone(options["owner_phone"])
            if owner is None:
                if not options["owner_name"]:
                    raise CommandError(
                        "Nobody is registered with that phone number. "
                        'Add --owner-name "Their Name" to register them.'
                    )
                owner = people_services.create_person(
                    full_name=options["owner_name"], phone=options["owner_phone"]
                )
            gym = services.create_gym(name=options["name"], owner=owner)
        except (InvalidPhoneNumber, ValueError) as error:
            raise CommandError(str(error)) from error

        self.stdout.write(self.style.SUCCESS(f"Created {gym.name}, owned by {owner}."))
        if not hasattr(owner, "user_account"):
            self.stdout.write("Note: the owner has no login account yet, so cannot sign in.")
