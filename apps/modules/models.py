from django.db import models
from django.utils import timezone

from apps.gyms.models import GymOwnedModel


class ModuleActivation(GymOwnedModel):
    """
    Says that a gym has (or once had) one of the applications switched on.

    Switching an application off keeps the row and records when, so the history
    is never lost. Gym Operations is always on and has no row.

    Gym staff can only read these rows. Only the platform side can change them
    (the database enforces this; see migration 0002).
    """

    module = models.CharField(max_length=40)
    is_active = models.BooleanField(default=True)
    activated_at = models.DateTimeField(default=timezone.now)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["gym", "module"], name="one_activation_per_module_per_gym"
            ),
        ]

    def __str__(self):
        state = "on" if self.is_active else "off"
        return f"{self.gym.name}: {self.module} ({state})"


class ModuleRequest(GymOwnedModel):
    """
    A gym asking for an application it does not have.

    The applications page used to end at "contact the platform team", which is
    a wall: the owner reads that a thing exists, wants it, and has nowhere to
    press. This row is the press. The platform side reads them and sees who
    wants what, instead of waiting for a phone call.

    One standing ask per gym per application. Asking twice changes nothing,
    and the gym can take the ask back.
    """

    module = models.CharField(max_length=40)
    asked_by = models.ForeignKey(
        "people.Person", on_delete=models.PROTECT, related_name="+", null=True
    )
    asked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-asked_at"]
        constraints = [
            models.UniqueConstraint(fields=["gym", "module"], name="one_request_per_module_per_gym")
        ]

    def __str__(self):
        return f"{self.gym.name} wants {self.module}"
