"""Frozen six-task Claude Code benchmark for JevTO search-result selection.

The harness runs 6 synthetic coding tasks x 3 arms x 2 repeats. It pins Claude
Haiku 4.5, installs only the project-local JevTO hook for JevTO arms, keeps the
OpenRouter key in process memory, and stops instead of using extra credits.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
MODEL = "claude-haiku-4-5-20251001"
ARMS = ("native", "deterministic", "adaptive")
REPEATS = 2
SESSION_TIMEOUT_SECONDS = 300
MAX_TURNS = 12
SESSION_COST_CAP_USD = 0.20
OPENROUTER_CAMPAIGN_CAP_USD = 1.00
SEARCH_COMMAND = "rg -n -H FIXME_JEVTO src"
CAPTURE = re.compile(r"capture=([0-9a-f-]{36})", re.I)
FINAL = re.compile(
    r"visible=(\d+) failed=(\d+) child_exit=(\d+) "
    r"warning_preserved=(yes|no) capture_id=([^\s]+)", re.I
)


@dataclass(frozen=True)
class Task:
    task_id: str
    goal: str
    function: str
    relevant_index: int
    source: str
    visible_cases: tuple[tuple[tuple[object, ...], object], ...]
    holdout_cases: tuple[tuple[tuple[object, ...], object], ...]


TASKS = (
    Task(
        "retry_floor",
        "Make normalize_retries convert the input to an integer and clamp negative retry counts to zero.",
        "normalize_retries",
        3,
        "def normalize_retries(value):  # FIXME_JEVTO retry count implementation\n"
        "    # FIXME_JEVTO contract: integer result with negative values clamped to zero\n"
        "    parsed = int(value)  # FIXME_JEVTO conversion stays supported\n"
        "    return parsed  # FIXME_JEVTO bug: negative values escape\n"
        "# FIXME_JEVTO end retry count implementation\n",
        ((("4",), 4), ((0,), 0), ((-2,), 0), ((9,), 9), ((-1,), 0), ((12,), 12)),
        (((-99,), 0), (("17",), 17), ((-0,), 0)),
    ),
    Task(
        "slug_whitespace",
        "Make build_slug trim outer whitespace, lowercase text, and collapse each internal whitespace run to one hyphen.",
        "build_slug",
        17,
        "def build_slug(text):  # FIXME_JEVTO slug implementation\n"
        "    # FIXME_JEVTO contract: trim, lowercase, and collapse whitespace runs\n"
        "    cleaned = text.strip().lower()  # FIXME_JEVTO outer whitespace\n"
        "    return cleaned.replace(' ', '-')  # FIXME_JEVTO bug: runs and tabs survive\n"
        "# FIXME_JEVTO end slug implementation\n",
        (((" Hello World ",), "hello-world"), (("A   B",), "a-b"), (("X\tY",), "x-y"),
         (("solo",), "solo"), (("  MIXED Case",), "mixed-case"), (("a\n b",), "a-b")),
        ((("  many\t spaces  here ",), "many-spaces-here"), (("",), "")),
    ),
    Task(
        "port_range",
        "Make parse_port return an integer only for decimal ports from 1 through 65535; return None for invalid text or out-of-range values.",
        "parse_port",
        8,
        "def parse_port(text):  # FIXME_JEVTO port parser implementation\n"
        "    # FIXME_JEVTO contract: decimal integer in the inclusive network port range\n"
        "    value = int(text)  # FIXME_JEVTO bug: invalid text raises\n"
        "    return value  # FIXME_JEVTO bug: range is unchecked\n"
        "# FIXME_JEVTO end port parser implementation\n",
        ((("80",), 80), (("1",), 1), (("65535",), 65535), (("0",), None),
         (("65536",), None), (("abc",), None)),
        ((("-4",), None), (("443",), 443), (("12.5",), None)),
    ),
    Task(
        "header_match",
        "Make header_matches compare the complete actual and expected header after trimming both and ignoring ASCII case; prefixes must not match.",
        "header_matches",
        22,
        "def header_matches(actual, expected):  # FIXME_JEVTO header matcher implementation\n"
        "    # FIXME_JEVTO contract: trimmed complete equality ignoring case\n"
        "    left = actual.strip().lower()  # FIXME_JEVTO normalize actual\n"
        "    return left.startswith(expected.lower())  # FIXME_JEVTO bug: prefix accepted\n"
        "# FIXME_JEVTO end header matcher implementation\n",
        (((" Content-Type ", "content-type"), True), (("X-ID", "x-id"), True),
         (("X-ID-extra", "x-id"), False), (("A", "AB"), False),
         ((" cache ", " CACHE "), True), (("etag", "tag"), False)),
        ((("Hostess", "host"), False), ((" HOST ", "host"), True)),
    ),
    Task(
        "quota_rounding",
        "Make quota_blocks return the number of 100-unit blocks needed for a nonnegative integer, rounding partial blocks upward and keeping zero at zero.",
        "quota_blocks",
        1,
        "def quota_blocks(units):  # FIXME_JEVTO quota implementation\n"
        "    # FIXME_JEVTO contract: ceiling division by one hundred for nonnegative input\n"
        "    value = int(units)  # FIXME_JEVTO conversion\n"
        "    return value // 100  # FIXME_JEVTO bug: partial block rounds down\n"
        "# FIXME_JEVTO end quota implementation\n",
        (((0,), 0), ((1,), 1), ((99,), 1), ((100,), 1), ((101,), 2), ((250,), 3)),
        (((199,), 2), ((200,), 2), ((1001,), 11)),
    ),
    Task(
        "stable_tags",
        "Make unique_tags remove later case-insensitive duplicates while preserving the original spelling and first-seen order.",
        "unique_tags",
        14,
        "def unique_tags(tags):  # FIXME_JEVTO tag implementation\n"
        "    # FIXME_JEVTO contract: stable first occurrence with case-insensitive identity\n"
        "    values = list(tags)  # FIXME_JEVTO retain caller values\n"
        "    return list(set(values))  # FIXME_JEVTO bug: order and case identity are lost\n"
        "# FIXME_JEVTO end tag implementation\n",
        (((["A", "b", "a"],), ["A", "b"]), ((["x", "X", "y"],), ["x", "y"]),
         (([],), []), ((["One"],), ["One"]), ((["a", "b", "c"],), ["a", "b", "c"]),
         ((["B", "b", "B"],), ["B"])),
        (((["Alpha", "beta", "ALPHA", "Beta", "gamma"],), ["Alpha", "beta", "gamma"]),),
    ),
)

DECOY_TOPICS = (
    "format a display label without changing punctuation",
    "count active workers in a local snapshot",
    "join relative path components for a preview",
    "select a default color name for an empty field",
    "copy a short identifier for a diagnostic",
    "compare two build labels with exact case",
    "return a cached note when it is already present",
    "parse a day name for a calendar heading",
    "measure the length of a rendered title",
    "choose a fallback locale for a demo",
    "strip one trailing marker from a display value",
    "sort numeric sample values for a chart",
    "convert an optional label to a string",
    "test whether a preview list is empty",
    "repeat a glyph for a progress sample",
    "read a synthetic category from a mapping",
    "append a suffix to a generated fixture name",
    "pick the first sample row for a tutorial",
    "return the last status from a local list",
    "reverse a demo sequence for an animation",
    "replace underscores in a human-readable label",
    "wrap a diagnostic value in brackets",
    "limit a preview string to a display width",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(argv: list[str], cwd: Path, env: dict[str, str], timeout: int = 60) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=timeout, check=False)


def checked(argv: list[str], cwd: Path, env: dict[str, str], timeout: int = 60) -> str:
    result = run(argv, cwd, env, timeout)
    if result.returncode:
        raise RuntimeError(f"{argv[0]} exited {result.returncode}: {result.stderr[-1200:]!r}")
    return result.stdout.decode("utf-8", "replace").strip()


def decoy_source(index: int, topic: str) -> str:
    return (
        f"# FIXME_JEVTO decoy {index}: {topic}\n"
        f"def unrelated_{index}(value):  # FIXME_JEVTO unrelated helper\n"
        f"    text = str(value)  # FIXME_JEVTO synthetic conversion\n"
        f"    return text  # FIXME_JEVTO unrelated behavior remains unchanged\n"
        f"# FIXME_JEVTO end decoy {index}\n"
    )


def verifier_source(task: Task, cases: tuple[tuple[tuple[object, ...], object], ...]) -> str:
    target = f"candidate_{task.relevant_index:02}.py"
    return f"""from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

