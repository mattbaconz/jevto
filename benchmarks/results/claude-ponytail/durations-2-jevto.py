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

    full_pattern = r"^(?:\s*(\d+)([hms]))+\s*$"
    if not re.match(full_pattern, text, re.IGNORECASE):
        raise ValueError("Invalid duration format")

    pair_pattern = r"(\d+)([hms])"
    pairs = re.findall(pair_pattern, text, re.IGNORECASE)

    seen_units = set()
    unit_order = {"h": 0, "m": 1, "s": 2}
    last_order = -1
    total_seconds = 0

    for num_str, unit in pairs:
        unit_lower = unit.lower()

        if unit_lower in seen_units:
            raise ValueError(f"Repeated unit: {unit}")

        current_order = unit_order[unit_lower]
        if current_order <= last_order:
            raise ValueError("Out of order units")

        seen_units.add(unit_lower)
        last_order = current_order

        num = int(num_str)
        if unit_lower == "h":
            total_seconds += num * 3600
        elif unit_lower == "m":
            total_seconds += num * 60
        elif unit_lower == "s":
            total_seconds += num

    return total_seconds
