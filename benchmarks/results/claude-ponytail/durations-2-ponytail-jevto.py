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

    if not re.match(r'^((?:\s*\d+\s*[hms])+)$', text, re.IGNORECASE):
        raise ValueError("invalid duration format")

    matches = re.findall(r'(\d+)\s*([hms])', text, re.IGNORECASE)
    if not matches:
        raise ValueError("no valid duration parts")

    total = 0
    seen_units = set()
    last_order = -1
    order = {'h': 0, 'm': 1, 's': 2}
    seconds_per = {'h': 3600, 'm': 60, 's': 1}

    for value_str, unit in matches:
        unit_lower = unit.lower()
        if unit_lower in seen_units:
            raise ValueError("repeated unit")
        unit_idx = order[unit_lower]
        if unit_idx <= last_order:
            raise ValueError("out-of-order unit")
        seen_units.add(unit_lower)
        last_order = unit_idx
        total += int(value_str) * seconds_per[unit_lower]

    return total
