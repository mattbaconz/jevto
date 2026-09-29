"""Render the committed benchmark records as standalone, hand-built SVG charts.

Run from anywhere with ``python benchmarks/render_charts.py``. Only the Python
standard library is needed; measurements and outcome counts come from JSON.
"""

from __future__ import annotations

import json
import math
import statistics
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks" / "results"
CHARTS = ROOT / "site" / "assets" / "charts"

PAYLOAD_ARMS = ("native", "rtk", "jevto")
SESSION_ARMS = ("native", "ponytail", "jevto", "ponytail+jevto")
ARM_LABELS = {
    "native": "Native",
    "rtk": "RTK",
    "jevto": "JevTO",
    "ponytail": "Ponytail",
    "ponytail+jevto": "Ponytail + JevTO",
}
ARM_CLASSES = {
    "native": "native",
    "rtk": "rtk",
    "jevto": "jevto",
    "ponytail": "ponytail",
    "ponytail+jevto": "combo",
}
SCENARIO_LABELS = {
    "rust-test-failure": "cargo test, 1 failure",
    "go-test-failure": "go test -v, 1 failure",
    "python-unittest-failure": "unittest -v, 1 failure",
    "python-quiet-warning": "unittest -v, 1 warning",
    "node-test-failure": "node --test, 1 failure",
    "rust-test-lean": "cargo test, 3 passing",
    "service-log-triage": "service log, 2,400 lines",
    "search-many-hits": "rg, 160 matches",
    "git-diff-lockfile": "git diff + lockfile",
    "git-diff-multi-file": "git diff, 14 files",
    "git-log-history": "git log, 260 commits",
    "cargo-multi-failure": "cargo test, 3 failures",
    "tsc-type-errors": "tsc, 3 type errors",
    "go-package-failure": "go test ./..., 12 pkgs",
    "node-all-pass": "node --test, 300 passing",
    "python-stdlib-traceback": "unittest, stdlib traceback",
    "rg-todo-sweep": "rg TODO, 160 matches",
    "git-diff-rename-plus-fix": "git diff, rename + fix",
    "cargo-build-warnings": "cargo build, 40 warnings",
    "json-log-triage": "JSON log, 3,000 lines",
    "semantic-log-signout": "log, sign-out cause",
    "semantic-search-deadline": "rg, vendor deadline",
    "semantic-diff-rounding": "git diff, rounding",
}
SUITE_TITLES = {
    "dev": "Dev set · rules tuned here",
    "holdout": "Holdout · written after the rules froze",
    "semantic": "Semantic · goal and answer share no words",
}

# Brand: ink, paper, signal red, ash. JevTO plots in ink (paper on dark);
# red is reserved for misses.
STYLE = """
svg { font-family: "Segoe UI", -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif;
      font-size: 12px; font-variant-numeric: tabular-nums; }
.text { fill: #111214; }
.muted { fill: #6B6F77; }
.heading { font-size: 13px; font-weight: 700; }
.suite { font-size: 11px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; fill: #6B6F77; }
.big { font-size: 22px; font-weight: 700; }
.grid, .axis, .connector { fill: none; stroke: #E3E1DB; }
.grid { stroke-width: 1; }
.axis { stroke-width: 1.2; stroke: #C9C6BF; }
.connector { stroke-width: 1.3; }
.native-fill { fill: #B4B7BD; }
.rtk-fill { fill: #D9921E; }
.jevto-fill { fill: #111214; }
.ponytail-fill { fill: #4E9A3F; }
.combo-fill { fill: #3F7FBF; }
.native-stroke { fill: none; stroke: #B4B7BD; }
.rtk-stroke { fill: none; stroke: #D9921E; }
.jevto-stroke { fill: none; stroke: #111214; }
.ponytail-stroke { fill: none; stroke: #4E9A3F; }
.combo-stroke { fill: none; stroke: #3F7FBF; }
.miss { fill: none; stroke: #E5231B; stroke-width: 1.9; stroke-linecap: round; }
.unrouted-outer { fill: none; stroke: #B4B7BD; stroke-width: 1.5; }
.unrouted-inner { fill: none; stroke: #D9921E; stroke-width: 1.3; }
.bar { opacity: .95; }
.median { stroke-width: 2.7; stroke-linecap: round; }
.accent { fill: #E5231B; }
@media (prefers-color-scheme: dark) {
  .text { fill: #F2F0EA; }
  .muted, .suite { fill: #9A9EA6; }
  .grid, .axis, .connector { stroke: #2E3137; }
  .jevto-fill { fill: #F2F0EA; }
  .jevto-stroke { stroke: #F2F0EA; }
  .native-fill { fill: #5C6068; }
  .native-stroke, .unrouted-outer { stroke: #5C6068; }
}
""".strip()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def svg(width: int, height: int, ident: str, title: str, description: str,
        content: list[str]) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-labelledby="{ident}-title {ident}-desc">\n'
        f'<title id="{ident}-title">{escape(title)}</title>\n'
        f'<desc id="{ident}-desc">{escape(description)}</desc>\n'
        f'<style>\n{STYLE}\n</style>\n'
        + "\n".join(content)
        + "\n</svg>\n"
    )


