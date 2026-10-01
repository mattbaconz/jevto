# Cursor exact-command MCP route — 2026-09-28 local time

This is a bounded, disposable host check, not a paired token benchmark. Cursor CLI `2026.09.23-86fc751` requested the exact model ID `grok-4.7-xhigh` and reported `Grok 4.7 256K Extra High` in both completed turns. No Fast or substitute model, OpenRouter Jev call, or provider bill was involved. The JevTO executable was `<workdir>\bin\jevto-0.1.0-c560828114fc.exe`, SHA256 `c560828114fc10a13585c821872a02a7c59ed8bde8419c31f968a82a070e5e56`.

## Route and permission

Each run had a new disposable workspace, store, and project `.cursor/mcp.json`. The separate `jevto_exec` server used `jevto mcp --run-policy` with a policy outside the workspace. Its sole `inventory` command fixed the absolute Python executable and exact `verify_inventory.py` argument; the model could supply only the command ID. The seven normal user MCP servers were disabled **for these workspaces**. Before targeted approval, the first project listed `jevto_exec: not loaded (needs approval)`. `cursor-agent mcp enable jevto_exec` made it ready. The model calls used `--force --trust --sandbox disabled` on Windows, so this does not establish behavior without force or a host sandbox. The child runs with the MCP process's permissions. Do not treat the command policy as a shell sandbox.

Both model turns looked up the tool schema, called `jevto_run` once with `{"command_id":"inventory"}`, received its text result, and completed with one successful Cursor terminal result. The normal user MCP registration remained the three non-executing tools; `jevto_exec` appeared only in these project files. Afterward, `cursor-agent mcp disable jevto_exec` left it disabled in both projects, and the normal `<workspace>` workspace listed only its pre-existing user servers, including `jevto: ready`.

| Observation | Passing fixture | Failing fixture |
| --- | ---: | ---: |
| Child exit reported by model and JevTO receipt | 0 | 101 |
| Captured stdout + stderr | 2,268 bytes | 2,327 bytes |
| JevTO delivered wrapper bytes | 915 bytes | 1,023 bytes |
| Cursor-reported input tokens | 27,624 | 14,318 |
| Cursor-reported output tokens | 463 | 734 |
| Cursor-reported cache-read tokens | 31,616 | 44,928 |
| Model-call wall time | 119.559 s | 94.736 s |

The passing model answer reported 80 explicit passing tests, including the 78 deferred lines, plus the matching capture ID. The failing answer reported `suite::case_79`, its `left == right` assertion, child exit 101, and the matching capture ID. The MCP call itself completed successfully in both cases; its text distinguished the failed child from a transport error. In both projects, `jevto recall CAPTURE_ID --full` returned captured stdout and stderr byte for byte, and those streams matched a native execution of the fixture. The full recall was checked locally after the model turns; Cursor did not call recall in these turns.

The receipt byte difference measures local output selection before the MCP envelope, schema lookup, tool metadata, and other agent context. These two different tasks cannot be compared as native/JevTO arms. The token fields are Cursor-reported session usage, with no matched native arm or provider bill. They establish neither net token saving nor cost, speed, or quality improvement for the MCP route. Ordinary Cursor shell calls were not intercepted.

Artifacts are ignored local directories `.bench-runs/cursor-mcp-run-20260927T185749Z/` and `.bench-runs/cursor-mcp-run-20260927T190306Z/`, each with a manifest, project config, policy, Cursor stream, result, receipt, capture, and approval/listing text. The first run used the success-only harness; the later `cursor_mcp_run_probe.py` revision accepts both scenarios, verifies the completed MCP call, and disables the project server on exit. To reproduce the bounded probe with the exact model and a reviewed executable:

```powershell
python benchmarks\cursor_mcp_run_probe.py --scenario success --cursor-cli C:\path\to\cursor-agent.ps1 --jevto C:\path\to\jevto.exe --timeout-seconds 210
python benchmarks\cursor_mcp_run_probe.py --scenario failure --cursor-cli C:\path\to\cursor-agent.ps1 --jevto C:\path\to\jevto.exe --timeout-seconds 210
```

The exact-command runner has no per-command timeout. A granted MCP child may mutate files outside what a host's shell sandbox would permit, so keep its external policy specific and its host grant project scoped.

## Installer-based model retest — 2026-09-28 local time

The revised `cursor_mcp_run_probe.py` used `init-cursor-runner --apply` with release `<workdir>\bin\jevto-0.1.0-bee26c2ee809.exe` (SHA256 `bee26c2ee80921121ef644c5d17f5fb52cdc2b7a41bc2e8a6b10cc312398a2e0`). The generated project entry pinned the external policy SHA-256. Cursor CLI `2026.09.23-86fc751` listed exact `grok-4.7-xhigh` separately from its Fast variant, explicitly enabled `jevto_exec` for each disposable project, and reported `Grok 4.7 256K Extra High` in both completed model turns. The project server was disabled through Cursor and removed with `disable-cursor-runner --apply` after each run. The normal user registration still listed only recall, review, and status; no `jevto_exec` remained in the global server list.

