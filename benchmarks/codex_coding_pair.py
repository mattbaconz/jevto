"""Exploratory Codex edit-and-verify pair for the Windows pre-tool hook.

This is a diagnostic, not the Grok-pinned Cursor benchmark or a savings claim.
It runs the JevTO arm first and stops if that arm does not finish and verify.
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


ROOT = Path(__file__).resolve().parents[1]
TEST_COUNT = 500
MODEL = "gpt-6-sol"
EFFORT = "max"
CAPTURE = re.compile(r"capture_id:\s*([0-9a-f-]{36})", re.IGNORECASE)
RESULT = re.compile(r"test result: ok\.\s*(\d+) passed;\s*(\d+) failed")
PROMPT = (
    "In this disposable Rust project, fix `total` in src/lib.rs so it sums "
    "only lines whose trimmed content begins with `value=`. Ignore warning "
    "and note lines even if they mention `value=`; preserve negative values. "
    "Read src/lib.rs and tests/z_critical.rs, edit only src/lib.rs, then run "
    "exactly `cargo test --workspace` in Windows PowerShell and wait for it. "
    "In your final answer report the total passed and failed test counts and "
    "any JevTO capture ID shown. Do not change tests or host configuration, "
    "install dependencies, use the network, or run unrelated commands."
)
LIBRARY = """pub fn total(lines: &[&str]) -> i32 {
    lines
        .iter()
        .filter_map(|line| {
            line.split_once("value=")
                .and_then(|(_, value)| value.trim().parse::<i32>().ok())
        })
        .sum()
}
"""
CRITICAL = """use jevto_coding_fixture::total;

#[test]
fn ignores_noise_with_value_marker() {
    assert_eq!(total(&["value=2", "warning: value=99", "value=-1"]), 1);
}
"""
HOLDOUT = """#[path = r"{source}"]
mod candidate;

#[test]
fn ignores_prefixed_noise() {{
    assert_eq!(candidate::total(&["note: value=900", "value=3"]), 3);
}}

#[test]
fn keeps_negative_and_leading_space() {{
    assert_eq!(candidate::total(&["  value=-4", "value=9"]), 5);
}}

