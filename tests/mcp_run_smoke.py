"""Exercise the opt-in exact-command MCP route against real child processes."""

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
EXE = Path(os.environ["JEVTO_TEST_EXE"]) if os.environ.get("JEVTO_TEST_EXE") else ROOT / "target" / "debug" / ("jevto.exe" if os.name == "nt" else "jevto")


def mcp(arguments, requests, environment):
    process = subprocess.run(
        [str(EXE), *arguments],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True,
        text=True,
        env=environment,
        cwd=ROOT,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    return [json.loads(line) for line in process.stdout.splitlines()]


def request(number, method, params=None):
    value = {"jsonrpc": "2.0", "id": number, "method": method}
    if params is not None:
        value["params"] = params
    return value


def tool_call(number, command_id):
    return request(number, "tools/call", {"name": "jevto_run", "arguments": {"command_id": command_id}})


def main():
    assert EXE.is_file(), "build jevto before running MCP smoke"
    with tempfile.TemporaryDirectory(prefix="jevto-mcp-run-") as directory:
        base = Path(directory)
        workspace = base / "workspace"
        workspace.mkdir()
        store = base / "store"
        policy_path = base / "policy.json"
        success_code = "import sys;sys.stdout.write(''.join('test suite::case_%d ... ok\\n'%i for i in range(80)))"
        failure_code = "import sys;sys.stdout.write('warning: cache invalidated\\nFAILED case_17 assertion=9\\n');sys.stderr.write('verification failed\\n');sys.exit(7)"
        commands = [
            {"id": "passing_tests", "program": sys.executable, "argv": ["-c", success_code]},
            {"id": "failing_tests", "program": sys.executable, "argv": ["-c", failure_code]},
            {"id": "too_large", "program": sys.executable, "argv": ["-c", "import sys;sys.stdout.write('x'*2097152)"]},
            {"id": "binary", "program": sys.executable, "argv": ["-c", "import sys;sys.stdout.buffer.write(bytes([255, 0, 254]))"]},
        ]
        policy_path.write_text(json.dumps({"schema_version": 1, "workspace": str(workspace), "workspace_id": "mcp-smoke", "commands": commands}), encoding="utf-8")
        policy_hash = hashlib.sha256(policy_path.read_bytes()).hexdigest()
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        disabled = mcp(["--store-dir", str(store), "mcp"], [request(1, "tools/list"), tool_call(2, "passing_tests")], environment)
        assert len(disabled[0]["result"]["tools"]) == 3
        assert disabled[1]["result"]["isError"] is True
        assert not store.exists(), "disabled call created a store"

        pinned = mcp(["--store-dir", str(store), "mcp", "--run-policy", str(policy_path), "--run-policy-sha256", policy_hash], [request(1, "tools/list")], environment)
        assert len(pinned[0]["result"]["tools"]) == 4

        results = mcp(
            ["--store-dir", str(store), "mcp", "--run-policy", str(policy_path)],
            [request(1, "tools/list"), tool_call(2, "not_allowed"), tool_call(3, "passing_tests"), tool_call(4, "failing_tests"), tool_call(5, "too_large"), tool_call(6, "binary")],
            environment,
        )
        tools = results[0]["result"]["tools"]
        assert len(tools) == 4 and tools[-1]["name"] == "jevto_run"
        assert tools[-1]["inputSchema"]["properties"]["command_id"]["enum"] == ["passing_tests", "failing_tests", "too_large", "binary"]
        assert results[1]["result"]["isError"] is True
        passing = results[2]["result"]
        failing = results[3]["result"]
        assert passing["isError"] is False and passing["structuredContent"]["child_exit"] == 0
        assert passing["structuredContent"]["delivered_bytes"] < passing["structuredContent"]["raw_bytes"]
        assert failing["isError"] is False and failing["structuredContent"]["child_exit"] == 7
        assert "child_exit=7" in failing["content"][0]["text"]
        assert "FAILED case_17 assertion=9" in failing["content"][0]["text"]
        assert results[4]["result"]["isError"] is True
        assert "MCP output exceeded" in results[4]["result"]["content"][0]["text"]
        assert results[5]["result"]["isError"] is False
        assert base64.b64decode(results[5]["result"]["structuredContent"]["stdout_base64"]) == bytes([255, 0, 254])
        for result, command in [(passing, commands[0]), (failing, commands[1])]:
            data = result["structuredContent"]
            assert data["capture_id"] and data["session_id"]
            native = subprocess.run([command["program"], *command["argv"]], capture_output=True, cwd=workspace, env=environment)
            recalled = subprocess.run([str(EXE), "--store-dir", str(store), "recall", data["capture_id"], "--full"], capture_output=True, env=environment)
            assert recalled.returncode == 0
            assert (recalled.stdout, recalled.stderr) == (native.stdout, native.stderr)
            assert "stdout_base64" not in data and "stderr_base64" not in data
            receipt = json.loads(subprocess.run([str(EXE), "--store-dir", str(store), "report", "--session", data["session_id"], "--json"], capture_output=True, env=environment).stdout)[0]
            assert receipt["child_exit"] == native.returncode

        policy_path.write_bytes(policy_path.read_bytes() + b"\n")
        changed = subprocess.run([str(EXE), "mcp", "--run-policy", str(policy_path), "--run-policy-sha256", policy_hash], input="", capture_output=True, text=True, env=environment)
        assert changed.returncode == 2 and "changed since approval" in changed.stderr

        inside = workspace / "policy.json"
        inside.write_text(policy_path.read_text(encoding="utf-8"), encoding="utf-8")
        refused = subprocess.run([str(EXE), "mcp", "--run-policy", str(inside)], input="", capture_output=True, text=True, env=environment)
        assert refused.returncode == 2 and "outside its existing workspace" in refused.stderr
    print("MCP exact-command smoke passed")


if __name__ == "__main__":
    main()