| Observation | Passing fixture | Failing fixture |
| --- | ---: | ---: |
| Valid terminal result and one completed `jevto_run` call | Yes | Yes |
| Child exit reported by model and receipt | 0 | 101 |
| Captured raw / selected wrapper bytes | 2,268 / 915 | 2,327 / 1,023 |
| Cursor-reported input / output tokens | 14,288 / 465 | 14,329 / 630 |
| Cursor-reported cache-read tokens | 44,928 | 44,928 |
| Model-call wall time | 100.064 s | 100.317 s |
| Exact stdout and stderr recall against native fixture | Yes | Yes |

The passing answer counted all 80 tests; the failing answer named `suite::case_79`, retained its assertion, and reported exit 101. The two receipt IDs were `c42d8a27-cd31-44ab-a7ea-c675ad038a83` and `0d3ddcc7-9caf-4d96-be0d-3ebeca3eb01a`. Separate `local-validation.json` files record byte-for-byte recall comparisons, policy pin checks, and entry removal. The ignored local artifacts are `.bench-runs/cursor-mcp-run-20260927T194036Z/` and `.bench-runs/cursor-mcp-run-20260927T194252Z/`; they are not public repository contents. The harness now checks exact recall, the fixture hash, and policy pin on future runs.

These are two different verifier scenarios, not native/JevTO matched benchmark arms. Their Cursor usage is session-reported and has no provider bill. They establish that the installed, approved runner delivers correct passing and failing evidence on this named host path; they do not establish token savings, ordinary shell interception, unforced approval behavior, or a realistic coding-task outcome. No OpenRouter Jev request was made.

## Tracked-project discovery diagnostic

A coding-task attempt then put the same installer in a disposable Git checkout and asked the exact model to edit `summarize.py` before running an exact `verify_once.py` command. The generated `jevto_exec` server was enabled and `cursor-agent mcp list` said `ready`. During the model turn, `getMcpTools(server="jevto_exec")` returned `namespace "jevto_exec" not found`; its available namespace list omitted that server. The first 240-second attempt had `.cursor/` Git ignored and left a source edit that passed independent visible and holdout checks, but no verifier marker, JevTO receipt, terminal result, or usage event. The second attempt left `.cursor/mcp.json` visible to Git and reproduced the same namespace error; it also ended without a receipt or terminal result. Neither is an eligible coding benchmark arm.

Two smaller controls used the earlier 80-test verifier prompt in tracked Git projects: one with the generated MCP file untracked, one with it committed. Both showed `jevto_exec: ready` to the CLI, but the model again received `namespace "jevto_exec" not found`, made no `jevto_run` call, and timed out without a terminal result. Their exact-model labels were `Grok 4.7 256K Extra High`. The committed-config run's first attempt stopped in a 30-second PowerShell model-listing preflight, before project setup; it is not another model result. Direct staged-Node preflight then listed the exact xhigh ID and enabled the completed control. All four started projects had their server disabled in Cursor and removed by `disable-cursor-runner`; the normal user server list afterward had no `jevto_exec`.

| Local artifact under `.bench-runs/` | Project MCP state | Model result |
| --- | --- | --- |
| `cursor-runner-coding-jevto-20260927T195232Z/` | Git checkout; `.cursor/` ignored | 242.294 s timeout; 0 receipts; namespace absent; independently passing edit is ineligible. |
| `cursor-runner-coding-jevto-20260927T195817Z/` | Git checkout; config unignored and untracked | 242.407 s timeout; 0 receipts; namespace absent. |
| `cursor-mcp-run-20260927T200320Z/` | Minimal Git checkout; config untracked | 211.865 s timeout; 0 receipts; namespace absent. |
| `cursor-mcp-run-20260927T200948Z/` | Minimal Git checkout; config committed | 211.656 s timeout; 0 receipts; namespace absent. |

By contrast, the two completed installer-based verifier turns above used disposable directories without a Git repository and had model-visible `jevto_run`. This is an observed difference on Cursor CLI `2026.09.23-86fc751`, not proof of its internal cause or a general Cursor rule. [Cursor's CLI MCP documentation](https://prod.cursor.com/docs/cli/mcp) says project MCP configuration should be discoverable; the tested headless model path disagreed for these Git projects. Do not claim the project runner supports tracked coding repositories on this version, or compare token usage from these incomplete arms. The `cursor_runner_coding_probe.py` harness retains the exact prompts, initial tree hashes, streams, candidate diff, independent verifier results, and cleanup status. A new host signal or verified workaround is needed before rerunning this route.

## Current installed CLI retest

Cursor CLI `2026.09.26-dd393fe` still listed the disposable tracked Git project's `jevto_exec` server as ready, but the exact `grok-4.7-xhigh` model's tool discovery returned `namespace "jevto_exec" not found`. The 242.054-second turn produced no JevTO receipt or terminal usage event and is ineligible for a token comparison. The installer and Cursor disable commands removed the project server afterward; the normal user servers were untouched. Artifact: `.bench-runs/cursor-mcp-run-20260927T211122Z/`. This repeats the tracked-project discovery failure on a second named CLI version. It does not establish a general MCP defect outside this tested path. The separate project-rule route is documented in [the Cursor route report](cursor-route-pair-report.md).
