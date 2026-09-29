"""Exercise the automatic Claude Code route without a model call.

Installs the project hooks into a disposable workspace, feeds the same JSON
events Claude Code sends, runs the rewritten command through bash, and checks
exit status, selected output, byte-exact recall, and clean removal.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE = ROOT / "target" / "debug" / ("jevto.exe" if os.name == "nt" else "jevto")


def bash() -> str:
    if os.name == "nt":
        for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
            if Path(candidate).is_file():
                return candidate
    found = shutil.which("bash")
    assert found, "bash is required"
    return found


def hook(args, event, env):
    result = subprocess.run([str(EXECUTABLE), *args], input=json.dumps(event).encode(), capture_output=True, env=env, check=True)
    return result.stdout.decode().strip()


def main():
    assert EXECUTABLE.is_file(), "build jevto before running the smoke check"
    with tempfile.TemporaryDirectory(prefix="jevto-claude-auto-") as temporary:
        root = Path(temporary)
        workspace = root / "project"
        workspace.mkdir()
        store = root / "store"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        tests = ["import unittest", "", "class T(unittest.TestCase):"]
        for n in range(60):
            tests.append(f"    def test_ok_{n:02}(self):\n        self.assertTrue(True)\n")
        tests.append("    def test_parse_rejects_space(self):\n        self.assertEqual(1, 2, 'leading space must fail')\n")
        (workspace / "test_parse.py").write_text("\n".join(tests), encoding="utf-8")
        settings = workspace / ".claude" / "settings.local.json"
        settings.parent.mkdir()
        settings.write_text('{"permissions": {"allow": ["Read"]}}\n', encoding="utf-8")

        base = [str(EXECUTABLE), "--store-dir", str(store)]
        preview = subprocess.run([*base, "init-claude-auto", "--workspace", str(workspace)], capture_output=True, text=True, check=True)
        assert "Preview only" in preview.stdout
        subprocess.run([*base, "init-claude-auto", "--workspace", str(workspace), "--apply"], capture_output=True, check=True)
        installed = json.loads(settings.read_text(encoding="utf-8"))
        assert installed["permissions"]["allow"] == ["Read"]
        pre = installed["hooks"]["PreToolUse"][0]["hooks"][0]
        prompt = installed["hooks"]["UserPromptSubmit"][0]["hooks"][0]

        session = "smoke-session"
        hook(prompt["args"], {"session_id": session, "prompt": "Fix the parser test", "hook_event_name": "UserPromptSubmit"}, env)
        response = hook(pre["args"], {"session_id": session, "tool_name": "Bash", "tool_input": {"command": "python -m unittest -v", "description": "tests"}}, env)
        output = json.loads(response)["hookSpecificOutput"]
        assert "permissionDecision" not in output
        assert output["updatedInput"]["description"] == "tests"
        command = output["updatedInput"]["command"]
        assert " run --host claude --session smoke-session " in command and command.endswith("-- python -m unittest -v")

        native = subprocess.run([bash(), "-c", "python -m unittest -v"], cwd=workspace, env=env, capture_output=True)
        routed = subprocess.run([bash(), "-c", command], cwd=workspace, env=env, capture_output=True)
        assert routed.returncode == native.returncode == 1, (routed.returncode, native.returncode)
        view = routed.stdout.decode()
        assert "test_parse_rejects_space" in view and "leading space must fail" in view, view
        assert "passing tests hidden" in view, view
        assert len(routed.stdout) * 3 < len(native.stdout) + len(native.stderr), (len(routed.stdout), len(native.stderr))
        capture = re.search(r"recall ([0-9a-f-]{8,36})", view).group(1)
        recalled = subprocess.run([*base, "recall", capture], capture_output=True, check=True)
        normalize = lambda data: re.sub(rb"in \d+\.\d+s", b"in Xs", data)
        assert normalize(recalled.stdout + recalled.stderr) == normalize(native.stdout + native.stderr)

        ignored = hook(pre["args"], {"session_id": session, "tool_name": "Bash", "tool_input": {"command": "python -m unittest -v | tail -3"}}, env)
        assert ignored == "", ignored

        subprocess.run([*base, "disable-claude-auto", "--workspace", str(workspace), "--apply"], capture_output=True, check=True)
        removed = json.loads(settings.read_text(encoding="utf-8"))
        assert removed["permissions"]["allow"] == ["Read"]
        assert removed["hooks"]["PreToolUse"] == [] and removed["hooks"]["UserPromptSubmit"] == []
    print("Claude auto-route smoke passed: install, goal capture, rewrite, selection, exact recall, pipe passthrough, removal")


if __name__ == "__main__":
    sys.exit(main())
