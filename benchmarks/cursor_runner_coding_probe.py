"""Exploratory Grok 4.7 xhigh edit-and-verify probe: native or JevTO route.

Run each arm in a fresh disposable checkout. Compare only completed, verified
arms with matching initial tree and host settings; this is not a main benchmark.
"""

from __future__ import annotations

import atexit
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import cursor_explicit_route
import cursor_mcp_run_probe
import pilot


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
MODEL_LABELS = {pilot.MODEL, "Grok 4.7 Extra High", "Grok 4.7  Extra High", "Grok 4.7 256K Extra High"}
MARKER = "JEVTO_CODING_PROBE_DONE"
VERIFY_WRAPPER = (
    "import hashlib, runpy\n"
    "from pathlib import Path\n"
    "runpy.run_path('verify.py', run_name='__main__')\n"
    "digest = hashlib.sha256(Path('summarize.py').read_bytes()).hexdigest()\n"
    "Path('.verified-source-sha256').write_text(digest, encoding='ascii')\n"
    "print('source_sha256=' + digest)\n"
)


def check_command(command: list[str], cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=timeout)


def prepare_fixture(destination: Path) -> Path:
    fixture = destination / "fixture"
    shutil.copytree(HERE / "fixtures" / "quiet_warning", fixture,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    verifier = fixture / "verify.py"
    original = verifier.read_text(encoding="utf-8")
    assert "range(24)" in original
    verifier.write_text(original.replace("range(24)", "range(80)"), encoding="utf-8")
    (fixture / "verify_once.py").write_text(VERIFY_WRAPPER, encoding="utf-8")
    (fixture / ".gitignore").write_text(".verified-source-sha256\n__pycache__/\n", encoding="utf-8")
    failed = check_command([sys.executable, "verify_once.py"], fixture)
    if failed.returncode == 0 or (fixture / ".verified-source-sha256").exists():
        raise RuntimeError("coding fixture did not fail before editing")
    return fixture


def run_probe(arm: str, launcher: Path, jevto: Path, timeout: int, destination: Path) -> dict:
    preflight = cursor_mcp_run_probe.preflight_direct(launcher, jevto)
    fixture = prepare_fixture(destination)
    checkout = destination / "checkout"
    initial_hash = pilot.init_checkout(fixture, checkout)
    configured = pilot.configured_mcp_names()
    pilot.disable_project_mcps(launcher, checkout, configured)
    store = destination / "store"
    policy = None
    cleanup_runner = None
    if arm == "jevto":
        policy = destination / "policy.json"
        policy.write_text(json.dumps({
            "schema_version": 1,
            "workspace": str(checkout),
            "workspace_id": destination.name,
            "commands": [{"id": "verify", "program": sys.executable, "argv": ["verify_once.py"]}],
        }, indent=2), encoding="utf-8")
        setup = check_command([str(jevto), "--store-dir", str(store), "init-cursor-runner",
                               "--workspace", str(checkout), "--policy", str(policy), "--apply"], destination)
        (destination / "runner-setup.txt").write_text(setup.stdout + setup.stderr, encoding="utf-8")
        if setup.returncode != 0:
            raise RuntimeError("Cursor runner setup failed")
        (destination / "project-mcp-installed.json").write_bytes((checkout / ".cursor" / "mcp.json").read_bytes())

        def cleanup_runner() -> bool:
            try:
                host_cleanup = check_command(cursor_explicit_route.direct_launcher(launcher, "mcp", "disable", "jevto_exec"), checkout)
                local_cleanup = check_command([str(jevto), "--store-dir", str(store), "disable-cursor-runner",
                                               "--workspace", str(checkout), "--apply"], destination)
                (destination / "runner-cleanup.txt").write_text(
                    "Cursor host:\n" + host_cleanup.stdout + host_cleanup.stderr
                    + "\nJevTO installer:\n" + local_cleanup.stdout + local_cleanup.stderr,
                    encoding="utf-8")
                return host_cleanup.returncode == 0 and local_cleanup.returncode == 0
            except Exception as error:
                (destination / "runner-cleanup.txt").write_text(str(error), encoding="utf-8")
                return False

        atexit.register(cleanup_runner)
        ignored = check_command(["git", "check-ignore", ".cursor/mcp.json"], checkout)
        if ignored.returncode != 1:
            raise RuntimeError("project MCP configuration must not be Git ignored in this probe")
        approved = check_command(cursor_explicit_route.direct_launcher(launcher, "mcp", "enable", "jevto_exec"), checkout)
        (destination / "mcp-approval.txt").write_text(approved.stdout + approved.stderr, encoding="utf-8")
        if approved.returncode != 0:
            raise RuntimeError("Cursor project runner approval failed")
        listing = check_command(cursor_explicit_route.direct_launcher(launcher, "mcp", "list"), checkout, 35)
        (destination / "mcp-list.txt").write_text(listing.stdout + listing.stderr, encoding="utf-8")
        if listing.returncode != 0 or "jevto_exec: ready" not in listing.stdout:
            raise RuntimeError("installed Cursor runner did not become ready")

    base_prompt = (
        "In this disposable project, fix summarize.total so it sums only lines beginning "
        "with `value=` and ignores warnings or notes, including misleading mentions of value=. "
        "Read summarize.py and verify.py, edit only summarize.py, and preserve the tests. "
        "Do not inspect Git history or install dependencies. After the edit, run the verifier "
        "once, wait for its result, and report whether all tests passed. "
    )
    if arm == "jevto":
        prompt = base_prompt + (
            "Run verification with the project jevto_exec MCP tool jevto_run using command_id "
            "`verify`. Do not run the verifier through a shell. Report the child exit and "
            "capture ID from that result. End with `" + MARKER + "`."
        )
    elif arm == "explicit":
        prompt = base_prompt + (
            "Run verification in the shell using this exact command: `"
            + str(jevto) + " run -- python verify_once.py`. Do not run the plain "
            "verifier separately. Report the child exit and capture ID from that "
            "result. End with `" + MARKER + "`."
        )
    else:
        prompt = base_prompt + (
            "Run verification with `python verify_once.py` in the shell. "
            "Report its exit. End with `" + MARKER + "`."
        )
    environment = os.environ.copy()
    environment.pop("OPENROUTER_API_KEY", None)
    environment.pop("JEVTO_STORE_DIR", None)
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1",
                        "GIT_PAGER": "cat", "PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"})
    if arm == "explicit":
        environment["JEVTO_STORE_DIR"] = str(store)
    command = cursor_explicit_route.direct_launcher(
        launcher, "-p", "--force", "--trust", "--sandbox",
        "disabled" if os.name == "nt" else "enabled", "--output-format", "stream-json",
        "--model", pilot.MODEL, "--workspace", str(checkout), prompt,
    )
    manifest = {"kind": "cursor_runner_coding_probe", "arm": arm, "preflight": preflight,
                "initial_tree_sha256": initial_hash, "prompt": prompt,
                "configured_user_mcps_disabled_for_project": configured,
                "route": "project_mcp" if arm == "jevto" else "explicit_cli" if arm == "explicit" else "native_shell",
                "policy_sha256": hashlib.sha256(policy.read_bytes()).hexdigest() if policy else None,
                "project_mcp_gitignored": False if policy else None,
                "timeout_seconds": timeout, "not_a_confirmatory_benchmark": True}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    stream_path = destination / "cursor-stream.jsonl"
    stderr_path = destination / "cursor-stderr.txt"
    started = time.monotonic()
    try:
        with stream_path.open("wb") as stream, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                command, cwd=checkout, env=environment, stdin=subprocess.DEVNULL,
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
    finally:
        if cleanup_runner is not None:
            cleanup_ok = cleanup_runner()
            atexit.unregister(cleanup_runner)
        else:
            cleanup_ok = True

    stream_text = stream_path.read_text(encoding="utf-8", errors="replace")
    observations = pilot.stream_observations(stream_text)
    reported_model = observations["reported_model"]
    if reported_model is not None and reported_model not in MODEL_LABELS:
        raise RuntimeError(f"Cursor substituted model {reported_model}; invalid run")
    events = []
    for line in stream_text.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    terminal = [event for event in events if event.get("type") == "result"]
    final_text = str(terminal[-1].get("result", "")) if terminal else ""
    completed_calls = [event for event in events if event.get("type") == "tool_call" and event.get("subtype") == "completed"]
    verification = pilot.verify_candidate("quiet_warning", checkout)
    (destination / "candidate.diff").write_text(verification.pop("tracked_diff"), encoding="utf-8")
    source = checkout / "summarize.py"
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
    tests_unchanged = all(
        (checkout / name).is_file() and (checkout / name).read_bytes() == (fixture / name).read_bytes()
        for name in ("verify.py", "verify_once.py")
    )
    marker_path = checkout / ".verified-source-sha256"
    marker_matches = (source_hash is not None and marker_path.is_file()
                      and marker_path.read_text(encoding="ascii") == source_hash)
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in (store / "receipts").glob("*.json")]
    reasons = []
    if timed_out or cli_exit != 0 or observations["result_event_count"] != 1 or observations["result_subtype"] != "success":
        reasons.append("model_turn_not_completed")
    if reported_model is None:
        reasons.append("model_identity_missing")
    if MARKER not in final_text:
        reasons.append("final_marker_missing")
    if not verification["verified"]:
        reasons.append("final_candidate_failed_visible_or_holdout")
    if not marker_matches:
        reasons.append("verifier_did_not_run_on_final_source")
    if not tests_unchanged:
        reasons.append("verifier_files_changed")
    if not cleanup_ok:
        reasons.append("project_runner_cleanup_failed")
    exact_recall = False
    capture_in_completed_tool = False
    capture_id_in_final = False
    if arm in {"jevto", "explicit"}:
        if arm == "jevto" and not any("jevto_run" in json.dumps(call.get("tool_call", {})) for call in completed_calls):
            reasons.append("model_jevto_run_call_missing")
        if arm == "explicit" and not any(
            jevto.name in json.dumps(call.get("tool_call", {})) and "verify_once.py" in json.dumps(call.get("tool_call", {}))
            for call in completed_calls
        ):
            reasons.append("model_explicit_jevto_command_missing")
        if arm == "explicit" and len(receipts) != 1:
            reasons.append("explicit_verifier_call_count_not_one")
        native_final = subprocess.run([sys.executable, "verify_once.py"], cwd=checkout,
                                      env=environment, capture_output=True, timeout=20)
        for receipt in receipts:
            capture_id = receipt.get("capture_id")
            if not isinstance(capture_id, str) or receipt.get("child_exit") != 0:
                continue
            capture_in_completed_tool |= any(
                capture_id in json.dumps(call.get("tool_call", {})) for call in completed_calls)
            capture_id_in_final |= capture_id in final_text
            recalled = subprocess.run([str(jevto), "--store-dir", str(store), "recall", capture_id, "--full"],
                                      capture_output=True, timeout=20)
            if recalled.returncode == 0 and recalled.stdout == native_final.stdout and recalled.stderr == native_final.stderr:
                exact_recall = True
        if not exact_recall:
            reasons.append("no_successful_final_source_receipt_with_exact_recall")
        if not capture_in_completed_tool:
            reasons.append("capture_id_missing_from_completed_tool_result")
    elif not any("verify_once.py" in json.dumps(call.get("tool_call", {})) for call in completed_calls):
        reasons.append("completed_native_verifier_call_missing")
    result = {"arm": arm, "model_requested": pilot.MODEL, "reported_model": reported_model,
              "initial_tree_sha256": initial_hash, "cli_exit": cli_exit, "timed_out": timed_out,
              "elapsed_seconds": round(time.monotonic() - started, 3), "transport": observations,
              "verified": verification["verified"], "verification": verification,
              "source_hash": source_hash, "verifier_marker_matches_final_source": marker_matches,
              "verifier_files_unchanged": tests_unchanged,
              "jevto_receipt_count": len(receipts), "jevto_receipt_exact_for_final_source": exact_recall,
              "capture_id_in_completed_tool": capture_in_completed_tool,
              "capture_id_in_final": capture_id_in_final,
              "project_runner_cleanup_ok": cleanup_ok, "valid": not reasons, "invalid_reasons": reasons,
              "provider_bill": None, "provider_bill_provenance": "not exposed by Cursor CLI stream"}
    (destination / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=["native", "jevto", "explicit"], required=True)
    parser.add_argument("--cursor-cli", type=Path, required=True)
    parser.add_argument("--jevto", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    destination = args.output or ROOT / ".bench-runs" / datetime.now(timezone.utc).strftime(f"cursor-runner-coding-{args.arm}-%Y%m%dT%H%M%SZ")
    destination.mkdir(parents=True, exist_ok=False)
    result = run_probe(args.arm, args.cursor_cli.resolve(), args.jevto.resolve(), args.timeout_seconds, destination)
    print(json.dumps({key: result[key] for key in ("arm", "reported_model", "cli_exit", "timed_out", "elapsed_seconds", "verified", "verifier_marker_matches_final_source", "jevto_receipt_count", "jevto_receipt_exact_for_final_source", "valid", "invalid_reasons", "transport")}, indent=2))
    print(destination)
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
