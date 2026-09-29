"""Cross-harness agent pilot: native vs Ponytail vs JevTO vs both, on Codex CLI or OpenCode.

Same task, repository, visible suite, and hidden holdout as
`claude_ponytail_pilot.py`. Arms are wired with each harness's own mechanisms:

Codex CLI
    JevTO     `jevto init-codex-pre-hook` (project PreToolUse rewrite)
    Ponytail  its Claude/Codex hook scripts registered as project SessionStart
              and UserPromptSubmit hooks, with PLUGIN_DATA set so they emit
              Codex-shaped output; a mode flag file proves they ran
OpenCode
    JevTO     `integrations/opencode/jevto.js` project plugin
    Ponytail  its OpenCode plugin (system-prompt transform)

Codex usage is reported by the CLI's `turn.completed` events; there is no
dollar estimate for subscription use. This is an exploratory pilot.

Usage:
    python benchmarks/agent_pilot.py --harness codex --codex PATH/codex.exe \
        --jevto target/release/jevto.exe --ponytail PONYTAIL_PLUGIN_DIR --node PATH/node.exe --reps 3
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

from claude_ponytail_pilot import ARMS, HOLDOUT, PROMPT, build_repo

ROOT = Path(__file__).resolve().parents[1]


def run(argv, cwd, env=None, timeout=120):
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=timeout)


def ps_quote(text) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def add_codex_hooks(checkout: Path, groups: dict) -> None:
    path = checkout / ".codex" / "hooks.json"
    config = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"hooks": {}}
    for event, group in groups.items():
        config["hooks"].setdefault(event, []).append(group)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def ponytail_codex_hook(args, script: str, data: Path) -> dict:
    command = (f"$env:PLUGIN_DATA={ps_quote(data)}; "
               f"& {ps_quote(args.node)} {ps_quote(Path(args.ponytail) / 'hooks' / script)}")
    return {"hooks": [{"type": "command", "command": command, "timeout": 10}]}


def parse_codex(stream: str) -> dict:
    usage = None
    commands = []
    edits = 0
    turns = 0
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            usage = event.get("usage") or usage
            turns += 1
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "command_execution":
            commands.append(item)
        if event.get("type") == "item.completed" and item.get("type") == "file_change":
            edits += 1
    tests = [item for item in commands if "unittest" in str(item.get("command", ""))]
    usage = usage or {}
    return {
        "usage": usage,
        "input_tokens": usage.get("input_tokens"),
        "cached_input_tokens": usage.get("cached_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
        "combined_tokens": (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0) if usage else None,
        "shell_calls": len(commands),
        "test_runs": len(tests),
        "test_output_bytes": sum(len(str(item.get("aggregated_output", "")).encode("utf-8")) for item in tests),
        "file_changes": edits,
        "completed_turns": turns,
    }


def parse_opencode(stream: str) -> dict:
    tokens = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
    cost = 0.0
    tests = []
    shell_calls = 0
    errors = []
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "error":
            errors.append(str((event.get("error") or {}).get("data", {}).get("message", event.get("error")))[:200])
        part = event.get("part") or {}
        if event.get("type") == "step_finish" or part.get("type") == "step-finish":
            step = part.get("tokens") or {}
            tokens["input"] += step.get("input", 0)
            tokens["output"] += step.get("output", 0)
            tokens["reasoning"] += step.get("reasoning", 0)
            cache = step.get("cache") or {}
            tokens["cache_read"] += cache.get("read", 0)
            tokens["cache_write"] += cache.get("write", 0)
            cost += part.get("cost") or 0.0
        if part.get("type") == "tool" and part.get("tool") == "bash":
            state = part.get("state") or {}
            if state.get("status") == "completed":
                shell_calls += 1
                command = str((state.get("input") or {}).get("command", ""))
                if "unittest" in command:
                    tests.append(str(state.get("output", "")))
    return {
        "usage": tokens, "cost_usd": cost, "errors": errors,
        "input_tokens": tokens["input"] + tokens["cache_read"] + tokens["cache_write"],
        "cached_input_tokens": tokens["cache_read"],
        "output_tokens": tokens["output"] + tokens["reasoning"],
        "combined_tokens": sum(tokens.values()),
        "shell_calls": shell_calls, "test_runs": len(tests),
        "test_output_bytes": sum(len(text.encode("utf-8")) for text in tests),
    }


def score(checkout: Path, arm_dir: Path, env: dict) -> dict:
    visible = run([sys.executable, "-m", "unittest", "-q"], checkout, env)
    (arm_dir / "holdout.py").write_text(HOLDOUT, encoding="utf-8")
    holdout = run([sys.executable, str(arm_dir / "holdout.py")], checkout, env)
    body = (checkout / "timekit" / "durations.py").read_text(encoding="utf-8")
    (arm_dir / "durations.py").write_text(body, encoding="utf-8")
    impl = [line for line in body.split('"""')[-1].splitlines() if line.strip() and not line.strip().startswith("#")]
    status = run(["git", "status", "--porcelain", "--untracked-files=all"], checkout).stdout.decode()
    changed = sorted(line[3:] for line in status.splitlines() if line.strip())
    return {
        "visible_pass": visible.returncode == 0,
        "holdout_pass": holdout.returncode == 0,
        "holdout_detail": holdout.stdout.decode(errors="replace").strip()[:400],
        "implementation_lines": len(impl),
        "changed_files": changed,
        "tests_untouched": not any(path.startswith("tests/") for path in changed),
    }


