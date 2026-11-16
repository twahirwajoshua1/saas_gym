"""
Tests for the sample data command, on a small, short run.

The run happens inside one test so it is rolled back afterwards. It checks the
generated gyms obey the same rules as real ones: branch numbers, books that
balance, no data crossing between gyms, and Rwandan phone numbers.
"""

import io

import pytest
from django.core.management import CommandError, call_command
from django.db.models import Sum

from apps.billing import services as billing
from apps.billing.models import Allocation, Invoice, Payment
from apps.gyms.models import Branch, Gym, StaffAssignment
from apps.members.models import Member
from apps.people.models import Person
from apps.people.phones import RWANDA_MOBILE_PATTERN
from apps.sampledata import rwanda

pytestmark = pytest.mark.django_db


def test_sample_gyms(settings):
    settings.DEBUG = True
    out = io.StringIO()

    call_command("create_sample_gyms", "--days", "8", "--scale", "0.1", stdout=out)

    # Five gyms with 1, 2, 3, 4 and 5 branches.
    gyms = list(Gym.objects.all())
    assert len(gyms) == 5
    assert sorted(Branch.objects.for_gym(g).count() for g in gyms) == [1, 2, 3, 4, 5]
    assert "Every login below uses the password" in out.getvalue()

    for gym in gyms:
        # Staff, members and sales in every gym.
        assert StaffAssignment.objects.for_gym(gym).filter(role="owner").count() == 1
        assert StaffAssignment.objects.for_gym(gym).filter(role="receptionist").exists()
        assert Member.objects.for_gym(gym).exists()
        assert Invoice.objects.for_gym(gym).exists()

        # The books balance.
        invoiced = Invoice.objects.for_gym(gym).aggregate(s=Sum("total_rwf"))["s"] or 0
        live = Payment.objects.for_gym(gym).filter(reversal__isnull=True)
        received = live.aggregate(s=Sum("amount_rwf"))["s"] or 0
        allocated = (
            Allocation.objects.for_gym(gym)
            .filter(payment__reversal__isnull=True)
            .aggregate(s=Sum("amount_rwf"))["s"]
            or 0
        )
        assert billing.total_outstanding(gym) == invoiced - received
        assert received == allocated

    # Nothing crosses between gyms.
    for member in Member.objects.select_related("branch"):
        assert member.branch.gym_id == member.gym_id
    for payment in Payment.objects.select_related("member"):
        assert payment.member.gym_id == payment.gym_id

    # Rwandan mobile numbers, and Kinyarwanda women's surnames only for women.
    for phone in Person.objects.values_list("phone", flat=True):
        assert RWANDA_MOBILE_PATTERN.fullmatch(phone.lstrip("+"))
    for member in Member.objects.filter(gender="male"):
        assert not member.full_name.split()[-1].startswith(rwanda.FEMALE_ONLY_PREFIXES)

    # A second run is refused rather than duplicating gyms.
    with pytest.raises(CommandError, match="already exist"):
        call_command("create_sample_gyms", "--days", "2", stdout=io.StringIO())


def test_it_refuses_a_real_installation(settings):
    settings.DEBUG = False

    with pytest.raises(CommandError, match="DEBUG is off"):
        call_command("create_sample_gyms", stdout=io.StringIO())
