"""Paired Cursor xhigh pilot with harness-delivered native or JevTO evidence.

This is a payload experiment, not a host adapter test. The harness executes the
same initial verifier, then gives its original or locally selected output to
Cursor. Cursor edits only; the harness performs visible and holdout checks.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

import pilot
import pilot_pty


FINISH_MARKER = "JEVTO_DONE_7429"
SOURCES = {"quiet_warning": "summarize.py", "already_lean": "label.py"}
AGENT_GOALS = {
    "quiet_warning": "Fix summarize.total so it sums value= events and ignores other log lines, including early warnings.",
    "already_lean": "Fix label.label so it strips surrounding whitespace and uppercases names after unit=.",
}


def initial_evidence(task: str, arm: str, checkout: Path, jevto: Path, store: Path) -> dict:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["JEVTO_STORE_DIR"] = str(store)
    original = pilot.run([sys.executable, "verify.py"], cwd=checkout, env=env)
    if original.returncode == 0:
        raise RuntimeError("fixture passed before agent work")
    if arm == "native":
        shown = original
        view = "Original verifier output"
    else:
        shown = pilot.run([str(jevto), "run", "--", sys.executable, "verify.py"], cwd=checkout, env=env)
        if shown.returncode != original.returncode:
            raise RuntimeError("JevTO did not preserve verifier exit code")
        view = "JevTO deterministic selected view of verifier output"
    return {"view": view, "original_exit": original.returncode, "shown_exit": shown.returncode,
            "original_stdout": original.stdout, "original_stderr": original.stderr,
            "shown_stdout": shown.stdout, "shown_stderr": shown.stderr,
            "original_payload_bytes": len((original.stdout + original.stderr).encode("utf-8")),
            "shown_payload_bytes": len((shown.stdout + shown.stderr).encode("utf-8"))}


def agent_prompt(task: str, checkout: Path, evidence: dict) -> str:
    source_name = SOURCES[task]
    source = (checkout / source_name).read_text(encoding="utf-8")
    payload = {"goal": AGENT_GOALS[task], "source_file": source_name,
               "source_text": source, "evidence_view": evidence["view"],
               "verifier_exit": evidence["shown_exit"],
               "verifier_stdout": evidence["shown_stdout"],
               "verifier_stderr": evidence["shown_stderr"]}
    return ("Edit the named source file in this disposable checkout to meet the goal. "
            "Use file tools only; do not run shell commands, install packages, or use the network. "
            "The harness will run the verifier and hidden checks after your edit. "
            "Finish with a line made by joining JEVTO, DONE, and 7429 with underscores. "
            "Task data (JSON): " + json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


def one_run(task: str, arm: str, destination: Path, launcher: Path, jevto: Path,
            timeout: int, mcp_names: list[str]) -> dict:
    checkout = destination / "checkout"
    initial_hash = pilot.init_checkout(pilot.HERE / "fixtures" / task, checkout)
    pilot_pty.disable_project_mcps(launcher, checkout, mcp_names)
    store = destination / "jevto-store"
    evidence = initial_evidence(task, arm, checkout, jevto, store)
    (destination / "initial-evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    env = os.environ.copy()
    env["JEVTO_STORE_DIR"] = str(store)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    terminal = pilot_pty.execute_terminal(launcher, checkout, agent_prompt(task, checkout, evidence),
                                          env, destination / "cursor-terminal.ansi.txt", timeout,
                                          finish_marker=FINISH_MARKER)
    verification = pilot.verify_candidate(task, checkout)
    (destination / "candidate.diff").write_text(verification.pop("tracked_diff"), encoding="utf-8")
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in (store / "receipts").glob("*.json")]
    eligible = terminal["invalid_reason"] is None and terminal["completion_signal"] == "final_response_marker"
    result = {"task": task, "arm": arm, "model_requested": pilot.MODEL,
              "initial_tree_sha256": initial_hash, "benchmark_eligible": eligible,
              "transport": "Cursor interactive CLI through Windows ConPTY",
              "intervention": "Harness supplies native or JevTO verifier output in prompt; Cursor file tools edit",
              "host_interception_coverage": 0, "agent_controlled_jevto_calls": 0,
              "project_mcp_servers_disabled": mcp_names,
              "terminal": terminal, "verification": verification,
              "original_payload_bytes": evidence["original_payload_bytes"],
              "shown_payload_bytes": evidence["shown_payload_bytes"],
              "jevto_receipts": receipts, "jevto_receipt_count": len(receipts),
              "provider_usage": None, "provider_bill": None,
              "provider_usage_provenance": "Cursor interactive CLI did not expose session usage in captured terminal",
              "tool_call_count": None, "retry_count": None,
              "note": "Exploratory paired payload pilot only; no whole-session interception or savings claim"}
    (destination / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cursor-cli", type=Path, required=True)
    parser.add_argument("--winpty-path", type=Path, required=True)
    parser.add_argument("--start-index", type=int, choices=(0, 2), default=0)
    parser.add_argument("--max-runs", type=int, choices=(2, 4), default=4)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("ConPTY transport is Windows-only")
    sys.path.insert(0, str(args.winpty_path.resolve()))
    import winpty  # noqa: F401

    jevto = (pilot.ROOT / "target" / "debug" / "jevto.exe").resolve()
    preflight = pilot_pty.check_cursor(args.cursor_cli.resolve(), jevto)
    mcp_names = pilot_pty.configured_mcp_names()
    fixtures = [pilot.check_fixture(task) for task in pilot.TASKS]
    order = [(task, arm) for task, data in pilot.TASKS.items() for arm in data["order"]]
    if args.start_index + args.max_runs > len(order):
        raise RuntimeError("selected run range exceeds planned order")
    destination = args.output or pilot.ROOT / ".bench-runs" / datetime.now(timezone.utc).strftime("pilot-injected-%Y%m%dT%H%M%SZ")
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "kind": "bounded_payload_pilot", "preflight": preflight,
                "fixtures": fixtures, "planned_order": order, "executed_order": [],
                "timeout_seconds": args.timeout_seconds, "project_mcp_servers_disabled": mcp_names,
                "intervention": "Harness-mediated initial verifier output; no host interception",
                "exclusions": ["adaptive Jev unavailable", "RTK/Headroom/Ponytail unavailable",
                               "provider usage and bill unavailable"],
                "model_rejection_rule": "Abort when exact grok-4.7-xhigh is absent or Extra High label is not observed"}
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for task, arm in order[args.start_index:args.start_index + args.max_runs]:
        run_dir = destination / f"{task}-{arm}"
        run_dir.mkdir()
        result = one_run(task, arm, run_dir, args.cursor_cli.resolve(), jevto,
                         args.timeout_seconds, mcp_names)
        terminal = result["terminal"]
        manifest["executed_order"].append({"task": task, "arm": arm,
                                           "benchmark_eligible": result["benchmark_eligible"],
                                           "invalid_reason": terminal["invalid_reason"],
                                           "verified": result["verification"]["verified"] if result["benchmark_eligible"] else None,
                                           "elapsed_seconds": terminal["submission_seconds"],
                                           "shown_payload_bytes": result["shown_payload_bytes"]})
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"{task} {arm}: eligible={result['benchmark_eligible']} "
              f"verified={result['verification']['verified']} "
              f"elapsed={terminal['submission_seconds']}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
