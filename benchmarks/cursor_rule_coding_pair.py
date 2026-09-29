"""Bounded edit-and-verify pilot for Cursor project-rule routing.

Run one arm per fresh process. Native and JevTO arms start from the same
application tree and receive the same task prompt. Only the committed adapter
rule differs. This is exploratory, not a confirmatory savings benchmark.
"""

from __future__ import annotations

import argparse
import atexit
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import cursor_explicit_route
import cursor_mcp_run_probe
import cursor_runner_coding_probe
import pilot


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
MARKER = "JEVTO_RULE_CODING_DONE"
ALLOWED_MODELS = {pilot.MODEL, "Grok 4.7 Extra High", "Grok 4.7  Extra High",
                  "Grok 4.7 256K Extra High"}


def command(argv: list[str], cwd: Path, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=timeout)


def git_commit(checkout: Path, rule: Path) -> str:
    for argv in (["git", "add", rule.relative_to(checkout).as_posix()],
                 ["git", "-c", "user.name=JevTO Probe", "-c", "user.email=jevto-probe@invalid",
                  "commit", "-qm", "Disposable verifier route"]):
        result = command(argv, checkout)
        if result.returncode != 0:
            raise RuntimeError(f"disposable adapter commit failed: {result.stderr}")
    tree = command(["git", "rev-parse", "HEAD^{tree}"], checkout)
    if tree.returncode != 0:
        raise RuntimeError("disposable adapter tree hash unavailable")
    return tree.stdout.strip()


