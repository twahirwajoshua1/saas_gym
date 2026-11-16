"""
Recording and reading the audit log.

Rule for the whole project: any action that changes money, access or
configuration records an entry, inside the same database transaction as the
action itself. If the entry cannot be saved, the action does not happen.
"""

import json

from apps.audit.models import AuditEntry


def record(*, action, summary, gym=None, actor=None, target=None, details=None) -> AuditEntry:
    """
    Add one entry to the audit log.

    gym:     the gym the action belongs to, or None for platform-level actions.
    actor:   the Person who did it, or None for the system or a platform command.
    target:  the record the action was about (a model instance), if any.
    details: extra facts as a dictionary.
    """
    target_type = target_id = ""
    if target is not None:
        target_type = target._meta.label_lower
        target_id = str(getattr(target, "public_id", None) or target.pk)

    # Round-trip through JSON so that identifiers and dates become plain text.
    clean_details = json.loads(json.dumps(details or {}, default=str))

    return AuditEntry.objects.create(
        gym=gym,
        actor=actor,
        action=action,
        summary=summary[:300],
        target_type=target_type,
        target_id=target_id,
        details=clean_details,
    )


def entries_for(gym, limit: int = 100):
    """The most recent entries for one gym, newest first."""
    return AuditEntry.objects.for_gym(gym).select_related("actor")[:limit]