spec = spec_from_file_location("target", Path("src") / {target!r})
module = module_from_spec(spec)
spec.loader.exec_module(module)
function = getattr(module, {task.function!r})
cases = {cases!r}
print("warning: benchmark verifier preserved")
failed = 0
for index, (arguments, expected) in enumerate(cases):
    try:
        actual = function(*arguments)
    except Exception as error:
        actual = "EXCEPTION:" + type(error).__name__
    if actual == expected:
        print(f"test visible::{{index}} ... ok")
    else:
        failed += 1
        print(f"test visible::{{index}} ... FAILED expected={{expected!r}} actual={{actual!r}}")
passed = len(cases) - failed
print(f"test result: {{'ok' if failed == 0 else 'FAILED'}}. {{passed}} passed; {{failed}} failed")
sys.exit(1 if failed else 0)
"""


def prepare_fixture(task: Task, checkout: Path, arm_dir: Path, env: dict[str, str]) -> dict:
    (checkout / "src").mkdir(parents=True)
    topics = iter(DECOY_TOPICS)
    for index in range(24):
        source = task.source if index == task.relevant_index else decoy_source(index, next(topics))
        (checkout / "src" / f"candidate_{index:02}.py").write_text(source, encoding="utf-8")
    (checkout / "verify.py").write_text(verifier_source(task, task.visible_cases), encoding="utf-8")
    holdout = arm_dir / "holdout.py"
    holdout.write_text(verifier_source(task, task.holdout_cases).replace("visible::", "holdout::"), encoding="utf-8")
    (checkout / ".gitignore").write_text("__pycache__/\n.jevto-store/\n", encoding="utf-8")
    initial = run([sys.executable, "verify.py"], checkout, env)
    if initial.returncode == 0:
        raise RuntimeError(f"{task.task_id} baseline unexpectedly passes")
    search = run(["rg", "-n", "-H", "FIXME_JEVTO", "src"], checkout, env)
    if search.returncode or len(search.stdout) > 15_000:
        raise RuntimeError(f"{task.task_id} search fixture is invalid or too large")
    (arm_dir / "frozen-search.stdout").write_bytes(search.stdout)
    (arm_dir / "frozen-search.stderr").write_bytes(search.stderr)
    checked(["git", "init", "-q"], checkout, env)
    checked(["git", "add", "."], checkout, env)
    tree = checked(["git", "write-tree"], checkout, env)
    checked(["git", "-c", "user.name=JevTO Benchmark", "-c",
             "user.email=benchmark@invalid.local", "commit", "-q", "-m", "frozen fixture"], checkout, env)
    return {
        "tree": tree,
        "search_sha256": sha(search.stdout),
        "search_bytes": len(search.stdout),
        "visible_sha256": sha((checkout / "verify.py").read_bytes()),
        "holdout_sha256": sha(holdout.read_bytes()),
        "target": f"src/candidate_{task.relevant_index:02}.py",
        "target_initial_sha256": sha((checkout / "src" / f"candidate_{task.relevant_index:02}.py").read_bytes()),
        "visible_count": len(task.visible_cases),
    }


def schedule() -> list[tuple[int, Task, str]]:
    rows = []
    for repeat in range(REPEATS):
        for index, task in enumerate(TASKS):
            order = ARMS[index % 3:] + ARMS[:index % 3]
            if repeat == 1:
                order = tuple(reversed(order))
            rows.extend((repeat + 1, task, arm) for arm in order)
    return rows


def scrub(text: str, secrets: tuple[str, ...]) -> str:
    for value in secrets:
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def run_claude(argv: list[str], cwd: Path, env: dict[str, str], secrets: tuple[str, ...]) -> tuple[int | None, str, str, str | None]:
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace", bufsize=1)
    assert process.stdout and process.stderr
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    events: queue.Queue[str] = queue.Queue()

    def pump(stream, destination, notify=False):
        for line in stream:
            destination.append(line)
            if notify:
                events.put(line)

    out_thread = threading.Thread(target=pump, args=(process.stdout, stdout_lines, True), daemon=True)
    err_thread = threading.Thread(target=pump, args=(process.stderr, stderr_lines), daemon=True)
    out_thread.start()
    err_thread.start()
    deadline = time.monotonic() + SESSION_TIMEOUT_SECONDS
    message_ids: set[str] = set()
    stopped = None
    while process.poll() is None:
        if time.monotonic() >= deadline:
            stopped = "session_timeout"
            process.terminate()
            break
        try:
            line = events.get(timeout=0.25)
        except queue.Empty:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant" and isinstance(event.get("message", {}).get("id"), str):
            message_ids.add(event["message"]["id"])
            if len(message_ids) > MAX_TURNS:
                stopped = "turn_cap_exceeded"
                process.terminate()
                break
    try:
        code = process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        code = process.wait(timeout=5)
    out_thread.join(timeout=2)
    err_thread.join(timeout=2)
    return code, scrub("".join(stdout_lines), secrets), scrub("".join(stderr_lines), secrets), stopped


def parse_trace(trace: str, cli_exit: int | None, stopped: str | None) -> dict:
    events = [json.loads(line) for line in trace.splitlines() if line.strip()]
    terminal = [event for event in events if event.get("type") == "result"]
    reasons = []
    result = terminal[0] if len(terminal) == 1 else {}
    if stopped or cli_exit != 0 or len(terminal) != 1 or result.get("subtype") != "success" or result.get("is_error"):
        reasons.append(stopped or "incomplete_or_failed_claude_turn")
    usage_by_model = result.get("modelUsage")
    if not isinstance(usage_by_model, dict) or set(usage_by_model) != {MODEL}:
        reasons.append("missing_or_substituted_model")
        usage = {}
    else:
        usage = usage_by_model[MODEL]
    fields = ("inputTokens", "cacheCreationInputTokens", "cacheReadInputTokens", "outputTokens")
    if any(not isinstance(usage.get(field), int) or usage[field] < 0 for field in fields):
        reasons.append("missing_or_invalid_token_usage")
        combined = None
    else:
        combined = sum(usage[field] for field in fields)
    turns = result.get("num_turns")
    if not isinstance(turns, int) or not 1 <= turns <= MAX_TURNS:
        reasons.append("turn_cap_invalid")
    cost = result.get("total_cost_usd")
    if not isinstance(cost, (int, float)) or cost < 0:
        reasons.append("missing_cost_estimate")
    elif cost > SESSION_COST_CAP_USD:
        reasons.append("cli_estimated_cost_cap_exceeded")
    calls = []
    results = {}
    for event in events:
        content = event.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                calls.append({"id": block.get("id"), "name": block.get("name"), "input": block.get("input", {})})
            elif block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                results[block["tool_use_id"]] = block
    rate_events = [event.get("rate_limit_info", {}) for event in events if event.get("type") == "rate_limit_event"]
    return {"valid": not reasons, "reasons": reasons, "terminal": result,
            "usage": usage, "combined_tokens": combined,
            "cost_usd": cost, "calls": calls, "results": results,
            "rate_limit": rate_events[-1] if rate_events else None,
            "final_answer": result.get("result", "")}


def command_call(parsed: dict, command: str) -> tuple[dict | None, dict | None]:
    matches = [call for call in parsed["calls"]
               if call.get("name") in {"Bash", "PowerShell"}
               and call.get("input", {}).get("command") == command]
    if len(matches) != 1:
        return None, None
    return matches[0], parsed["results"].get(matches[0]["id"])


def receipt_review(arm: str, arm_dir: Path, jevto: Path, parsed: dict, env: dict[str, str]) -> dict:
    receipts = [json.loads(path.read_text(encoding="utf-8"))
                for path in sorted((arm_dir / "store" / "receipts").glob("*.json"))]
    if arm == "native":
        return {"valid": not receipts, "receipts": receipts, "reason": "native_has_no_hook"}
    if len(receipts) != 1:
        return {"valid": False, "receipts": receipts, "reason": "expected_one_search_receipt"}
    receipt = receipts[0]
    capture_id = receipt.get("capture_id")
    if not isinstance(capture_id, str):
        return {"valid": False, "receipts": receipts, "reason": "missing_capture_id"}
    capture_file = arm_dir / "store" / "captures" / f"{capture_id}.stdout"
    if not capture_file.is_file():
        return {"valid": False, "receipts": receipts, "reason": "missing_capture_bytes"}
    captured = capture_file.read_bytes()
    frozen = (arm_dir / "frozen-search.stdout").read_bytes()
    host_boundary_ok = captured == frozen or captured == frozen.rstrip(b"\r\n")
    recall = run([str(jevto), "--store-dir", str(arm_dir / "store"),
                  "recall", capture_id, "--full"], arm_dir, env)
    exact_recall = recall.returncode == 0 and recall.stdout == captured
    adaptive = arm == "adaptive"
    jev_ok = (not adaptive or (receipt.get("mode") == "adaptive"
              and isinstance(receipt.get("jev_usage"), dict)
              and receipt.get("jev_model", "").startswith("typesafe/jev-1.13-")
              and receipt.get("jev_call_attempted") is True
              and receipt.get("jev_cache_hit") is False))
    request_ok = (receipt.get("replacement_requested") is True) if adaptive else True
    return {"valid": host_boundary_ok and exact_recall and jev_ok and request_ok,
            "receipts": receipts, "capture_id": capture_id,
            "host_visible_matches_frozen_except_optional_trailing_newline": host_boundary_ok,
            "byte_exact_hook_visible_recall": exact_recall,
            "jev_valid": jev_ok, "replacement_requested": receipt.get("replacement_requested"),
            "raw_bytes": receipt.get("raw_bytes"), "delivered_bytes": receipt.get("delivered_bytes")}


def independent_review(task: Task, checkout: Path, arm_dir: Path, baseline: dict,
                       env: dict[str, str]) -> dict:
    visible = run([sys.executable, "verify.py"], checkout, env)
    holdout = run([sys.executable, str(arm_dir / "holdout.py")], checkout, env)
    diff = run(["git", "diff", "--binary", "HEAD", "--"], checkout, env)
    names = checked(["git", "diff", "--name-only", "HEAD", "--"], checkout, env).splitlines()
    status = checked(["git", "status", "--porcelain", "--untracked-files=all"], checkout, env).splitlines()
    unrelated_untracked = [line for line in status if ".claude/settings.local.json" not in line
                           and ".jevto-store" not in line and "__pycache__" not in line]
    tests_unchanged = sha((checkout / "verify.py").read_bytes()) == baseline["visible_sha256"]
    settings = checkout / ".claude" / "settings.local.json"
    settings_unchanged = (settings.is_file()
                          and sha(settings.read_bytes()) == baseline["settings_sha256"])
    return {
        "visible_passed": visible.returncode == 0,
        "visible_exit": visible.returncode,
        "holdout_passed": holdout.returncode == 0,
        "holdout_exit": holdout.returncode,
        "file_scope_passed": names == [baseline["target"]] and not unrelated_untracked,
        "changed_paths": names,
        "unrelated_untracked": unrelated_untracked,
        "tests_unchanged": tests_unchanged,
        "settings_unchanged": settings_unchanged,
        "diff": diff.stdout.decode("utf-8", "replace"),
        "source_sha256": sha((checkout / baseline["target"]).read_bytes()),
    }


def diff_review(task: Task, checkout: Path, baseline: dict, independent: dict) -> dict:
    source = (checkout / baseline["target"]).read_text(encoding="utf-8")
    parse_error = None
    functions = []
    try:
        tree = ast.parse(source)
        functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    except SyntaxError as error:
        parse_error = f"{error.msg} at line {error.lineno}"
    diff = independent["diff"]
    changed_lines = sum(
        1 for line in diff.splitlines()
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )
    localized = independent["changed_paths"] == [baseline["target"]]
    target_present_once = functions.count(task.function) == 1
    valid = parse_error is None and localized and target_present_once and changed_lines <= 20
    summary = (
        f"{'One target file' if localized else 'Unexpected file scope'}; "
        f"{'parseable Python' if parse_error is None else parse_error}; "
        f"target function occurrences={functions.count(task.function)}; changed lines={changed_lines}."
    )
    return {"valid": valid, "localized": localized, "parse_error": parse_error,
            "target_function_occurrences": functions.count(task.function),
            "changed_lines": changed_lines, "summary": summary}


def output_review(task: Task, arm: str, parsed: dict, receipt: dict, baseline: dict) -> dict:
    search_call, search_result = command_call(parsed, SEARCH_COMMAND)
    verify_call, verify_result = command_call(parsed, "python verify.py")
    search_text = search_result.get("content", "") if isinstance(search_result, dict) else ""
    verify_text = verify_result.get("content", "") if isinstance(verify_result, dict) else ""
    final = parsed["final_answer"]
    match = FINAL.search(final)
    replacement_requested = receipt.get("replacement_requested") is True
    expected_capture = (receipt.get("capture_id")
                        if arm != "native" and replacement_requested else "none")
    final_ok = bool(match and int(match.group(1)) == baseline["visible_count"]
                    and int(match.group(2)) == 0 and int(match.group(3)) == 0
                    and match.group(4).lower() == "yes"
                    and match.group(5).lower() == str(expected_capture).lower()
                    and final.strip() == match.group(0))
    targets = {baseline["target"], baseline["target"].replace("/", "\\")}
    search_contains_target = any(target in search_text for target in targets)
    search_ok = bool(search_call and search_result and search_contains_target)
    if replacement_requested:
        search_ok = search_ok and "jevto exit=0" in search_text and str(expected_capture) in search_text
    else:
        search_ok = search_ok and "jevto exit=" not in search_text
    verifier_ok = bool(verify_call and verify_result
                       and "warning: benchmark verifier preserved" in verify_text
                       and f"{baseline['visible_count']} passed; 0 failed" in verify_text)
    return {"valid": search_ok and verifier_ok and final_ok,
            "search_command_once": search_call is not None,
            "search_contains_target": search_contains_target,
            "verifier_output_ok": verifier_ok, "final_claims_ok": final_ok,
            "final_answer": final}


def prompt(task: Task, visible_count: int) -> str:
    return (
        f"In this disposable synthetic Python project, {task.goal} "
        f"First run exactly `{SEARCH_COMMAND}` once, with no extra arguments. Use that result to locate the implementation. "
        "Read only the candidate files needed, edit only the one source file that defines the named function, then run exactly "
        "`python verify.py` once. Do not change tests or settings, install anything, access the network, or run other shell commands. "
        f"End with exactly `visible={visible_count} failed=0 child_exit=0 warning_preserved=yes capture_id=<id-or-none>`. "
        "Use the JevTO capture ID only if the search tool result actually shows one; otherwise write none."
    )


def run_arm(repeat: int, task: Task, arm: str, output: Path, claude: Path,
            jevto: Path, base_env: dict[str, str], openrouter_key: str) -> dict:
    arm_dir = output / f"r{repeat}-{task.task_id}-{arm}"
    checkout = arm_dir / "checkout"
    checkout.mkdir(parents=True)
    baseline = prepare_fixture(task, checkout, arm_dir, base_env)
    session_id = str(uuid.uuid4())
    task_frame = arm_dir / "task-frame.json"
    task_frame.write_text(json.dumps({"schema_version": 1, "session_id": session_id,
        "workspace_id": f"claude-search-{task.task_id}", "revision": 0,
        "goal": task.goal, "trusted_constraints": []}, indent=2) + "\n", encoding="utf-8")
    policy = arm_dir / "remote-policy.json"
    policy.write_text(json.dumps({"schema_version": 1, "data_classification": "synthetic",
        "allow_search_snippets": True, "allowed_roots": [str(checkout.resolve())]}, indent=2) + "\n",
        encoding="utf-8")
    env = base_env.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if arm != "native":
        installed = run([str(jevto), "--store-dir", str(arm_dir / "store"),
            "init-claude-hook", "--workspace", str(checkout), "--apply"], checkout, env)
        if installed.returncode:
            raise RuntimeError(installed.stderr.decode("utf-8", "replace"))
    else:
        (checkout / ".claude").mkdir()
        (checkout / ".claude" / "settings.local.json").write_text("{}\n", encoding="utf-8")
    baseline["settings_sha256"] = sha(
        (checkout / ".claude" / "settings.local.json").read_bytes()
    )
    if arm == "adaptive":
        env.update({"JEVTO_ADAPTIVE": "1", "JEVTO_TASK_FILE": str(task_frame),
                    "JEVTO_REMOTE_POLICY": str(policy), "OPENROUTER_API_KEY": openrouter_key})
    else:
        for name in ("JEVTO_ADAPTIVE", "JEVTO_TASK_FILE", "JEVTO_REMOTE_POLICY", "OPENROUTER_API_KEY"):
            env.pop(name, None)
    empty_mcp = arm_dir / "empty-mcp.json"
    empty_mcp.write_text('{"mcpServers":{}}\n', encoding="utf-8")
    argv = [str(claude), "-p", "--output-format", "stream-json", "--verbose",
        "--include-hook-events", "--no-session-persistence", "--no-chrome",
        "--strict-mcp-config", "--mcp-config", str(empty_mcp), "--setting-sources", "local",
        "--disable-slash-commands", "--model", MODEL, "--effort", "low",
        "--permission-mode", "dontAsk", "--permission-prompts", "none",
        "--allowedTools", f"Bash({SEARCH_COMMAND})", "Bash(python verify.py)", "Read", "Edit",
        "--tools", "Bash,Read,Edit", "--max-budget-usd", str(SESSION_COST_CAP_USD),
        "--session-id", session_id, prompt(task, baseline["visible_count"])]
    started = time.monotonic()
    code, trace, errors, stopped = run_claude(argv, checkout, env, (openrouter_key,))
    elapsed = time.monotonic() - started
    (arm_dir / "claude-stream.jsonl").write_text(trace, encoding="utf-8")
    (arm_dir / "claude-stderr.txt").write_text(errors, encoding="utf-8")
    parsed = parse_trace(trace, code, stopped)
    receipt = receipt_review(arm, arm_dir, jevto, parsed, base_env)
    independent = independent_review(task, checkout, arm_dir, baseline, base_env)
    qualitative = diff_review(task, checkout, baseline, independent)
    review = output_review(task, arm, parsed, receipt, baseline)
    verified = (parsed["valid"] and receipt["valid"] and review["valid"]
                and independent["visible_passed"] and independent["holdout_passed"]
                and independent["file_scope_passed"] and independent["tests_unchanged"]
                and independent["settings_unchanged"]
                and qualitative["valid"])
    invalid = parsed["reasons"] + ([] if receipt["valid"] else ["invalid_jevto_receipt"])
    if not review["valid"]:
        invalid.append("output_review_failed")
    if not independent["visible_passed"]:
        invalid.append("visible_verifier_failed")
    if not independent["holdout_passed"]:
        invalid.append("frozen_holdout_failed")
    if not independent["file_scope_passed"]:
        invalid.append("file_scope_failed")
    if not independent["settings_unchanged"]:
        invalid.append("settings_changed")
    if not qualitative["valid"]:
        invalid.append("qualitative_diff_review_failed")
    result = {"repeat": repeat, "task": task.task_id, "arm": arm,
        "status": "completed", "verified_completion": verified,
        "invalid_reasons": sorted(set(invalid)), "elapsed_seconds": elapsed,
        "baseline": baseline, "terminal": {key: parsed[key] for key in
            ("usage", "combined_tokens", "cost_usd", "rate_limit", "final_answer")},
        "tool_calls": parsed["calls"], "receipt_review": receipt,
        "output_review": review, "independent_review": independent,
        "qualitative_diff_review": qualitative}
    (arm_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def comparison_rows(results: list[dict]) -> list[dict]:
    lookup = {(item["repeat"], item["task"], item["arm"]): item for item in results
              if item.get("status") == "completed"}
    rows = []
    for repeat in range(1, REPEATS + 1):
        for task in TASKS:
            native = lookup.get((repeat, task.task_id, "native"))
            for arm in ("deterministic", "adaptive"):
                candidate = lookup.get((repeat, task.task_id, arm))
                eligible = bool(native and candidate and native.get("verified_completion")
                                and candidate.get("verified_completion"))
                native_tokens = native.get("terminal", {}).get("combined_tokens") if native else None
                arm_tokens = candidate.get("terminal", {}).get("combined_tokens") if candidate else None
                saving = ((native_tokens - arm_tokens) / native_tokens * 100
                          if eligible and native_tokens and isinstance(arm_tokens, int) else None)
                deterministic = lookup.get((repeat, task.task_id, "deterministic"))
                deterministic_tokens = (deterministic.get("terminal", {}).get("combined_tokens")
                                        if deterministic else None)
                direct_eligible = bool(arm == "adaptive" and deterministic and candidate
                                       and deterministic.get("verified_completion")
                                       and candidate.get("verified_completion"))
                direct_saving = ((deterministic_tokens - arm_tokens) / deterministic_tokens * 100
                                 if direct_eligible and deterministic_tokens
                                 and isinstance(arm_tokens, int) else None)
                rows.append({"repeat": repeat, "task": task.task_id, "arm": arm,
                             "equal_verified_outcomes": eligible, "token_saving_percent": saving,
                             "adaptive_vs_deterministic_percent": direct_saving})
    return rows


def write_report(output: Path, manifest: dict) -> None:
    results = manifest["results"]
    comparisons = comparison_rows(results)
    lines = ["# Claude adaptive search benchmark", "",
        f"Campaign: `{output.name}`. Model required: `{MODEL}`. Planned sessions: 36.", "",
        "This local pilot uses frozen synthetic code only. JevTO arms use a project-scoped Claude PostToolUse hook; "
        "adaptive arms allow one bounded OpenRouter Jev decision and deterministic fallback. Claude list-price values are estimates under Pro, not billed savings.", "",
        f"Limits: {SESSION_TIMEOUT_SECONDS} seconds, {MAX_TURNS} turns, and ${SESSION_COST_CAP_USD:.2f} Claude list estimate per session; ${OPENROUTER_CAMPAIGN_CAP_USD:.2f} response-reported OpenRouter cost for the campaign. No overage is permitted.", "",
        "Safe mode is intentionally not used because it disables project hooks. Isolation instead uses only project-local settings, an empty strict MCP config, disabled slash commands, and a fixed tool allowlist.", "",
        "| Repeat | Task | Arm | Status | Verified | Input | Cache write | Cache read | Output | Combined | List estimate | Seconds | Exclusion |",
        "| ---: | --- | --- | --- | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for item in results:
        terminal = item.get("terminal", {})
        usage = terminal.get("usage", {})
        reasons = ", ".join(item.get("invalid_reasons", []) or item.get("exclusion_reasons", [])) or "none"
        lines.append(f"| {item['repeat']} | {item['task']} | {item['arm']} | {item['status']} | "
                     f"{'yes' if item.get('verified_completion') else 'no'} | "
                     f"{usage.get('inputTokens', 'missing')} | {usage.get('cacheCreationInputTokens', 'missing')} | "
                     f"{usage.get('cacheReadInputTokens', 'missing')} | {usage.get('outputTokens', 'missing')} | "
                     f"{terminal.get('combined_tokens', 'missing')} | {terminal.get('cost_usd', 'missing')} | "
                     f"{item.get('elapsed_seconds', 'missing')} | {reasons} |")
    eligible = [row for row in comparisons if row["token_saving_percent"] is not None]
    adaptive = [row["token_saving_percent"] for row in eligible if row["arm"] == "adaptive"]
    deterministic = [row["token_saving_percent"] for row in eligible if row["arm"] == "deterministic"]
    adaptive_vs_deterministic = [row["adaptive_vs_deterministic_percent"] for row in comparisons
                                 if row["adaptive_vs_deterministic_percent"] is not None]
    if manifest.get("preflight_rate_limit"):
        rate = manifest["preflight_rate_limit"]
        lines += ["", "## Allowance boundary", "",
            f"No campaign session started. The latest Claude event reported status `{rate.get('status')}`, "
            f"five-hour utilization `{rate.get('utilization')}`, reset `{rate.get('resetsAt')}`, and "
            f"overage `{rate.get('isUsingOverage')}`. The harness therefore recorded all planned arms as excluded."]
    lines += ["", "## Equal-outcome comparisons", "",
        f"Eligible adaptive pairs: {len(adaptive)}; median whole-session token saving: "
        f"{statistics.median(adaptive):.2f}%" if adaptive else "Eligible adaptive pairs: 0; no savings claim.",
        f"Eligible deterministic pairs: {len(deterministic)}; median whole-session token saving: "
        f"{statistics.median(deterministic):.2f}%" if deterministic else "Eligible deterministic pairs: 0; no savings claim.",
        f"Eligible adaptive-versus-deterministic pairs: {len(adaptive_vs_deterministic)}; median whole-session token saving: "
        f"{statistics.median(adaptive_vs_deterministic):.2f}%" if adaptive_vs_deterministic else "Eligible adaptive-versus-deterministic pairs: 0; no savings claim.",
        "", "Targets: adaptive at least 20% below native and 10% below deterministic, lower total estimated cost, "
        "and median elapsed time within 10%, all on equal verified outcomes. Missing or failed arms are excluded from savings claims.",
        "", "## Output review", ""]
    completed = [item for item in results if item.get("status") == "completed"]
    lines.append(f"{sum(item.get('output_review', {}).get('valid', False) for item in completed)} of {len(completed)} completed sessions passed trace-grounded review of the search result, verifier count, exit, warning, and capture claim.")
    lines.append(f"{sum(item.get('qualitative_diff_review', {}).get('valid', False) for item in completed)} of {len(completed)} completed sessions passed the scoped maintainability review: parseable source, one named target function, one changed target file, and at most 20 changed lines. Visible and frozen holdout checks remain separate gates.")
    lines += ["", "## Conclusion", ""]
    if completed:
        lines.append("This campaign can support only task-specific conclusions for its six frozen synthetic tasks. One pilot cannot establish a general quality, token, latency, or cost benefit.")
    else:
        lines.append("No coding arm ran, so this campaign provides no token, quality, latency, or cost comparison and makes no savings claim. The implementation and frozen harness are locally verified; a fresh campaign remains pending after the Pro allowance resets.")
    lines.append("")
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def self_test() -> None:
    assert len(schedule()) == 36 and len(set((r, t.task_id, a) for r, t, a in schedule())) == 36
    with tempfile.TemporaryDirectory(prefix="jevto-search-self-test-") as temporary:
        root = Path(temporary)
        env = os.environ.copy()
        baseline = prepare_fixture(TASKS[0], root / "checkout", root, env)
        assert baseline["search_bytes"] < 15_000
        assert len((root / "frozen-search.stdout").read_text(encoding="utf-8").splitlines()) == 120
        fake = {"type": "result", "subtype": "success", "is_error": False,
            "num_turns": 2, "result": "ok", "total_cost_usd": 0.01,
            "modelUsage": {MODEL: {"inputTokens": 1, "cacheCreationInputTokens": 2,
                "cacheReadInputTokens": 3, "outputTokens": 4}}}
        parsed = parse_trace(json.dumps(fake) + "\n", 0, None)
        assert parsed["valid"] and parsed["combined_tokens"] == 10
        missing = json.loads(json.dumps(fake))
        del missing["modelUsage"][MODEL]["outputTokens"]
        assert "missing_or_invalid_token_usage" in parse_trace(json.dumps(missing) + "\n", 0, None)["reasons"]
        assert not parse_trace("", 1, "session_timeout")["valid"]
        fake["modelUsage"] = {"claude-fast-substitute": fake["modelUsage"].pop(MODEL)}
        assert "missing_or_substituted_model" in parse_trace(json.dumps(fake) + "\n", 0, None)["reasons"]
        absent = receipt_review("adaptive", root, Path("missing-jevto"), parsed, env)
        assert not absent["valid"] and absent["reason"] == "expected_one_search_receipt"
        (root / "checkout" / ".claude").mkdir()
        settings = root / "checkout" / ".claude" / "settings.local.json"
        settings.write_text("{}\n", encoding="utf-8")
        baseline["settings_sha256"] = sha(settings.read_bytes())
        failed = independent_review(TASKS[0], root / "checkout", root, baseline, env)
        assert not failed["holdout_passed"]
    print("Claude adaptive search benchmark self-test passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--claude-cli", type=Path)
    parser.add_argument("--jevto-cli", type=Path)
    parser.add_argument("--max-sessions", type=int, default=36)
    parser.add_argument("--preflight-rate-trace", type=Path,
        help="Record every arm as excluded when a current Claude trace proves a non-allowed rate status")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if os.name != "nt" or not args.claude_cli or not args.jevto_cli:
        parser.error("Windows, --claude-cli, and --jevto-cli are required")
    claude = args.claude_cli.resolve(strict=True)
    jevto = args.jevto_cli.resolve(strict=True)
    env = os.environ.copy()
    auth = json.loads(checked([str(claude), "auth", "status"], ROOT, env))
    if not (auth.get("loggedIn") and auth.get("authMethod") == "claude.ai"
            and auth.get("subscriptionType") == "pro"):
        raise SystemExit("Claude Code must use the signed-in Pro subscription")
    key = env.pop("OPENROUTER_API_KEY", "")
    if not key and not args.preflight_rate_trace and sys.stdin.isatty():
        key = getpass.getpass("OpenRouter key for adaptive arms (not saved): ").strip()
    output = ROOT / ".bench-runs" / f"claude-adaptive-search-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    output.mkdir(parents=True)
    plan = schedule()
    manifest = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL, "claude_version": checked([str(claude), "--version"], ROOT, env),
        "claude_sha256": sha(claude.read_bytes()), "jevto_sha256": sha(jevto.read_bytes()),
        "subscription": "pro", "openrouter_campaign_cap_usd": OPENROUTER_CAMPAIGN_CAP_USD,
        "session_cost_cap_usd": SESSION_COST_CAP_USD, "session_timeout_seconds": SESSION_TIMEOUT_SECONDS,
        "turn_cap": MAX_TURNS, "planned_sessions": len(plan), "results": []}
    spent = 0.0
    stop_reason = None
    if args.preflight_rate_trace:
        rate_events = [event.get("rate_limit_info", {}) for event in
            (json.loads(line) for line in args.preflight_rate_trace.read_text(encoding="utf-8").splitlines()
             if line.strip()) if event.get("type") == "rate_limit_event"]
        if not rate_events:
            raise SystemExit("preflight trace has no rate_limit_event")
        rate = rate_events[-1]
        reset = rate.get("resetsAt")
        if rate.get("status") == "allowed" and rate.get("isUsingOverage") is not True:
            raise SystemExit("preflight trace does not show the configured allowance boundary")
        if isinstance(reset, (int, float)) and datetime.now(timezone.utc).timestamp() >= reset:
            raise SystemExit("preflight rate-limit evidence has expired")
        stop_reason = "pro_allowance_boundary"
        manifest["preflight_rate_limit"] = rate
    for index, (repeat, task, arm) in enumerate(plan):
        if stop_reason:
            break
        if index >= args.max_sessions:
            stop_reason = "operator_session_limit"
            break
        if arm == "adaptive" and not key:
            manifest["results"].append({"repeat": repeat, "task": task.task_id, "arm": arm,
                "status": "excluded", "verified_completion": False,
                "exclusion_reasons": ["missing_openrouter_runtime_key"]})
            continue
        if arm == "adaptive" and spent >= OPENROUTER_CAMPAIGN_CAP_USD:
            manifest["results"].append({"repeat": repeat, "task": task.task_id, "arm": arm,
                "status": "excluded", "verified_completion": False,
                "exclusion_reasons": ["openrouter_campaign_cap"]})
            continue
        result = run_arm(repeat, task, arm, output, claude, jevto, env, key)
        manifest["results"].append(result)
        arm_costs = []
        for receipt in result.get("receipt_review", {}).get("receipts", []):
            cost = (receipt.get("jev_usage") or {}).get("billed_cost")
            if isinstance(cost, (int, float)):
                spent += cost
                arm_costs.append(cost)
        if arm == "adaptive" and len(arm_costs) != 1:
            stop_reason = "missing_openrouter_response_cost"
            break
        rate = result.get("terminal", {}).get("rate_limit") or {}
        if rate.get("status") != "allowed" or rate.get("isUsingOverage") is True:
            stop_reason = "pro_allowance_boundary"
            break
        if not result.get("terminal", {}).get("combined_tokens"):
            stop_reason = "missing_terminal_usage"
            break
        manifest["openrouter_response_reported_cost_usd"] = spent
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    attempted = {(item["repeat"], item["task"], item["arm"]) for item in manifest["results"]}
    for repeat, task, arm in plan:
        if (repeat, task.task_id, arm) not in attempted:
            manifest["results"].append({"repeat": repeat, "task": task.task_id, "arm": arm,
                "status": "excluded", "verified_completion": False,
                "exclusion_reasons": [stop_reason or "not_attempted"]})
    manifest["stop_reason"] = stop_reason
    manifest["openrouter_response_reported_cost_usd"] = spent
    manifest["comparisons"] = comparison_rows(manifest["results"])
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    write_report(output, manifest)
    print(output)


if __name__ == "__main__":
    main()