def stream_events(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def shell_commands(events: list[dict]) -> list[str]:
    commands = []
    for event in events:
        if event.get("type") != "tool_call" or event.get("subtype") != "completed":
            continue
        tool = event.get("tool_call", {})
        shell = tool.get("shellToolCall", {}) if isinstance(tool, dict) else {}
        args = shell.get("args", {}) if isinstance(shell, dict) else {}
        value = args.get("command") if isinstance(args, dict) else None
        if isinstance(value, str):
            commands.append(value)
    return commands


def run_arm(arm: str, launcher: Path, jevto: Path, timeout: int, destination: Path) -> dict:
    if arm not in {"native_rule", "jevto_rule"}:
        raise ValueError("arm must be native_rule or jevto_rule")
    preflight = cursor_mcp_run_probe.preflight_direct(launcher, jevto)
    fixture = cursor_runner_coding_probe.prepare_fixture(destination)
    checkout = destination / "checkout"
    initial_tree = pilot.init_checkout(fixture, checkout)
    disabled_mcps = pilot.configured_mcp_names()
    pilot.disable_project_mcps(launcher, checkout, disabled_mcps)
    store = destination / "store"
    rule = checkout / ".cursor" / "rules" / "jevto-verifier.mdc"
    owned = arm == "jevto_rule"
    if owned:
        setup = [str(jevto), "--store-dir", str(store), "init-cursor-rule",
                 "--workspace", str(checkout), "--", "python", "verify_once.py"]
        preview = command(setup, checkout)
        if preview.returncode != 0 or "Preview only" not in preview.stdout or rule.exists():
            raise RuntimeError("Cursor rule preview changed files or failed")
        (destination / "rule-preview.txt").write_text(preview.stdout, encoding="utf-8")
        applied = command(setup[:6] + ["--apply"] + setup[6:], checkout)
        if applied.returncode != 0 or not rule.is_file():
            raise RuntimeError("Cursor rule installer failed: " + applied.stderr)
    else:
        rule.parent.mkdir(parents=True)
        rule.write_text(
            "---\ndescription: Use the native verifier for this project\nalwaysApply: true\n---\n"
            "When verifying this project with `python verify_once.py`, run this exact shell "
            "command once: `python verify_once.py`. Wait for the process to finish and report "
            "its child exit and passing-test total. Do not rerun the verifier.\n",
            encoding="utf-8",
        )
    installed_rule = rule.read_bytes()
    (destination / "rule-installed.mdc").write_bytes(installed_rule)
    adapter_tree = git_commit(checkout, rule)

    cleanup_done = False

    def cleanup_rule() -> bool:
        nonlocal cleanup_done
        if cleanup_done:
            return True
        if not owned:
            cleanup_done = True
            return True
        removed = command([str(jevto), "--store-dir", str(store), "disable-cursor-rule",
                           "--workspace", str(checkout), "--apply"], checkout)
        (destination / "rule-disable.txt").write_text(removed.stdout + removed.stderr,
                                                        encoding="utf-8")
        cleanup_done = removed.returncode == 0 and not rule.exists()
        return cleanup_done

    if owned:
        atexit.register(cleanup_rule)

    prompt = (
        "In this disposable project, fix summarize.total so it sums only lines beginning "
        "with `value=` and ignores warnings or notes, including misleading mentions of value=. "
        "Read summarize.py and verify.py, edit only summarize.py, and preserve the tests. "
        "Do not inspect Git history or install dependencies. After the edit, run the project's "
        "verify_once.py verifier once according to the project instructions. Wait for its result "
        "and report the child exit, passing-test total, and any capture ID. "
        f"End with `{MARKER}`."
    )
    environment = os.environ.copy()
    environment.pop("OPENROUTER_API_KEY", None)
    environment.pop("JEVTO_STORE_DIR", None)
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1",
                        "GIT_PAGER": "cat", "PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"})
    invocation = cursor_explicit_route.direct_launcher(
        launcher, "-p", "--force", "--trust", "--sandbox",
        "disabled" if os.name == "nt" else "enabled", "--output-format", "stream-json",
        "--model", pilot.MODEL, "--workspace", str(checkout), prompt,
    )
    manifest = {
        "kind": "cursor_project_rule_coding_pair_arm", "arm": arm,
        "preflight": preflight, "prompt": prompt,
        "source_tree_before_adapter_sha256": initial_tree,
        "adapter_git_tree": adapter_tree,
        "adapter_rule_sha256": hashlib.sha256(installed_rule).hexdigest(),
        "frozen_holdout_sha256": pilot.sha_file(HERE / "holdout" / "quiet_warning.py"),
        "disabled_project_mcps": disabled_mcps,
        "timeout_seconds": timeout, "not_confirmatory": True,
        "provider_bill": "not exposed by Cursor CLI stream",
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    stream_path = destination / "cursor-stream.jsonl"
    stderr_path = destination / "cursor-stderr.txt"
    started = time.monotonic()
    with stream_path.open("wb") as stream, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            invocation, cwd=checkout, env=environment, stdin=subprocess.DEVNULL,
            stdout=stream, stderr=stderr,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        try:
            cli_exit = process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            cursor_explicit_route.stop_process(process)
            cli_exit = None
    elapsed = round(time.monotonic() - started, 3)
    events = stream_events(stream_path)
    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    observations = pilot.stream_observations(stream_path.read_text(encoding="utf-8", errors="replace"))
    reported_model = observations["reported_model"]
    if reported_model is not None and reported_model not in ALLOWED_MODELS:
        raise RuntimeError(f"Cursor substituted model {reported_model}; invalid run")
    terminal = [event for event in events if event.get("type") == "result"]
    final_text = str(terminal[-1].get("result", "")) if terminal else ""
    completed_commands = shell_commands(events)
    verification = pilot.verify_candidate("quiet_warning", checkout)
    (destination / "candidate.diff").write_text(verification.pop("tracked_diff"), encoding="utf-8")
    source = checkout / "summarize.py"
    source_hash = pilot.sha_file(source) if source.is_file() else None
    marker = checkout / ".verified-source-sha256"
    marker_matches = bool(source_hash and marker.is_file()
                          and marker.read_text(encoding="ascii") == source_hash)
    fixture_unchanged = all((checkout / name).is_file()
                            and (checkout / name).read_bytes() == (fixture / name).read_bytes()
                            for name in ("verify.py", "verify_once.py"))
    rule_unchanged = rule.is_file() and rule.read_bytes() == installed_rule
    changed_files = verification["git_status"]
    only_source_changed = changed_files == [" M summarize.py"]
    receipts = [json.loads(path.read_text(encoding="utf-8"))
                for path in (store / "receipts").glob("*.json")]
    native = subprocess.run([sys.executable, "verify_once.py"], cwd=checkout, env=environment,
                            capture_output=True, timeout=20)
    exact_recall = False
    capture_in_tool = False
    capture_in_final = False
    if owned and len(receipts) == 1:
        receipt = receipts[0]
        capture_id = receipt.get("capture_id")
        if isinstance(capture_id, str):
            recalled = subprocess.run([str(jevto), "--store-dir", str(store), "recall",
                                       capture_id, "--full"], cwd=checkout,
                                      capture_output=True, timeout=20)
            exact_recall = (recalled.returncode == 0 and recalled.stdout == native.stdout
                            and recalled.stderr == native.stderr)
            capture_in_tool = any(capture_id in json.dumps(event.get("tool_call", {}))
                                  for event in events if event.get("type") == "tool_call"
                                  and event.get("subtype") == "completed")
            capture_in_final = capture_id in final_text
    cleanup_ok = cleanup_rule()
    if owned:
        atexit.unregister(cleanup_rule)

    reasons = []
    if timed_out or cli_exit != 0 or observations["result_event_count"] != 1 or observations["result_subtype"] != "success":
        reasons.append("model_turn_not_completed")
    if reported_model is None or observations["provider_usage_reported"] is None:
        reasons.append("model_identity_or_usage_missing")
    if MARKER not in final_text:
        reasons.append("final_marker_missing")
    if not verification["verified"] or native.returncode != 0:
        reasons.append("final_candidate_failed_visible_or_frozen_holdout")
    if not marker_matches or not fixture_unchanged or not rule_unchanged or not only_source_changed:
        reasons.append("final_source_or_fixture_identity_invalid")
    if not cleanup_ok:
        reasons.append("owned_rule_cleanup_failed")
    verifier_calls = [value for value in completed_commands
                      if re.search(r"(?i)(?:^|\s|['\"])verify_once\.py(?:\s|['\"]|$)", value)
                      and not re.search(r"(?i)\b(?:Get-Content|cat|type|rg|grep)\b", value)]
    if len(verifier_calls) != 1:
        reasons.append("verifier_shell_call_count_not_one")
    if owned:
        if len(receipts) != 1 or receipts[0].get("child_exit") != 0:
            reasons.append("jevto_receipt_missing_or_failed")
        if not exact_recall or not capture_in_tool or not capture_in_final:
            reasons.append("capture_delivery_or_exact_recall_missing")
        if not any(jevto.name in value for value in verifier_calls):
            reasons.append("installed_rule_wrapper_not_followed")
    elif receipts:
        reasons.append("native_arm_has_jevto_receipt")
    model_rejected_before_turn = (not events and
                                  f"Cannot use this model: {pilot.MODEL}" in stderr_text)
    if not events:
        reasons = (["host_rejected_exact_model_before_turn"] if model_rejected_before_turn
                   else ["no_model_events"])
    result = {
        "arm": arm, "model_requested": pilot.MODEL, "reported_model": reported_model,
        "initial_tree_sha256": initial_tree, "adapter_git_tree": adapter_tree,
        "cli_exit": cli_exit, "timed_out": timed_out, "elapsed_seconds": elapsed,
        "model_rejected_before_turn": model_rejected_before_turn,
        "transport": observations, "verification": verification,
        "source_sha256": source_hash, "marker_matches_final_source": marker_matches,
        "fixture_unchanged": fixture_unchanged, "rule_unchanged_during_turn": rule_unchanged,
        "only_source_changed": only_source_changed,
        "completed_shell_commands": completed_commands,
        "verifier_shell_call_count": len(verifier_calls),
        "receipt_count": len(receipts),
        "receipt_summaries": [{"capture_id": item.get("capture_id"),
                               "raw_bytes": item.get("raw_bytes"),
                               "delivered_bytes": item.get("delivered_bytes"),
                               "child_exit": item.get("child_exit")} for item in receipts],
        "exact_recall_matches_final_native": exact_recall,
        "capture_in_completed_tool": capture_in_tool, "capture_in_final": capture_in_final,
        "owned_rule_cleanup_ok": cleanup_ok,
        "valid": not reasons, "invalid_reasons": reasons,
        "provider_bill": None, "provider_bill_provenance": "not exposed by Cursor CLI stream",
    }
    (destination / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=["native_rule", "jevto_rule"], required=True)
    parser.add_argument("--cursor-cli", type=Path, required=True)
    parser.add_argument("--jevto", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    destination = args.output or ROOT / ".bench-runs" / datetime.now(timezone.utc).strftime(
        f"cursor-rule-coding-{args.arm}-%Y%m%dT%H%M%SZ")
    destination.mkdir(parents=True, exist_ok=False)
    result = run_arm(args.arm, args.cursor_cli.resolve(), args.jevto.resolve(),
                     args.timeout_seconds, destination)
    print(json.dumps({key: result[key] for key in
                      ("arm", "reported_model", "cli_exit", "timed_out", "elapsed_seconds",
                       "source_sha256", "marker_matches_final_source", "only_source_changed",
                       "verifier_shell_call_count", "receipt_count", "valid", "invalid_reasons",
                       "transport")}, indent=2))
    print(destination)
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
