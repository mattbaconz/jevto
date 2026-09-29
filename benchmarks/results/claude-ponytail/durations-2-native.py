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
        raise ValueError("Empty text")

    pattern = r'(\d+)([hms])'
    matches = re.findall(pattern, text, re.IGNORECASE)

    if not matches:
        raise ValueError("No valid duration parts found")

    reconstructed = ''
    for num, unit in matches:
        reconstructed += num + unit.lower()

    text_no_spaces = ''.join(text.split()).lower()
    if reconstructed != text_no_spaces:
        raise ValueError("Invalid format")

    unit_order = {'h': 0, 'm': 1, 's': 2}
    unit_multipliers = {'h': 3600, 'm': 60, 's': 1}
    seen_units = set()
    total_seconds = 0
    last_order = -1

    for num_str, unit in matches:
        unit_lower = unit.lower()

        if unit_lower in seen_units:
            raise ValueError("Repeated unit")

        order = unit_order[unit_lower]
        if order <= last_order:
            raise ValueError("Out of order units")

        seen_units.add(unit_lower)
        last_order = order

        num = int(num_str)
        total_seconds += num * unit_multipliers[unit_lower]

    return total_seconds
