"""Exercise the built CLI against real child processes and byte-exact recall."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE = ROOT / "target" / "debug" / ("jevto.exe" if os.name == "nt" else "jevto")


def call(arguments, environment):
    return subprocess.run(arguments, cwd=ROOT, env=environment, capture_output=True)


def main():
    assert EXECUTABLE.is_file(), "build jevto before running the smoke check"
    with tempfile.TemporaryDirectory(prefix="jevto-smoke-") as temporary:
        environment = os.environ.copy()
        environment["JEVTO_STORE_DIR"] = temporary
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        command = [sys.executable, str(ROOT / "benchmarks" / "fixtures" / "quiet_warning" / "verify.py")]
        native = call(command, environment)
        wrapped = call([str(EXECUTABLE), "run", "--session", "smoke", "--", *command], environment)
        assert native.returncode == wrapped.returncode == 1
        assert b"warning: cache invalidated" in wrapped.stdout
        assert b"jevto exit=1" in wrapped.stdout
        assert b"recall: `" in wrapped.stdout
        match = re.search(rb"recall ([0-9a-f-]{8,36})", wrapped.stdout)
        assert match, wrapped.stdout.decode(errors="replace")
        capture_id = match.group(1).decode()
        recovered = call([str(EXECUTABLE), "recall", capture_id, "--full"], environment)
        assert recovered.returncode == 0
        assert (recovered.stdout, recovered.stderr) == (native.stdout, native.stderr)
        report = call([str(EXECUTABLE), "report", "--session", "smoke", "--json"], environment)
        receipts = json.loads(report.stdout)
        assert report.returncode == 0 and len(receipts) == 1
        assert receipts[0]["coverage"][0]["replaced"] is True
        assert receipts[0].get("provider_usage") is None
        # Views print the short ID; receipts keep the full one.
        assert len(capture_id) == 8 and receipts[0]["capture_id"].startswith(capture_id)
        assert receipts[0]["recalls"] == 1
        assert receipts[0]["recalled_bytes"] == len(native.stdout) + len(native.stderr)
        report_text = call([str(EXECUTABLE), "report", "--session", "smoke"], environment)
        assert report_text.returncode == 0
        assert b"Jev response-reported cost: none (no Jev calls)" in report_text.stdout

        lean = [sys.executable, str(ROOT / "benchmarks" / "fixtures" / "already_lean" / "verify.py")]
        lean_native = call(lean, environment)
        lean_wrapped = call([str(EXECUTABLE), "run", "--session", "lean", "--", *lean], environment)
        assert lean_native.returncode == lean_wrapped.returncode == 1
        assert (lean_native.stdout, lean_native.stderr) == (lean_wrapped.stdout, lean_wrapped.stderr)
        lean_receipts = json.loads(call([str(EXECUTABLE), "report", "--session", "lean", "--json"], environment).stdout)
        assert lean_receipts[0]["coverage"][0]["bypass_reason"] == "already_lean"

        denied = call([str(EXECUTABLE), "run", "--mode", "adaptive", "--session", "no-jev", "--", *command], environment)
        assert denied.returncode == 1
        assert b"warning: cache invalidated" in denied.stdout
        denied_receipts = json.loads(call([str(EXECUTABLE), "report", "--session", "no-jev", "--json"], environment).stdout)
        assert denied_receipts[0]["mode"] == "deterministic"
        assert denied_receipts[0]["adaptive_fallback"] == "adaptive mode requires --task-file"
        assert denied_receipts[0].get("jev_usage") is None

        task_file = Path(temporary) / "task.json"
        remote_policy = Path(temporary) / "remote-policy.json"
        remote_policy.write_text(json.dumps({
            "schema_version": 1,
            "data_classification": "synthetic",
            "allow_search_snippets": True,
            "allowed_roots": [str(ROOT)],
        }), encoding="utf-8")
        task_file.write_text(json.dumps({"schema_version": 1, "session_id": "privacy", "workspace_id": "smoke", "revision": 0, "goal": "Fix cache behavior", "trusted_constraints": []}), encoding="utf-8")
        offline_environment = environment.copy()
        offline_environment["OPENROUTER_API_KEY"] = "invalid-key-that-must-never-be-used"
        offline = call([str(EXECUTABLE), "run", "--mode", "adaptive", "--session", "privacy", "--workspace-id", "smoke", "--task-file", str(task_file), "--", *command], offline_environment)
        assert offline.returncode == 1
        offline_receipt = json.loads(call([str(EXECUTABLE), "report", "--session", "privacy", "--json"], offline_environment).stdout)[0]
        assert offline_receipt["mode"] == "deterministic"
        assert offline_receipt["adaptive_fallback"] == "remote_jev_not_authorized"
        assert offline_receipt.get("jev_call_attempted") is None

        task_file.write_text(json.dumps({"schema_version": 1, "session_id": "secret", "workspace_id": "smoke", "revision": 0, "goal": "Use sk-or-private-data", "trusted_constraints": []}), encoding="utf-8")
        privacy_denied = call([str(EXECUTABLE), "run", "--mode", "adaptive", "--allow-remote-jev", "--remote-policy", str(remote_policy), "--session", "secret", "--workspace-id", "smoke", "--task-file", str(task_file), "--", *command], offline_environment)
        assert privacy_denied.returncode == 1
        secret_receipt = json.loads(call([str(EXECUTABLE), "report", "--session", "secret", "--json"], offline_environment).stdout)[0]
        assert secret_receipt["adaptive_fallback"] == "privacy_denied_task"
        assert secret_receipt.get("jev_call_attempted") is None

        if shutil.which("rg"):
            search_workspace = Path(temporary) / "adaptive-preview"
            search_source = search_workspace / "src"
            search_source.mkdir(parents=True)
            for index in range(24):
                (search_source / f"candidate_{index:02}.py").write_text(
                    "".join(
                        f"# FIXME_JEVTO candidate {index} evidence line {line}\n"
                        for line in range(5)
                    ),
                    encoding="utf-8",
                )
            preview_task = Path(temporary) / "preview-task.json"
            preview_task.write_text(json.dumps({
                "schema_version": 1, "session_id": "search-preview",
                "workspace_id": "search-preview", "revision": 0,
                "goal": "Find candidate 17 evidence", "trusted_constraints": [],
            }), encoding="utf-8")
            preview_policy = Path(temporary) / "preview-policy.json"
            preview_policy.write_text(json.dumps({
                "schema_version": 1, "data_classification": "synthetic",
                "allow_search_snippets": True,
                "allowed_roots": [str(search_workspace)],
            }), encoding="utf-8")
            preview = subprocess.run([
                str(EXECUTABLE), "run", "--mode", "adaptive",
                "--task-file", str(preview_task), "--remote-policy", str(preview_policy),
                "--jev-preview", "--", "rg", "-n", "-H", "FIXME_JEVTO", "src",
            ], cwd=search_workspace, env=offline_environment, capture_output=True)
            assert preview.returncode == 0
            assert b"Jev outbound preview (no request sent)" in preview.stderr
            assert b"invalid-key-that-must-never-be-used" not in preview.stderr
            preview_receipt = json.loads(subprocess.run([
                str(EXECUTABLE), "report", "--session", "search-preview", "--json",
            ], cwd=search_workspace, env=offline_environment, capture_output=True, check=True).stdout)[0]
            assert preview_receipt["adaptive_fallback"] == "jev_preview_only"
            assert preview_receipt.get("jev_call_attempted") is None

        inventory = [
            sys.executable,
            "-c",
            "import sys;sys.stdout.write(''.join('test suite::case_%d ... ok\\n'%i for i in range(80)))",
        ]
        frame = {
            "schema_version": 1,
            "session_id": "revision",
            "workspace_id": "smoke",
            "revision": 0,
            "goal": "Keep suite::case_17 visible",
            "trusted_constraints": [],
        }
        frame_args = [
            str(EXECUTABLE), "run", "--session", "revision", "--workspace-id", "smoke",
            "--task-file", str(task_file),
        ]
        seen_runs = set()

        def new_revision_receipt():
            receipts = json.loads(call(
                [str(EXECUTABLE), "report", "--session", "revision", "--json"],
                environment,
            ).stdout)
            new = [receipt for receipt in receipts if receipt["run_id"] not in seen_runs]
            assert len(new) == 1
            seen_runs.add(new[0]["run_id"])
            return new[0]

        task_file.write_text(json.dumps(frame), encoding="utf-8")
        first_frame = call([*frame_args, "--", *inventory], environment)
        assert first_frame.returncode == 0
        assert b"suite::case_17 ... ok" in first_frame.stdout
        assert new_revision_receipt()["task_revision"] == 0

        frame["goal"] = "Keep suite::case_18 visible"
        task_file.write_text(json.dumps(frame), encoding="utf-8")
        conflicted = call(
            [*frame_args, "--mode", "adaptive", "--allow-remote-jev", "--", *inventory],
            offline_environment,
        )
        assert conflicted.returncode == 0
        conflict_receipt = new_revision_receipt()
        assert conflict_receipt["task_frame_fallback"].startswith("task_revision_conflict:")
        assert conflict_receipt["adaptive_fallback"].startswith("task_revision_conflict:")
        assert conflict_receipt.get("jev_call_attempted") is None
        assert conflict_receipt.get("task_revision") is None

        frame["revision"] = 1
        task_file.write_text(json.dumps(frame), encoding="utf-8")
        revised = call([*frame_args, "--", *inventory], environment)
        assert revised.returncode == 0
        assert b"suite::case_18 ... ok" in revised.stdout
        assert new_revision_receipt()["task_revision"] == 1

        frame["revision"] = 0
        task_file.write_text(json.dumps(frame), encoding="utf-8")
        stale = call([*frame_args, "--", *inventory], environment)
        assert stale.returncode == 0
        stale_receipt = new_revision_receipt()
        assert stale_receipt["task_frame_fallback"].startswith("stale_task_revision:")
        assert stale_receipt.get("task_revision") is None

        inflight_frame = {
            **frame,
            "session_id": "inflight",
            "revision": 0,
            "goal": "Keep suite::case_17 visible",
        }
        inflight_file = Path(temporary) / "inflight-task.json"
        marker = Path(temporary) / "inflight-child-started"
        inflight_file.write_text(json.dumps(inflight_frame), encoding="utf-8")
        waiting_child = (
            "from pathlib import Path; import sys,time;"
            f"Path({str(marker)!r}).write_text('started');"
            "time.sleep(3);"
            "sys.stdout.write(''.join('test suite::case_%d ... ok\\n'%i for i in range(80)))"
        )
        with subprocess.Popen(
            [
                str(EXECUTABLE), "run", "--mode", "adaptive", "--allow-remote-jev",
                "--session", "inflight", "--workspace-id", "smoke",
                "--task-file", str(inflight_file), "--", sys.executable, "-c", waiting_child,
            ],
            cwd=ROOT,
            env=offline_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as running:
            deadline = time.monotonic() + 5
            while not marker.exists() and running.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.exists(), "long-running child did not start"
            inflight_frame["revision"] = 1
            inflight_frame["goal"] = "Keep suite::case_18 visible"
            inflight_file.write_text(json.dumps(inflight_frame), encoding="utf-8")
            newer = call(
                [
                    str(EXECUTABLE), "run", "--session", "inflight", "--workspace-id", "smoke",
                    "--task-file", str(inflight_file), "--", sys.executable, "-c", "print('new revision accepted')",
                ],
                environment,
            )
            assert newer.returncode == 0
            old_stdout, old_stderr = running.communicate(timeout=10)
            assert running.returncode == 0, old_stderr.decode(errors="replace")
            assert old_stdout
        inflight_receipts = json.loads(call(
            [str(EXECUTABLE), "report", "--session", "inflight", "--json"],
            environment,
        ).stdout)
        assert {receipt.get("task_revision") for receipt in inflight_receipts} == {None, 1}
        old_receipt = next(receipt for receipt in inflight_receipts if receipt.get("task_revision") is None)
        assert old_receipt["task_frame_fallback"].startswith("stale_task_revision:")
        assert old_receipt["adaptive_fallback"].startswith("stale_task_revision:")
        assert old_receipt.get("jev_call_attempted") is None
        old_recall = call(
            [str(EXECUTABLE), "recall", old_receipt["capture_id"], "--full"],
            environment,
        )
        assert old_recall.returncode == 0
        assert b"suite::case_17 ... ok" in old_recall.stdout

        ripgrep = shutil.which("rg")
        if ripgrep:
            search_file = Path(temporary) / "needle.rs"
            search_file.write_text(
                'let needle = cache.lookup(user_id).unwrap_or_default();\n' * 80,
                encoding="utf-8",
            )
            search = [ripgrep, "-n", "-H", "needle", str(search_file)]
            search_native = call(search, environment)
            search_wrapped = call(
                [str(EXECUTABLE), "run", "--session", "search", "--", *search],
                environment,
            )
            assert search_native.returncode == search_wrapped.returncode == 0
            assert b"omitted=76" in search_wrapped.stdout, (ripgrep, search_wrapped.stdout.decode(errors="replace")[:2000])
            assert b"needle.rs:1:" in search_wrapped.stdout
            assert b"needle.rs:80:" in search_wrapped.stdout
            assert b"needle.rs:40:" not in search_wrapped.stdout
            search_id = re.search(rb"recall ([0-9a-f-]{8,36})", search_wrapped.stdout)
            assert search_id
            search_recall = call(
                [str(EXECUTABLE), "recall", search_id.group(1).decode(), "--full"],
                environment,
            )
            assert (search_recall.stdout, search_recall.stderr) == (
                search_native.stdout,
                search_native.stderr,
            )

        long_store = Path(temporary) / ("nested" * 4) / ("store" * 4)
        long_store.mkdir(parents=True)
        for count, expected_reason in (
            (20, "rendered_pack_no_net_reduction"),
            (80, "rendered_pack_exceeds_budget"),
        ):
            inventory = [
                sys.executable,
                "-c",
                f"import sys;sys.stdout.write(''.join('test case_%02d_xxxx ... ok\\n'%i for i in range({count})))",
            ]
            original = call(inventory, environment)
            budget = 65536
            if count == 80:
                probe = call(
                    [str(EXECUTABLE), "--store-dir", str(long_store), "run",
                     "--session", "long-prefix-budget-probe", "--format", "verbose", "--budget", "65536", "--", *inventory],
                    environment,
                )
                assert probe.returncode == 0 and b"capture_id: " in probe.stdout
                assert len(probe.stdout) < len(original.stdout)
                budget = len(probe.stdout) - 1
            packs_before = {path.name for path in (long_store / "packs").glob("*.json")}
            session = f"long-prefix-{count}"
            selected = call(
                [
                    str(EXECUTABLE),
                    "--store-dir",
                    str(long_store),
                    "run",
                    "--session",
                    session,
                    "--format",
                    "verbose",
                    "--budget",
                    str(budget),
                    "--",
                    *inventory,
                ],
                environment,
            )
            assert selected.returncode == original.returncode == 0
            assert (selected.stdout, selected.stderr) == (
                original.stdout,
                original.stderr,
            )
            receipt = json.loads(
                call(
                    [str(EXECUTABLE), "--store-dir", str(long_store), "report", "--session", session, "--json"],
                    environment,
                ).stdout
            )[0]
            assert receipt["coverage"][0]["replaced"] is False
            assert receipt["coverage"][0]["bypass_reason"] == expected_reason
            assert receipt["delivered_bytes"] == len(original.stdout)
            assert {path.name for path in (long_store / "packs").glob("*.json")} == packs_before

        doctor = json.loads(call([str(EXECUTABLE), "doctor", "--json"], environment).stdout)
        assert doctor["network_default"] == "off"
        assert doctor["adaptive_jev"].startswith("auto_when_openrouter_key_present_workspace_scoped_choice_noul_score")
        assert any(path["tool_path"].startswith("Claude Code project PreToolUse") for path in doctor["paths"])
        assert doctor["output_formats"] == ["compact", "verbose"]
        assert [item["host"] for item in doctor["host_registrations"]] == ["codex", "cursor", "claude"]
        assert all(item["state"] in {
            "current", "different_executable", "missing_executable", "missing",
            "unmanaged", "conflict", "unreadable",
        } for item in doctor["host_registrations"])
        codex_mcp = next(path for path in doctor["paths"] if path["tool_path"] == "Codex 0.158.0-alpha.2.1 JevTO MCP status/recall/review")
        assert codex_mcp["replacement"] == "unsupported"
        assert codex_mcp["tool_calls"] == "model-driven success on named version"
        assert "successful_cargo_progress" in doctor["deterministic_formats"]
        cargo_check_route = next(path for path in doctor["paths"] if path["tool_path"] == "Codex 0.158.0-alpha.2.1 project PreToolUse Bash hook: plain Windows PowerShell cargo check")
        assert cargo_check_route["replacement"].startswith("observed on a successful 24-crate check")
        assert "outer exit 101" in cargo_check_route["failed_command"]
        cargo_build_route = next(path for path in doctor["paths"] if path["tool_path"] == "Codex 0.158.0-alpha.2.1 project PreToolUse Bash hook: plain Windows PowerShell cargo build")
        assert cargo_build_route["replacement"].startswith("observed on a successful 24-crate build")
        assert "outer exit 101" in cargo_build_route["failed_command"]

        bounded_environment = environment.copy()
        bounded_environment["JEVTO_MAX_CAPTURE_BYTES"] = "128"
        bounded_doctor = json.loads(call([str(EXECUTABLE), "doctor", "--json"], bounded_environment).stdout)
        assert bounded_doctor["store"]["run_capture_limit_bytes"] == 128
        large_command = [
            sys.executable,
            "-c",
            "import sys;sys.stdout.buffer.write(bytes(range(256))*512);"
            "sys.stderr.buffer.write(b'ERR\\x00'*32768);sys.exit(7)",
        ]
        large_native = call(large_command, bounded_environment)
        large_wrapped = call(
            [str(EXECUTABLE), "run", "--mode", "adaptive", "--session", "bounded", "--", *large_command],
            bounded_environment,
        )
        assert large_native.returncode == large_wrapped.returncode == 7
        assert (large_wrapped.stdout, large_wrapped.stderr) == (
            large_native.stdout,
            large_native.stderr,
        )
        bounded_receipt = json.loads(call(
            [str(EXECUTABLE), "report", "--session", "bounded", "--json"],
            bounded_environment,
        ).stdout)[0]
        assert bounded_receipt["coverage"][0] == {
            "tool_path": "explicit_cli_run",
            "captured": False,
            "replaced": False,
            "bypass_reason": "capture_limit_exceeded",
        }
        assert bounded_receipt["raw_bytes"] == len(large_native.stdout) + len(large_native.stderr)
        assert bounded_receipt["delivered_bytes"] == bounded_receipt["raw_bytes"]
        assert bounded_receipt["mode"] == "passthrough"
        assert bounded_receipt["adaptive_fallback"] == "capture_limit_exceeded"
        assert bounded_receipt["child_exit"] == 7
        assert bounded_receipt.get("capture_id") is None
        assert bounded_receipt.get("jev_call_attempted") is None

        small_command = [
            sys.executable,
            "-c",
            "import sys;sys.stdout.buffer.write(b'A'*80);sys.stderr.buffer.write(b'B'*32)",
        ]
        small_wrapped = call(
            [str(EXECUTABLE), "run", "--session", "bounded-small", "--", *small_command],
            bounded_environment,
        )
        assert small_wrapped.returncode == 0
        assert small_wrapped.stdout == b"A" * 80 and small_wrapped.stderr == b"B" * 32
        small_receipt = json.loads(call(
            [str(EXECUTABLE), "report", "--session", "bounded-small", "--json"],
            bounded_environment,
        ).stdout)[0]
        assert small_receipt["coverage"][0]["captured"] is True
        assert small_receipt["raw_bytes"] == 112

        streaming_command = [
            sys.executable,
            "-c",
            "import sys,time;sys.stdout.buffer.write(b'X'*256);sys.stdout.flush();"
            "time.sleep(2);sys.stdout.buffer.write(b'Y'*16)",
        ]
        with subprocess.Popen(
            [str(EXECUTABLE), "run", "--session", "streaming", "--", *streaming_command],
            cwd=ROOT,
            env=bounded_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as streaming:
            assert streaming.stdout is not None
            prefix = streaming.stdout.read(256)
            assert prefix == b"X" * 256
            assert streaming.poll() is None, "oversized output was held until the child exited"
            tail, error = streaming.communicate(timeout=5)
            assert streaming.returncode == 0
            assert tail == b"Y" * 16 and error == b"", (tail, error)

        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "jevto_status", "arguments": {"session_id": "smoke"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "jevto_run", "arguments": {}}},
        ]
        mcp = subprocess.run([str(EXECUTABLE), "mcp"], cwd=ROOT, env=environment, input=("\n".join(json.dumps(request) for request in requests) + "\n").encode(), capture_output=True, timeout=5)
        assert mcp.returncode == 0, mcp.stderr.decode(errors="replace")
        responses = [json.loads(line) for line in mcp.stdout.splitlines()]
        assert responses[0]["result"]["serverInfo"]["name"] == "jevto"
        assert {tool["name"] for tool in responses[1]["result"]["tools"]} == {"jevto_status", "jevto_recall", "jevto_review"}
        assert responses[2]["result"]["structuredContent"]["runs"] == 1
        assert responses[2]["result"]["structuredContent"]["captured_runs"] == 1
        assert responses[2]["result"]["structuredContent"]["run_details"][0]["child_exit"] == 1
        assert responses[3]["result"]["isError"] is True

        if os.name == "nt":
            large_exit = call([str(EXECUTABLE), "run", "--", sys.executable, "-c", "import sys;sys.exit(301)"], environment)
            assert large_exit.returncode == 301, large_exit.returncode
        print("CLI smoke passed: child status, exact recall, bounded streaming, task revision fallback, adaptive search preview, offline fallback, and payload sizing; search checked where rg is installed")


if __name__ == "__main__":
    main()
