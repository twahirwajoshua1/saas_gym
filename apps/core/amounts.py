"""Reading amounts of money typed by people."""

import re


def parse_rwf(typed) -> int | None:
    """
    Turn "30,000", "30 000" or "30000" into 30000.

    Returns None if the text is not a whole, non-negative number of francs.
    """
    cleaned = re.sub(r"[,\s]", "", str(typed or ""))
    if not cleaned.isdigit():
        return None
    return int(cleaned)
