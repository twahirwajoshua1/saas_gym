"""
Issue the monthly corporate bills that are due.

Run it every evening (for example at 23:30). On the last day of a month it
issues that month's bills; any day after, it catches up on bills not issued
yet. Running it twice does no harm: a bill is issued once.

    python manage.py issue_corporate_bills
"""

from django.core.management.base import BaseCommand

from apps.corporate import services


class Command(BaseCommand):
    help = "Issue the monthly corporate bills that are due."

    def handle(self, *args, **options):
        bills = services.issue_due_bills()
        for bill in bills:
            self.stdout.write(
                f"{bill.display_number}  {bill.gym.name} -> {bill.company.name}  "
                f"RWF {bill.total_rwf:,}"
            )
        self.stdout.write(self.style.SUCCESS(f"{len(bills)} bill(s) issued."))
