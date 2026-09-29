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

    pattern = r'(\d+)([hms])'
    matches = re.findall(pattern, text, re.IGNORECASE)

    if not matches:
        raise ValueError("no valid duration parts")

    reconstructed = ''.join(f"{num}{unit}" for num, unit in matches)
    normalized = text.replace(' ', '')
    if reconstructed.lower() != normalized.lower():
        raise ValueError("invalid format")

    unit_values = {'h': 3600, 'm': 60, 's': 1}
    unit_rank = {'h': 0, 'm': 1, 's': 2}
    seen_units = set()
    last_rank = -1
    total = 0

    for num_str, unit in matches:
        unit_lower = unit.lower()
        if unit_lower in seen_units:
            raise ValueError(f"repeated unit: {unit}")

        current_rank = unit_rank[unit_lower]
        if current_rank <= last_rank:
            raise ValueError("out of order units")

        seen_units.add(unit_lower)
        last_rank = current_rank
        total += int(num_str) * unit_values[unit_lower]

    return total
