"""Prepare frozen offline tasks. This module cannot launch a coding agent or Jev.

Exported evaluators are separated by directory, not by an OS security boundary.
A future campaign must verify agent/evaluator isolation before any paid run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile

from fresh_task_data import TASKS

ROOT = Path(__file__).resolve().parents[1]
ARMS = ("native", "rules", "rules_jev")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def environment():
    # Only toolchain/OS plumbing; no API credentials, agent config or inherited
    # optimization mode. Evaluation has no provider client or agent launcher.
    allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "RUSTUP_HOME", "RUSTUP_TOOLCHAIN", "CARGO_HOME", "RUSTC", "USERPROFILE", "HOME",
               "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PROGRAMDATA", "SYSTEMDRIVE", "LOCALAPPDATA", "APPDATA",
               "LIB", "LIBPATH", "INCLUDE", "VCINSTALLDIR", "VSINSTALLDIR", "VCTOOLSINSTALLDIR", "WINDOWSSDKDIR", "WINDOWSSDKVERSION"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(PYTHONDONTWRITEBYTECODE="1", CARGO_NET_OFFLINE="true")
    return env


def source_identity():
    result = subprocess.run(["git", "--no-optional-locks", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
    files = list((ROOT / "crates").rglob("*.rs")) + [ROOT / "Cargo.toml", ROOT / "Cargo.lock"]
    files += [Path(__file__).with_name(name) for name in ("fresh_tasks.py", "fresh_task_data.py", "receipt_accounting.py", "token_bench.py")]
    hashes = {path.relative_to(ROOT).as_posix(): digest(path.read_bytes()) for path in sorted(files)}
    return {"git_head": result.stdout.strip() if result.returncode == 0 else None,
            "source_sha256": digest(json.dumps(hashes, sort_keys=True).encode()), "files": hashes}


def prepare(destination: Path, seed=20260930):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    for task in TASKS:
        workspace = destination / "workspaces" / task["id"]
        for name, text in task["files"].items():
            write(workspace / name, text)
        verification = "python -m unittest -v" if task["language"] == "python" else "cargo build --offline --target-dir target\npython -m unittest -v"
        write(workspace / "TASK.md", task["prompt"] + "\n\nVisible verification:\n```sh\n" + verification + "\n```\n")
        write(workspace / "test_visible.py", task["visible"])
        write(destination / "evaluators" / task["id"] / "test_hidden.py", task["hidden"])
        for label, files in [("reference", task["reference"]), *[(f"faulty-{i}", files) for i, files in enumerate(task["mutants"])]]:
            for name, text in files.items():
                write(destination / "controls" / task["id"] / label / name, text)
    schedule = [{"task": task["id"], "arm": arm, "rep": 1} for task in TASKS for arm in ARMS]
    random.Random(seed).shuffle(schedule)
    hashes = {path.relative_to(destination).as_posix(): digest(path.read_bytes()) for path in sorted(destination.rglob("*")) if path.is_file()}
    manifest = {
        "schema_version": 1, "seed": seed, "source": source_identity(),
        "tasks": [{"id": t["id"], "language": t["language"]} for t in TASKS],
        "arms": {"native": {"routing": "native", "jev_network": False},
                 "rules": {"routing": "same_host_hook", "mode": "rules", "jev_network": False},
                 "rules_jev": {"routing": "same_host_hook", "mode": "adaptive", "jev_network": "requires separately approved budget and policy"}},
        "schedule": schedule, "files": hashes,
        "cache_policy": "Fresh independent store per task/arm/rep; no preseeded Jev decisions. Within-session cache reuse is measured.",
        "isolation_verified": False, "paid_runs_authorized": False,
        "execution_status": "offline_preparation_only",
        "pending": ["host/model versions", "agent/evaluator isolation proof", "paid budget and repetitions", "host routing and permissions parity"],
        "record_fields": ["task", "arm", "rep", "source_sha256", "candidate_sha256", "host", "model", "usage_raw", "input_total", "input_uncached", "cache_read", "cache_write", "output_tokens", "reasoning_tokens", "coding_cost_known", "coding_cost_complete", "jev_attempted", "jev_successful", "jev_failed", "jev_cached", "jev_unknown", "jev_cost_known", "jev_cost_complete", "elapsed_seconds", "jev_elapsed_ms", "agent_retries", "tool_retries", "recalls", "visible_pass", "hidden_pass", "tests_untouched", "permission_denials", "timed_out", "isolation_verified"],
    }
    write(destination / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def verify(destination: Path):
    destination = destination.resolve()
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    actual = {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file() or path.is_symlink()}
    failures = sorted(actual - set(manifest["files"]) - {"manifest.json"})
    for name, expected in manifest["files"].items():
        path = (destination / name).resolve()
        if not path.is_relative_to(destination) or not path.is_file() or digest(path.read_bytes()) != expected:
            failures.append(name)
    return failures


def run_checks(task, workspace, evaluator):
    env = environment()
    if task["language"] == "rust":
        cargo = shutil.which("cargo", path=env.get("PATH"))
        if not cargo:
            raise RuntimeError("An installed Rust toolchain is required; nothing is installed automatically")
        built = subprocess.run([cargo, "build", "--offline", "--jobs", "1", "--target-dir", str(workspace / "target")], cwd=workspace, env=env, capture_output=True, timeout=60)
        if built.returncode:
            return False, False, "Rust compile failed: " + built.stderr.decode(errors="replace")[-1200:]
    visible = subprocess.run([sys.executable, "-I", "-B", str(workspace / "test_visible.py")], cwd=workspace, env=env, capture_output=True, timeout=20)
    # The candidate agent has already exited before a real evaluator runs.
    env["JEVTO_EVAL_WORKSPACE"] = str(workspace)
    hidden = subprocess.run([sys.executable, "-I", "-B", str(evaluator)], cwd=evaluator.parent, env=env, capture_output=True, timeout=20)
    detail = (visible.stderr + hidden.stderr).decode(errors="replace")[-2000:]
    return visible.returncode == 0, hidden.returncode == 0, detail


def self_test():
    results = []
    with tempfile.TemporaryDirectory(prefix="jevto-fresh-controls-") as directory:
        root = Path(directory)
        for task in TASKS:
            evaluator = root / "evaluators" / task["id"] / "test_hidden.py"
            write(evaluator, task["hidden"])
            row = {"task": task["id"], "faulty_visible_passes": [], "faulty_hidden_rejected": []}
            for index, files in enumerate([task["reference"], *task["mutants"]]):
                workspace = root / "workspaces" / task["id"] / str(index)
                for name, text in files.items():
                    write(workspace / name, text)
                write(workspace / "test_visible.py", task["visible"])
                visible, hidden, detail = run_checks(task, workspace, evaluator)
                if index == 0:
                    row.update(reference_visible_pass=visible, reference_hidden_pass=hidden)
                    if not (visible and hidden):
                        row["reference_failure"] = detail
                else:
                    row["faulty_visible_passes"].append(visible)
                    row["faulty_hidden_rejected"].append(not hidden)
                    if not visible or hidden:
                        row.setdefault("control_failures", []).append(detail)
            results.append(row)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", type=Path)
    group.add_argument("--verify", type=Path)
    group.add_argument("--self-test", action="store_true")
    parser.add_argument("--seed", type=int, default=20260930)
    args = parser.parse_args()
    if args.prepare:
        manifest = prepare(args.prepare, args.seed)
        print(json.dumps({"manifest": str(args.prepare / "manifest.json"), "manifest_sha256": digest((args.prepare / "manifest.json").read_bytes()), "tasks": len(TASKS), "paid_runs_authorized": manifest["paid_runs_authorized"]}))
        return 0
    if args.verify:
        failures = verify(args.verify)
        print(json.dumps({"changed_or_missing": failures}))
        return int(bool(failures))
    results = self_test()
    print(json.dumps({"kind": "offline_fixture_controls_not_agent_results", "results": results}, indent=2))
    return int(any(not (r["reference_visible_pass"] and r["reference_hidden_pass"] and all(r["faulty_visible_passes"]) and all(r["faulty_hidden_rejected"])) for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
