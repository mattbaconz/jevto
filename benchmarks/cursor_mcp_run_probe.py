"""Bounded Cursor model-driven probe of the opt-in exact-command MCP runner.

This installs an owned runner only in a disposable project, using an external
command policy. Normal user MCP registration remains non-executing.
"""

from __future__ import annotations

import atexit
import argparse
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
import pilot


MARKER = "JEVTO_CURSOR_MCP_RUN_DONE"
SERVER = "jevto_exec"


def preflight_direct(launcher: Path, jevto: Path) -> dict:
    if not launcher.is_file() or not jevto.is_file():
        raise RuntimeError("Cursor launcher or JevTO executable is missing")
    models = subprocess.run(cursor_explicit_route.direct_launcher(launcher, "models"),
                            cwd=pilot.ROOT, capture_output=True, text=True, timeout=30)
    if models.returncode != 0 or not any(line.split(" - ")[0] == pilot.MODEL for line in models.stdout.splitlines()):
        raise RuntimeError("exact grok-4.7-xhigh model unavailable; Fast and substitutes are forbidden")
    version = subprocess.run(cursor_explicit_route.direct_launcher(launcher, "--version"),
                             cwd=pilot.ROOT, capture_output=True, text=True, timeout=20)
    doctor = subprocess.run([str(jevto), "doctor", "--json"],
                            cwd=pilot.ROOT, capture_output=True, text=True, timeout=20)
    if version.returncode != 0 or doctor.returncode != 0:
        raise RuntimeError("Cursor version or JevTO doctor failed")
    return {"cursor_cli_version": version.stdout.strip(),
            "cursor_launcher_sha256": pilot.sha_file(launcher),
            "model_requested": pilot.MODEL,
            "model_listing": "exact grok-4.7-xhigh present",
            "jevto_sha256": pilot.sha_file(jevto),
            "jevto_version": json.loads(doctor.stdout)["version"],
            "sandbox_mode": "disabled (Cursor sandbox unavailable on Windows)" if os.name == "nt" else "enabled",
            "preflight_launcher": "direct staged Node entry for Windows PowerShell launcher" if launcher.suffix.lower() == ".ps1" else "launcher"}


