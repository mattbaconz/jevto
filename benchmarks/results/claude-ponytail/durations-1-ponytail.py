"""Duration parsing."""

import re


def parse_duration(text: str) -> int:
    """Return the total number of seconds in a duration string.

    Accepted: one or more <integer><unit> parts, where unit is h, m, or s
    (case-insensitive), in the order h, m, s, each unit at most once.
    Parts may be separated by single spaces or written together; surrounding
    whitespace is ignored. Examples: "90s" -> 90, "15m" -> 900,
    "1h30m" -> 5400, "1h 30m 15s" -> 5415, " 2H " -> 7200.

    Raise ValueError for anything else: empty text, a number without a unit,
    unknown units, fractions, signs, repeated or out-of-order units.
    """
    text = text.strip()
    if not text:
        raise ValueError("empty text")

    cleaned = re.sub(r'\s+', '', text)
    if not re.match(r'^(\d+[hms])+$', cleaned, re.IGNORECASE):
        raise ValueError("invalid format")

    matches = re.findall(r'(\d+)([hms])', text, re.IGNORECASE)
    if not matches:
        raise ValueError("no valid parts")

    total_seconds = 0
    unit_order = {'h': 0, 'm': 1, 's': 2}
    last_unit_order = -1
    unit_multipliers = {'h': 3600, 'm': 60, 's': 1}

    for number, unit in matches:
        unit_lower = unit.lower()
        current_order = unit_order[unit_lower]

        if current_order <= last_unit_order:
            raise ValueError("repeated or out-of-order units")
        last_unit_order = current_order

        total_seconds += int(number) * unit_multipliers[unit_lower]

    return total_seconds
