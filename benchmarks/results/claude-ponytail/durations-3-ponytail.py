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

    unit_multipliers = {"h": 3600, "m": 60, "s": 1}
    unit_order = {"h": 0, "m": 1, "s": 2}
    seen_units = set()
    last_order = -1
    total = 0

    parts = re.findall(r"(\d+)([a-zA-Z])", text)
    if not parts:
        raise ValueError("invalid format")

    for num_str, unit in parts:
        unit_lower = unit.lower()
        if unit_lower not in unit_multipliers:
            raise ValueError(f"unknown unit: {unit}")
        if unit_lower in seen_units:
            raise ValueError(f"repeated unit: {unit_lower}")

        current_order = unit_order[unit_lower]
        if current_order <= last_order:
            raise ValueError(f"out-of-order unit: {unit_lower}")

        seen_units.add(unit_lower)
        last_order = current_order
        total += int(num_str) * unit_multipliers[unit_lower]

    remaining_text = re.sub(r"(\d+)([a-zA-Z])", "", text).strip()
    if remaining_text:
        raise ValueError("invalid characters in text")

    return total
