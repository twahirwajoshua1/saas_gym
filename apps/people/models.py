import uuid

from django.db import models


class Person(models.Model):
    """
    A human being known to the platform.

    A person exists once, whatever their roles: member at one gym, trainer at
    another, employee of an organization. Other modules link to this record
    instead of storing the name and phone number again.
    """

    # A random identifier that is safe to show outside the system
    # (in web addresses and, later, in the API).
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    full_name = models.CharField(max_length=150)

    # Always stored in standard form, for example +250788123456.
    phone = models.CharField(max_length=16, unique=True)

    email = models.EmailField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "people"
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.full_name} ({self.phone})"
