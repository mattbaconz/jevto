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

    matches = re.findall(r'(\d+)([a-zA-Z])', text)

    if not matches:
        raise ValueError("No valid duration parts found")

    if text.replace(' ', '') != ''.join([f"{num}{unit}" for num, unit in matches]):
        raise ValueError("Invalid duration format")

    total_seconds = 0
    seen_units = set()
    unit_order = {'h': 0, 'm': 1, 's': 2}
    last_unit_order = -1

    for num_str, unit in matches:
        unit_lower = unit.lower()

        if unit_lower not in unit_order:
            raise ValueError(f"Unknown unit: {unit}")

        current_unit_order = unit_order[unit_lower]
        if current_unit_order <= last_unit_order:
            raise ValueError("Units out of order or repeated")
        last_unit_order = current_unit_order

        if unit_lower in seen_units:
            raise ValueError(f"Duplicate unit: {unit_lower}")
        seen_units.add(unit_lower)

        num = int(num_str)
        if unit_lower == 'h':
            total_seconds += num * 3600
        elif unit_lower == 'm':
            total_seconds += num * 60
        else:
            total_seconds += num

    return total_seconds
