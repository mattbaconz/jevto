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
        raise ValueError("Empty duration string")

    pattern = r'(\d+)([hms])'
    matches = re.findall(pattern, text, re.IGNORECASE)

    if not matches:
        raise ValueError("No valid duration components found")

    text_no_spaces = text.replace(' ', '')
    reconstructed = ''.join(f"{num}{unit}" for num, unit in matches)
    if reconstructed.lower() != text_no_spaces.lower():
        raise ValueError("Invalid characters in duration string")

    unit_seconds = {'h': 3600, 'm': 60, 's': 1}
    unit_order = {'h': 0, 'm': 1, 's': 2}

    total_seconds = 0
    last_unit_index = -1
    seen_units = set()

    for num_str, unit in matches:
        unit_lower = unit.lower()

        if unit_lower in seen_units:
            raise ValueError(f"Duplicate unit: {unit_lower}")

        unit_index = unit_order[unit_lower]
        if unit_index <= last_unit_index:
            raise ValueError("Units out of order")

        last_unit_index = unit_index
        seen_units.add(unit_lower)
        total_seconds += int(num_str) * unit_seconds[unit_lower]

    return total_seconds