def txt(x: float, y: float, value: object, classes: str = "text",
        anchor: str | None = None) -> str:
    anchor_attr = f' text-anchor="{anchor}"' if anchor else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" class="{classes}"'
            f'{anchor_attr}>{escape(str(value))}</text>')


def line(x1: float, y1: float, x2: float, y2: float, classes: str) -> str:
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" '
            f'x2="{x2:.1f}" y2="{y2:.1f}" class="{classes}"/>')


def circle(x: float, y: float, radius: float, classes: str) -> str:
    return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" '
            f'class="{classes}"/>')


def rect(x: float, y: float, width: float, height: float, classes: str) -> str:
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" '
            f'height="{height:.1f}" rx="2" class="{classes}"/>')


def missed_mark(x: float, y: float) -> list[str]:
    # Keep the cross within the hollow ring so close arm values do not collide.
    return [line(x - 2, y - 2, x + 2, y + 2, "miss"),
            line(x + 2, y - 2, x - 2, y + 2, "miss")]


def not_routed(rewritten: object) -> bool:
    if not isinstance(rewritten, str):
        return True
    marker = rewritten.strip().casefold().replace("_", " ").replace("-", " ")
    return not marker or marker in {"null", "none"} or "not routed" in marker


def facts(arm: dict) -> str:
    return f"{arm['needles_found']}/{arm['needles_total']}"


def payload_totals(rows: list[dict]) -> dict[str, dict[str, int]]:
    return {
        arm: {
            "tokens": sum(row["arms"][arm]["est_tokens"] for row in rows),
            "found": sum(row["arms"][arm]["needles_found"] for row in rows),
            "total": sum(row["arms"][arm]["needles_total"] for row in rows),
        }
        for arm in PAYLOAD_ARMS
    }


def short_number(value: int) -> str:
    if value >= 1_000_000 and value % 1_000_000 == 0:
        return f"{value // 1_000_000}M"
    if value >= 1000 and value % 1000 == 0:
        return f"{value // 1000}k"
    return str(value)


def nice_axis(maximum: int, desired_steps: int) -> tuple[int, list[int]]:
    rough = maximum / desired_steps
    magnitude = 10 ** math.floor(math.log10(rough))
    step = next(int(factor * magnitude) for factor in (1, 2, 2.5, 5, 10)
                if factor * magnitude >= rough)
    end = math.ceil(maximum / step) * step
    return end, list(range(0, end + 1, step))


