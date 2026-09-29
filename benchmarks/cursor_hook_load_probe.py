"""Check whether the named Cursor CLI loads trusted project hooks at all."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import cursor_explicit_route
import pilot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cursor-cli", type=Path, required=True)
    parser.add_argument("--jevto", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    args = parser.parse_args()
    launcher = args.cursor_cli.resolve()
    preflight = {
        "cursor_launcher_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
        "jevto_sha256": hashlib.sha256(args.jevto.resolve().read_bytes()).hexdigest(),
        "model_requested": pilot.MODEL,
        "model_listing": "not repeated; this diagnostic validates the stream identity",
    }
    destination = pilot.ROOT / ".bench-runs" / datetime.now(timezone.utc).strftime("cursor-hook-load-%Y%m%dT%H%M%SZ")
    workspace = destination / "workspace"
    hook_dir = workspace / ".cursor"
    hook_dir.mkdir(parents=True)
    names = pilot.configured_mcp_names()
    pilot.disable_project_mcps(launcher, workspace, names)
    (hook_dir / "observe.py").write_text(
        "import json, pathlib, sys\n"
        "event = json.load(sys.stdin)\n"
        "name = event.get('hook_event_name', 'unknown')\n"
        "path = pathlib.Path(__file__).with_name('observed-' + name + '.json')\n"
        "path.write_text(json.dumps({'event': name, 'keys': sorted(event)}), encoding='utf-8')\n"
        "print(json.dumps({'continue': True} if name == 'beforeSubmitPrompt' else {}))\n",
        encoding="utf-8",
    )
    (hook_dir / "hooks.json").write_text(json.dumps({
        "version": 1,
        "hooks": {
            "workspaceOpen": [{"command": "python .cursor/observe.py"}],
            "beforeSubmitPrompt": [{"command": "python .cursor/observe.py"}],
        },
    }, indent=2), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True, timeout=10)
    subprocess.run(["git", "add", ".cursor/hooks.json", ".cursor/observe.py"], cwd=workspace, check=True, timeout=10)
    subprocess.run([
        "git", "-c", "user.name=JevTO Probe", "-c", "user.email=jevto-probe@invalid",
        "commit", "-qm", "Disposable hook fixture",
    ], cwd=workspace, check=True, timeout=10)
    prompt = "Reply with exactly JEVTO_HOOK_LOAD_DONE. Do not call a tool."
    environment = os.environ.copy()
    environment.update({"NO_COLOR": "1", "GIT_PAGER": "cat", "PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"})
    command = cursor_explicit_route.direct_launcher(
        launcher, "-p", "--force", "--trust", "--sandbox",
        "disabled" if os.name == "nt" else "enabled", "--output-format", "stream-json",
        "--model", pilot.MODEL, "--workspace", str(workspace), prompt,
    )
    (destination / "manifest.json").write_text(json.dumps({
        "kind": "cursor_project_hook_load_diagnostic",
        "preflight": preflight,
        "model_requested": pilot.MODEL,
        "workspace": str(workspace),
        "prompt": prompt,
        "normal_user_mcp_servers_disabled_for_workspace": names,
        "timeout_seconds": args.timeout_seconds,
    }, indent=2), encoding="utf-8")
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
            cli_exit = process.wait(timeout=args.timeout_seconds)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            cursor_explicit_route.stop_process(process)
            cli_exit = None
    observations = pilot.stream_observations(stream_path.read_text(encoding="utf-8", errors="replace"))
    if observations["reported_model"] not in (None, pilot.MODEL, "Grok 4.7 Extra High", "Grok 4.7  Extra High", "Grok 4.7 256K Extra High"):
        raise RuntimeError(f"Cursor substituted model {observations['reported_model']}; invalid run")
    events = sorted(path.name for path in hook_dir.glob("observed-*.json"))
    result = {
        "cli_exit": cli_exit,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "transport": observations,
        "observed_hook_files": events,
        "workspace_open_observed": "observed-workspaceOpen.json" in events,
        "before_submit_observed": "observed-beforeSubmitPrompt.json" in events,
        "valid_host_turn": (
            not timed_out
            and cli_exit == 0
            and observations["result_event_count"] == 1
            and observations["result_subtype"] == "success"
            and observations["reported_model"] in (
                pilot.MODEL, "Grok 4.7 Extra High", "Grok 4.7  Extra High", "Grok 4.7 256K Extra High"
            )
        ),
    }
    (destination / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(destination)
    return 0 if result["valid_host_turn"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
