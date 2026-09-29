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
        raise ValueError()

    # Match all <number><unit> pairs, allowing spaces between them
    matches = re.findall(r'(\d+)\s*([a-zA-Z])', text)

    if not matches:
        raise ValueError()

    # Verify that we parsed the entire string (no leftover characters)
    reconstructed = ''.join(num + unit for num, unit in matches)
    if reconstructed != text.replace(' ', ''):
        raise ValueError()

    # Validate order and sum up
    total_seconds = 0
    unit_order = {'h': 0, 'm': 1, 's': 2}
    last_order = -1
    seen_units = set()

    for num_str, unit_char in matches:
        unit = unit_char.lower()

        if unit not in unit_order:
            raise ValueError()

        if unit in seen_units:
            raise ValueError()

        current_order = unit_order[unit]
        if current_order <= last_order:
            raise ValueError()

        seen_units.add(unit)
        last_order = current_order

        num = int(num_str)
        if unit == 'h':
            total_seconds += num * 3600
        elif unit == 'm':
            total_seconds += num * 60
        else:  # 's'
            total_seconds += num

    return total_seconds