def scenario_chart(rows: list[dict], totals: dict[str, dict[str, int]]) -> str:
    rows = [row for row in rows if not row.get("skipped")]
    suites = [suite for suite in SUITE_TITLES if any(row.get("suite", "dev") == suite for row in rows)]
    width = 880
    plot_left, plot_right = 250, 840
    row_gap, header_gap, top = 30, 44, 78
    layout, y = [], top
    for suite in suites:
        y += header_gap
        layout.append(("header", suite, y - 22))
        for row in rows:
            if row.get("suite", "dev") == suite:
                layout.append(("row", row, y))
                y += row_gap
    axis_y = y + 4
    height = axis_y + 58
    values = [row["arms"][arm]["est_tokens"] for row in rows for arm in PAYLOAD_ARMS]
    first_power = math.floor(math.log10(min(values)))
    last_power = math.ceil(math.log10(max(values)))
    ticks = [10 ** power for power in range(first_power, last_power + 1)]

    def xpos(value: int) -> float:
        return plot_left + (math.log10(value) - first_power) / (last_power - first_power) * (plot_right - plot_left)

    description = (
        f"Estimated tokens per command on a log scale for {len(rows)} scenarios in {len(suites)} suites. "
        + "; ".join(f"{ARM_LABELS[arm]} totals {totals[arm]['tokens']:,} tokens and "
                    f"{totals[arm]['found']}/{totals[arm]['total']} facts" for arm in PAYLOAD_ARMS)
        + ". Hollow dots with a red cross missed a required fact; double rings were not routed by RTK."
    )
    out: list[str] = []
    legend_x = plot_left
    for arm, label in (("native", "Native"), ("rtk", "RTK"), ("jevto", "JevTO")):
        out.append(circle(legend_x, 29, 4.5, f"{ARM_CLASSES[arm]}-fill"))
        out.append(txt(legend_x + 11, 33, label))
        legend_x += 80
    out.append(circle(legend_x + 10, 29, 4.5, "jevto-stroke"))
    out.extend(missed_mark(legend_x + 10, 29))
    out.append(txt(legend_x + 24, 33, "missed a fact"))
    out.append(circle(legend_x + 138, 29, 5, "unrouted-outer"))
    out.append(circle(legend_x + 138, 29, 3, "unrouted-inner"))
    out.append(txt(legend_x + 150, 33, "RTK did not route"))
    for tick in ticks:
        x = xpos(tick)
        out.append(line(x, top - 8, x, axis_y, "grid"))
        out.append(txt(x, axis_y + 20, short_number(tick), "muted", "middle"))
    out.append(line(plot_left, axis_y, plot_right, axis_y, "axis"))
    for kind, item, center in layout:
        if kind == "header":
            out.append(txt(24, center, SUITE_TITLES[item], "suite"))
            continue
        row = item
        label = SCENARIO_LABELS.get(row["name"], row["name"].replace("-", " "))
        out.append(txt(230, center + 4, label, "text", "end"))
        arms = row["arms"]
        native_x = xpos(arms["native"]["est_tokens"])
        positions = {
            "native": (native_x, center - 8),
            "rtk": (native_x if not_routed(arms["rtk"]["rewritten"]) else xpos(arms["rtk"]["est_tokens"]), center),
            "jevto": (xpos(arms["jevto"]["est_tokens"]), center + 8),
        }
        for arm in ("rtk", "jevto"):
            x, y = positions[arm]
            out.append(line(native_x, center - 8, x, y, "connector"))
        for arm in PAYLOAD_ARMS:
            x, y = positions[arm]
            record = arms[arm]
            unrouted = arm == "rtk" and not_routed(record["rewritten"])
            missed = record["needles_found"] < record["needles_total"]
            note = "; not routed, plotted at native value" if unrouted else ""
            note += "; missed required fact" if missed else ""
            tooltip = (f"{label}: {ARM_LABELS[arm]}, {record['est_tokens']:,} estimated tokens, "
                       f"{facts(record)} facts{note}. Command: {row['command']}")
            out.append(f"<g><title>{escape(tooltip)}</title>")
            if unrouted:
                out.append(circle(x, y, 5, "unrouted-outer"))
                out.append(circle(x, y, 3, "unrouted-inner"))
            elif missed:
                out.append(circle(x, y, 4.5, f"{ARM_CLASSES[arm]}-stroke"))
            else:
                out.append(circle(x, y, 4.5, f"{ARM_CLASSES[arm]}-fill"))
            if missed:
                out.extend(missed_mark(x, y))
            out.append("</g>")
    out.append(txt((plot_left + plot_right) / 2, axis_y + 44, "Estimated tokens per command (log scale)", "muted", "middle"))
    return svg(width, height, "scenarios", "Tokens per command, by scenario", description, out)