def run_probe(launcher: Path, jevto: Path, timeout: int, destination: Path,
              scenario: str, tracked_workspace: bool = False,
              commit_project_mcp: bool = False) -> dict:
    preflight = preflight_direct(launcher, jevto)
    workspace = destination / "workspace"
    workspace.mkdir(parents=True)
    script = workspace / "verify_inventory.py"
    if scenario == "success":
        fixture = (
            "import sys\n"
            "sys.stdout.write(''.join('test suite::case_%d ... ok\\n' % i for i in range(80)))\n"
            "sys.stdout.write('test result: ok. 80 passed; 0 failed\\n')\n"
        )
        expected_exit, expected_ok = 0, 80
    else:
        fixture = (
            "import sys\n"
            "sys.stdout.write(''.join('test suite::case_%d ... ok\\n' % i for i in range(79)))\n"
            "sys.stdout.write('test suite::case_79 ... FAILED\\n')\n"
            "sys.stdout.write('test result: FAILED. 79 passed; 1 failed\\n')\n"
            "sys.stderr.write('assertion failed: left == right in suite::case_79\\n')\n"
            "sys.exit(101)\n"
        )
        expected_exit, expected_ok = 101, 79
    script.write_text(fixture, encoding="utf-8")
    if tracked_workspace:
        for command in (["git", "init", "-q"], ["git", "add", "verify_inventory.py"],
                        ["git", "-c", "user.name=JevTO Probe", "-c", "user.email=probe@invalid.local",
                         "commit", "-q", "-m", "fixture baseline"]):
            initialized = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=20)
            if initialized.returncode != 0:
                raise RuntimeError("tracked fixture setup failed: " + initialized.stderr)
    native = subprocess.run([sys.executable, str(script)], cwd=workspace, capture_output=True, timeout=10)
    if native.returncode != expected_exit or sum(line.endswith(b" ... ok") for line in native.stdout.splitlines()) != expected_ok:
        raise RuntimeError("fixture self-check failed")
    if scenario == "failure" and (b"case_79 ... FAILED" not in native.stdout or b"assertion failed" not in native.stderr):
        raise RuntimeError("failure fixture self-check failed")
    configured = pilot.configured_mcp_names()
    pilot.disable_project_mcps(launcher, workspace, configured)
    policy = destination / "policy.json"
    policy.write_text(json.dumps({
        "schema_version": 1,
        "workspace": str(workspace),
        "workspace_id": destination.name,
        "commands": [{"id": "inventory", "program": sys.executable, "argv": ["verify_inventory.py"]}],
    }, indent=2), encoding="utf-8")
    store = destination / "store"
    project_mcp = workspace / ".cursor" / "mcp.json"
    setup = subprocess.run(
        [str(jevto), "--store-dir", str(store), "init-cursor-runner",
         "--workspace", str(workspace), "--policy", str(policy), "--apply"],
        cwd=destination, capture_output=True, text=True, timeout=20,
    )
    (destination / "runner-setup.txt").write_text(setup.stdout + setup.stderr, encoding="utf-8")
    if setup.returncode != 0 or not project_mcp.is_file():
        raise RuntimeError("disposable Cursor runner installer failed")
    installed_config = project_mcp.read_bytes()
    (destination / "project-mcp-installed.json").write_bytes(installed_config)
    def disable_runner() -> bool:
        try:
            host_cleanup = subprocess.run(
                cursor_explicit_route.direct_launcher(launcher, "mcp", "disable", SERVER),
                cwd=workspace, capture_output=True, text=True, timeout=20,
            )
            local_cleanup = subprocess.run(
                [str(jevto), "--store-dir", str(store), "disable-cursor-runner",
                 "--workspace", str(workspace), "--apply"],
                cwd=destination, capture_output=True, text=True, timeout=20,
            )
            (destination / "mcp-cleanup.txt").write_text(
                "Cursor host:\n" + host_cleanup.stdout + host_cleanup.stderr
                + "\nJevTO installer:\n" + local_cleanup.stdout + local_cleanup.stderr,
                encoding="utf-8",
            )
            return (host_cleanup.returncode == 0 and local_cleanup.returncode == 0
                    and SERVER not in json.loads(project_mcp.read_text(encoding="utf-8")).get("mcpServers", {}))
        except Exception as error:
            (destination / "mcp-cleanup.txt").write_text(str(error), encoding="utf-8")
            return False

    atexit.register(disable_runner)
    if commit_project_mcp:
        if not tracked_workspace:
            raise RuntimeError("committing project MCP requires a tracked workspace")
        for command in (["git", "add", ".cursor/mcp.json"],
                        ["git", "-c", "user.name=JevTO Probe", "-c", "user.email=probe@invalid.local",
                         "commit", "-q", "-m", "reviewed runner config"]):
            committed = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=20)
            if committed.returncode != 0:
                raise RuntimeError("disposable project MCP commit failed: " + committed.stderr)
    approval = subprocess.run(
        cursor_explicit_route.direct_launcher(launcher, "mcp", "enable", SERVER),
        cwd=workspace, capture_output=True, text=True, timeout=20,
    )
    (destination / "mcp-approval.txt").write_text(approval.stdout + approval.stderr, encoding="utf-8")
    if approval.returncode != 0:
        raise RuntimeError("disposable Cursor MCP runner approval failed")
    listing = subprocess.run(
        cursor_explicit_route.direct_launcher(launcher, "mcp", "list"),
        cwd=workspace, capture_output=True, text=True, timeout=35,
    )
    (destination / "mcp-list.txt").write_text(listing.stdout + listing.stderr, encoding="utf-8")
    if listing.returncode != 0 or not re.search(rf"(?m)^{SERVER}: ready$", listing.stdout):
        raise RuntimeError("disposable Cursor MCP runner did not become ready")
    if scenario == "success":
        prompt = (
            f"Call the {SERVER} server's jevto_run tool once with command_id `inventory`. "
            "Wait for the result. Report the child exit, the total explicit ok-test count "
            "including deferred tests, and the capture ID. End with "
            f"`child_exit=<n> tests=<n> capture_id=<id> {MARKER}`."
        )
    else:
        prompt = (
            f"Call the {SERVER} server's jevto_run tool once with command_id `inventory`. "
            "Wait for the result. Report the failed child exit, the failed test name, "
            "the assertion, and the capture ID. End with "
            f"`child_exit=<n> failed=<name> capture_id=<id> {MARKER}`."
        )
    environment = os.environ.copy()
    environment.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_COLOR": "1",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
    })
    command = cursor_explicit_route.direct_launcher(
        launcher, "-p", "--force", "--trust", "--sandbox",
        "disabled" if os.name == "nt" else "enabled", "--output-format", "stream-json",
        "--model", pilot.MODEL, "--workspace", str(workspace), prompt,
    )
    manifest = {
        "kind": "cursor_exact_command_mcp_probe",
        "scenario": scenario,
        "tracked_workspace": tracked_workspace,
        "project_mcp_committed": commit_project_mcp,
        "preflight": preflight,
        "workspace": str(workspace),
        "policy": str(policy),
        "project_mcp": str(project_mcp),
        "project_mcp_installed_sha256": hashlib.sha256(installed_config).hexdigest(),
        "runner_setup": "jevto init-cursor-runner --apply",
        "policy_sha256_pinned": hashlib.sha256(policy.read_bytes()).hexdigest(),
        "normal_user_mcp_config_not_edited_by_harness": True,
        "normal_user_mcp_servers_disabled_for_workspace": configured,
        "project_mcp_server_approved_for_workspace": SERVER,
        "fixture_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "fixture_stdout_sha256": hashlib.sha256(native.stdout).hexdigest(),
        "fixture_stdout_bytes": len(native.stdout),
        "fixture_stderr_sha256": hashlib.sha256(native.stderr).hexdigest(),
        "fixture_stderr_bytes": len(native.stderr),
        "prompt": prompt,
        "timeout_seconds": timeout,
        "not_a_paired_benchmark": True,
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
    allowed = {pilot.MODEL, "Grok 4.7 Extra High", "Grok 4.7  Extra High", "Grok 4.7 256K Extra High"}
    if observations["reported_model"] is not None and observations["reported_model"] not in allowed:
        raise RuntimeError(f"Cursor substituted model {observations['reported_model']}; invalid run")
    events = []
    for line in stream_text.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    results = [event for event in events if event.get("type") == "result"]
    final_text = str(results[-1].get("result", "")) if results else ""
    run_calls = [
        event["tool_call"]["mcpToolCall"]
        for event in events
        if event.get("type") == "tool_call"
        and event.get("subtype") == "completed"
        and isinstance(event.get("tool_call"), dict)
        and isinstance(event["tool_call"].get("mcpToolCall"), dict)
    ]
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in (store / "receipts").glob("*.json")]
    capture_id = receipts[0].get("capture_id") if len(receipts) == 1 else None
    reasons = []
    if observations["reported_model"] is None:
        reasons.append("model_identity_missing")
    if timed_out or cli_exit != 0:
        reasons.append("turn_not_completed")
    if observations["result_event_count"] != 1 or observations["result_subtype"] != "success":
        reasons.append("terminal_result_missing_or_failed")
    if len(receipts) != 1 or receipts[0].get("child_exit") != expected_exit:
        reasons.append("jevto_receipt_missing_or_ambiguous")
    if len(run_calls) != 1 or run_calls[0].get("args", {}).get("toolName") != "jevto_run" or run_calls[0].get("args", {}).get("args") != {"command_id": "inventory"}:
        reasons.append("expected_jevto_tool_call_missing_or_ambiguous")
    elif not isinstance(capture_id, str) or capture_id not in json.dumps(run_calls[0].get("result", {})):
        reasons.append("jevto_tool_result_missing_capture")
    if MARKER not in final_text:
        reasons.append("final_marker_missing")
    if not re.search(rf"\bchild_exit\s*=\s*{expected_exit}\b", final_text):
        reasons.append("child_exit_report_missing_or_wrong")
    if scenario == "success":
        if not re.search(r"\btests\s*=\s*80\b", final_text):
            reasons.append("test_count_report_missing_or_wrong")
    elif not re.search(r"\bfailed\s*=\s*(?:suite::)?case_79\b", final_text) or "assertion" not in final_text.lower():
        reasons.append("failure_report_missing_or_wrong")
    if not isinstance(capture_id, str) or capture_id not in final_text:
        reasons.append("capture_id_report_missing_or_wrong")
    policy_hash = hashlib.sha256(policy.read_bytes()).hexdigest()
    installed_entry = json.loads(installed_config)["mcpServers"][SERVER]
    if policy_hash != manifest["policy_sha256_pinned"] or installed_entry["args"][-1] != policy_hash:
        reasons.append("installed_policy_pin_changed")
    if hashlib.sha256(script.read_bytes()).hexdigest() != manifest["fixture_sha256"]:
        reasons.append("fixture_changed_during_model_turn")
    recall_exact = False
    if isinstance(capture_id, str):
        try:
            recall = subprocess.run(
                [str(jevto), "--store-dir", str(store), "recall", capture_id, "--full"],
                cwd=destination, capture_output=True, timeout=20,
            )
            recall_exact = (recall.returncode == 0
                            and recall.stdout == native.stdout and recall.stderr == native.stderr)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if not recall_exact:
        reasons.append("full_recall_did_not_match_native_streams")
    cleanup_ok = disable_runner()
    atexit.unregister(disable_runner)
    if not cleanup_ok:
        reasons.append("project_runner_cleanup_failed")
    result = {
        "model_requested": pilot.MODEL,
        "scenario": scenario,
        "reported_model": observations["reported_model"],
        "cli_exit": cli_exit,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "transport": observations,
        "receipt_count": len(receipts),
        "completed_mcp_run_calls": len(run_calls),
        "full_recall_matched_native_streams": recall_exact,
        "project_runner_disabled_after_probe": cleanup_ok,
        "receipt_summaries": [{
            "raw_bytes": receipt.get("raw_bytes"),
            "delivered_bytes": receipt.get("delivered_bytes"),
            "child_exit": receipt.get("child_exit"),
            "capture_id": receipt.get("capture_id"),
        } for receipt in receipts],
        "valid": not reasons,
        "invalid_reasons": reasons,
        "provider_bill": None,
        "provider_bill_provenance": "not exposed by Cursor CLI stream",
        "stream_path": str(stream_path),
    }
    (destination / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cursor-cli", type=Path, required=True)
    parser.add_argument("--jevto", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=210)
    parser.add_argument("--scenario", choices=["success", "failure"], default="success")
    parser.add_argument("--tracked-workspace", action="store_true")
    parser.add_argument("--commit-project-mcp", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    destination = args.output or pilot.ROOT / ".bench-runs" / datetime.now(timezone.utc).strftime("cursor-mcp-run-%Y%m%dT%H%M%SZ")
    destination.mkdir(parents=True, exist_ok=False)
    result = run_probe(args.cursor_cli.resolve(), args.jevto.resolve(), args.timeout_seconds,
                       destination, args.scenario, args.tracked_workspace,
                       args.commit_project_mcp)
    print(json.dumps({key: result[key] for key in ["reported_model", "cli_exit", "timed_out", "elapsed_seconds", "receipt_count", "valid", "invalid_reasons", "transport"]}, indent=2))
    print(destination)
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