#[test]
fn ignores_empty_and_unrelated_lines() {{
    assert_eq!(candidate::total(&["", "warning: cache stale", "value=2"]), 2);
}}
"""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, check=False)


def required(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 60) -> str:
    result = run(command, cwd=cwd, env=env, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{command[0]} exited {result.returncode}: {result.stderr[-1200:]}")
    return result.stdout.strip()


def make_fixture(path: Path, env: dict[str, str]) -> dict:
    (path / "src").mkdir(parents=True)
    (path / "tests").mkdir()
    (path / "Cargo.toml").write_text(
        '[package]\nname = "jevto-coding-fixture"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n',
        encoding="utf-8",
    )
    (path / ".gitignore").write_text("target/\n.codex/\n.jevto-store/\n", encoding="utf-8")
    (path / "src" / "lib.rs").write_text(LIBRARY, encoding="utf-8")
    tests = "use jevto_coding_fixture::total;\n\n"
    for index in range(TEST_COUNT - 1):
        a, b = index - 249, (index % 17) - 8
        tests += f'#[test]\nfn case_{index}() {{ assert_eq!(total(&["value={a}", "value={b}"]), {a+b}); }}\n'
    (path / "tests" / "a_inventory.rs").write_text(tests, encoding="utf-8")
    (path / "tests" / "z_critical.rs").write_text(CRITICAL, encoding="utf-8")
    initial = run(["cargo", "test", "--workspace"], cwd=path, env=env, timeout=120)
    if initial.returncode == 0 or "ignores_noise_with_value_marker" not in initial.stdout:
        raise RuntimeError("fixture did not fail at the intended critical test")
    required(["git", "init", "-q"], cwd=path, env=env)
    required(["git", "add", "."], cwd=path, env=env)
    tree = required(["git", "write-tree"], cwd=path, env=env)
    required(["git", "-c", "user.name=JevTO Pilot", "-c", "user.email=pilot@invalid.local",
              "commit", "-q", "-m", "fixture baseline"], cwd=path, env=env)
    return {"git_tree": tree, "source_sha256": digest(path / "src" / "lib.rs"),
            "tests_sha256": [digest(path / "tests" / name) for name in ("a_inventory.rs", "z_critical.rs")],
            "initial_test_exit": initial.returncode,
            "initial_passing_inventory": "499 passed; 0 failed" in initial.stdout}


def holdout(path: Path, output: Path, env: dict[str, str]) -> dict:
    source = str((path / "src" / "lib.rs").resolve())
    file = output / f"holdout-{path.name}.rs"
    file.write_text(HOLDOUT.format(source=source), encoding="utf-8")
    binary = output / f"holdout-{path.name}.exe"
    compile_result = run(["rustc", "--test", str(file), "-o", str(binary)], cwd=output, env=env)
    if compile_result.returncode:
        return {"passed": False, "compile_exit": compile_result.returncode,
                "output": (compile_result.stdout + compile_result.stderr)[-2000:]}
    result = run([str(binary)], cwd=output, env=env)
    return {"passed": result.returncode == 0 and "3 passed; 0 failed" in result.stdout,
            "compile_exit": 0, "test_exit": result.returncode,
            "output": (result.stdout + result.stderr)[-2000:]}


def parse_trace(path: Path) -> dict:
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    completed = [(index, e["item"]) for index, e in enumerate(events)
                 if e.get("type") == "item.completed"]
    commands = [(index, item) for index, item in completed if item.get("type") == "command_execution"]
    cargo = [(index, item) for index, item in commands if "cargo test --workspace" in item.get("command", "")]
    source_edits = [index for index, item in completed if item.get("type") == "file_change"
                    and any(change.get("path", "").replace("\\", "/").endswith("/src/lib.rs")
                            for change in item.get("changes", []))]
    messages = [e["item"].get("text", "") for e in events if e.get("type") == "item.completed"
                and e.get("item", {}).get("type") == "agent_message"]
    usage = [e.get("usage") for e in events if e.get("type") == "turn.completed"]
    final = messages[-1] if messages else ""
    last_output = cargo[-1][1].get("aggregated_output", "") if cargo else ""
    counts = [(int(passed), int(failed)) for passed, failed in RESULT.findall(last_output)]
    capture = CAPTURE.search(last_output)
    return {"thread_id": next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None),
            "terminal_turn_count": len(usage), "provider_usage": usage[-1] if usage else None,
            "completed_shell_calls": len(commands), "cargo_calls": len(cargo),
            "cargo_after_source_edit": bool(cargo and source_edits and max(source_edits) < cargo[-1][0]),
            "last_cargo_exit": cargo[-1][1].get("exit_code") if cargo else None,
            "last_cargo_result_counts": counts,
            "last_cargo_output_bytes": len(last_output.encode("utf-8")),
            "capture_id": capture.group(1) if capture else None,
            "capture_reported_in_final": bool(capture and capture.group(1) in final),
            "final_answer": final}


def arm_run(arm: str, cli: Path, jevto: Path, path: Path, output: Path,
            env: dict[str, str], timeout: int, expected: dict) -> dict:
    manage_store = output / "manage-store"
    installed = False
    if arm == "jevto":
        required([str(jevto), "--store-dir", str(manage_store), "init-codex-pre-hook",
                  "--workspace", str(path), "--apply"], cwd=output, env=env)
        installed = True
    trace = output / f"{arm}.jsonl"
    stderr = output / f"{arm}-stderr.txt"
    command = [str(cli), "exec", "--json", "--ephemeral", "--dangerously-bypass-hook-trust",
               "--sandbox", "workspace-write", "--model", MODEL,
               "-c", f"model_reasoning_effort={EFFORT}", "-C", str(path), PROMPT]
    started = time.monotonic()
    try:
        with trace.open("w", encoding="utf-8") as out, stderr.open("w", encoding="utf-8") as err:
            try:
                result = subprocess.run(command, cwd=path, env=env, stdin=subprocess.DEVNULL,
                                        stdout=out, stderr=err, text=True, timeout=timeout, check=False)
                cli_exit, timed_out = result.returncode, False
            except subprocess.TimeoutExpired:
                cli_exit, timed_out = None, True
    finally:
        elapsed = round(time.monotonic() - started, 3)
        if installed:
            disabled = run([str(jevto), "--store-dir", str(manage_store), "disable-codex-pre-hook",
                            "--workspace", str(path), "--apply"], cwd=output, env=env)
            cleanup_ok = disabled.returncode == 0
            (output / "hook-cleanup.txt").write_text(disabled.stdout + disabled.stderr, encoding="utf-8")
        else:
            cleanup_ok = True
    parsed = parse_trace(trace)
    after = {"source_sha256": digest(path / "src" / "lib.rs"),
             "tests_sha256": [digest(path / "tests" / name) for name in ("a_inventory.rs", "z_critical.rs")],
             "git_diff": required(["git", "diff", "--", "src/lib.rs"], cwd=path, env=env),
             "git_status": required(["git", "status", "--short"], cwd=path, env=env)}
    (output / f"{arm}.diff").write_text(after.pop("git_diff") + "\n", encoding="utf-8")
    independent = holdout(path, output, env)
    receipts = []
    for file in (path / ".jevto-store" / "receipts").glob("*.json"):
        receipts.append(json.loads(file.read_text(encoding="utf-8")))
    matching_receipts = [r for r in receipts if r.get("capture_id") == parsed["capture_id"]]
    valid = (not timed_out and cli_exit == 0 and cleanup_ok and parsed["terminal_turn_count"] == 1
             and parsed["last_cargo_exit"] == 0 and parsed["cargo_after_source_edit"]
             and sum(p for p, _ in parsed["last_cargo_result_counts"]) == TEST_COUNT
             and sum(f for _, f in parsed["last_cargo_result_counts"]) == 0
             and f"{TEST_COUNT} passed" in parsed["final_answer"]
             and "0 failed" in parsed["final_answer"]
             and independent["passed"] and after["source_sha256"] != expected["source_sha256"]
             and after["tests_sha256"] == expected["tests_sha256"]
             and after["git_status"] == "M src/lib.rs")
    if arm == "jevto":
        valid = valid and parsed["capture_reported_in_final"] and len(matching_receipts) == 1
        valid = valid and bool(matching_receipts) and (
            matching_receipts[0].get("raw_bytes", 0) > matching_receipts[0].get("delivered_bytes", 0))
    else:
        valid = valid and not parsed["capture_id"] and not receipts
    observation = {"arm": arm, "valid": valid, "cli_exit": cli_exit, "timed_out": timed_out,
                   "elapsed_seconds": elapsed, "hook_cleanup_ok": cleanup_ok,
                   "trace": str(trace), "stderr": str(stderr), "parsed": parsed,
                   "independent_holdout": independent, "after": after,
                   "receipt_count": len(receipts), "matching_receipts": matching_receipts}
    (output / f"{arm}-result.json").write_text(json.dumps(observation, indent=2) + "\n", encoding="utf-8")
    return observation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-cli", required=True, type=Path)
    parser.add_argument("--jevto-cli", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    if os.name != "nt" or args.timeout_seconds < 60 or args.timeout_seconds > 600:
        parser.error("requires Windows and a 60–600 second per-arm timeout")
    cli = args.codex_cli.resolve(strict=True)
    jevto = args.jevto_cli.resolve(strict=True)
    output = ROOT / ".bench-runs" / f"codex-coding-pair-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("JEVTO_STORE_DIR", None)
    env["RUSTUP_HOME"] = str(ROOT.parent / ".tooling" / "rustup-isolated")
    setup = {}
    for arm in ("jevto", "native"):
        setup[arm] = make_fixture(output / arm, env)
    if setup["jevto"] != setup["native"] or not setup["jevto"]["initial_passing_inventory"]:
        raise RuntimeError("disposable fixtures do not have identical failing initial states")
    manifest = {"purpose": "exploratory Codex coding task, separate from Grok Cursor benchmark",
                "codex_version": required([str(cli), "--version"], cwd=output, env=env),
                "codex_sha256": digest(cli), "jevto_sha256": digest(jevto),
                "model_requested": MODEL, "reasoning_effort_requested": EFFORT,
                "resolved_model_provenance": "CLI does not expose resolved model in JSONL",
                "sandbox": "workspace-write", "hook_trust": "bypassed for reviewed local fixture hook",
                "prompt": PROMPT, "fixture": setup["jevto"], "arm_order": ["jevto", "native"],
                "runs": [], "provider_bill": None}
    try:
        for arm in manifest["arm_order"]:
            result = arm_run(arm, cli, jevto, output / arm, output, env, args.timeout_seconds, setup[arm])
            manifest["runs"].append(result)
            (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            print(f"{arm}: valid={result['valid']} elapsed={result['elapsed_seconds']}s "
                  f"usage={result['parsed']['provider_usage']}", flush=True)
            if not result["valid"]:
                break
    finally:
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0 if len(manifest["runs"]) == 2 and all(run["valid"] for run in manifest["runs"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
