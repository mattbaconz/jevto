"""Duration parsing."""


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

    units = {'h': 3600, 'm': 60, 's': 1}
    unit_order = {'h': 0, 'm': 1, 's': 2}

    seen_units = set()
    last_order = -1
    total_seconds = 0
    i = 0

    while i < len(text):
        if text[i] == ' ':
            i += 1
            continue

        if not text[i].isdigit():
            raise ValueError("invalid character")

        num_str = ""
        while i < len(text) and text[i].isdigit():
            num_str += text[i]
            i += 1

        if i >= len(text):
            raise ValueError("number without unit")

        unit = text[i].lower()

        if unit not in units:
            raise ValueError("unknown unit")

        if unit in seen_units:
            raise ValueError("repeated unit")

        current_order = unit_order[unit]
        if current_order < last_order:
            raise ValueError("out-of-order unit")

        last_order = current_order
        seen_units.add(unit)
        total_seconds += int(num_str) * units[unit]
        i += 1

    return total_seconds
