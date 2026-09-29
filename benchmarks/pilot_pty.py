"""Windows ConPTY fallback for Cursor CLI headless sessions that emit no events.

Requires pywinpty==3.0.5. Runs the same pinned model and fixture pairs as pilot.py.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
from datetime import datetime, timezone

import pilot


MODEL_LABEL = re.compile(r"Grok 4\.7(?:\s+256K)?\s+Extra High(?!\s+Fast)")
IDLE_FOOTER = re.compile(r"Grok 4\.7(?:\s+256K)?\s+Extra High[^\r\n]{0,30}\d+(?:\.\d+)?%")


def launcher_command(launcher: Path, *args: str) -> list[str]:
    if launcher.suffix.lower() == ".ps1":
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launcher), *args]
    return [str(launcher), *args]


def check_cursor(launcher: Path, jevto: Path) -> dict:
    if not launcher.is_file() or not jevto.is_file():
        raise RuntimeError("Cursor launcher or JevTO binary is missing")
    version = pilot.run(launcher_command(launcher, "--version"), cwd=pilot.ROOT)
    models = pilot.run(launcher_command(launcher, "models"), cwd=pilot.ROOT)
    if version.returncode != 0 or models.returncode != 0:
        raise RuntimeError("Cursor CLI preflight failed")
    if not any(line.split(" - ")[0] == pilot.MODEL for line in models.stdout.splitlines()):
        raise RuntimeError("exact Grok 4.7 xhigh is unavailable; no substitution is allowed")
    doctor = pilot.run([str(jevto), "doctor", "--json"], cwd=pilot.ROOT)
    if doctor.returncode != 0:
        raise RuntimeError("JevTO preflight failed")
    return {"cursor_cli_version": version.stdout.strip(), "cursor_launcher_sha256": pilot.sha_file(launcher),
            "model_requested": pilot.MODEL, "model_listing": "exact grok-4.7-xhigh present",
            "jevto_sha256": pilot.sha_file(jevto), "jevto_version": json.loads(doctor.stdout)["version"],
            "transport": "Windows ConPTY; CLI interactive mode", "sandbox_mode": "disabled (unavailable on Windows)"}


def configured_mcp_names() -> list[str]:
    path = Path.home() / ".cursor" / "mcp.json"
    if not path.is_file():
        return []
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    servers = config.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise RuntimeError("Cursor MCP configuration is malformed")
    return list(servers)


def disable_project_mcps(launcher: Path, checkout: Path, names: list[str]) -> None:
    for name in names:
        result = pilot.run(launcher_command(launcher, "mcp", "disable", name), cwd=checkout)
        if result.returncode != 0:
            raise RuntimeError(f"could not disable {name} MCP in disposable checkout")


def execute_terminal(launcher: Path, checkout: Path, prompt: str, env: dict[str, str], trace_path: Path,
                     timeout: int, finish_marker: str | None = None) -> dict:
    from winpty import PtyProcess

    command = launcher_command(launcher, "--model", pilot.MODEL, "--force", "--sandbox", "disabled")
    process = PtyProcess.spawn(command, cwd=str(checkout), env=env, dimensions=(30, 110))
    events: queue.Queue[str | None] = queue.Queue()

    def drain() -> None:
        with trace_path.open("w", encoding="utf-8") as trace:
            while True:
                try:
                    chunk = process.read(4096)
                except EOFError:
                    events.put(None)
                    return
                trace.write(chunk)
                trace.flush()
                events.put(chunk)

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    started = time.monotonic()
    recent = ""
    label = None
    ready = False
    working = False
    answer_observed = False
    last_output = started
    last_enter = started
    submitted_at = None
    invalid_reason = None
    trust_accepted = False
    completion_signal = None
    try:
        while time.monotonic() - started < min(timeout, 90):
            try:
                chunk = events.get(timeout=1)
            except queue.Empty:
                continue
            if chunk is None:
                invalid_reason = "cli_exited_before_prompt"
                break
            recent = (recent + chunk)[-20000:]
            if not trust_accepted and "Trust this workspace" in recent:
                process.write("a")
                trust_accepted = True
                recent = ""
                continue
            match = MODEL_LABEL.search(recent)
            if match and "Plan, search, build anything" in recent:
                label = match.group(0)
                ready = True
                break
        if not ready and invalid_reason is None:
            invalid_reason = "interactive_startup_timeout"
        if ready:
            process.write(prompt)
            time.sleep(0.2)
            process.write("\r")
            last_enter = time.monotonic()
            submitted_at = last_enter
            recent = ""
            while time.monotonic() - started < timeout:
                try:
                    chunk = events.get(timeout=1)
                except queue.Empty:
                    chunk = ""
                if chunk is None:
                    break
                if chunk:
                    last_output = time.monotonic()
                    recent = (recent + chunk)[-30000:]
                    if "Working" in chunk or "Thinking" in chunk:
                        working = True
                    if working and finish_marker and finish_marker in recent:
                        answer_observed = True
                        completion_signal = "final_response_marker"
                        break
                if not working and time.monotonic() - last_enter > 10:
                    process.write("\r")
                    last_enter = time.monotonic()
                # The model/context footer is also shown while tools are running. A
                # quiet terminal after the turn is the only safe PTY completion signal.
                if working and IDLE_FOOTER.search(recent) and time.monotonic() - last_output > 5:
                    answer_observed = True
                    completion_signal = "terminal_quiet_after_model_footer"
                    break
            if not working and invalid_reason is None:
                invalid_reason = "prompt_not_submitted"
            elif not answer_observed and invalid_reason is None:
                invalid_reason = "no_completed_response_before_timeout"
    finally:
        process.terminate(force=True)
        thread.join(timeout=5)
    return {"model_display_observed": label, "prompt_submitted": working,
            "response_observed": answer_observed, "invalid_reason": invalid_reason,
            "completion_signal": completion_signal,
            "disposable_workspace_trust_accepted": trust_accepted,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "submission_seconds": round(time.monotonic() - submitted_at, 3) if submitted_at else None}


def one_run(task: str, arm: str, destination: Path, launcher: Path, jevto: Path, timeout: int,
            mcp_names: list[str]) -> dict:
    checkout = destination / "checkout"
    initial_hash = pilot.init_checkout(pilot.HERE / "fixtures" / task, checkout)
    disable_project_mcps(launcher, checkout, mcp_names)
    store = destination / "jevto-store"
    env = os.environ.copy()
    env["JEVTO_STORE_DIR"] = str(store)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    terminal = execute_terminal(launcher, checkout, pilot.prompt(task, arm, jevto), env,
                                destination / "cursor-terminal.ansi.txt", timeout)
    verification = pilot.verify_candidate(task, checkout)
    (destination / "candidate.diff").write_text(verification.pop("tracked_diff"), encoding="utf-8")
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in (store / "receipts").glob("*.json")]
    result = {"task": task, "arm": arm, "model_requested": pilot.MODEL,
              "initial_tree_sha256": initial_hash, "benchmark_eligible": terminal["invalid_reason"] is None,
              "project_mcp_servers_disabled": mcp_names,
              "terminal": terminal, "verification": verification,
              "jevto_receipts": receipts, "jevto_receipt_count": len(receipts),
              "provider_usage": None, "provider_bill": None,
              "provider_usage_provenance": "Cursor interactive CLI did not expose session usage in the captured terminal trace",
              "tool_call_count": None, "retry_count": None,
              "note": "Pilot only; no whole-session interception or savings claim"}
    (destination / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cursor-cli", type=Path, required=True)
    parser.add_argument("--winpty-path", type=Path, help="folder containing an isolated pywinpty installation")
    parser.add_argument("--max-runs", type=int, choices=(2, 4), default=4)
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("ConPTY transport is Windows-only; use pilot.py on other platforms")
    if args.winpty_path:
        sys.path.insert(0, str(args.winpty_path.resolve()))
    import winpty  # noqa: F401

    jevto = (pilot.ROOT / "target" / "debug" / "jevto.exe").resolve()
    requirements = check_cursor(args.cursor_cli.resolve(), jevto)
    mcp_names = configured_mcp_names()
    fixture_checks = [pilot.check_fixture(task) for task in pilot.TASKS]
    destination = args.output or pilot.ROOT / ".bench-runs" / datetime.now(timezone.utc).strftime("pilot-pty-%Y%m%dT%H%M%SZ")
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "kind": "bounded_pilot", "preflight": requirements,
                "fixtures": fixture_checks, "timeout_seconds": args.timeout_seconds,
                "planned_order": [(task, arm) for task, data in pilot.TASKS.items() for arm in data["order"]],
                "executed_order": [], "project_mcp_servers_disabled": mcp_names,
                "exclusions": ["adaptive Jev unavailable", "RTK/Headroom/Ponytail unavailable"],
                "provider_usage_source": "Cursor interactive terminal; absent fields are unknown"}
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for task, arm in manifest["planned_order"][:args.max_runs]:
        run_dir = destination / f"{task}-{arm}"
        run_dir.mkdir()
        result = one_run(task, arm, run_dir, args.cursor_cli.resolve(), jevto, args.timeout_seconds, mcp_names)
        manifest["executed_order"].append({"task": task, "arm": arm,
                                           "benchmark_eligible": result["benchmark_eligible"],
                                           "invalid_reason": result["terminal"]["invalid_reason"],
                                           "verified": result["verification"]["verified"] if result["benchmark_eligible"] else None,
                                           "elapsed_seconds": result["terminal"]["elapsed_seconds"]})
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"{task} {arm}: eligible={result['benchmark_eligible']} verified={result['verification']['verified']} elapsed={result['terminal']['elapsed_seconds']}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
