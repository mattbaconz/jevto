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
    matches = list(re.finditer(pattern, text, re.IGNORECASE))

    if not matches:
        raise ValueError("No valid parts found")

    matched_positions = set()
    for match in matches:
        for i in range(match.start(), match.end()):
            matched_positions.add(i)

    for i, char in enumerate(text):
        if not char.isspace() and i not in matched_positions:
            raise ValueError("Invalid text")

    unit_order = {'h': 0, 'm': 1, 's': 2}
    seen_units = {}
    last_order = -1

    total = 0
    for match in matches:
        number = int(match.group(1))
        unit = match.group(2).lower()

        if unit in seen_units:
            raise ValueError("Repeated unit")

        current_order = unit_order[unit]
        if current_order < last_order:
            raise ValueError("Out of order units")

        seen_units[unit] = True
        last_order = current_order

        if unit == 'h':
            total += number * 3600
        elif unit == 'm':
            total += number * 60
        elif unit == 's':
            total += number

    return total
