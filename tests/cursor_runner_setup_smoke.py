"""Exercise the project Cursor runner installer with a real MCP child."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
EXE = Path(os.environ["JEVTO_TEST_EXE"]) if os.environ.get("JEVTO_TEST_EXE") else ROOT / "target" / "debug" / ("jevto.exe" if os.name == "nt" else "jevto")
BOM = b"\xef\xbb\xbf"


def cli(store, *args):
    return subprocess.run([str(EXE), "--store-dir", str(store), *map(str, args)], capture_output=True, text=True)


def config_value(path):
    return json.loads(path.read_bytes().removeprefix(BOM))


def cursor_command(launcher, *args):
    if launcher.suffix.lower() == ".ps1":
        return [str(launcher.with_name("node.exe")), str(launcher.with_name("index.js")), *args]
    return [str(launcher), *args]


def main():
    assert EXE.is_file(), "build jevto before running the Cursor runner smoke"
    with tempfile.TemporaryDirectory(prefix="jevto-cursor-runner-") as directory:
        base = Path(directory)
        workspace = base / "workspace"
        cursor_dir = workspace / ".cursor"
        cursor_dir.mkdir(parents=True)
        config = cursor_dir / "mcp.json"
        original = BOM + b'{"mcpServers":{"other":{"command":"keep"}},"unrelated":true}'
        config.write_bytes(original)
        store = base / "store"
        policy_path = base / "policy.json"
        policy_path.write_text(json.dumps({"schema_version": 1, "workspace": str(workspace), "workspace_id": "cursor-setup-smoke", "commands": [{"id": "verify", "program": sys.executable, "argv": ["-c", "print('test suite::case_0 ... ok')"]}]}), encoding="utf-8")
        policy_hash = hashlib.sha256(policy_path.read_bytes()).hexdigest()

        preview = cli(store, "init-cursor-runner", "--workspace", workspace, "--policy", policy_path)
        assert preview.returncode == 0 and "Preview only" in preview.stdout, preview.stderr
        assert policy_hash in preview.stdout and config.read_bytes() == original
        assert not store.exists()

        applied = cli(store, "init-cursor-runner", "--workspace", workspace, "--policy", policy_path, "--apply")
        assert applied.returncode == 0 and "Installed" in applied.stdout, applied.stderr
        assert config.read_bytes().startswith(BOM)
        parsed = config_value(config)
        assert parsed["mcpServers"]["other"] == {"command": "keep"}
        assert parsed["unrelated"] is True
        entry = parsed["mcpServers"]["jevto_exec"]
        assert entry["args"][-1] == policy_hash
        payload = "\n".join(json.dumps(item) for item in [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "jevto_run", "arguments": {"command_id": "verify"}}},
        ]) + "\n"
        served = subprocess.run([entry["command"], *entry["args"]], input=payload, capture_output=True, text=True)
        assert served.returncode == 0, served.stderr
        responses = [json.loads(line) for line in served.stdout.splitlines() if line.strip()]
        assert len(responses[0]["result"]["tools"]) == 4
        result = responses[1]["result"]
        assert result["isError"] is False and result["structuredContent"]["child_exit"] == 0
        assert "case_0" in result["content"][0]["text"]
        capture_id = result["structuredContent"]["capture_id"]
        recalled = cli(store, "recall", capture_id, "--full")
        assert recalled.returncode == 0 and recalled.stdout == "test suite::case_0 ... ok\n"

        cursor_cli = os.environ.get("JEVTO_CURSOR_CLI")
        if cursor_cli:
            launcher = Path(cursor_cli)
            try:
                approved = subprocess.run(cursor_command(launcher, "mcp", "enable", "jevto_exec"), cwd=workspace, capture_output=True, text=True, timeout=25)
                assert approved.returncode == 0, approved.stdout + approved.stderr
                listed = subprocess.run(cursor_command(launcher, "mcp", "list-tools", "jevto_exec"), cwd=workspace, capture_output=True, text=True, timeout=35)
                assert listed.returncode == 0 and "jevto_run" in listed.stdout, listed.stdout + listed.stderr
            finally:
                disabled = subprocess.run(cursor_command(launcher, "mcp", "disable", "jevto_exec"), cwd=workspace, capture_output=True, text=True, timeout=25)
                assert disabled.returncode == 0, disabled.stdout + disabled.stderr

        policy_path.write_bytes(policy_path.read_bytes() + b"\n")
        refused = subprocess.run([entry["command"], *entry["args"]], input="", capture_output=True, text=True)
        assert refused.returncode == 2 and "changed since approval" in refused.stderr

        removal_preview = cli(store, "disable-cursor-runner", "--workspace", workspace)
        assert removal_preview.returncode == 0 and "Preview only" in removal_preview.stdout
        assert "jevto_exec" in config_value(config)["mcpServers"]
        removed = cli(store, "disable-cursor-runner", "--workspace", workspace, "--apply")
        assert removed.returncode == 0 and "Disabled" in removed.stdout, removed.stderr
        after = config_value(config)
        assert config.read_bytes().startswith(BOM)
        assert "jevto_exec" not in after["mcpServers"]
        assert after["mcpServers"]["other"] == {"command": "keep"}
        assert after["unrelated"] is True
        assert cli(store, "recall", capture_id, "--full").returncode == 0

    print("Cursor runner setup smoke passed")


if __name__ == "__main__":
    main()
