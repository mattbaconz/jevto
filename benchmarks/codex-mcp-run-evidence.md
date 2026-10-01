# Codex exact-command MCP check — 2026-09-28 local time

This is a disposable host integration and permission check. It is not a benchmark arm or a claim that MCP reduces whole-task tokens. The Cursor benchmark remains pinned to `grok-4.7-xhigh`; Codex was used only to test JevTO's new host path. No OpenRouter Jev call was made.

## Candidate and setup

- JevTO local release executable: `<workdir>\bin\jevto-0.1.0-99ed54c0127f.exe`, SHA256 `99ed54c0127fd0c2523710d2bcc19d2755ef934f8c54351d62b59a0d30a5fa6e`.
- Host: desktop-bundled Codex CLI `0.158.0-alpha.2.1`, executable SHA256 `8f0554ede25bbc5450921897c468b2e84635aa513c5017457997af0954581f49`, model `gpt-6-luna`, Windows, a disposable workspace under `<workdir>\mcp-run-host-probe-0158`, noninteractive approval policy `never`, and read-only shell sandbox. The probe loaded only one JevTO MCP tool. It did not modify the normal user MCP registration. An initial probe accidentally used the PATH CLI `0.144.4`; its traces are kept separately under `mcp-run-host-probe` and were not used to label the results below.
- The command policy was outside the disposable workspace. It fixed absolute Python executable and exact argv for three named commands: `write_marker`, `passing_tests`, and `failing_check`. The agent could supply only a command ID.
- Before the final release probe, local debug-binary checks exercised successful and failing children. The final release was then checked directly by `tests/mcp_run_smoke.py` for default-disabled behavior, allowlist refusal, successful reduction, nonzero child status, byte-exact recall, binary output, oversized output, and rejection of a policy stored inside the workspace.

## Model-driven observations on the final release

| Host grant | Command | Observed result |
| --- | --- | --- |
| `approval_mode = "auto"` | `write_marker` | Codex returned `MCP tool call requires approval, but approval policy is never`. No marker file or child exit appeared. |
| `approval_mode = "approve"` | `passing_tests` | The tool completed with child exit 0, a capture ID, 2,230 raw bytes, and 764 selected wrapper bytes. The agent reported exit 0 and that the call was allowed. Text was not duplicated as base64 in structured content. Full historical recall matched the 2,230-byte native stdout. |
| `approval_mode = "approve"` | `failing_check` | The tool completed as an MCP call while preserving child exit 7, the failed assertion text, stderr, and a capture ID. The agent reported exit 7 and that the call was allowed. Full historical recall matched native stdout and stderr byte for byte. |

The approved `write_marker` check used the same final JevTO build, Codex version, and read-only shell sandbox. It wrote `ran.txt` inside the disposable workspace. This shows that Codex's shell sandbox did not constrain an approved MCP child. The specific MCP tool grant must be treated as a separate execution permission. The normal `jevto init` path still exposes and grants only recall, review, and status; it does not add `jevto_run`.

Traces are local at `<workdir>\mcp-run-host-probe-0158\codex-auto-write_marker.jsonl`, `codex-approve-passing_tests.jsonl`, `codex-approve-failing_check.jsonl`, and `codex-approve-write_marker.jsonl`. The release smoke ran with `JEVTO_TEST_EXE` pointing to the exact release above. Full recall of both host-run test commands matched native stdout/stderr. The final release's direct MCP smoke passed after the host checks.

## Limits

The `raw_bytes` and `delivered_bytes` receipt fields compare the child's output with the selected wrapper output. MCP envelope text and structured metadata add input to the agent, so these numbers are not the total model-visible payload or a provider-token saving. The tiny Codex calls also carried existing prompt and tool context; no matched native arm was run. `jevto_run` currently accepts fixed commands only, has no per-command timeout, and returns an explicit bounded error if output exceeds 1 MiB after a command may have run. Cursor and Claude execution approval and failure paths remain untested. This candidate is a local source and binary draft, not a published release.
