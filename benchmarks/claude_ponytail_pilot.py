"""Claude Code agent pilot: native vs Ponytail vs JevTO vs Ponytail + JevTO.

Ponytail (write side) injects "simplest working code" instructions. JevTO
(read side) routes the agent's test commands through `jevto run` via the
`init-claude-auto` hooks. They act on different token streams, so the pilot
measures both code size and session tokens on one realistic task:

    implement parse_duration() in a small stdlib-only Python package, then
    verify with the full `python -m unittest -v` suite (260 tests).

Each arm runs in a fresh copy of the same repository with
`--setting-sources project,local`, so the user's global hooks, plugins, and
output style are excluded. Ponytail is loaded per arm with `--plugin-dir`;
JevTO is installed per arm into the checkout's `.claude/settings.local.json`.
After each session the harness reruns the visible suite and a hidden holdout.

This is a small exploratory pilot on a subscription (costs are Claude CLI
list-price estimates), not a general cost or quality claim.

Usage:
    python benchmarks/claude_ponytail_pilot.py --jevto target/release/jevto.exe \
        --ponytail PATH_TO_PONYTAIL_PLUGIN --reps 2
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
MODEL = "claude-haiku-4-5-20251001"
ARMS = ("native", "ponytail", "jevto", "ponytail+jevto")
PROMPT = (
    "This repository is a small stdlib-only Python package. Implement "
    "`parse_duration(text)` in `timekit/durations.py` according to its docstring. "
    "Do not modify anything under `tests/`. Verify with the full test suite by "
    "running exactly `python -m unittest -v` from the repository root, fix any "
    "failures, and finish with a one-line summary of the result."
)

DURATIONS = '''"""Duration parsing."""


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
    raise NotImplementedError
'''

VISIBLE_TESTS = '''import unittest

from timekit.durations import parse_duration


class ParseDurationTest(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(parse_duration("90s"), 90)

    def test_minutes(self):
        self.assertEqual(parse_duration("15m"), 900)

    def test_hours_and_minutes(self):
        self.assertEqual(parse_duration("1h30m"), 5400)

    def test_spaced_parts(self):
        self.assertEqual(parse_duration("1h 30m 15s"), 5415)

    def test_case_and_whitespace(self):
        self.assertEqual(parse_duration(" 2H "), 7200)

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            parse_duration("")

    def test_rejects_unknown_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("3d")

    def test_rejects_missing_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("10")


if __name__ == "__main__":
    unittest.main()
'''

HOLDOUT = '''import sys
sys.path.insert(0, ".")
from timekit.durations import parse_duration

ok = [("0s", 0), ("1h1m1s", 3661), ("45M", 2700), ("2h 5s", 7205), ("\\t10m\\n", 600)]
bad = ["1.5h", "-5m", "1m1h", "1h1h", "h1", "1x", "1h  30m", " ", "5 m", "+5m"]
failures = []
for text, want in ok:
    try:
        got = parse_duration(text)
        if got != want:
            failures.append(f"{text!r}: got {got}, want {want}")
    except Exception as error:
        failures.append(f"{text!r}: raised {type(error).__name__}")
for text in bad:
    try:
        got = parse_duration(text)
        failures.append(f"{text!r}: returned {got}, want ValueError")
    except ValueError:
        pass
    except Exception as error:
        failures.append(f"{text!r}: raised {type(error).__name__}, want ValueError")
print("\\n".join(failures) or "holdout ok")
sys.exit(1 if failures else 0)
'''


def build_repo(root: Path) -> None:
    (root / "timekit").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "timekit" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (root / "timekit" / "durations.py").write_text(DURATIONS, encoding="utf-8", newline="\n")
    (root / "timekit" / "text.py").write_text(
        "def slug(value: str) -> str:\n    return '-'.join(value.lower().split())\n\n\n"
        "def initials(value: str) -> str:\n    return ''.join(word[0].upper() for word in value.split())\n",
        encoding="utf-8", newline="\n")
    lines = ["import unittest", "", "from timekit.text import initials, slug", "", "", "class TextTest(unittest.TestCase):"]
    for n in range(126):
        lines.append(f"    def test_slug_{n:03}(self):\n        self.assertEqual(slug('Alpha Beta {n}'), 'alpha-beta-{n}')\n")
        lines.append(f"    def test_initials_{n:03}(self):\n        self.assertEqual(initials('ada b{n}'), 'AB')\n")
    lines.append("\nif __name__ == '__main__':\n    unittest.main()\n")
    (root / "tests" / "test_text.py").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    (root / "tests" / "test_durations.py").write_text(VISIBLE_TESTS, encoding="utf-8", newline="\n")
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "pilot@example.invalid"],
                 ["config", "user.name", "Pilot"], ["config", "core.autocrlf", "false"],
                 ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def run(argv, cwd, env=None, timeout=120):
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=timeout)


def parse_stream(text: str) -> dict:
    terminal = None
    tool_uses = {}
    result_bytes = {}
    init = None
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            init = event
        if event.get("type") == "result":
            terminal = event
        message = event.get("message")
        content_blocks = message.get("content") if isinstance(message, dict) else None
        for block in content_blocks if isinstance(content_blocks, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tool_uses[block.get("id")] = {"name": block.get("name"), "input": block.get("input") or {}}
            if block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
                result_bytes[block.get("tool_use_id")] = len(str(content or "").encode("utf-8"))
    return {"terminal": terminal or {}, "tool_uses": tool_uses, "result_bytes": result_bytes, "init": init or {}}


def run_arm(arm: str, rep: int, args, out: Path, env: dict) -> dict:
    arm_dir = out / f"{rep}-{arm.replace('+', '-')}"
    checkout = arm_dir / "checkout"
    checkout.mkdir(parents=True)
    build_repo(checkout)
    arm_env = dict(env, PYTHONDONTWRITEBYTECODE="1", NO_COLOR="1", GIT_PAGER="cat", PAGER="cat")
    if "jevto" in arm:
        store = arm_dir / "store"
        arm_env["JEVTO_STORE_DIR"] = str(store)
        setup = run([args.jevto, "init-claude-auto", "--workspace", str(checkout), "--apply"], checkout, arm_env)
        if setup.returncode:
            raise RuntimeError(setup.stderr.decode(errors="replace"))
        # Keep the hook config out of the agent's `git status`/diff noise.
        (checkout / ".git" / "info" / "exclude").write_text(".claude/\n", encoding="utf-8")
    argv = [args.claude, "-p", PROMPT, "--model", MODEL, "--setting-sources", "project,local",
            "--strict-mcp-config", "--no-session-persistence",
            "--tools", "Read,Edit,Write,Bash,Glob,Grep",
            "--allowedTools", "Read,Edit,Write,Bash,Glob,Grep",
            "--permission-mode", "acceptEdits",
            "--max-turns", "30", "--max-budget-usd", str(args.budget),
            "--output-format", "stream-json", "--verbose"]
    if "ponytail" in arm:
        argv += ["--plugin-dir", args.ponytail]
    started = time.monotonic()
    try:
        process = run(argv, checkout, arm_env, timeout=args.timeout)
        stream, cli_exit, timed_out = process.stdout.decode("utf-8", "replace"), process.returncode, False
    except subprocess.TimeoutExpired as error:
        stream, cli_exit, timed_out = (error.stdout or b"").decode("utf-8", "replace"), None, True
    elapsed = round(time.monotonic() - started, 1)
    (arm_dir / "stream.jsonl").write_text(stream, encoding="utf-8")
    (arm_dir / "meta.json").write_text(json.dumps({"elapsed_s": elapsed, "cli_exit": cli_exit, "timed_out": timed_out}), encoding="utf-8")
    return analyze(arm, rep, arm_dir, arm_env)


def analyze(arm: str, rep: int, arm_dir: Path, arm_env: dict) -> dict:
    """Score a finished session from its saved stream and checkout."""
    checkout = arm_dir / "checkout"
    meta = json.loads((arm_dir / "meta.json").read_text(encoding="utf-8"))
    elapsed, cli_exit, timed_out = meta["elapsed_s"], meta["cli_exit"], meta["timed_out"]
    stream = (arm_dir / "stream.jsonl").read_text(encoding="utf-8")
    parsed = parse_stream(stream)
    terminal = parsed["terminal"]
    usage = (terminal.get("modelUsage") or {}).get(MODEL, {})
    fields = ("inputTokens", "cacheCreationInputTokens", "cacheReadInputTokens", "outputTokens")
    combined = sum(usage.get(field, 0) for field in fields) if usage else None
    plugins = [plugin.get("name") for plugin in parsed["init"].get("plugins") or []]
    test_calls = [(tool_id, use) for tool_id, use in parsed["tool_uses"].items()
                  if use["name"] == "Bash" and "unittest" in str(use["input"].get("command", ""))]
    test_result_bytes = sum(parsed["result_bytes"].get(tool_id, 0) for tool_id, _ in test_calls)
    receipts = list((arm_dir / "store" / "receipts").glob("*.json")) if "jevto" in arm else []
    # Independent verification on the final tree.
    visible = run([sys.executable, "-m", "unittest", "-q"], checkout, arm_env)
    (arm_dir / "holdout.py").write_text(HOLDOUT, encoding="utf-8")
    holdout = run([sys.executable, str(arm_dir / "holdout.py")], checkout, arm_env)
    numstat = run(["git", "diff", "--numstat", "--", "timekit/durations.py"], checkout).stdout.decode()
    added = removed = 0
    for line in numstat.splitlines():
        a, r, _ = line.split("\t", 2)
        added, removed = added + int(a), removed + int(r)
    changed = run(["git", "status", "--porcelain", "--untracked-files=all"], checkout).stdout.decode().split("\n")
    changed = sorted(line[3:] for line in changed if line.strip())
    body = (checkout / "timekit" / "durations.py").read_text(encoding="utf-8")
    impl_lines = [line for line in body.split('"""')[-1].splitlines() if line.strip() and not line.strip().startswith("#")]
    (arm_dir / "durations.py").write_text(body, encoding="utf-8")
    record = {
        "arm": arm, "rep": rep, "elapsed_s": elapsed, "cli_exit": cli_exit, "timed_out": timed_out,
        "subtype": terminal.get("subtype"), "num_turns": terminal.get("num_turns"),
        "cost_usd_list_estimate": terminal.get("total_cost_usd"),
        "usage": {field: usage.get(field) for field in fields}, "combined_tokens": combined,
        "plugins": plugins, "bash_test_runs": len(test_calls), "test_result_bytes": test_result_bytes,
        "jevto_receipts": len(receipts),
        "visible_pass": visible.returncode == 0, "holdout_pass": holdout.returncode == 0,
        "holdout_detail": holdout.stdout.decode(errors="replace").strip()[:600],
        "lines_added": added, "lines_removed": removed, "implementation_lines": len(impl_lines),
        "changed_files": changed,
        "permission_denials": stream.count("has been denied"),
        "valid": terminal.get("subtype") == "success" and stream.count("has been denied") == 0 and not timed_out,
        "tests_untouched": not any(path.startswith("tests/") for path in changed),
    }
    (arm_dir / "record.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def summarize(records: list[dict]) -> str:
    lines = [
        "# Claude Code pilot: native vs Ponytail vs JevTO",
        "",
        f"Model `{MODEL}`, Claude Code with `--setting-sources project,local` (user hooks, plugins, and output style excluded). Task: implement `parse_duration` and verify with the full 260-test `unittest -v` suite. Holdout: 15 hidden cases. Costs are Claude CLI list-price estimates under a subscription, not a bill.",
        "",
        "| Arm | Rep | Holdout | Visible | Impl. lines | Test runs | Test output B seen | Turns | Output tok | Combined tok | Est. $ | Time s |",
        "| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in records:
        lines.append(
            f"| {r['arm']} | {r['rep']} | {'pass' if r['holdout_pass'] else 'FAIL'} | {'pass' if r['visible_pass'] else 'FAIL'} | "
            f"{r['implementation_lines']} | {r['bash_test_runs']} | {r['test_result_bytes']:,} | {r['num_turns']} | "
            f"{(r['usage'].get('outputTokens') or 0):,} | {(r['combined_tokens'] or 0):,} | {(r['cost_usd_list_estimate'] or 0):.4f} | {r['elapsed_s']} |")
    lines += ["", "## Medians by arm", "", "| Arm | n | Holdout passes | Impl. lines | Test output B | Output tok | Combined tok | Est. $ | Time s |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for arm in ARMS:
        rows = [r for r in records if r["arm"] == arm]
        if not rows:
            continue
        med = lambda key: statistics.median(key(r) or 0 for r in rows)
        lines.append(
            f"| {arm} | {len(rows)} | {sum(r['holdout_pass'] for r in rows)}/{len(rows)} | {med(lambda r: r['implementation_lines']):.0f} | "
            f"{med(lambda r: r['test_result_bytes']):,.0f} | {med(lambda r: r['usage'].get('outputTokens')):,.0f} | "
            f"{med(lambda r: r['combined_tokens']):,.0f} | {med(lambda r: r['cost_usd_list_estimate']):.4f} | {med(lambda r: r['elapsed_s']):.1f} |")
    lines += ["", "Invalid or failing arms are reported, not dropped. With n this small, differences are indicative only.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jevto", required=True)
    parser.add_argument("--ponytail", required=True)
    parser.add_argument("--claude", default=shutil.which("claude") or "claude")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--budget", type=float, default=0.60)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--out", default=str(ROOT / "benchmarks" / "results" / "claude-ponytail"))
    parser.add_argument("--reanalyze", help="score an existing work dir without new sessions")
    args = parser.parse_args()
    args.jevto = str(Path(args.jevto).resolve())
    arms = [arm for arm in args.arms.split(",") if arm in ARMS]
    records = []
    if args.reanalyze:
        out = Path(args.reanalyze)
        for arm_dir in sorted(path for path in out.iterdir() if (path / "meta.json").is_file()):
            rep_text, arm_name = arm_dir.name.split("-", 1)
            arm = arm_name.replace("ponytail-jevto", "ponytail+jevto")
            arm_env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", JEVTO_STORE_DIR=str(arm_dir / "store"))
            records.append(analyze(arm, int(rep_text), arm_dir, arm_env))
        args.reps = 0
    else:
        out = Path(tempfile.mkdtemp(prefix="jevto-ponytail-pilot-")).resolve()
    print(f"[pilot] work dir {out}", flush=True)
    for rep in range(1, args.reps + 1):
        order = arms if rep % 2 else list(reversed(arms))
        for arm in order:
            print(f"[pilot] rep {rep} {arm} ...", flush=True)
            record = run_arm(arm, rep, args, out, os.environ.copy())
            records.append(record)
            print(f"    holdout={'pass' if record['holdout_pass'] else 'FAIL'} impl_lines={record['implementation_lines']} "
                  f"test_bytes={record['test_result_bytes']} combined={record['combined_tokens']} "
                  f"cost={record['cost_usd_list_estimate']} turns={record['num_turns']} plugins={record['plugins']}", flush=True)
    result_dir = Path(args.out)
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "records.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    (result_dir / "report.md").write_text(summarize(records), encoding="utf-8")
    for record in records:
        source = out / f"{record['rep']}-{record['arm'].replace('+', '-')}" / "durations.py"
        shutil.copy2(source, result_dir / f"durations-{record['rep']}-{record['arm'].replace('+', '-')}.py")
    print(f"[pilot] wrote {result_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
