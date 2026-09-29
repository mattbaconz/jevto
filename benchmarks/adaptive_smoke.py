"""Run one synthetic opt-in Jev decision through the CLI.

Requires OPENROUTER_API_KEY. The key and raw capture are not written here.
This checks the transport and local safety path, not coding-agent savings.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "target" / "debug" / ("jevto.exe" if os.name == "nt" else "jevto")


def main():
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("Set OPENROUTER_API_KEY in the process environment before this one-call smoke")
    if not EXE.is_file():
        raise SystemExit("Build jevto before running the smoke")
    with tempfile.TemporaryDirectory(prefix="jevto-adaptive-") as directory:
        temp = Path(directory)
        task = temp / "task.json"
        task.write_text(json.dumps({
            "schema_version": 1,
            "session_id": "adaptive-smoke",
            "workspace_id": "synthetic-smoke",
            "revision": 0,
            "goal": "Find the storage reconnect implementation",
            "trusted_constraints": [],
        }), encoding="utf-8")
        policy = temp / "remote-policy.json"
        policy.write_text(json.dumps({
            "schema_version": 1,
            "data_classification": "synthetic",
            "allow_search_snippets": True,
            "allowed_roots": [str(temp)],
        }), encoding="utf-8")
        source = temp / "src"
        source.mkdir()
        for index in range(12):
            topic = ("storage reconnect implementation with retry ordering"
                     if index == 7 else f"unrelated synthetic feature {index}")
            (source / f"candidate_{index:02}.rs").write_text(
                "\n".join(f"// FIXME_JEVTO {topic} detail {line}" for line in range(1, 6)) + "\n",
                encoding="utf-8",
            )
        env = os.environ.copy()
        env["JEVTO_STORE_DIR"] = str(temp / "store")
        command = ["rg", "-n", "-H", "FIXME_JEVTO", "src"]
        native = subprocess.run(command, cwd=temp, env=env, capture_output=True, check=True)
        run = subprocess.run([
            str(EXE), "run", "--mode", "adaptive", "--allow-remote-jev",
            "--remote-policy", str(policy), "--task-file", str(task), "--", *command,
        ], cwd=temp, env=env, capture_output=True, timeout=25)
        report = subprocess.run([
            str(EXE), "report", "--session", "adaptive-smoke", "--json",
        ], cwd=temp, env=env, capture_output=True, check=True, timeout=5)
        receipt = json.loads(report.stdout)[0]
        usage = receipt.get("jev_usage") or {}
        result = {
            "child_exit": run.returncode,
            "mode": receipt["mode"],
            "adaptive_fallback": receipt.get("adaptive_fallback"),
            "jev_model": usage.get("model"),
            "jev_input_tokens": usage.get("total_input_tokens"),
            "jev_reported_cost_usd": usage.get("billed_cost"),
            "jev_elapsed_ms": receipt.get("jev_elapsed_ms"),
            "raw_bytes": receipt["raw_bytes"],
            "delivered_bytes": receipt["delivered_bytes"],
            "selected_reconnect_visible": b"storage reconnect implementation" in run.stdout,
            "capture_visible": b"capture=" in run.stdout,
        }
        print(json.dumps(result, indent=2))
        assert run.returncode == 0 and receipt["mode"] == "adaptive", result
        assert usage.get("model", "").startswith("typesafe/jev-1.13-"), result
        assert result["selected_reconnect_visible"] and result["capture_visible"], result
        assert receipt["delivered_bytes"] < receipt["raw_bytes"] == len(native.stdout), result
        assert 0 <= usage["billed_cost"] < 0.01, result


if __name__ == "__main__":
    main()