def totals_chart(rows: list[dict], totals: dict[str, dict[str, int]], rtk_version: str) -> str:
    """Headline: total tokens per arm, dev set vs unseen scenarios."""
    rows = [row for row in rows if not row.get("skipped")]
    groups = [
        ("Dev set", "11 scenarios, rules tuned here", [r for r in rows if r.get("suite", "dev") == "dev"]),
        ("Unseen", "holdout + semantic, written after the rules froze", [r for r in rows if r.get("suite", "dev") != "dev"]),
    ]
    width, height = 880, 300
    rtk_label = "RTK " + ".".join(rtk_version.split()[-1].split(".")[:2])
    out: list[str] = []
    column_width = 400
    for index, (title, subtitle, group) in enumerate(groups):
        if not group:
            continue
        left = 24 + index * (column_width + 32)
        group_totals = payload_totals(group)
        native_total = group_totals["native"]["tokens"]
        out.append(txt(left, 30, title, "text heading"))
        out.append(txt(left, 48, f"{len(group)} scenarios · {subtitle.split(', ', 1)[-1]}", "muted"))
        bar_left, bar_right = left + 70, left + column_width - 10
        for row_index, arm in enumerate(PAYLOAD_ARMS):
            y = 76 + row_index * 64
            total = group_totals[arm]
            share = total["tokens"] / native_total
            label = rtk_label if arm == "rtk" else ARM_LABELS[arm]
            out.append(txt(left, y + 14, label, "text"))
            out.append(rect(bar_left, y, max(3.0, share * (bar_right - bar_left)), 20, f"bar {ARM_CLASSES[arm]}-fill"))
            reduction = "" if arm == "native" else f"−{round(100 * (1 - share))}% · "
            miss = total["found"] < total["total"]
            out.append(txt(bar_left, y + 40, f"{reduction}{total['tokens']:,} tokens · {total['found']}/{total['total']} facts",
                           "accent" if miss else "muted"))
    out.append(txt(24, height - 12, "Estimated tokens (bytes/4) delivered to the agent, summed per suite. "
                   "Facts = required strings visible without recall; red = at least one missed.", "muted"))
    description = "; ".join(
        f"{title}: " + ", ".join(
            f"{ARM_LABELS[arm]} {payload_totals(group)[arm]['tokens']:,} tokens, "
            f"{payload_totals(group)[arm]['found']}/{payload_totals(group)[arm]['total']} facts"
            for arm in PAYLOAD_ARMS)
        for title, _, group in groups if group)
    return svg(width, height, "totals", "Total tokens: dev set vs unseen scenarios", description, out)


def grouped_records(records: list[dict]) -> dict[str, list[dict]]:
    grouped = {arm: sorted((row for row in records if row["arm"] == arm),
                           key=lambda row: row["rep"])
               for arm in SESSION_ARMS}
    if sum(map(len, grouped.values())) != len(records):
        raise ValueError("Unrecognized session arm")
    counts = {len(grouped[arm]) for arm in SESSION_ARMS}
    if len(counts) != 1 or not counts or 0 in counts:
        raise ValueError("Session arms must have equal, nonzero run counts")
    if any(not row["valid"] for row in records):
        raise ValueError("Session chart requires all plotted runs to be valid")
    return grouped


def median_by_arm(grouped: dict[str, list[dict]], field: str) -> dict[str, int]:
    return {arm: int(statistics.median(row[field] for row in grouped[arm]))
            for arm in SESSION_ARMS}


def bytes_label(value: int) -> str:
    return f"{value / 1000:.1f} KB" if value >= 1000 else f"{value:,} B"


