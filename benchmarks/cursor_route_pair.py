"""Run one arm of a bounded, exact-model Cursor command pair.

The same one-command task is given to fresh native and JevTO sessions. This is
a narrow transport and provider-usage pilot, not a whole-task savings claim.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

import cursor_explicit_route
import cursor_mcp_run_probe
import pilot


MARKER = "JEVTO_CURSOR_PAIR_DONE"
ALLOWED_DISPLAY = {
    pilot.MODEL,
    "Grok 4.7 Extra High",
    "Grok 4.7  Extra High",
    "Grok 4.7 256K Extra High",
}


def run_arm(arm: str, launcher: Path, jevto: Path, timeout: int, destination: Path,
            tracked_workspace: bool = False, installed_rule: bool = False,
            scenario: str = "success") -> dict:
    if arm == "rule" and not tracked_workspace:
        raise ValueError("the project-rule diagnostic requires --tracked-workspace")
    if installed_rule and arm != "rule":
        raise ValueError("--installed-rule requires --arm rule")
    preflight = cursor_mcp_run_probe.preflight_direct(launcher, jevto)
    workspace = destination / "workspace"
    workspace.mkdir(parents=True)
    script = workspace / "verify_inventory.py"
    if scenario == "success":
        source = (
            "import sys\n"
            "sys.stdout.write(''.join('test suite::case_%d ... ok\\n' % i for i in range(80)))\n"
            "sys.stdout.write('test result: ok. 80 passed; 0 failed\\n')\n"
        )
        expected_exit, expected_ok = 0, 80
    else:
        source = (
            "import sys\n"
            "sys.stdout.write(''.join('test suite::case_%d ... ok\\n' % i for i in range(79)))\n"
            "sys.stdout.write('test suite::case_79 ... FAILED\\n')\n"
            "sys.stdout.write('test result: FAILED. 79 passed; 1 failed\\n')\n"
            "sys.stderr.write('assertion failed: left == right in suite::case_79\\n')\n"
            "sys.exit(101)\n"
        )
        expected_exit, expected_ok = 101, 79
    script.write_text(source, encoding="utf-8")
    fixture = subprocess.run(
        ["python", str(script)], cwd=workspace, capture_output=True, timeout=10,
    )
    assert fixture.returncode == expected_exit and sum(
        line.endswith(b" ... ok") for line in fixture.stdout.splitlines()
    ) == expected_ok
    if scenario == "failure":
        assert b"case_79 ... FAILED" in fixture.stdout and b"assertion failed" in fixture.stderr
    initial_git_tree = None
    if tracked_workspace:
        for command in (["git", "init", "-q"], ["git", "add", "verify_inventory.py"],
                        ["git", "-c", "user.name=JevTO Probe", "-c", "user.email=jevto-probe@invalid",
                         "commit", "-qm", "Disposable fixture"]):
            subprocess.run(command, cwd=workspace, check=True, timeout=10)
        initial_git_tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=workspace,
                                          check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    names = pilot.configured_mcp_names()
    pilot.disable_project_mcps(launcher, workspace, names)
    store = destination / "store"
    session = "cursor-pair-" + destination.name
    command_text = "python verify_inventory.py"
    wrapped_command = f"{jevto} run --session {session} -- {command_text}"
    if arm == "jevto":
        command_text = wrapped_command
    rule_file = None
    if arm == "rule":
        if installed_rule:
            rule_file = workspace / ".cursor" / "rules" / "jevto-verifier.mdc"
            setup = [str(jevto), "--store-dir", str(store), "init-cursor-rule",
                     "--workspace", str(workspace), "--", "python", "verify_inventory.py"]
            preview = subprocess.run(setup, cwd=workspace, check=True, capture_output=True,
                                     text=True, timeout=15)
            assert "Preview only" in preview.stdout and not rule_file.exists()
            (destination / "rule-preview.txt").write_text(preview.stdout, encoding="utf-8")
            subprocess.run(setup[:6] + ["--apply"] + setup[6:], cwd=workspace,
                           check=True, capture_output=True, timeout=15)
        else:
            rule_file = workspace / ".cursor" / "rules" / "jevto.mdc"
            rule_file.parent.mkdir(parents=True, exist_ok=True)
            rule_command = f"{jevto} --store-dir {store} run --session {session} -- python verify_inventory.py"
            rule_file.write_text(
                "---\n"
                "description: Use JevTO for this project's verifier\n"
                "alwaysApply: true\n"
                "---\n"
                "When verifying this project, run this exact shell command once: "
                f"`{rule_command}`. It runs `python verify_inventory.py` and preserves "
                "the child exit. Do not run the plain verifier separately. Report the "
                "child exit, full passed-test count (including deferred results), and "
                "capture ID from the result.\n",
                encoding="utf-8",
            )
        (destination / "rule-installed.mdc").write_bytes(rule_file.read_bytes())
        for git_command in (["git", "add", rule_file.relative_to(workspace).as_posix()],
                            ["git", "-c", "user.name=JevTO Probe", "-c", "user.email=jevto-probe@invalid",
                             "commit", "-qm", "Disposable JevTO rule"]):
            subprocess.run(git_command, cwd=workspace, check=True, timeout=10)
        initial_git_tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=workspace,
                                          check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    if arm == "hook":
        hook_dir = workspace / ".cursor"
        hook_dir.mkdir(exist_ok=True)
        hook_script = hook_dir / "route.py"
        hook_script.write_text(
            "import json, os, pathlib, sys\n"
            "event = json.load(sys.stdin)\n"
            "inputs = event.get('tool_input') or {}\n"
            "original = inputs.get('command') if isinstance(inputs, dict) else None\n"
            "rewrite = event.get('tool_name') == 'Shell' and original == 'python verify_inventory.py'\n"
            "record = {'tool_name': event.get('tool_name'), 'original_command': original, 'rewritten': rewrite}\n"
            "pathlib.Path(__file__).with_name('hook-observation.json').write_text(json.dumps(record), encoding='utf-8')\n"
            "output = {'permission': 'allow'}\n"
            "if rewrite:\n"
            "    output['updated_input'] = dict(inputs, command=os.environ['JEVTO_CURSOR_HOOK_COMMAND'])\n"
            "print(json.dumps(output))\n",
            encoding="utf-8",
        )
        (hook_dir / "hooks.json").write_text(json.dumps({
            "version": 1,
            "hooks": {"preToolUse": [{"command": "python .cursor/route.py", "matcher": "Shell"}]},
        }, indent=2), encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=workspace, check=True, timeout=10)
        subprocess.run(
            ["git", "add", "verify_inventory.py", ".cursor/hooks.json", ".cursor/route.py"],
            cwd=workspace, check=True, timeout=10,
        )
        subprocess.run(
            ["git", "-c", "user.name=JevTO Probe", "-c", "user.email=jevto-probe@invalid",
             "commit", "-qm", "Disposable fixture"],
            cwd=workspace, check=True, timeout=10,
        )
    if arm == "rule":
        prompt = (
            "Run the project's `verify_inventory.py` verifier once according to the project "
            "instructions. If it runs in the background, wait for its result. Do not edit "
            "files. Report the child exit code, total passed-test count, any failure assertion, "
            "and any capture ID. "
            f"End with `child_exit=<n> passed=<n> {MARKER}`."
        )
    else:
        prompt = (
            "Run this exact verification command once in the disposable workspace: "
            f"`{command_text}`. If it runs in the background, wait for its result. "
            "Do not edit files or inspect other paths. Report the child exit code, "
            "total passed-test count, and any failure assertion. End with "
            f"`child_exit=<n> passed=<n> {MARKER}`."
        )
    environment = os.environ.copy()
    environment.update({
        "JEVTO_STORE_DIR": str(store),
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_COLOR": "1",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "JEVTO_CURSOR_HOOK_COMMAND": wrapped_command,
    })
    command = cursor_explicit_route.direct_launcher(
        launcher, "-p", "--force", "--trust", "--sandbox",
        "disabled" if os.name == "nt" else "enabled", "--output-format", "stream-json",
        "--model", pilot.MODEL, "--workspace", str(workspace), prompt,
    )
    manifest = {
        "kind": "cursor_one_command_pair_arm",
        "arm": arm,
        "scenario": scenario,
        "model_requested": pilot.MODEL,
        "preflight": preflight,
        "workspace": str(workspace),
        "fixture_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "fixture_stdout_sha256": hashlib.sha256(fixture.stdout).hexdigest(),
        "fixture_stdout_bytes": len(fixture.stdout),
        "command": command_text,
        "prompt": prompt,
        "project_mcps_disabled": names,
        "timeout_seconds": timeout,
        "not_a_whole_task_benchmark": True,
        "project_pre_tool_hook": arm == "hook",
        "project_rule_sha256": hashlib.sha256(rule_file.read_bytes()).hexdigest() if rule_file else None,
        "installer_generated_rule": installed_rule,
        "tracked_git_workspace": tracked_workspace,
        "initial_git_tree": initial_git_tree,
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    stream_path = destination / "cursor-stream.jsonl"
    stderr_path = destination / "cursor-stderr.txt"
    started = time.monotonic()
    with stream_path.open("wb") as stream, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            command, cwd=workspace, env=environment, stdin=subprocess.DEVNULL,
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
    stream_text = stream_path.read_text(encoding="utf-8", errors="replace")
    observations = pilot.stream_observations(stream_text)
    if observations["reported_model"] is not None and observations["reported_model"] not in ALLOWED_DISPLAY:
        raise RuntimeError(f"Cursor substituted model {observations['reported_model']}; invalid run")
    events = []
    for line in stream_text.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    results = [event for event in events if event.get("type") == "result"]
    final_text = str(results[-1].get("result", "")) if results else ""
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in (store / "receipts").glob("*.json")]
    capture_id = receipts[0].get("capture_id") if len(receipts) == 1 else None
    completed_tools = [event for event in events if event.get("type") == "tool_call"
                       and event.get("subtype") == "completed"]
    capture_in_completed_tool = bool(capture_id and any(
        capture_id in json.dumps(event.get("tool_call", {})) for event in completed_tools))
    exact_recall = None
    if arm in {"jevto", "hook", "rule"} and isinstance(capture_id, str):
        recalled = subprocess.run([str(jevto), "--store-dir", str(store), "recall", capture_id, "--full"],
                                  cwd=workspace, capture_output=True, timeout=15)
        exact_recall = (recalled.returncode == 0 and recalled.stdout == fixture.stdout
                        and recalled.stderr == fixture.stderr)
    reasons = []
    if observations["reported_model"] is None:
        reasons.append("model_identity_missing")
    if timed_out or cli_exit != 0:
        reasons.append("turn_not_completed")
    if observations["result_event_count"] != 1 or observations["result_subtype"] != "success":
        reasons.append("terminal_result_missing_or_failed")
    if MARKER not in final_text:
        reasons.append("final_marker_missing")
    if not re.search(rf"\bchild_exit\s*=\s*{expected_exit}\b", final_text):
        reasons.append("child_exit_report_missing_or_wrong")
    if not re.search(rf"\bpassed\s*=\s*{expected_ok}\b", final_text):
        reasons.append("pass_count_report_missing_or_wrong")
    if scenario == "failure" and ("case_79" not in final_text or "assertion" not in final_text):
        reasons.append("failure_detail_missing")
    if arm in {"jevto", "hook", "rule"} and (len(receipts) != 1 or receipts[0].get("child_exit") != expected_exit):
        reasons.append("jevto_receipt_missing_or_ambiguous")
    if arm in {"jevto", "hook", "rule"} and (not capture_in_completed_tool or not exact_recall):
        reasons.append("capture_not_delivered_or_recall_mismatch")
    rule_wrapper_call_observed = any(
        jevto.name in json.dumps(event.get("tool_call", {}))
        and "verify_inventory.py" in json.dumps(event.get("tool_call", {}))
        for event in completed_tools
    )
    if arm == "rule" and (not rule_wrapper_call_observed or not isinstance(capture_id, str)
                          or capture_id not in final_text):
        reasons.append("project_rule_wrapper_not_followed_or_reported")
    if arm == "native" and receipts:
        reasons.append("unexpected_jevto_receipt")
    hook_observation_path = workspace / ".cursor" / "hook-observation.json"
    hook_observation = json.loads(hook_observation_path.read_text(encoding="utf-8")) if hook_observation_path.is_file() else None
    if arm == "hook" and not (hook_observation and hook_observation.get("rewritten")):
        reasons.append("cursor_pre_tool_hook_not_observed")
    result = {
        "arm": arm,
        "scenario": scenario,
        "model_requested": pilot.MODEL,
        "reported_model": observations["reported_model"],
        "cli_exit": cli_exit,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "transport": observations,
        "receipt_count": len(receipts),
        "capture_in_completed_tool": capture_in_completed_tool,
        "exact_recall_matches_native_fixture": exact_recall,
        "receipt_summaries": [{
            "raw_bytes": receipt.get("raw_bytes"),
            "delivered_bytes": receipt.get("delivered_bytes"),
            "child_exit": receipt.get("child_exit"),
            "capture_id": receipt.get("capture_id"),
        } for receipt in receipts],
        "hook_observation": hook_observation,
        "rule_wrapper_call_observed": rule_wrapper_call_observed if arm == "rule" else None,
        "valid": not reasons,
        "invalid_reasons": reasons,
        "provider_bill": None,
        "provider_bill_provenance": "not exposed by Cursor CLI stream",
        "stream_path": str(stream_path),
    }
    if installed_rule:
        disable = [str(jevto), "--store-dir", str(store), "disable-cursor-rule",
                   "--workspace", str(workspace), "--apply"]
        disabled = subprocess.run(disable, cwd=workspace, capture_output=True, text=True, timeout=15)
        result["rule_disable_exit"] = disabled.returncode
        result["rule_removed"] = not rule_file.exists()
        if disabled.returncode != 0 or rule_file.exists():
            result["valid"] = False
            result["invalid_reasons"].append("installed_rule_disable_failed")
        (destination / "rule-disable.txt").write_text(disabled.stdout + disabled.stderr,
                                                        encoding="utf-8")
    (destination / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=["native", "jevto", "hook", "rule"], required=True)
    parser.add_argument("--cursor-cli", type=Path, required=True)
    parser.add_argument("--jevto", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=210)
    parser.add_argument("--tracked-workspace", action="store_true")
    parser.add_argument("--installed-rule", action="store_true")
    parser.add_argument("--scenario", choices=["success", "failure"], default="success")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    destination = args.output or pilot.ROOT / ".bench-runs" / datetime.now(timezone.utc).strftime(f"cursor-route-pair-{args.arm}-%Y%m%dT%H%M%SZ")
    destination.mkdir(parents=True, exist_ok=False)
    result = run_arm(args.arm, args.cursor_cli.resolve(), args.jevto.resolve(), args.timeout_seconds,
                     destination, args.tracked_workspace, args.installed_rule, args.scenario)
    print(json.dumps({key: result[key] for key in ["arm", "reported_model", "cli_exit", "timed_out", "elapsed_seconds", "receipt_count", "valid", "invalid_reasons", "transport"]}, indent=2))
    print(destination)
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