def run_codex_arm(arm: str, rep: int, args, out: Path) -> dict:
    arm_dir = out / f"{rep}-{arm.replace('+', '-')}"
    checkout = arm_dir / "checkout"
    checkout.mkdir(parents=True)
    build_repo(checkout)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", NO_COLOR="1", GIT_PAGER="cat", PAGER="cat")
    exclude = [".codex/", ".jevto-store/"]
    if "jevto" in arm:
        setup = run([args.jevto, "--store-dir", str(arm_dir / "manage-store"), "init-codex-pre-hook",
                     "--workspace", str(checkout), "--apply"], arm_dir, env)
        if setup.returncode:
            raise RuntimeError(setup.stderr.decode(errors="replace"))
    ponytail_data = arm_dir / "ponytail-data"
    if "ponytail" in arm:
        ponytail_data.mkdir()
        add_codex_hooks(checkout, {
            "SessionStart": ponytail_codex_hook(args, "ponytail-activate.js", ponytail_data),
            "UserPromptSubmit": ponytail_codex_hook(args, "ponytail-mode-tracker.js", ponytail_data),
        })
    (checkout / ".git" / "info" / "exclude").write_text("\n".join(exclude) + "\n", encoding="utf-8")
    argv = [args.codex, "exec", "--json", "--ephemeral", "--dangerously-bypass-hook-trust",
            "--sandbox", args.sandbox, "--model", args.model,
            "-c", f"model_reasoning_effort={args.effort}", "-c", "features.memories=false",
            "-C", str(checkout), PROMPT]
    started = time.monotonic()
    try:
        process = subprocess.run(argv, cwd=checkout, env=env, stdin=subprocess.DEVNULL,
                                 capture_output=True, timeout=args.timeout)
        stream, cli_exit, timed_out = process.stdout.decode("utf-8", "replace"), process.returncode, False
        (arm_dir / "stderr.txt").write_bytes(process.stderr)
    except subprocess.TimeoutExpired as error:
        stream, cli_exit, timed_out = (error.stdout or b"").decode("utf-8", "replace"), None, True
    elapsed = round(time.monotonic() - started, 1)
    (arm_dir / "stream.jsonl").write_text(stream, encoding="utf-8")
    parsed = parse_codex(stream)
    receipts = list((checkout / ".jevto-store" / "receipts").glob("*.json"))
    ponytail_ran = any(ponytail_data.rglob("*")) if "ponytail" in arm else None
    record = {"harness": "codex", "arm": arm, "rep": rep, "elapsed_s": elapsed, "cli_exit": cli_exit,
              "timed_out": timed_out, **parsed, "jevto_receipts": len(receipts),
              "ponytail_hook_ran": ponytail_ran, **score(checkout, arm_dir, env)}
    record["valid"] = (cli_exit == 0 and not timed_out and record["test_runs"] >= 1
                       and ("jevto" not in arm or record["jevto_receipts"] >= 1)
                       and ("ponytail" not in arm or bool(ponytail_ran)))
    (arm_dir / "record.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


OPENCODE_PONYTAIL_SHIM = "export { default } from {path};\n"


def run_opencode_arm(arm: str, rep: int, args, out: Path) -> dict:
    arm_dir = out / f"{rep}-{arm.replace('+', '-')}"
    checkout = arm_dir / "checkout"
    checkout.mkdir(parents=True)
    build_repo(checkout)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", NO_COLOR="1", GIT_PAGER="cat", PAGER="cat")
    plugins = checkout / ".opencode" / "plugins"
    if "jevto" in arm:
        plugins.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "integrations" / "opencode" / "jevto.js", plugins / "jevto.js")
        env["JEVTO_BIN"] = args.jevto
        env["JEVTO_STORE_DIR"] = str(arm_dir / "store")
    if "ponytail" in arm:
        plugins.mkdir(parents=True, exist_ok=True)
        source = Path(args.ponytail_opencode).resolve().as_uri()
        (plugins / "ponytail.js").write_text(OPENCODE_PONYTAIL_SHIM.replace("{path}", json.dumps(source)), encoding="utf-8")
    (checkout / ".git" / "info" / "exclude").write_text(".opencode/\n", encoding="utf-8")
    argv = [args.opencode, "run", "--format", "json", "--dangerously-skip-permissions", "-m", args.model, PROMPT]
    started = time.monotonic()
    try:
        process = subprocess.run(argv, cwd=checkout, env=env, stdin=subprocess.DEVNULL,
                                 capture_output=True, timeout=args.timeout)
        stream, cli_exit, timed_out = process.stdout.decode("utf-8", "replace"), process.returncode, False
        (arm_dir / "stderr.txt").write_bytes(process.stderr)
    except subprocess.TimeoutExpired as error:
        stream, cli_exit, timed_out = (error.stdout or b"").decode("utf-8", "replace"), None, True
    elapsed = round(time.monotonic() - started, 1)
    (arm_dir / "stream.jsonl").write_text(stream, encoding="utf-8")
    parsed = parse_opencode(stream)
    receipts = list((arm_dir / "store" / "receipts").glob("*.json")) if "jevto" in arm else []
    record = {"harness": "opencode", "arm": arm, "rep": rep, "elapsed_s": elapsed, "cli_exit": cli_exit,
              "timed_out": timed_out, **parsed, "jevto_receipts": len(receipts), **score(checkout, arm_dir, env)}
    record["valid"] = (cli_exit == 0 and not timed_out and not parsed["errors"] and record["test_runs"] >= 1
                       and ("jevto" not in arm or record["jevto_receipts"] >= 1))
    (arm_dir / "record.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def report(records: list[dict], args) -> str:
    title = {"codex": f"Codex CLI ({args.model}, effort {args.effort})", "opencode": f"OpenCode ({args.model})"}[args.harness]
    lines = [
        f"# Agent pilot on {title}: native vs Ponytail vs JevTO",
        "",
        "Task, repository, visible 260-test suite, and 15-case hidden holdout match the Claude pilot. Token fields are the harness's own usage report (Codex `turn.completed`; input includes cached input).",
        "",
        "| Arm | Rep | Valid | Holdout | Visible | Impl. lines | Test runs | Test output B | Input tok | Cached in | Output tok | Time s |",
        "| --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in records:
        lines.append(
            f"| {r['arm']} | {r['rep']} | {'yes' if r['valid'] else 'NO'} | {'pass' if r['holdout_pass'] else 'FAIL'} | "
            f"{'pass' if r['visible_pass'] else 'FAIL'} | {r['implementation_lines']} | {r['test_runs']} | {r['test_output_bytes']:,} | "
            f"{(r['input_tokens'] or 0):,} | {(r['cached_input_tokens'] or 0):,} | {(r['output_tokens'] or 0):,} | {r['elapsed_s']} |")
    lines += ["", "## Medians of valid runs", "", "| Arm | n valid | Holdout passes | Impl. lines | Test output B | Input tok | Uncached in | Output tok | Time s |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for arm in ARMS:
        rows = [r for r in records if r["arm"] == arm and r["valid"]]
        if not rows:
            continue
        med = lambda key: statistics.median(key(r) or 0 for r in rows)
        lines.append(
            f"| {arm} | {len(rows)} | {sum(r['holdout_pass'] for r in rows)}/{len(rows)} | {med(lambda r: r['implementation_lines']):.0f} | "
            f"{med(lambda r: r['test_output_bytes']):,.0f} | {med(lambda r: r['input_tokens']):,.0f} | "
            f"{med(lambda r: (r['input_tokens'] or 0) - (r['cached_input_tokens'] or 0)):,.0f} | {med(lambda r: r['output_tokens']):,.0f} | {med(lambda r: r['elapsed_s']):.1f} |")
    lines += ["", "Invalid runs are listed and excluded from medians. Small n; indicative only.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--harness", choices=["codex", "opencode"], required=True)
    parser.add_argument("--jevto", required=True)
    parser.add_argument("--ponytail", required=True, help="Ponytail plugin directory (contains hooks/)")
    parser.add_argument("--ponytail-opencode", help="Ponytail OpenCode plugin file (.opencode/plugins/ponytail.mjs)")
    parser.add_argument("--codex", default=shutil.which("codex") or "codex")
    parser.add_argument("--opencode", default=shutil.which("opencode") or "opencode")
    parser.add_argument("--node", default=shutil.which("node") or "node")
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--out", default=None)
    parser.add_argument("--work-root", default=None)
    parser.add_argument("--sandbox", default="danger-full-access", help="Codex sandbox mode (the user default here)")
    args = parser.parse_args()
    args.jevto = str(Path(args.jevto).resolve())
    args.model = args.model or ("gpt-6-sol" if args.harness == "codex" else "opencode/big-pickle")
    out_dir = Path(args.out or ROOT / "benchmarks" / "results" / f"{args.harness}-ponytail")
    # Codex's Windows sandbox denies %TEMP%; keep checkouts under the ignored .bench-runs.
    work_root = Path(args.work_root or ROOT / ".bench-runs")
    work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"{args.harness}-pilot-", dir=work_root)).resolve()
    print(f"[pilot] work dir {work}", flush=True)
    arms = [arm for arm in args.arms.split(",") if arm in ARMS]
    runner = run_codex_arm if args.harness == "codex" else run_opencode_arm
    records = []
    for rep in range(1, args.reps + 1):
        for arm in (arms if rep % 2 else list(reversed(arms))):
            print(f"[pilot] rep {rep} {arm} ...", flush=True)
            record = runner(arm, rep, args, work)
            records.append(record)
            print(f"    valid={record['valid']} holdout={record['holdout_pass']} impl={record['implementation_lines']} "
                  f"tests={record['test_runs']} test_bytes={record['test_output_bytes']} in={record['input_tokens']} "
                  f"out={record['output_tokens']} receipts={record['jevto_receipts']} t={record['elapsed_s']}", flush=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "records.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(report(records, args), encoding="utf-8")
    print(f"[pilot] wrote {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
