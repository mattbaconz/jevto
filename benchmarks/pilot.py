"""Bounded, paired Cursor CLI pilot. Synthetic fixtures; no spending automation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
MODEL = "grok-4.7-xhigh"
ARMS = ("native", "jevto_full_deterministic")
TASKS = {
    "quiet_warning": {
        "goal": "Fix summarize.total so it sums value= events and ignores other log lines, including early warnings. Keep the implementation small and verify with python verify.py.",
        "order": ("native", "jevto_full_deterministic"),
    },
    "already_lean": {
        "goal": "Fix label.label so it strips surrounding whitespace and uppercases names after unit=. Keep the implementation small and verify with python verify.py.",
        "order": ("jevto_full_deterministic", "native"),
    },
}


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts):
        digest.update(item.relative_to(path).as_posix().encode())
        digest.update(bytes.fromhex(sha_file(item)))
    return digest.hexdigest()


def check_fixture(task: str) -> dict:
    path = HERE / "fixtures" / task
    visible = run([sys.executable, "verify.py"], cwd=path)
    assert visible.returncode != 0, f"{task} already passes before treatment"
    return {"task": task, "initial_tree_sha256": tree_hash(path), "initial_verifier_exit": visible.returncode}


def launcher_command(launcher: Path, *args: str) -> list[str]:
    if launcher.suffix.lower() == ".ps1":
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(launcher), *args]
    return [str(launcher), *args]


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
        result = run(launcher_command(launcher, "mcp", "disable", name), cwd=checkout)
        if result.returncode:
            raise RuntimeError(f"could not disable {name} MCP in disposable checkout")


def preflight(jevto: Path, launcher: Path) -> dict:
    if not launcher.is_file():
        raise RuntimeError(f"Cursor CLI unavailable: {launcher}")
    models = run(launcher_command(launcher, "models"), cwd=ROOT)
    if models.returncode != 0 or not any(line.split(" - ")[0] == MODEL for line in models.stdout.splitlines()):
        raise RuntimeError(f"exact Cursor model {MODEL} unavailable; Fast and substitutes are forbidden")
    if not jevto.is_file():
        raise RuntimeError(f"JevTO executable missing: {jevto}")
    version = run(launcher_command(launcher, "--version"), cwd=ROOT)
    doctor = run([str(jevto), "doctor", "--json"], cwd=ROOT)
    if version.returncode != 0 or doctor.returncode != 0:
        raise RuntimeError("Cursor CLI version or JevTO doctor failed")
    return {"cursor_cli_version": version.stdout.strip(), "cursor_launcher_sha256": sha_file(launcher),
            "model_requested": MODEL,
            "model_listing": "exact grok-4.7-xhigh present", "jevto_sha256": sha_file(jevto),
            "jevto_version": json.loads(doctor.stdout)["version"],
            "sandbox_mode": "disabled (Cursor sandbox unavailable on Windows)" if sys.platform == "win32" else "enabled"}


def init_checkout(source: Path, destination: Path) -> str:
    shutil.copytree(source, destination)
    for command in (["git", "init", "-q"], ["git", "add", "."],
                    ["git", "-c", "user.name=JevTO Pilot", "-c", "user.email=pilot@invalid.local",
                     "commit", "-q", "-m", "fixture baseline"]):
        result = run(command, cwd=destination)
        if result.returncode != 0:
            raise RuntimeError(f"fixture Git setup failed: {result.stderr.strip()}")
    return tree_hash(destination)


def prompt(task: str, arm: str, jevto: Path) -> str:
    common = (TASKS[task]["goal"] + " Work only in this disposable fixture. "
              "Do not install dependencies or use the network. Preserve the test files. "
              "A passing check must run on your final edited candidate, not a prior snapshot. ")
    if arm == "native":
        return common + "Run the verifier directly with `python verify.py`."
    return (common + "Run the verifier through the explicit local wrapper: `" + str(jevto)
            + " run -- python verify.py`. Use JevTO recall if the selected view omits a needed line. "
              "You may inspect source files normally; native tool calls outside the wrapper are not JevTO coverage.")


def stream_observations(text: str) -> dict:
    events = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    init_models = [e.get("model") for e in events if e.get("type") == "system" and e.get("subtype") == "init"]
    tool_events = [e for e in events if e.get("type") in ("tool_call", "tool_result")]
    result_events = [e for e in events if e.get("type") == "result"]
    usage = [e.get("usage") for e in result_events if isinstance(e.get("usage"), dict)]
    terminal_usage = usage[-1] if usage else None
    token_fields = ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")
    derived_total = (sum(terminal_usage[field] for field in token_fields)
                     if terminal_usage is not None and all(
                         type(terminal_usage.get(field)) is int and terminal_usage[field] >= 0
                         for field in token_fields) else None)
    return {"json_event_count": len(events), "reported_model": init_models[0] if init_models else None,
            "tool_event_count": len(tool_events), "result_event_count": len(result_events),
            "result_subtype": result_events[-1].get("subtype") if result_events else None,
            "result_is_error": result_events[-1].get("is_error") if result_events else None,
            "provider_usage_reported": terminal_usage,
            "derived_total_tokens": derived_total}


def verify_candidate(task: str, checkout: Path) -> dict:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    visible = run([sys.executable, "verify.py"], cwd=checkout, env=environment)
    script = "import os,runpy,sys;sys.path.insert(0,os.getcwd());runpy.run_path(sys.argv[1],run_name='__main__')"
    hidden = run([sys.executable, "-c", script, str(HERE / "holdout" / f"{task}.py")], cwd=checkout, env=environment)
    diff = run(["git", "diff", "--binary", "HEAD"], cwd=checkout)
    status = run(["git", "status", "--porcelain=v1"], cwd=checkout)
    return {"visible_exit": visible.returncode, "holdout_exit": hidden.returncode,
            "verified": visible.returncode == 0 and hidden.returncode == 0,
            "visible_stdout": visible.stdout[-1000:], "visible_stderr": visible.stderr[-1000:],
            "holdout_stderr": hidden.stderr[-1000:], "candidate_tree_sha256": tree_hash(checkout),
            "diff_sha256": hashlib.sha256(diff.stdout.encode()).hexdigest(),
            "diff_lines": len(diff.stdout.splitlines()), "git_status": status.stdout.splitlines(),
            "tracked_diff": diff.stdout}


def one_run(task: str, arm: str, destination: Path, launcher: Path, jevto: Path,
            timeout: int, mcp_names: list[str]) -> dict:
    checkout = destination / "checkout"
    initial_hash = init_checkout(HERE / "fixtures" / task, checkout)
    disable_project_mcps(launcher, checkout, mcp_names)
    store = destination / "jevto-store"
    env = os.environ.copy()
    env["JEVTO_STORE_DIR"] = str(store)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["NO_COLOR"] = "1"
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env["GIT_TERMINAL_PROMPT"] = "0"
    sandbox_mode = "disabled" if sys.platform == "win32" else "enabled"
    command = launcher_command(launcher, "-p", "--force", "--trust", "--sandbox", sandbox_mode,
                               "--output-format", "stream-json", "--model", MODEL,
                               "--workspace", str(checkout), prompt(task, arm, jevto))
    started = time.monotonic()
    stream_path = destination / "cursor-stream.jsonl"
    stderr_path = destination / "cursor-stderr.txt"
    with stream_path.open("wb") as stream_file, stderr_path.open("wb") as stderr_file:
        process = subprocess.Popen(command, cwd=checkout, env=env, stdin=subprocess.DEVNULL,
                                   stdout=stream_file, stderr=stderr_file,
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0)
        try:
            cli_exit = process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            else:
                process.kill()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            cli_exit = None
            timed_out = True
    elapsed = time.monotonic() - started
    stdout = stream_path.read_text(encoding="utf-8", errors="replace")
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    observations = stream_observations(stdout)
    reported = observations["reported_model"]
    permitted_display = {MODEL, "Grok 4.7 Extra High", "Grok 4.7  Extra High",
                         "Grok 4.7 256K Extra High"}
    if reported is not None and reported not in permitted_display:
        raise RuntimeError(f"Cursor reported a substituted model: {observations['reported_model']}")
    invalid_reason = None
    if timed_out and observations["json_event_count"] == 0:
        invalid_reason = "cursor_timeout_before_init"
    elif reported is None:
        invalid_reason = "model_identity_not_observed"
    elif observations["result_event_count"] == 0:
        invalid_reason = "no_terminal_result"
    elif observations["result_subtype"] != "success" or observations["result_is_error"] is True:
        invalid_reason = "cursor_result_error"
    elif cli_exit != 0:
        invalid_reason = f"cursor_exit_{cli_exit}"
    verification = verify_candidate(task, checkout)
    (destination / "candidate.diff").write_text(verification.pop("tracked_diff"), encoding="utf-8")
    receipts = []
    for path in (store / "receipts").glob("*.json"):
        receipts.append(json.loads(path.read_text(encoding="utf-8")))
    if arm != "native" and not receipts and invalid_reason is None:
        invalid_reason = "no_jevto_receipt"
    result = {"task": task, "arm": arm, "model_requested": MODEL,
              "initial_tree_sha256": initial_hash, "cursor_exit": cli_exit,
              "timed_out": timed_out, "elapsed_seconds": round(elapsed, 3),
              "benchmark_eligible": invalid_reason is None,
              "invalid_reason": invalid_reason,
              "stream": observations, "verification": verification,
              "project_mcp_servers_disabled": mcp_names,
              "jevto_receipts": receipts, "jevto_receipt_count": len(receipts),
              "provider_bill": None, "provider_bill_provenance": "not exposed by Cursor CLI stream",
              "note": "Pilot only; no whole-session interception or savings claim"}
    (destination / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true", help="verify fixtures fail before agent runs")
    parser.add_argument("--max-runs", type=int, choices=(2, 4), default=4)
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--cursor-cli", type=Path, help="exact installed Cursor CLI launcher")
    parser.add_argument("--jevto", type=Path, help="JevTO executable to test")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    fixture_checks = [check_fixture(task) for task in TASKS]
    if args.self_check:
        print(json.dumps(fixture_checks, indent=2))
        return 0
    default_cursor = shutil.which("cursor-agent")
    launcher = (args.cursor_cli or (Path(default_cursor) if default_cursor else None))
    if launcher is None:
        raise RuntimeError("Cursor CLI unavailable")
    launcher = launcher.resolve()
    jevto = (args.jevto or ROOT / "target" / "debug" / "jevto.exe").resolve()
    requirements = preflight(jevto, launcher)
    mcp_names = configured_mcp_names()
    destination = args.output or ROOT / ".bench-runs" / datetime.now(timezone.utc).strftime("pilot-%Y%m%dT%H%M%SZ")
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": 1, "kind": "bounded_pilot", "preflight": requirements,
                "fixtures": fixture_checks, "timeout_seconds": args.timeout_seconds,
                "planned_order": [(task, arm) for task, data in TASKS.items() for arm in data["order"]],
                "executed_order": [], "exclusions": ["adaptive Jev unavailable", "RTK/Headroom/Ponytail unavailable"],
                "provider_usage_source": "Cursor CLI stream only; absent fields are unknown",
                "project_mcp_servers_disabled": mcp_names,
                "git_pager": "cat", "git_terminal_prompt": "disabled"}
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for task, arm in manifest["planned_order"][:args.max_runs]:
        run_dir = destination / f"{task}-{arm}"
        run_dir.mkdir()
        result = one_run(task, arm, run_dir, launcher, jevto, args.timeout_seconds, mcp_names)
        manifest["executed_order"].append({"task": task, "arm": arm,
                                           "benchmark_eligible": result["benchmark_eligible"],
                                           "invalid_reason": result["invalid_reason"],
                                           "verified": result["verification"]["verified"] if result["benchmark_eligible"] else None,
                                           "elapsed_seconds": result["elapsed_seconds"]})
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"{task} {arm}: verified={result['verification']['verified']} elapsed={result['elapsed_seconds']}s", flush=True)
        if not result["benchmark_eligible"]:
            manifest["stopped_early_reason"] = f"{task} {arm}: {result['invalid_reason']}"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            break
    return 2 if "stopped_early_reason" in manifest else 0


if __name__ == "__main__":
    raise SystemExit(main())
