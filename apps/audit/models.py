from django.db import models


class AuditQuerySet(models.QuerySet):
    def for_gym(self, gym):
        """Only the entries that belong to this gym."""
        return self.filter(gym=gym)


class AuditEntry(models.Model):
    """
    One line in the permanent record of what happened.

    Entries are never changed or deleted: the database refuses (see migration
    0002). A mistake is corrected by recording a new entry.

    Entries that belong to a gym are visible only to that gym. Entries about
    the platform itself (no gym) are visible only to the platform owner.
    """

    gym = models.ForeignKey(
        "gyms.Gym", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    # Who did it. Empty means the system or a platform command, not a person.
    actor = models.ForeignKey(
        "people.Person", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    # A short machine-readable name such as "staff.assigned".
    action = models.CharField(max_length=60)

    # A sentence a person can read, such as "Aline Uwase was made Receptionist".
    summary = models.CharField(max_length=300)

    # What the action was about, for example "gyms.branch" and its public id.
    target_type = models.CharField(max_length=60, blank=True)
    target_id = models.CharField(max_length=64, blank=True)

    # Extra facts. Never passwords, codes or other secrets.
    details = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        verbose_name_plural = "audit entries"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"
