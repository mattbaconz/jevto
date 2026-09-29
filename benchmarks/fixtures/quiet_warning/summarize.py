"""Sum value events from a line-oriented log."""


def total(lines):
    values = []
    for line in lines:
        if line.strip():
            values.append(int(line.split("value=", 1)[1]))
    return sum(values)

