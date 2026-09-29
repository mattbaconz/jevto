# Benchmarks

## Payload benchmark (`token_bench.py`)

`python benchmarks/token_bench.py` builds 23 realistic workspaces from scratch in three suites: **dev** (11 scenarios the rules were tuned on), **holdout** (9, written after the rules froze, in [`holdout_scenarios.py`](holdout_scenarios.py) with a run log), and **semantic** (3 vocabulary-mismatch cases where only Jev ranking can help). It covers Rust, Go, Python unittest, node:test, tsc, service and JSON logs, ripgrep, lockfile, refactor and multi-file diffs, and `git log`, and runs each command four ways, each chosen by that tool's own Claude Code hook: native, RTK (`rtk hook claude`), JevTO deterministic (`jevto hook claude-pre` with the goal recorded by `jevto hook claude-prompt`), and JevTO adaptive with Jev. It records bytes, bytes/4 token estimates, exit parity, **answerability** (required facts visible without a recall), **effective tokens** (a missed fact is charged the native payload), and the size and cost of the Jev request the adaptive arm would send. Views for every arm are saved under `results/views/` for audit. Results: [`results/token-bench.md`](results/token-bench.md).

Options: `--suite dev,holdout,semantic`, `--scenarios a,b`, `--render-only` (rewrite the report from saved JSON), `--extra-path DIR` (prepend a bundled `rg` or `node`), `--live-jev` (allow Jev calls on cache misses; needs `OPENROUTER_API_KEY`). Live decisions are cached in `jev-cache/` so the adaptive arm can be replayed offline.

This measures one command's payload, not a whole task or a bill. The agent-level pilots below measure sessions.

## Agent-level pilots

These are local, exploratory fixtures. They do not establish provider savings or quality equivalence. `pilot_injected.py` is the completed Windows pilot path. It starts a fresh Cursor CLI session for each arm, pins `grok-4.7-xhigh`, checks the visible Extra High model label, and gives the agent either the original verifier output or JevTO's deterministic view. The harness verifies the edited checkout afterward. This tests a selected payload delivered by the harness; it does **not** test automatic host interception.

The separate [Claude Code coding pilot](claude-coding-pilot.md) used signed-in Pro CLI `2.1.283` and pinned `claude-haiku-4-5-20251001` in five isolated native, deterministic, and adaptive arms. On one Rust edit-and-verify task, all three outcomes passed 500 visible plus three holdout tests and answer review; JevTO's two arms used 2,226–2,306 fewer Claude-reported combined session tokens than native, while taking longer. On the Python control, both code edits passed, but native Claude falsely called a source hash a JevTO capture ID, so that pair is ineligible for an equal-output saving. One adaptive Jev call cost `$0.000046158` as reported by OpenRouter. This is an instructed-wrapper pilot with one run per arm, not evidence of automatic Claude interception, billed savings, or general quality equivalence. `claude_coding_pilot.py` retains the harness and local artifact paths.

The newer [Claude adaptive-search pilot](claude-adaptive-search-pilot.md) implements a reversible project-local `PostToolUse` hook and a 36-session frozen campaign across six synthetic search tasks. Native Claude Code `2.1.283` applied the compact replacement to a Bash search result on pinned Haiku; the rebuilt Grep path preserves Claude's observed object shape and passed exact-event replay. The coding campaign did not start because the current Pro five-hour event reached the protocol's `allowed_warning` boundary. Its manifest records all 36 arms as excluded, so it makes no new savings claim. `claude_adaptive_search_benchmark.py` rejects model substitution, missing usage or receipts, incomplete turns, failed holdouts, scope drift, and unsupported final-answer claims.

The separate [adaptive CLI smoke](adaptive-smoke-report.md) exercises one opt-in OpenRouter Jev Choice call on synthetic test names. Run `python benchmarks/adaptive_smoke.py` only with `OPENROUTER_API_KEY` already supplied in the process environment. It makes one paid request and checks the protected warning, failure, exit code, resolved model, and response-reported cost. It does not measure coding-agent tokens.

