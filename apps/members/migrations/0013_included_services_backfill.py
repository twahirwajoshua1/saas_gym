"""
Say which services the packages and memberships that already exist include.

Before this change a package did not list its services: buying any package let
you use everything the gym offered. That is now written down, so the owner can
sell a swimming-only membership. Nobody should lose anything on the day of the
change, so every package and every membership that already exists is given all
of its gym's services.
"""

from django.db import migrations


def fill(apps, schema_editor):
    Service = apps.get_model("members", "Service")
    Package = apps.get_model("members", "Package")
    Membership = apps.get_model("members", "Membership")
    PackageService = apps.get_model("members", "PackageService")
    MembershipService = apps.get_model("members", "MembershipService")

    by_gym: dict[int, list[int]] = {}
    for gym_id, service_id in Service.objects.filter(is_active=True).values_list("gym_id", "id"):
        by_gym.setdefault(gym_id, []).append(service_id)

    rows = [
        PackageService(gym_id=gym_id, package_id=package_id, service_id=service_id)
        for package_id, gym_id in Package.objects.values_list("id", "gym_id")
        for service_id in by_gym.get(gym_id, ())
    ]
    PackageService.objects.bulk_create(rows, batch_size=1000)

    rows = [
        MembershipService(gym_id=gym_id, membership_id=membership_id, service_id=service_id)
        for membership_id, gym_id in Membership.objects.values_list("id", "gym_id")
        for service_id in by_gym.get(gym_id, ())
    ]
    MembershipService.objects.bulk_create(rows, batch_size=1000)


def undo(apps, schema_editor):
    apps.get_model("members", "PackageService").objects.all().delete()
    apps.get_model("members", "MembershipService").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("members", "0012_ticketbook_ticketbookservice_ticketuse_package_kind_and_more"),
    ]

    operations = [migrations.RunPython(fill, undo)]
