"""Focused Claude Code coding pilot: native, deterministic JevTO, adaptive JevTO.

This is a local exploratory study, not a general savings or quality claim.
The OpenRouter key is read from a TTY, held only in memory, and passed only to
the adaptive Claude process. Every saved Claude stream is scrubbed before write.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

import codex_coding_pair


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
MODEL = "claude-haiku-4-5-20251001"
ARMS = (
    ("rust", "native"),
    ("rust", "adaptive"),
    ("rust", "deterministic"),
    ("python", "deterministic"),
    ("python", "native"),
)
CAPTURE_RE = re.compile(r"capture_id:\s*([0-9a-f-]{36})", re.IGNORECASE)
RUST_PROMPT = (
    "In this disposable Rust project, fix `total` in src/lib.rs so it sums only "
    "lines whose trimmed content begins with `value=`. Ignore warning and note "
    "lines even if they mention `value=`; preserve negative values. Read "
    "src/lib.rs and tests/z_critical.rs, edit only src/lib.rs, then run exactly "
    "the verifier command below once and wait for it. Report the passed and "
    "failed test counts, child exit, and any JevTO capture ID. Do not change "
    "tests or host configuration, install dependencies, access the network "
    "except through the specified verifier, or run unrelated commands."
)
PYTHON_PROMPT = (
    "In this disposable Python project, fix summarize.total so it sums only "
    "lines beginning with `value=` and ignores warnings or notes, including "
    "misleading mentions of value=. A leading-space line does not begin with "
    "`value=`. Read summarize.py and verify.py, edit only summarize.py, then "
    "run exactly the verifier command below once and wait for it. Report the "
    "passed-test total, child exit, preserved warning, and any JevTO capture "
    "ID. Do not change tests or host configuration, install dependencies, "
    "access the network, or run unrelated commands."
)
PYTHON_WRAPPER = (
    "import hashlib, runpy\n"
    "from pathlib import Path\n"
    "runpy.run_path('verify.py', run_name='__main__')\n"
    "digest = hashlib.sha256(Path('summarize.py').read_bytes()).hexdigest()\n"
    "Path('.verified-source-sha256').write_text(digest, encoding='ascii')\n"
    "print('source_sha256=' + digest)\n"
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def canonical_hash(stdout: bytes, stderr: bytes) -> str:
    return sha(len(stdout).to_bytes(8, "big") + stdout + len(stderr).to_bytes(8, "big") + stderr)


def run(argv: list[str], cwd: Path, env: dict[str, str], timeout: int = 60) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=timeout, check=False)


def checked(argv: list[str], cwd: Path, env: dict[str, str], timeout: int = 60) -> str:
    result = run(argv, cwd, env, timeout)
    if result.returncode:
        raise RuntimeError(f"{argv[0]} exited {result.returncode}: {result.stderr[-1000:]!r}")
    return result.stdout.decode("utf-8", "replace").strip()


def git_baseline(path: Path, env: dict[str, str]) -> str:
    checked(["git", "init", "-q"], path, env)
    checked(["git", "add", "."], path, env)
    tree = checked(["git", "write-tree"], path, env)
    checked(["git", "-c", "user.name=JevTO Pilot", "-c",
             "user.email=pilot@invalid.local", "commit", "-q", "-m", "fixture baseline"], path, env)
    return tree


def prepare_python(path: Path, env: dict[str, str]) -> dict:
    shutil.copytree(HERE / "fixtures" / "quiet_warning", path,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    verifier = path / "verify.py"
    source = verifier.read_text(encoding="utf-8")
    if "range(24)" not in source:
        raise RuntimeError("Python fixture changed unexpectedly")
    verifier.write_text(source.replace("range(24)", "range(80)"), encoding="utf-8")
    (path / "verify_once.py").write_text(PYTHON_WRAPPER, encoding="utf-8")
    (path / ".gitignore").write_text(".verified-source-sha256\n__pycache__/\n", encoding="utf-8")
    initial = run([sys.executable, "verify_once.py"], path, env, 30)
    if initial.returncode == 0 or (path / ".verified-source-sha256").exists():
        raise RuntimeError("Python fixture was not failing at baseline")
    return {"git_tree": git_baseline(path, env),
            "source_sha256": file_sha(path / "summarize.py"),
            "test_sha256": [file_sha(path / name) for name in ("verify.py", "verify_once.py")],
            "initial_exit_nonzero": True}


def prepare_fixtures(output: Path, env: dict[str, str]) -> dict:
    fixtures: dict[str, dict] = {}
    for task, arm in ARMS:
        path = output / f"{task}-{arm}" / "checkout"
        path.parent.mkdir(parents=True)
        if task == "rust":
            fixtures[f"{task}-{arm}"] = codex_coding_pair.make_fixture(path, env)
            with (path / ".git" / "info" / "exclude").open("a", encoding="utf-8") as excluded:
                excluded.write("\ntask-frame.json\n")
        else:
            fixtures[f"{task}-{arm}"] = prepare_python(path, env)
    for task in ("rust", "python"):
        values = [fixtures[f"{t}-{arm}"] for t, arm in ARMS if t == task]
        if any(value != values[0] for value in values[1:]):
            raise RuntimeError(f"{task} fixture arms have different baseline trees")
    return fixtures


def preflight(claude: Path, env: dict[str, str], output: Path) -> dict:
    auth = json.loads(checked([str(claude), "auth", "status"], output, env))
    if not (auth.get("loggedIn") and auth.get("authMethod") == "claude.ai"
            and auth.get("subscriptionType") == "pro"):
        raise RuntimeError("Claude Code must use the signed-in Pro subscription")
    usage = run([str(claude), "--safe-mode", "--strict-mcp-config",
                 "--no-session-persistence", "--model", MODEL,
                 "--output-format", "json", "-p", "/usage"], output, env, 30)
    if usage.returncode:
        raise RuntimeError("Claude subscription usage check failed")
    usage_result = json.loads(usage.stdout)
    usage_text = str(usage_result.get("result", ""))
    if (usage_result.get("is_error") or "using your subscription" not in usage_text
            or "usage credits" in usage_text.lower()):
        raise RuntimeError("Claude usage cannot be confirmed as subscription-only")
    command = [str(claude), "--safe-mode", "--strict-mcp-config",
               "--no-session-persistence", "--model", MODEL,
               "--permission-mode", "dontAsk", "--permission-prompts", "none",
               "--max-budget-usd", "0.02", "--max-turns", "1",
               "--output-format", "json", "-p", "Reply READY only."]
    control = run(command, output, env, 45)
    if control.returncode:
        raise RuntimeError("Claude pinned-model preflight failed")
    result = json.loads(control.stdout)
    parsed = parse_terminal(result, control.returncode)
    if not parsed["valid"] or result.get("result", "").strip() != "READY":
        raise RuntimeError(f"Claude preflight invalid: {parsed['reasons']}")
    (output / "preflight.json").write_text(json.dumps({
        "model_usage": result["modelUsage"], "usage": result.get("usage"),
        "list_price_estimate_usd": result.get("total_cost_usd"),
        "subscription_check": "Pro subscription; no usage-credits row exposed",
    }, indent=2) + "\n", encoding="utf-8")
    return {"model": MODEL, "auth_method": "claude.ai", "subscription": "pro",
            "list_price_estimate_usd": result.get("total_cost_usd"),
            "usage_credits_row": False}


def parse_terminal(result: dict, cli_exit: int | None) -> dict:
    reasons = []
    if (cli_exit != 0 or result.get("type") != "result"
            or result.get("subtype") != "success" or result.get("is_error")
            or result.get("stop_reason") != "end_turn"
            or not isinstance(result.get("num_turns"), int)
            or not 1 <= result["num_turns"] <= 12):
        reasons.append("incomplete_or_failed_claude_turn")
    model_usage = result.get("modelUsage")
    if not isinstance(model_usage, dict) or set(model_usage) != {MODEL}:
        reasons.append("missing_or_substituted_model")
        usage = {}
    else:
        usage = model_usage[MODEL]
    fields = ("inputTokens", "cacheCreationInputTokens", "cacheReadInputTokens", "outputTokens")
    if any(not isinstance(usage.get(field), int) or usage[field] < 0 for field in fields):
        reasons.append("missing_or_invalid_token_usage")
    total = sum(usage[field] for field in fields) if not any(
        not isinstance(usage.get(field), int) or usage[field] < 0 for field in fields) else None
    cost = result.get("total_cost_usd")
    if not isinstance(cost, (int, float)) or cost < 0:
        reasons.append("missing_cost_estimate")
    elif cost > 0.20:
        reasons.append("cli_estimated_cost_cap_exceeded")
    return {"valid": not reasons, "reasons": reasons, "model_usage": usage,
            "combined_tokens": total,
            "list_price_estimate_usd": result.get("total_cost_usd"),
            "final_answer": result.get("result", "")}


def review_answer(task: str, arm: str, final: str, capture_id: str | None) -> dict:
    if task == "rust":
        counts_ok = (bool(re.search(r"(?:500\s+passed|passed\D{0,12}500)", final, re.I))
                     and bool(re.search(r"(?:0\s+failed|failed\D{0,12}0)", final, re.I)))
        warning_ok = True
    else:
        counts_ok = bool(re.search(r"(?:81\s+passed|passed(?:-test)?\s+total\D{0,12}81)", final, re.I))
        warning_ok = "warning: cache invalidated before the failed summary" in final
    exit_ok = bool(re.search(r"child\s+exit[^\n]{0,45}\b0\b", final, re.I))
    capture_lines = [line.lower() for line in final.splitlines() if "jevto capture id" in line.lower()]
    if arm == "native":
        capture_ok = all(any(value in line for value in ("not present", "none", "no capture"))
                         for line in capture_lines)
    else:
        capture_ok = bool(capture_id) and any(capture_id in line for line in capture_lines)
    return {"valid": counts_ok and warning_ok and exit_ok and capture_ok,
            "counts_ok": counts_ok, "warning_ok": warning_ok,
            "exit_ok": exit_ok, "capture_claim_ok": capture_ok}


def parse_stream(stream: str) -> tuple[dict, list[dict]]:
    events = [json.loads(line) for line in stream.splitlines() if line.strip()]
    results = [event for event in events if event.get("type") == "result"]
    if len(results) != 1:
        raise ValueError(f"expected one terminal result, got {len(results)}")
    tool_calls = []
    for event in events:
        if event.get("type") != "assistant":
            continue
        content = event.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_calls.append({"id": block.get("id"), "name": block.get("name"),
                                   "input": block.get("input", {})})
    return results[0], tool_calls


def tool_result(stream: str, call_id: str) -> dict | None:
    matches = [block for line in stream.splitlines() if line.strip()
               for event in [json.loads(line)] if event.get("type") == "user"
               for block in event.get("message", {}).get("content", [])
               if isinstance(block, dict) and block.get("type") == "tool_result"
               and block.get("tool_use_id") == call_id]
    return matches[0] if len(matches) == 1 else None


def verifier_result_ok(task: str, arm: str, result: dict | None,
                       source_sha256: str, capture_id: str | None) -> bool:
    if not result or result.get("is_error") is not False or not isinstance(result.get("content"), str):
        return False
    selected = result["content"]
    if task == "rust":
        counts = [(int(passed), int(failed)) for passed, failed in
                  re.findall(r"test result: ok\.\s*(\d+) passed;\s*(\d+) failed", selected)]
        correct_output = bool(counts) and sum(passed for passed, _ in counts) == 500 and all(
            failed == 0 for _, failed in counts)
    else:
        correct_output = ("warning: cache invalidated before the failed summary" in selected
                          and f"source_sha256={source_sha256}" in selected
                          and (selected.count("... ok") == 81 if arm == "native" else
                               "test inventory: 81 explicit ok results" in selected))
    if arm != "native":
        correct_output = (correct_output and bool(capture_id)
                          and f"capture_id: {capture_id}" in selected
                          and "process: SUCCEEDED (exit 0)" in selected)
    return correct_output


def verifier_command(task: str, arm: str, jevto: Path, task_frame: Path | None) -> str:
    child = "cargo test --workspace" if task == "rust" else "python verify_once.py"
    if arm == "native":
        return child
    prefix = "jevto.exe run"
    if arm == "adaptive":
        assert task_frame is not None
        prefix += f" --mode adaptive --allow-remote-jev --task-file {task_frame.name}"
    return prefix + " -- " + child


def check_rust(path: Path, output: Path, env: dict[str, str]) -> dict:
    visible = run(["cargo", "test", "--workspace"], path, env, 120)
    (output / "independent-visible.stdout").write_bytes(visible.stdout)
    (output / "independent-visible.stderr").write_bytes(visible.stderr)
    holdout = codex_coding_pair.holdout(path, output, env)
    counts = [(int(passed), int(failed)) for passed, failed in
              re.findall(rb"test result: ok\.\s*(\d+) passed;\s*(\d+) failed", visible.stdout)]
    return {"visible_passed": visible.returncode == 0 and
            sum(passed for passed, _ in counts) == 500 and
            sum(failed for _, failed in counts) == 0,
            "visible_counts": counts, "visible_exit": visible.returncode, "holdout": holdout}


def check_python(path: Path, output: Path, env: dict[str, str]) -> dict:
    visible = run([sys.executable, "verify_once.py"], path, env, 30)
    (output / "independent-visible.stdout").write_bytes(visible.stdout)
    (output / "independent-visible.stderr").write_bytes(visible.stderr)
    holdout_path = HERE / "holdout" / "quiet_warning.py"
    holdout = run([sys.executable, "-c", "import runpy; runpy.run_path(" + repr(str(holdout_path)) + ")"],
                  path, env, 30)
    source_digest = file_sha(path / "summarize.py")
    marker = path / ".verified-source-sha256"
    return {"visible_passed": visible.returncode == 0 and
            visible.stdout.count(b"... ok") == 81 and
            b"test suite::summary ... ok" in visible.stdout and
            b"warning: cache invalidated" in visible.stdout,
            "visible_exit": visible.returncode,
            "holdout": {"passed": holdout.returncode == 0,
                        "exit": holdout.returncode,
                        "output": (holdout.stdout + holdout.stderr).decode("utf-8", "replace")[-1200:]},
            "final_source_marker_matches": marker.is_file() and
            marker.read_text(encoding="ascii") == source_digest}


def capture_check(arm: str, arm_dir: Path, jevto: Path, trace: str,
                  env: dict[str, str], selected: str = "") -> dict:
    store = arm_dir / "store"
    receipts = [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted((store / "receipts").glob("*.json"))]
    if arm == "native":
        return {"valid": not receipts, "receipts": receipts, "reason": "native_no_receipt"}
    if len(receipts) != 1:
        return {"valid": False, "receipts": receipts, "reason": "expected_one_receipt"}
    receipt = receipts[0]
    cid = receipt.get("capture_id")
    if not isinstance(cid, str):
        return {"valid": False, "receipts": receipts, "reason": "missing_capture_id"}
    record_path = store / "captures" / f"{cid}.json"
    if not record_path.is_file():
        return {"valid": False, "receipts": receipts, "reason": "missing_capture_record"}
    record = json.loads(record_path.read_text(encoding="utf-8"))
    raw_stdout = (store / "captures" / f"{cid}.stdout").read_bytes()
    raw_stderr = (store / "captures" / f"{cid}.stderr").read_bytes()
    recall = run([str(jevto), "--store-dir", str(store), "recall", cid, "--full"],
                 arm_dir, env, 30)
    raw_valid = (record.get("raw_sha256") == canonical_hash(raw_stdout, raw_stderr)
                 and record.get("stdout_bytes") == len(raw_stdout)
                 and record.get("stderr_bytes") == len(raw_stderr))
    packs = [json.loads(p.read_text(encoding="utf-8")) for p in (store / "packs").glob("*.json")]
    normalized = lambda value: value.replace("\r\r\n", "\r\n").replace("\r\n", "\n")
    blocks_valid = len(packs) == 1 and packs[0].get("capture_id") == cid and packs[0].get(
        "raw_sha256") == record.get("raw_sha256")
    if blocks_valid:
        for block in packs[0].get("blocks", []):
            source = raw_stdout if block.get("stream") == "stdout" else raw_stderr
            start, end = block.get("byte_start"), block.get("byte_end")
            if (block.get("stream") not in ("stdout", "stderr")
                    or not isinstance(start, int) or not isinstance(end, int)
                    or not 0 <= start <= end <= len(source)):
                blocks_valid = False
                break
            raw_block = source[start:end]
            rendered = block.get("rendered_text")
            if (sha(raw_block) != block.get("sha256") or not isinstance(rendered, str)
                    or raw_block.decode("utf-8", "strict") != rendered
                    or normalized(rendered) not in normalized(selected)):
                blocks_valid = False
                break
    recall_valid = recall.returncode == 0 and recall.stdout == raw_stdout and recall.stderr == raw_stderr
    (arm_dir / "recalled.stdout").write_bytes(recall.stdout)
    (arm_dir / "recalled.stderr").write_bytes(recall.stderr)
    return {"valid": raw_valid and recall_valid and blocks_valid and cid in trace and
            receipt.get("raw_bytes", 0) > receipt.get("delivered_bytes", 0),
            "reason": "exact_recall_checked", "receipt": receipt,
            "capture_record": record, "raw_digest_valid": raw_valid,
            "recall_byte_exact": recall_valid, "selected_blocks_match_raw": blocks_valid,
            "capture_visible_in_trace": cid in trace,
            "raw_stdout_sha256": sha(raw_stdout), "raw_stderr_sha256": sha(raw_stderr)}


def candidate_quality(task: str, independent: dict, source_only: bool,
                      source_changed: bool, tests_unchanged: bool) -> bool:
    return bool(independent["visible_passed"] and independent["holdout"]["passed"]
                and source_only and source_changed and tests_unchanged
                and (task != "python" or independent["final_source_marker_matches"]))


def run_arm(task: str, arm: str, claude: Path, jevto: Path,
            output: Path, initial: dict, base_env: dict[str, str], key: str | None) -> dict:
    name = f"{task}-{arm}"
    arm_dir = output / name
    checkout = arm_dir / "checkout"
    env = base_env.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["NO_COLOR"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["PAGER"] = "cat"
    task_frame = None
    if arm != "native":
        env["JEVTO_STORE_DIR"] = str(arm_dir / "store")
    if arm == "adaptive":
        if not key:
            raise RuntimeError("adaptive arm requires a runtime OpenRouter key")
        env["OPENROUTER_API_KEY"] = key
        task_frame = checkout / "task-frame.json"
        task_frame.write_text(json.dumps({
            "schema_version": 1, "session_id": "claude-pilot-rust-adaptive",
            "workspace_id": "claude-pilot-rust", "revision": 0,
            "goal": "Fix total to sum trimmed value lines and ignore warning or note lines",
            "trusted_constraints": [],
        }, indent=2) + "\n", encoding="utf-8")
    command = verifier_command(task, arm, jevto, task_frame)
    prompt = (RUST_PROMPT if task == "rust" else PYTHON_PROMPT) + "\nVerifier command: `" + command + "`"
    (arm_dir / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    # Restrict the key-bearing adaptive process to the exact verifier shell call.
    allowed = "Read,Edit,Bash(" + command + ")"
    argv = [str(claude), "--safe-mode", "--strict-mcp-config", "--no-chrome",
            "--no-session-persistence", "--model", MODEL,
            "--tools", "Read,Edit,Bash", "--allowedTools", allowed,
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--max-budget-usd", "0.20", "--max-turns", "12",
            "--output-format", "stream-json", "--verbose", "-p", prompt]
    start = time.monotonic()
    try:
        process = run(argv, checkout, env, 300)
        timed_out = False
        cli_exit = process.returncode
        stdout = process.stdout
        stderr = process.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        cli_exit = None
        stdout = error.stdout or b""
        stderr = error.stderr or b""
    elapsed = round(time.monotonic() - start, 3)
    scrub = lambda data: data.decode("utf-8", "replace").replace(key, "[REDACTED]") if key else data.decode("utf-8", "replace")
    trace = scrub(stdout)
    stderr_text = scrub(stderr)
    (arm_dir / "claude-stream.jsonl").write_text(trace, encoding="utf-8")
    (arm_dir / "claude-stderr.txt").write_text(stderr_text, encoding="utf-8")
    try:
        terminal, calls = parse_stream(trace)
        parsed = parse_terminal(terminal, cli_exit)
    except (ValueError, json.JSONDecodeError) as error:
        terminal, calls = {}, []
        parsed = {"valid": False, "reasons": ["missing_or_invalid_terminal_result", str(error)],
                  "model_usage": {}, "combined_tokens": None,
                  "list_price_estimate_usd": None, "final_answer": ""}
    verify_calls = [call for call in calls if call["name"] == "Bash" and
                    ("cargo test --workspace" if task == "rust" else "verify_once.py")
                    in str(call["input"].get("command", ""))]
    verifier_response = (tool_result(trace, verify_calls[0]["id"])
                         if len(verify_calls) == 1 and isinstance(verify_calls[0].get("id"), str)
                         else None)
    selected = (verifier_response.get("content") if verifier_response and
                isinstance(verifier_response.get("content"), str) else "")
    (arm_dir / "verifier-result.txt").write_text(selected, encoding="utf-8")
    source = checkout / ("src/lib.rs" if task == "rust" else "summarize.py")
    tests = [checkout / path for path in (("tests/a_inventory.rs", "tests/z_critical.rs")
                                    if task == "rust" else ("verify.py", "verify_once.py"))]
    changed = checked(["git", "status", "--short"], checkout, base_env)
    diff = checked(["git", "diff", "--", str(source.relative_to(checkout))], checkout, base_env)
    (arm_dir / "candidate.diff").write_text(diff + "\n", encoding="utf-8")
    independent = check_rust(checkout, arm_dir, base_env) if task == "rust" else check_python(checkout, arm_dir, base_env)
    capture = capture_check(arm, arm_dir, jevto, trace, base_env, selected)
    receipts = [] if arm == "native" else [capture.get("receipt", {})] if capture.get("receipt") else capture.get("receipts", [])
    jev_cost = sum((r.get("jev_usage") or {}).get("billed_cost") or 0 for r in receipts)
    source_only = changed == ("M src/lib.rs" if task == "rust" else "M summarize.py")
    quality = candidate_quality(
        task, independent, source_only, file_sha(source) != initial["source_sha256"],
        [file_sha(test) for test in tests] == initial["tests_sha256" if task == "rust" else "test_sha256"])
    final = str(parsed["final_answer"])
    capture_id = (capture.get("receipt") or {}).get("capture_id")
    verifier_output_ok = verifier_result_ok(task, arm, verifier_response,
                                            file_sha(source), capture_id)
    answer_review = review_answer(task, arm, final, capture_id)
    answer_ok = answer_review["valid"]
    route_ok = len(verify_calls) == 1 and command in str(verify_calls[0]["input"].get("command", ""))
    if arm == "adaptive":
        adaptive_ok = (len(receipts) == 1 and receipts[0].get("mode") == "adaptive"
                       and bool(receipts[0].get("jev_usage"))
                       and receipts[0].get("jev_call_attempted") is True
                       and jev_cost < 1.0)
    else:
        adaptive_ok = True
    reasons = list(parsed["reasons"])
    if timed_out:
        reasons.append("timeout")
    if not route_ok:
        reasons.append("verifier_not_run_once_through_requested_route")
    if not capture["valid"]:
        reasons.append("capture_or_recall_invalid")
    if not verifier_output_ok:
        reasons.append("verifier_output_invalid")
    if not quality:
        reasons.append("final_candidate_failed_visible_holdout_or_file_scope")
    if not answer_ok:
        reasons.append("final_answer_not_trace_faithful")
    if not adaptive_ok:
        reasons.append("adaptive_jev_not_observed_or_over_budget")
    result = {"task": task, "arm": arm, "valid": not reasons,
              "invalid_reasons": reasons, "cli_exit": cli_exit,
              "timed_out": timed_out, "elapsed_seconds": elapsed,
              "model": MODEL, "verifier_command": command,
              "completed_tool_calls": calls, "verifier_call_count": len(verify_calls),
              "provider_usage": parsed["model_usage"],
              "combined_tokens": parsed["combined_tokens"],
              "list_price_estimate_usd": parsed["list_price_estimate_usd"],
              "jev_reported_cost_usd": jev_cost,
              "final_answer": final, "git_status": changed,
              "source_sha256": file_sha(source), "quality_checks": independent,
              "capture_check": capture, "verifier_output_ok": verifier_output_ok,
              "verifier_result_sha256": sha(selected.encode("utf-8")),
              "answer_ok": answer_ok,
              "answer_review": answer_review,
              "source_and_tests_ok": quality,
              "trace_path": str(arm_dir / "claude-stream.jsonl"),
              "diff_path": str(arm_dir / "candidate.diff")}
    (arm_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def self_test() -> None:
    sample = {"type": "result", "subtype": "success", "is_error": False,
              "stop_reason": "end_turn", "num_turns": 1, "result": "READY",
              "total_cost_usd": 0.01, "modelUsage": {MODEL: {
                  "inputTokens": 1, "cacheCreationInputTokens": 2,
                  "cacheReadInputTokens": 3, "outputTokens": 4}}}
    assert parse_terminal(sample, 0)["combined_tokens"] == 10
    assert not parse_terminal({**sample, "modelUsage": {"other": {}}}, 0)["valid"]
    assert not parse_terminal({**sample, "modelUsage": {}}, 0)["valid"]
    assert not parse_terminal({**sample, "modelUsage": {MODEL: {}}}, 0)["valid"]
    assert not parse_terminal(sample, 1)["valid"]
    assert not parse_terminal({**sample, "subtype": "error_max_turns"}, 0)["valid"]
    assert not parse_terminal({**sample, "stop_reason": "max_tokens"}, 0)["valid"]
    assert not parse_terminal({**sample, "num_turns": 13}, 0)["valid"]
    assert not parse_terminal({**sample, "total_cost_usd": 0.21}, 0)["valid"]
    assert canonical_hash(b"abc", b"def") != canonical_hash(b"abcdef", b"")
    with tempfile.TemporaryDirectory() as temp_dir:
        assert not capture_check("deterministic", Path(temp_dir), Path("unused"), "", {})["valid"]
    checks = {"visible_passed": True, "holdout": {"passed": True},
              "final_source_marker_matches": True}
    assert candidate_quality("python", checks, True, True, True)
    assert not candidate_quality("python", {**checks, "holdout": {"passed": False}},
                                 True, True, True)
    assert review_answer("python", "native",
                         "Passed-test total: 81\nChild exit: 0\n"
                         "Preserved warning: warning: cache invalidated before the failed summary\n"
                         "JevTO capture ID: source_sha256=abc", None)["capture_claim_ok"] is False
    print("Claude pilot parser self-test passed")


def review_existing(output: Path) -> None:
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    jevto = output / "bin" / "jevto.exe"
    for summary in manifest["runs"]:
        arm_dir = output / f"{summary['task']}-{summary['arm']}"
        result_path = arm_dir / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        trace = (arm_dir / "claude-stream.jsonl").read_text(encoding="utf-8")
        terminal, calls = parse_stream(trace)
        parsed = parse_terminal(terminal, result["cli_exit"])
        verifier_calls = [call for call in calls if call["name"] == "Bash" and
                          result["verifier_command"] in str(call["input"].get("command", ""))]
        response = (tool_result(trace, verifier_calls[0]["id"])
                    if len(verifier_calls) == 1 else None)
        selected = (response.get("content") if response and
                    isinstance(response.get("content"), str) else "")
        (arm_dir / "verifier-result.txt").write_text(selected, encoding="utf-8")
        capture = capture_check(result["arm"], arm_dir, jevto, trace, os.environ.copy(), selected)
        capture_id = (capture.get("receipt") or {}).get("capture_id")
        output_ok = verifier_result_ok(result["task"], result["arm"], response,
                                       result["source_sha256"], capture_id)
        answer_review = review_answer(result["task"], result["arm"],
                                      result["final_answer"], capture_id)
        reassessed = {"final_answer_not_trace_faithful", "capture_or_recall_invalid",
                      "verifier_output_invalid", "missing_or_substituted_model",
                      "missing_or_invalid_token_usage", "incomplete_or_failed_claude_turn",
                      "missing_cost_estimate", "cli_estimated_cost_cap_exceeded"}
        reasons = [reason for reason in result["invalid_reasons"]
                   if reason not in reassessed]
        reasons.extend(parsed["reasons"])
        if not capture["valid"]:
            reasons.append("capture_or_recall_invalid")
        if not output_ok:
            reasons.append("verifier_output_invalid")
        if not answer_review["valid"]:
            reasons.append("final_answer_not_trace_faithful")
        result["capture_check"] = capture
        result["verifier_output_ok"] = output_ok
        result["verifier_result_sha256"] = sha(selected.encode("utf-8"))
        result["answer_review"] = answer_review
        result["answer_ok"] = answer_review["valid"]
        result["invalid_reasons"] = reasons
        result["valid"] = not reasons
        result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        summary["invalid_reasons"] = reasons
        summary["valid"] = result["valid"]
        summary["answer_ok"] = result["answer_ok"]
        print(f"reviewed {result['task']}-{result['arm']}: valid={result['valid']} "
              f"verifier={output_ok} capture={capture['valid']} answer={answer_review}")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude-cli", type=Path)
    parser.add_argument("--jevto-cli", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--review-existing", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.review_existing:
        review_existing(args.review_existing.resolve(strict=True))
        return 0
    if os.name != "nt" or not args.claude_cli or not args.jevto_cli:
        parser.error("Windows, --claude-cli, and --jevto-cli are required")
    claude = args.claude_cli.resolve(strict=True)
    jevto = args.jevto_cli.resolve(strict=True)
    output = ROOT / ".bench-runs" / f"claude-coding-pilot-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("JEVTO_STORE_DIR", None)
    env["RUSTUP_HOME"] = str(ROOT.parent / ".tooling" / "rustup-isolated")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    bin_dir = output / "bin"
    bin_dir.mkdir()
    staged_jevto = bin_dir / "jevto.exe"
    shutil.copy2(jevto, staged_jevto)
    if file_sha(staged_jevto) != file_sha(jevto):
        raise RuntimeError("staged JevTO binary hash mismatch")
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    fixtures = prepare_fixtures(output, env)
    manifest = {"purpose": "focused exploratory Claude Code pilot",
                "claude_version": checked([str(claude), "--version"], output, env),
                "claude_sha256": file_sha(claude), "jevto_sha256": file_sha(jevto),
                "staged_jevto_sha256": file_sha(staged_jevto),
                "model_requested": MODEL, "arm_order": [f"{t}-{a}" for t, a in ARMS],
                "max_seconds_per_arm": 300, "max_turns_per_arm": 12,
                "max_list_price_estimate_usd_per_arm": 0.20,
                "rust_initial": fixtures["rust-native"],
                "python_initial": fixtures["python-native"],
                "python_holdout_sha256": file_sha(HERE / "holdout" / "quiet_warning.py"),
                "rust_holdout_template_sha256": sha(codex_coding_pair.HOLDOUT.encode("utf-8")),
                "provider_bill_usd": None,
                "prompt_difference": "verifier command only; explicit wrapper",
                "runs": []}
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"artifact_dir={output}", flush=True)
    if args.prepare_only:
        print("fixtures prepared; no model calls", flush=True)
        return 0
    manifest["preflight"] = preflight(claude, env, output)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if not sys.stdin.isatty():
        raise RuntimeError("adaptive key must be entered from a TTY")
    key = getpass.getpass("OpenRouter key for one adaptive Jev call: ")
    if not key.startswith("sk-or-"):
        raise RuntimeError("invalid OpenRouter key format")
    try:
        for task, arm in ARMS:
            print(f"starting {task}-{arm}", flush=True)
            result = run_arm(task, arm, claude, jevto, output, fixtures[f"{task}-{arm}"], env,
                             key if arm == "adaptive" else None)
            manifest["runs"].append({name: value for name, value in result.items()
                                     if name not in {"completed_tool_calls", "final_answer",
                                                     "capture_check", "quality_checks"}})
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            print(f"finished {task}-{arm}: valid={result['valid']} "
                  f"tokens={result['combined_tokens']} reasons={result['invalid_reasons']}", flush=True)
            if ("missing_or_substituted_model" in result["invalid_reasons"]
                    or "missing_or_invalid_token_usage" in result["invalid_reasons"]
                    or "incomplete_or_failed_claude_turn" in result["invalid_reasons"]):
                print("stopping after a host/model/usage failure", flush=True)
                break
    finally:
        key = ""
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"artifact_dir={output}", flush=True)
    return 0 if len(manifest["runs"]) == len(ARMS) else 2


if __name__ == "__main__":
    raise SystemExit(main())
