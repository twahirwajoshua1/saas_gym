"""
The member's card: one summary used by both the card page and the API.

Both read through the same function, which is how the plan's "API-ready"
promise is tested: a future React or React Native member app would call the
same thing.
"""

from dataclasses import dataclass

import segno

from apps.attendance import services as attendance
from apps.billing import services as billing
from apps.core.templatetags.phones import phone_masked
from apps.members.models import Member


@dataclass
class CardSummary:
    member: Member
    gym_name: str
    phone_masked: str
    status: attendance.Assessment
    receipts: list
    payments_on: bool


def card_summary(member: Member) -> CardSummary:
    payments_on = billing.payments_enabled(member.gym)
    receipts = list(billing.payments_for(member)[:5]) if payments_on else []
    return CardSummary(
        member=member,
        gym_name=member.gym.name,
        phone_masked=phone_masked(member.person.phone),
        status=attendance.assess(member),
        receipts=receipts,
        payments_on=payments_on,
    )


def card_as_data(summary: CardSummary) -> dict:
    """The card as plain data, for the API. Same facts as the page."""
    status = summary.status
    membership = status.membership
    return {
        "gym": summary.gym_name,
        "member": {"name": summary.member.full_name, "phone": summary.phone_masked},
        "allowed_in_today": status.allowed,
        "reason": status.reason or None,
        "membership": None
        if membership is None
        else {
            "package": membership.package_name,
            "start_date": membership.start_date.isoformat(),
            "end_date": membership.end_date.isoformat(),
            "frozen_until": status.frozen_until.isoformat() if status.frozen_until else None,
        },
        "days_left": status.days_left if status.allowed else 0,
        "owes_rwf": status.owes_rwf,
        "receipts": [
            {
                "number": p.display_number,
                "date": p.received_on.isoformat(),
                "amount_rwf": p.amount_rwf,
                "method": p.get_method_display(),
                "reversed": p.is_reversed,
            }
            for p in summary.receipts
        ],
    }


def qr_svg(text: str, scale: int = 6) -> str:
    """A QR code as an SVG drawing, to place straight into a page."""
    return segno.make(text, error="m").svg_inline(
        scale=scale, dark="#10201a", light="#ffffff", omitsize=True
    )
