"""Business rules for switching applications on and off for a gym."""

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.gyms.models import Gym, StaffAssignment
from apps.gyms.permissions import MANAGER_ROLES, require_role
from apps.modules.catalog import BY_CODE, CATALOG, AppModule
from apps.modules.models import ModuleActivation, ModuleRequest
from apps.people.models import Person


class ModuleError(Exception):
    """Base for every problem with switching a module on or off."""


class UnknownModule(ModuleError):
    """There is no application with that code."""


class MissingDependencies(ModuleError):
    """The application needs others that are not switched on."""

    def __init__(self, module: AppModule, missing: list[AppModule]):
        self.module, self.missing = module, missing
        names = ", ".join(m.name for m in missing)
        super().__init__(f"{module.name} needs {names} to be switched on first.")


class HasActiveDependents(ModuleError):
    """Other switched-on applications need this one."""

    def __init__(self, module: AppModule, dependents: list[AppModule]):
        self.module, self.dependents = module, dependents
        names = ", ".join(m.name for m in dependents)
        super().__init__(f"{module.name} cannot be switched off while {names} is on.")


class CannotAsk(ModuleError):
    """Asked for something that cannot be asked for."""


class CannotDisable(ModuleError):
    """Gym Operations is always on."""


def get_module(code: str) -> AppModule:
    try:
        return BY_CODE[code]
    except KeyError:
        valid = ", ".join(BY_CODE)
        raise UnknownModule(f"'{code}' is not an application. Choose one of: {valid}.") from None


def is_enabled(gym: Gym, code: str) -> bool:
    """Has this gym got this application switched on right now?"""
    module = get_module(code)
    if module.always_on:
        return True
    return ModuleActivation.objects.for_gym(gym).filter(module=code, is_active=True).exists()


def enabled_codes(gym: Gym) -> set[str]:
    switched_on = set(
        ModuleActivation.objects.for_gym(gym)
        .filter(is_active=True)
        .values_list("module", flat=True)
    )
    return switched_on | {m.code for m in CATALOG if m.always_on}


def module_status(gym: Gym) -> list[dict]:
    """Every application, whether this gym has it, and what it needs. For screens."""
    on = enabled_codes(gym)
    return [
        {
            "module": module,
            "enabled": module.code in on,
            # What it still needs before it could be switched on.
            "missing": [BY_CODE[c] for c in module.requires if c not in on],
        }
        for module in CATALOG
    ]


@transaction.atomic
def enable_module(*, gym: Gym, code: str, actor: Person | None = None) -> bool:
    """
    Switch an application on for a gym. Returns False if it was already on.

    Raises UnknownModule, or MissingDependencies if what it needs is not on.
    """
    module = get_module(code)
    if is_enabled(gym, code):
        return False

    missing = [BY_CODE[c] for c in module.requires if not is_enabled(gym, c)]
    if missing:
        raise MissingDependencies(module, missing)

    activation = ModuleActivation.objects.for_gym(gym).filter(module=code).first()
    if activation is None:
        ModuleActivation.objects.create(gym=gym, module=code)
    else:
        activation.is_active = True
        activation.activated_at = timezone.now()
        activation.deactivated_at = None
        activation.save()

    audit.record(
        action="module.enabled",
        summary=f"{module.name} was switched on for {gym.name}",
        gym=gym,
        actor=actor,
        target=gym,
        details={"module": code},
    )
    return True


@transaction.atomic
def disable_module(*, gym: Gym, code: str, actor: Person | None = None) -> bool:
    """
    Switch an application off for a gym. Returns False if it was already off.

    Raises UnknownModule, CannotDisable, or HasActiveDependents.
    """
    module = get_module(code)
    if module.always_on:
        raise CannotDisable(f"{module.name} is always on.")
    if not is_enabled(gym, code):
        return False

    dependents = [m for m in CATALOG if code in m.requires and is_enabled(gym, m.code)]
    if dependents:
        raise HasActiveDependents(module, dependents)

    activation = ModuleActivation.objects.for_gym(gym).get(module=code)
    activation.is_active = False
    activation.deactivated_at = timezone.now()
    activation.save()

    audit.record(
        action="module.disabled",
        summary=f"{module.name} was switched off for {gym.name}",
        gym=gym,
        actor=actor,
        target=gym,
        details={"module": code},
    )
    return True


def requested_modules(gym: Gym) -> dict[str, ModuleRequest]:
    """Which applications this gym has asked for, by code."""
    return {row.module: row for row in ModuleRequest.objects.for_gym(gym)}


def ask_for_module(*, by: StaffAssignment, code: str) -> ModuleRequest:
    """
    Record that this gym wants an application. Managers only.

    Asking does not switch anything on: only the platform side does that. It
    puts the gym on a list somebody can act on.
    """
    require_role(by, MANAGER_ROLES)
    module = get_module(code)
    if module.always_on:
        raise CannotAsk("Every gym already has that one.")
    if is_enabled(by.gym, code):
        raise CannotAsk("This gym already has that application.")
    asked, created = ModuleRequest.objects.get_or_create(
        gym=by.gym, module=code, defaults={"asked_by": by.person}
    )
    if created:
        audit.record(
            action="module.requested",
            summary=f"{by.gym.name} asked for {module.name}",
            gym=by.gym,
            actor=by.person,
            target=asked,
        )
    return asked


def withdraw_request(*, by: StaffAssignment, code: str) -> None:
    """Take back an ask. Managers only."""
    require_role(by, MANAGER_ROLES)
    ModuleRequest.objects.for_gym(by.gym).filter(module=code).delete()
