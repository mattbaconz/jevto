"""Bounded, exact-model Cursor check for one agent-routed JevTO CLI command.

This diagnoses shell delivery. It is not a paired savings benchmark.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

import pilot


MARKER = "JEVTO_CURSOR_ROUTE_DONE"


def direct_launcher(launcher: Path, *arguments: str) -> list[str]:
    if launcher.suffix.lower() == ".ps1":
        node = launcher.with_name("node.exe")
        index = launcher.with_name("index.js")
        if not node.is_file() or not index.is_file():
            raise RuntimeError("staged Cursor Node launcher is incomplete")
        return [str(node), str(index), *arguments]
    return pilot.launcher_command(launcher, *arguments)


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        else:
            process.kill()
        process.wait(timeout=10)


def run_probe(launcher: Path, jevto: Path, timeout: int, destination: Path) -> dict:
    requirements = pilot.preflight(jevto, launcher)
    workspace = destination / "workspace"
    workspace.mkdir(parents=True)
    script = workspace / "verify_inventory.py"
    script.write_text(
        "import sys\nsys.stdout.write(''.join('test suite::case_%d ... ok\\n' % i for i in range(80)))\n",
        encoding="utf-8",
    )
    names = pilot.configured_mcp_names()
    pilot.disable_project_mcps(launcher, workspace, names)
    store = destination / "store"
    session = "cursor-explicit-" + destination.name
    shell_command = f"{jevto} run --session {session} -- python verify_inventory.py"
    prompt = (
        "This is a one-command local tool check. Run this exact command once in the current "
        f"disposable workspace: `{shell_command}`. Do not inspect help, edit files, or use "
        "other tools. Report the child exit, the total number of explicit ok test results "
        "(including any deferred results), and the JevTO capture ID shown in the result. "
        f"End with `child_exit=<n> tests=<n> capture_id=<id> {MARKER}`."
    )
    environment = os.environ.copy()
    environment.update({
        "JEVTO_STORE_DIR": str(store),
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_COLOR": "1",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
    })
    command = direct_launcher(
        launcher, "-p", "--force", "--trust", "--sandbox", "disabled" if os.name == "nt" else "enabled",
        "--output-format", "stream-json", "--model", pilot.MODEL,
        "--workspace", str(workspace), prompt,
    )
    manifest = {
        "kind": "cursor_explicit_route_probe",
        "preflight": requirements,
        "workspace": str(workspace),
        "session": session,
        "script_sha256": pilot.sha_file(script),
        "command": shell_command,
        "prompt": prompt,
        "project_mcps_disabled": names,
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
            stop_process(process)
            cli_exit = None
    stream_text = stream_path.read_text(encoding="utf-8", errors="replace")
    observations = pilot.stream_observations(stream_text)
    allowed_display = {pilot.MODEL, "Grok 4.7 Extra High", "Grok 4.7  Extra High", "Grok 4.7 256K Extra High"}
    if observations["reported_model"] is not None and observations["reported_model"] not in allowed_display:
        raise RuntimeError(f"Cursor substituted model {observations['reported_model']}; invalid run")
    events = []
    for line in stream_text.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    terminal_results = [event for event in events if event.get("type") == "result"]
    final_text = str(terminal_results[-1].get("result", "")) if terminal_results else ""
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in (store / "receipts").glob("*.json")]
    wrapper_executed = len(receipts) == 1 and receipts[0].get("child_exit") == 0
    reasons = []
    if observations["reported_model"] is None:
        reasons.append("model_identity_missing")
    if timed_out or cli_exit != 0:
        reasons.append("turn_not_completed")
    if observations["result_event_count"] != 1 or observations["result_subtype"] != "success":
        reasons.append("terminal_result_missing_or_failed")
    if not wrapper_executed:
        reasons.append("jevto_receipt_missing_or_ambiguous")
    if MARKER not in final_text:
        reasons.append("final_marker_missing")
    if not re.search(r"\bchild_exit\s*=\s*0\b", final_text):
        reasons.append("child_exit_report_missing_or_wrong")
    if not re.search(r"\btests\s*=\s*80\b", final_text):
        reasons.append("test_count_report_missing_or_wrong")
    if receipts and receipts[0].get("capture_id") not in final_text:
        reasons.append("capture_id_report_missing_or_wrong")
    result = {
        "model_requested": pilot.MODEL,
        "reported_model": observations["reported_model"],
        "cli_exit": cli_exit,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "transport": observations,
        "receipt_count": len(receipts),
        "wrapper_executed": wrapper_executed,
        "receipt_summaries": [{
            "raw_bytes": receipt.get("raw_bytes"),
            "delivered_bytes": receipt.get("delivered_bytes"),
            "coverage": receipt.get("coverage"),
            "child_exit": receipt.get("child_exit"),
            "capture_id": receipt.get("capture_id"),
        } for receipt in receipts],
        "route_observed": not reasons,
        "agent_report_accurate": not any(reason.endswith("_report_missing_or_wrong") for reason in reasons),
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
    parser.add_argument("--timeout-seconds", type=int, default=150)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    destination = args.output or pilot.ROOT / ".bench-runs" / datetime.now(timezone.utc).strftime("cursor-explicit-%Y%m%dT%H%M%SZ")
    destination.mkdir(parents=True, exist_ok=False)
    result = run_probe(args.cursor_cli.resolve(), args.jevto.resolve(), args.timeout_seconds, destination)
    print(json.dumps({key: result[key] for key in ["reported_model", "cli_exit", "timed_out", "elapsed_seconds", "receipt_count", "wrapper_executed", "route_observed", "invalid_reasons"]}, indent=2))
    print(destination)
    return 0 if result["route_observed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