The [Codex hook diagnostic](codex-hook-diagnostic.md) is a separate host check. Its single pair reported higher input tokens with the hook installed and did not establish model-visible replacement on the newer Codex CLI. It is not a substitute model or arm for the pinned Cursor pilot.

The [Codex pre-hook pair](codex-prehook-pair-report.md) is another separate host check. On a synthetic one-command 500-test fixture, the working pre-hook produced 3,178–3,392 fewer provider-reported input tokens than native Codex in two matched runs, with all tests passing. This demonstrates a narrow session token reduction; it is not a realistic whole-task cost or quality benchmark.

The [Codex coding pair](codex-coding-pair-report.md) adds one completed edit-and-verify task on `gpt-6-sol` Max. Both arms made the same small fix, passed 500 visible tests and an independent holdout, and used one Cargo call after the edit. Codex reported 3,813 fewer session input tokens with the JevTO pre-hook, but that arm took 3.157 seconds longer. This one ordered pair is exploratory and separate from the Grok-pinned Cursor benchmark. `codex_coding_pair.py` retains the fixture, prompt, traces, diffs, receipts, and cleanup evidence.

The [Codex line-numbered search check](codex-rg-prehook-evidence.md) verifies the new narrow `rg -n -H` pre-hook route, exact recall, a missing-file failure, and reversible disable. It does not measure provider-token savings.

The [Codex Cargo check host check](codex-cargo-check-prehook-evidence.md) verifies a successful 24-crate check, exact recall, compiler failure with propagated exit 101, a workspace sandbox denial, and reversible disable. It measures one command's visible bytes, not provider-token savings.

The [Codex Cargo build host check](codex-cargo-build-prehook-evidence.md) verifies the same narrow route for a successful 24-crate build, exact recall, compiler failure with propagated exit 101, a workspace sandbox denial, and reversible disable. It is not a paired provider-token measurement.

The [Codex exact-command MCP check](codex-mcp-run-evidence.md) exercises the opt-in runner in a disposable workspace. Automatic approval was denied; a specific tool grant allowed successful and failed children, including a reduced Rust test result. Codex's read-only shell sandbox did not constrain an approved MCP child. The normal registration remains limited to the three non-executing tools. This is route and permission evidence, not a provider-token benchmark.

The [Cursor MCP transport diagnostic](cursor-mcp-transport-diagnostic.md) records the initial bounded stalls and the later responding path with `--force --sandbox disabled` on the exact `grok-4.7-xhigh` model. A headless stream contains a completed JevTO status tool call. Cursor forwarded only MCP text content, so JevTO added bounded receipt details there; a later check through the current registered binary reported the same 22 raw and delivered bytes, child exit 0, and `already_lean` reason as the local receipt. This verifies status on Cursor CLI `2026.09.26-dd393fe` under the tested settings. Recall/review, permission behavior without force, native shell interception, and coding-task savings remain unverified.

The later [headless coding pair attempt](cursor-headless-coding-pilot.md) used that exact model and the same 180-second cap in two isolated checkouts. Both turns timed out without terminal result events, verified edits, or usage fields. The JevTO arm asked for help but never ran the wrapper. A pager-controlled follow-up produced a passing native candidate but still no completed turn or usage; the harness stopped before another JevTO arm. These attempts are invalid for token comparison.

The [explicit-command Cursor pilot](cursor-route-pair-report.md) then completed a narrow one-command pair on `grok-4.7-xhigh`. Both arms reported the same 80 passing tests. Cursor reported 28,599 input tokens for native and 14,231 with an explicit JevTO wrapper; the JevTO arm made fewer Cursor tool calls and took longer, so the full token delta cannot be attributed to payload selection. This does not establish a realistic coding-task saving. `cursor_explicit_route.py` reproduces the preceding route/count diagnostic; `cursor_route_pair.py` runs one fresh arm at a time and records exact model, fixture, terminal usage, and local receipts.

