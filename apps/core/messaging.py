"""
Links that open the gym's own WhatsApp or text message, with the words ready.

The system never sends anything itself. It writes the message and hands it to
whatever the member of staff already uses, on the gym's own phone, and a
person presses send. That is the honest version until a gym connects a message
provider of its own, and it costs the gym nothing in the meantime.

These two functions exist so every page builds the same link the same way: a
WhatsApp number must have no plus sign and no spaces, while a text message
keeps the number as it is written.
"""

from urllib.parse import quote


def _digits(phone: str) -> str:
    return "".join(character for character in (phone or "") if character.isdigit())


def whatsapp_url(phone: str, text: str) -> str:
    """Open WhatsApp to this number with the message already typed."""
    return f"https://wa.me/{_digits(phone)}?text={quote(text)}"


def sms_url(phone: str, text: str) -> str:
    """Open the phone's text messages to this number with the message typed."""
    return f"sms:{(phone or '').strip()}?body={quote(text)}"
