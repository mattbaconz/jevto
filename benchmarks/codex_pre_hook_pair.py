"""Exploratory Codex native-vs-JevTO host pair on a synthetic Rust suite.

This is deliberately separate from the Grok-pinned Cursor benchmark. It records
provider-reported usage without treating one small pair as a savings claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_COUNT = 500
PROMPT = (
    "In this repository, run exactly `cargo test --workspace` once in Windows "
    "PowerShell using the shell tool. Do not read files or call other tools. "
    "In your final reply, give the passed and failed test counts and any "
    "JevTO capture ID shown. Do not rerun the command."
)
CAPTURE_ID = re.compile(r"capture_id:\s*([0-9a-f-]{36})", re.IGNORECASE)
PASS_COUNT = re.compile(r"test result: ok\.\s*(\d+) passed;\s*(\d+) failed")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 120) -> str:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"command exited {result.returncode}: {argv[0]}\n{result.stderr[-2000:]}")
    return result.stdout.strip()


def fixture(path: Path, env: dict[str, str]) -> None:
    (path / "src").mkdir(parents=True)
    (path / "Cargo.toml").write_text(
        '[package]\nname = "jevto-codex-pair"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n',
        encoding="utf-8",
    )
    (path / "src" / "lib.rs").write_text(
        "".join(f"#[test]\nfn case_{index}() {{ assert_eq!(1 + 1, 2); }}\n" for index in range(TEST_COUNT)),
        encoding="utf-8",
    )
    command(["git", "init", "-q"], cwd=path, env=env)
    command(["cargo", "test", "--workspace"], cwd=path, env=env)


def parse_trace(path: Path, arm: str) -> dict:
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    executions = [
        event["item"]
        for event in events
        if event.get("type") == "item.completed"
        and event.get("item", {}).get("type") == "command_execution"
    ]
    usage_events = [event["usage"] for event in events if event.get("type") == "turn.completed"]
    messages = [
        event["item"].get("text", "")
        for event in events
        if event.get("type") == "item.completed"
        and event.get("item", {}).get("type") == "agent_message"
    ]
    thread = next((event.get("thread_id") for event in events if event.get("type") == "thread.started"), None)
    if len(executions) != 1 or len(usage_events) != 1:
        raise RuntimeError(f"{arm}: expected one completed shell call and one usage event")
    execution = executions[0]
    output = execution.get("aggregated_output", "")
    counts = PASS_COUNT.findall(output)
    capture = CAPTURE_ID.search(output)
    final = messages[-1] if messages else ""
    if execution.get("exit_code") != 0 or (str(TEST_COUNT), "0") not in counts:
        raise RuntimeError(f"{arm}: test outcome was not {TEST_COUNT} passed, 0 failed")
    if arm == "hook" and (not capture or capture.group(1) not in final):
        raise RuntimeError("hook: capture was not delivered to both shell output and final answer")
    if arm == "baseline" and capture:
        raise RuntimeError("baseline: unexpected JevTO capture")
    return {
        "thread_id": thread,
        "shell_calls": len(executions),
        "shell_exit": execution.get("exit_code"),
        "tool_output_bytes": len(output.encode("utf-8")),
        "capture_id": capture.group(1) if capture else None,
        "test_count": TEST_COUNT,
        "verified": True,
        "provider_usage": usage_events[0],
        "final_answer": final,
    }


def run_codex(
    cli: Path,
    path: Path,
    trace: Path,
    arm: str,
    env: dict[str, str],
    timeout: int,
) -> dict:
    argv = [
        str(cli), "exec", "--json", "--ephemeral", "--dangerously-bypass-hook-trust",
        "--sandbox", "workspace-write", "--model", "gpt-6-luna", "-C", str(path), PROMPT,
    ]
    started = time.monotonic()
    try:
        with trace.open("w", encoding="utf-8") as output:
            result = subprocess.run(
                argv, cwd=path, env=env, stdout=output, stderr=subprocess.PIPE,
                text=True, timeout=timeout, check=False,
            )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"{arm}: Codex timed out after {timeout}s") from error
    elapsed = round(time.monotonic() - started, 3)
    if result.returncode:
        raise RuntimeError(f"{arm}: Codex exited {result.returncode}: {result.stderr[-2000:]}")
    observation = parse_trace(trace, arm)
    observation.update({"arm": arm, "elapsed_seconds": elapsed, "trace": str(trace)})
    return observation


def summarize(runs: list[dict]) -> dict:
    pairs = []
    for index in range(0, len(runs), 2):
        first, second = runs[index : index + 2]
        if {first["arm"], second["arm"]} != {"baseline", "hook"}:
            raise RuntimeError("each consecutive pair must have one native and one hooked run")
        native = first if first["arm"] == "baseline" else second
        hooked = first if first["arm"] == "hook" else second
        pairs.append({
            "native_input_tokens": native["provider_usage"]["input_tokens"],
            "hook_input_tokens": hooked["provider_usage"]["input_tokens"],
            "hook_minus_native_input_tokens": (
                hooked["provider_usage"]["input_tokens"]
                - native["provider_usage"]["input_tokens"]
            ),
            "cached_input_matched": (
                hooked["provider_usage"].get("cached_input_tokens")
                == native["provider_usage"].get("cached_input_tokens")
            ),
        })
    native_total = sum(run["provider_usage"]["input_tokens"] for run in runs if run["arm"] == "baseline")
    hook_total = sum(run["provider_usage"]["input_tokens"] for run in runs if run["arm"] == "hook")
    return {
        "pairs": pairs,
        "native_input_tokens": native_total,
        "hook_input_tokens": hook_total,
        "hook_minus_native_input_tokens": hook_total - native_total,
        "hook_minus_native_percent": round((hook_total - native_total) / native_total * 100, 2),
        "interpretation": "synthetic one-command task only; not a general cost or quality claim",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-cli", type=Path, required=True)
    parser.add_argument("--jevto-cli", type=Path, default=ROOT / "target" / "debug" / "jevto.exe")
    parser.add_argument("--pairs", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    if os.name != "nt" or args.pairs < 1 or args.pairs > 4:
        parser.error("requires Windows and 1–4 pairs")
    cli = args.codex_cli.resolve(strict=True)
    jevto = args.jevto_cli.resolve(strict=True)
    out = ROOT / ".bench-runs" / f"codex-prehook-pair-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True)
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("JEVTO_STORE_DIR", None)
    env["RUSTUP_HOME"] = str(ROOT.parent / ".tooling" / "rustup-isolated")
    fixtures = {arm: out / arm for arm in ("baseline", "hook")}
    for path in fixtures.values():
        fixture(path, env)
    manage_store = out / "manage-store"
    install = [str(jevto), "--store-dir", str(manage_store), "init-codex-pre-hook", "--workspace", str(fixtures["hook"]), "--apply"]
    disable = [str(jevto), "--store-dir", str(manage_store), "disable-codex-pre-hook", "--workspace", str(fixtures["hook"]), "--apply"]
    manifest = {
        "purpose": "exploratory paired Codex host check; not Cursor/Grok benchmark or savings claim",
        "codex_version": command([str(cli), "--version"], cwd=ROOT, env=env),
        "codex_sha256": sha256(cli),
        "jevto_sha256": sha256(jevto),
        "model": "gpt-6-luna",
        "sandbox": "workspace-write",
        "hook_trust": "bypassed only for reviewed local hook in unattended automation",
        "prompt": PROMPT,
        "tests": TEST_COUNT,
        "fixture_sha256": sha256(fixtures["baseline"] / "src" / "lib.rs"),
        "order": [],
        "runs": [],
    }
    installed = False
    try:
        command(install, cwd=ROOT, env=env)
        installed = True
        # ABBA for two pairs; repeat the balanced block if more pairs are requested.
        order = (["baseline", "hook", "hook", "baseline"] * ((args.pairs + 1) // 2))[: args.pairs * 2]
        manifest["order"] = order
        for number, arm in enumerate(order, 1):
            trace = out / f"{number:02d}-{arm}.jsonl"
            observation = run_codex(cli, fixtures[arm], trace, arm, env, args.timeout_seconds)
            manifest["runs"].append(observation)
            (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            usage = observation["provider_usage"]
            print(
                f"{number}/{len(order)} {arm}: input={usage.get('input_tokens')} "
                f"cached={usage.get('cached_input_tokens')} output={usage.get('output_tokens')} "
                f"tool_bytes={observation['tool_output_bytes']} elapsed={observation['elapsed_seconds']}s",
                flush=True,
            )
        manifest["summary"] = summarize(manifest["runs"])
    finally:
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if installed:
            command(disable, cwd=ROOT, env=env)
    print(f"manifest: {out / 'manifest.json'}")


if __name__ == "__main__":
    main()