A later [tracked Git explicit-wrapper check](cursor-tracked-explicit-report.md) used that same exact model in a disposable committed project. The agent ran `jevto run` through its shell, accurately reported exit 0 and 80 passed tests, and reached a terminal result. Full recall matched native fixture bytes and the Git tree stayed clean. This is a working instructed wrapper path in a tracked project, not automatic interception or an MCP runner fix; the single session has no native comparison.

The [Cursor explicit-wrapper coding pilot](cursor-explicit-coding-pilot.md) then attempted a native/JevTO edit-and-verify pair on exact `grok-4.7-xhigh`. Both agents ran their verifier after editing; the JevTO CLI selected 748 of 2,392 captured bytes and exact recall worked. The JevTO headless turn lost its terminal result and usage to a connection reset, then completed on the same session through a no-tool resume. The native arm completed normally. A disclosed posthoc literal-start holdout found that the JevTO-arm edit counted a leading-space `value=` line contrary to the task wording; the native edit passed. This is not a valid token pair or evidence of equal quality. The stronger holdout now protects future attempts.

The newer [Cursor project-rule coding pilot](cursor-rule-coding-pilot.md) used that stronger holdout **frozen before** both runs. The same exact model and CLI completed a native and an installer-generated JevTO-rule arm from the same application tree and prompt. Native passed; JevTO's route produced one correct receipt and exact recall, but its edit again counted the leading-space line and failed the holdout. Cursor reported 66,542 native versus 120,060 JevTO input tokens, with no matching bill. The outcomes differ, so this is not evidence of a token saving or equal quality. `cursor_rule_coding_pair.py` retains the prompt, rule bytes, terminal streams, final diffs, verification, receipt, and cleanup evidence. A preceding no-event model rejection is excluded.

The same harness has a diagnostic `--arm hook` mode. Two bounded project `preToolUse` probes, including one in a tracked Git fixture, produced no hook observation or JevTO receipt and no terminal result before timeout. This mode is not a supported Cursor integration or a valid savings arm; the report keeps the traces and the exact uncertainty.

Its `--arm rule --tracked-workspace --installed-rule` mode previews `init-cursor-rule`, applies one exact-command project rule in a disposable tracked Git fixture, verifies the model's wrapper call and byte-exact recall, then removes the unchanged rule with `disable-cursor-rule`. On Cursor CLI `2026.09.26-dd393fe`, exact `grok-4.7-xhigh` turns completed for both 80 passing tests and a failure with exit 101 and one assertion (`--scenario failure`). A preceding installer-rule turn routed the command but timed out while the agent polled recall. [The route report](cursor-route-pair-report.md) keeps all three outcomes. These distinct tasks have no matched native arms and measure no token saving.

The separate [Cursor exact-command MCP check](cursor-mcp-run-evidence.md) exercises an opted-in, policy-bound `jevto_run` server with the exact model. One passing and one failing fixture produced completed model calls and local receipts; full recall matched native stdout/stderr. This is host route evidence, not a paired token comparison. `cursor_mcp_run_probe.py` installs the pinned runner in a disposable project, requires targeted server approval, checks the model's call and answer, then disables and removes that project server. Four later tracked Git project turns exposed a model-facing namespace failure despite the CLI listing the server ready; no tracked-project coding or token claim follows. `cursor_runner_coding_probe.py` retains those invalid edit-and-verify traces. Its native arm should not be run as a comparison until the JevTO arm can complete on the same fixture and host settings.

The two tasks begin from failing fixture commits. `quiet_warning` contains a warning before a later failed summary; `already_lean` is short enough for JevTO to pass through. Each task has a visible verifier and a holdout. The planned order alternates arms: native then JevTO for `quiet_warning`, JevTO then native for `already_lean`. All four runs use the same CLI launcher, model ID, force mode, disabled Windows sandbox, and fresh session policy. Provider cache behavior is not exposed by Cursor.