def sessions_chart(claude: dict[str, list[dict]],
                   codex: dict[str, list[dict]]) -> str:
    width, height = 880, 380
    output_bytes = median_by_arm(claude, "test_result_bytes")
    claude_tokens = median_by_arm(claude, "combined_tokens")
    codex_tokens = median_by_arm(codex, "input_tokens")
    all_claude = [row for arm in SESSION_ARMS for row in claude[arm]]
    all_codex = [row for arm in SESSION_ARMS for row in codex[arm]]
    claude_passes = sum(row["holdout_pass"] for row in all_claude)
    codex_passes = sum(row["holdout_pass"] for row in all_codex)
    runs_per_arm = len(claude["native"])
    if any(len(codex[arm]) != runs_per_arm for arm in SESSION_ARMS):
        raise ValueError("Both harnesses must have the same runs per arm")

    description = (
        "Claude Code test output median bytes by arm: "
        + ", ".join(f"{ARM_LABELS[arm]} {output_bytes[arm]:,}"
                    for arm in SESSION_ARMS)
        + ". Each session run is a dot and each median is a vertical tick. "
        + "Claude combined-token medians: "
        + ", ".join(f"{ARM_LABELS[arm]} {claude_tokens[arm]:,}"
                    for arm in SESSION_ARMS)
        + ". Codex input-token medians (including cached input): "
        + ", ".join(f"{ARM_LABELS[arm]} {codex_tokens[arm]:,}"
                    for arm in SESSION_ARMS)
        + f". Holdout passes: Claude {claude_passes}/{len(all_claude)}, "
        + f"Codex {codex_passes}/{len(all_codex)}. "
        + f"{runs_per_arm} valid runs per arm on one task; indicative, not significant."
    )
    out: list[str] = [line(422, 20, 422, 348, "grid")]

    # Left: exact medians determine both bar lengths and the displayed byte labels.
    out.append(txt(26, 28, "Test output the model read", "text heading"))
    out.append(txt(26, 47, "Bytes, median · Claude Code", "muted"))
    left_start, left_end = 166, 306
    left_max, left_ticks = nice_axis(max(output_bytes.values()), 4)
    for tick in left_ticks:
        x = left_start + tick / left_max * (left_end - left_start)
        out.append(line(x, 84, x, 241, "grid"))
        out.append(txt(x, 76, short_number(tick), "muted", "middle"))
    out.append(line(left_start, 241, left_end, 241, "axis"))
    for index, arm in enumerate(SESSION_ARMS):
        y = 100 + index * 38
        out.append(txt(27, y + 13, ARM_LABELS[arm]))
        bar_width = output_bytes[arm] / left_max * (left_end - left_start)
        out.append(rect(left_start, y, bar_width, 16,
                        f"bar {ARM_CLASSES[arm]}-fill"))
        out.append(txt(316, y + 13, bytes_label(output_bytes[arm])))
    out.append(txt(26, 279, "Holdout passed (valid runs):", "muted"))
    out.append(txt(26, 298, f"Claude {claude_passes}/{len(all_claude)} · "
                   f"Codex {codex_passes}/{len(all_codex)}", "text"))

    # Right: the dot positions use individual sessions, including cached Codex
    # input; the median tick is computed independently from all three runs.
    out.append(txt(440, 28, "Session tokens per run", "text heading"))
    out.append(txt(440, 47, "Dots = runs · vertical tick = median", "muted"))
    out.append(txt(440, 64, "Codex input includes cached tokens", "muted"))
    right_start, right_end = 589, 850
    all_token_values = ([row["combined_tokens"] for row in all_claude]
                        + [row["input_tokens"] for row in all_codex])
    right_max, right_ticks = nice_axis(max(all_token_values), 3)

    def right_x(value: int) -> float:
        return right_start + value / right_max * (right_end - right_start)

    for tick in right_ticks:
        x = right_x(tick)
        out.append(line(x, 91, x, 333, "grid"))
        out.append(txt(x, 352, short_number(tick), "muted", "middle"))
    out.append(line(right_start, 333, right_end, 333, "axis"))

    for heading_y, first_y, heading, grouped, field, medians in (
        (87, 108, "Claude Code · Haiku 4.5", claude, "combined_tokens", claude_tokens),
        (216, 237, "Codex CLI · gpt-6-sol", codex, "input_tokens", codex_tokens),
    ):
        out.append(txt(440, heading_y, heading, "text heading"))
        for index, arm in enumerate(SESSION_ARMS):
            y = first_y + index * 23
            out.append(txt(440, y + 4, ARM_LABELS[arm]))
            color = ARM_CLASSES[arm]
            median = medians[arm]
            out.append(line(right_x(median), y - 8, right_x(median), y + 8,
                            f"{color}-stroke median"))
            for rep_index, row in enumerate(grouped[arm]):
                dot_y = y + (rep_index - (runs_per_arm - 1) / 2) * 5
                tooltip = (f"{heading}, {ARM_LABELS[arm]}, run {row['rep']}: "
                           f"{row[field]:,} tokens; median {median:,} tokens; "
                           f"holdout {'passed' if row['holdout_pass'] else 'failed'}")
                out.append(f"<g><title>{escape(tooltip)}</title>"
                           + circle(right_x(row[field]), dot_y, 3.1,
                                    f"{color}-fill") + "</g>")

    out.append(txt(26, 374,
                   f"n = {runs_per_arm} per arm, one task. Indicative, not significant.",
                   "muted"))
    return svg(width, height, "sessions", "Agent session benchmark",
               description, out)