## Reproduce locally

Build `jevto` first. On Windows, install the isolated PTY dependency from `requirements-windows.txt` into a folder of your choice. Point `--cursor-cli` to an installed Cursor CLI launcher whose `models` output lists the exact `grok-4.7-xhigh` ID. No other model is accepted.

For the Claude adaptive-search campaign, sign in to Claude Code Pro, build JevTO, and supply the OpenRouter key only in the benchmark process environment. The harness pins `claude-haiku-4-5-20251001`, applies a project-local hook only in JevTO arms, and stops on a non-allowed Pro rate event. It does not use Claude safe mode because safe mode disables the hook.

```powershell
$env:OPENROUTER_API_KEY = '<runtime secret>'
python benchmarks\claude_adaptive_search_benchmark.py --claude-cli C:\path\to\claude.exe --jevto-cli .\target\debug\jevto.exe
Remove-Item Env:OPENROUTER_API_KEY
```

```powershell
python -m pip install --target .tooling\pywinpty -r benchmarks\requirements-windows.txt
cargo build -p jevto
python benchmarks\pilot_injected.py --cursor-cli C:\path\to\cursor-agent.ps1 --winpty-path .tooling\pywinpty --max-runs 2 --timeout-seconds 150
python benchmarks\pilot_injected.py --cursor-cli C:\path\to\cursor-agent.ps1 --winpty-path .tooling\pywinpty --start-index 2 --max-runs 2 --timeout-seconds 150
```

Each invocation writes a new ignored `.bench-runs/pilot-injected-*` directory with a manifest, initial evidence, Cursor terminal trace, final Git diff, local JevTO receipts, and verifier results. Compare the two manifests' CLI version, launcher hash, binary hash, model label, fixture tree hash, and permission settings before combining them. A timeout or missing final marker is ineligible for timing; a passing edited tree does not rescue an unfinished Cursor turn. The output byte fields count the UTF-8 prompt text after Python's text-mode newline conversion, not provider tokens or a bill.

`pilot.py` is a headless stream-json transport experiment. `pilot_pty.py` is a direct interactive wrapper experiment. Both retain invalid transport attempts as evidence; neither produced a valid paired outcome in the 2026-09-25 pilot. See [pilot-report.md](pilot-report.md) for the recorded runs and exclusions.

For another bounded headless attempt, `pilot.py` accepts `--cursor-cli` and `--jevto` paths, pins the exact model, disables project MCP servers in each disposable checkout, records Cursor's terminal usage fields when present, and stops after an invalid arm. Its current tested command was:

```powershell
python benchmarks\pilot.py --cursor-cli D:\jevto\.tooling\cursor-agent-2026.09.23\dist-package\cursor-agent.ps1 --jevto D:\jevto\.tooling\bin\jevto-0.1.0-aac1ba3e26c4.exe --max-runs 2 --timeout-seconds 180
```

Those paths name the local test installation, not distribution paths. A completed model turn and a verified final candidate are required before comparing arms; the JevTO arm also needs an actual wrapper receipt.

For the separate one-command route pilot, use the same pinned CLI and built JevTO executable for both arms. Each command writes a fresh ignored `.bench-runs/cursor-route-pair-*` artifact; compare the two manifests' fixture hashes and model/launcher identities before interpreting usage. Run the native arm first or deliberately record a reversed order. A missing terminal result makes that arm ineligible.

```powershell
python benchmarks\cursor_route_pair.py --arm native --cursor-cli C:\path\to\cursor-agent.ps1 --jevto C:\path\to\jevto.exe --timeout-seconds 210
python benchmarks\cursor_route_pair.py --arm jevto --cursor-cli C:\path\to\cursor-agent.ps1 --jevto C:\path\to\jevto.exe --timeout-seconds 210
```