def main() -> None:
    payload = read_json(RESULTS / "token-bench.json")
    rows = payload["results"]
    totals = payload_totals(rows)
    claude = grouped_records(read_json(RESULTS / "claude-ponytail" / "records.json"))
    codex = grouped_records(read_json(RESULTS / "codex-ponytail" / "records.json"))

    charts = {
        "payload-by-scenario.svg": scenario_chart(rows, totals),
        "payload-totals.svg": totals_chart(rows, totals,
                                           payload["meta"]["rtk_version"]),
        "sessions.svg": sessions_chart(claude, codex),
    }
    CHARTS.mkdir(parents=True, exist_ok=True)
    for filename, content in charts.items():
        (CHARTS / filename).write_text(content, encoding="utf-8")

    print("Generated charts:")
    for filename in charts:
        path = CHARTS / filename
        print(f"  {path.relative_to(ROOT)}: {path.stat().st_size:,} bytes")
    print("Payload totals (estimated tokens, required facts, percent of native):")
    native_total = totals["native"]["tokens"]
    for arm in PAYLOAD_ARMS:
        total = totals[arm]
        print(f"  {ARM_LABELS[arm]}: {total['tokens']:,}, "
              f"{total['found']}/{total['total']}, "
              f"{round(100 * total['tokens'] / native_total)}%")
    unrouted = [row["name"] for row in rows
                if not_routed(row["arms"]["rtk"]["rewritten"])]
    print(f"RTK not routed ({len(unrouted)}): {', '.join(unrouted)}")
    print("Sessions (run tokens -> median; Claude median test bytes; "
          "Codex median uncached input):")
    for arm in SESSION_ARMS:
        claude_runs = [row["combined_tokens"] for row in claude[arm]]
        codex_runs = [row["input_tokens"] for row in codex[arm]]
        uncached = [row["input_tokens"] - row["cached_input_tokens"]
                    for row in codex[arm]]
        claude_bytes = int(statistics.median(
            row["test_result_bytes"] for row in claude[arm]))
        print(f"  {ARM_LABELS[arm]}: Claude {claude_runs} -> "
              f"{int(statistics.median(claude_runs)):,}, {claude_bytes:,} B; "
              f"Codex {codex_runs} -> {int(statistics.median(codex_runs)):,}, "
              f"uncached {int(statistics.median(uncached)):,}")
    for name, grouped in (("Claude", claude), ("Codex", codex)):
        records = [row for arm in SESSION_ARMS for row in grouped[arm]]
        print(f"{name}: {sum(row['valid'] for row in records)}/{len(records)} "
              f"valid, {sum(row['holdout_pass'] for row in records)}/"
              f"{len(records)} holdout passes")


if __name__ == "__main__":
    main()
