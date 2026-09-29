# JevTO host and pilot evidence ledger

This ledger preserves the detailed, version-specific host observations and pilots that preceded the current README. Links are relative to the repository root, so they point one level up from this file.


JevTO is an experimental local tool for coding agents. It captures bounded output from commands you explicitly wrap, defers predictable noise from Rust test inventories, successful Cargo progress, and repeated line-numbered search matches, and keeps captured stdout and stderr available for exact historical recall. A separate read-only review asks whether a Git diff may contain avoidable new files or dependencies. That review does not decide whether a change is correct.

The current build is an unreleased source draft. Its three non-executing MCP tools are registered locally in Cursor, Codex, and Claude Code. A separate opt-in exact-command MCP runner was tested in disposable Codex CLI `0.158.0-alpha.2.1` and Cursor CLI `2026.09.23-86fc751` workspaces; it is not part of the normal registration. Codex also used status, exact recall, and advisory review through a scoped tool grant. Cursor on exact `grok-4.7-xhigh` called status, explicitly ran `jevto run`, and called the exact-command MCP runner for both a passing and a failing test command. In a completed one-command shell pair it reported the correct 80-test result through both native and JevTO paths. On Cursor CLI `2026.09.26-dd393fe`, an installer-generated project rule also guided passing and failing tracked-Git verifier runs through JevTO; it is an instruction, not shell interception. A later [Cursor coding pilot](../benchmarks/cursor-explicit-coding-pilot.md) confirmed the instructed wrapper can run after an edit, but its JevTO turn needed recovery after a connection reset and its edit failed a stronger posthoc semantic check; no paired token result is available. These Cursor observations used force mode with the Windows sandbox disabled. An opt-in Codex project `PreToolUse` hook routed plain Windows PowerShell `cargo test`, `cargo check`, `cargo build`, and simple line-numbered `rg` calls through JevTO on the same CLI version; the agent received selected output on tested passing routes and retained errors on failures. In [one exploratory coding-task pair](../benchmarks/codex-coding-pair-report.md), Codex fixed the same Rust bug in both arms, passed 500 visible tests plus an independent holdout, and reported 3,813 fewer input tokens with the pre-hook; that arm was 3.157 seconds slower. The older `PostToolUse` hook worked for one passing path on CLI `0.144.4`, but its replacement did not reach a code-mode tool call on `0.158`. Other automatic host paths and full adapter coverage remain unverified. General whole-task savings and quality equivalence remain unproven.

A newer [Cursor project-rule coding pair](../benchmarks/cursor-rule-coding-pilot.md) completed on the exact xhigh model with the stronger holdout frozen before both arms. Native passed; the JevTO-guided edit failed that holdout despite its verifier passing and exact recall working. Cursor reported 66,542 native versus 120,060 JevTO input tokens; [summing all four reported usage fields](../benchmarks/cursor-usage-semantics.md) reverses that numerical comparison because cache-read usage differs. This is one exploratory pair with unequal outcomes and no provider bill, so neither count establishes a coding-task saving or quality gain.

A separate [Claude Code pilot](../benchmarks/claude-coding-pilot.md) ran five isolated arms on Pro CLI `2.1.283` and the exact `claude-haiku-4-5-20251001` snapshot. On one Rust edit, native, deterministic JevTO, and adaptive JevTO all passed 500 visible tests, three frozen holdouts, and answer review. The JevTO arms used 4.88–5.06% fewer combined Claude-reported session tokens, but took longer; adaptive Jev added no clear value over deterministic. The Python native code passed its checks but its final answer falsely claimed a JevTO capture ID, so that pair is not an equal-quality savings result. The wrapper was explicitly instructed, and Pro's CLI dollar figures are list-price estimates rather than billed savings. Cursor benchmarking remains pinned to Grok 4.7 xhigh.

The newer [Claude adaptive-search pilot](../benchmarks/claude-adaptive-search-pilot.md) adds a reversible project `PostToolUse` hook and a frozen 36-session campaign. Claude Code `2.1.283` on the same pinned Haiku model visibly consumed a compact Bash search result with exact hook-visible recall. The Grep parser now handles Claude's single-file `line:text` result while preserving its object fields; the fix passed replay of the exact saved native event. A post-fix native Grep turn and the coding campaign remain pending because the Pro five-hour event reached the protocol's warning boundary. All planned campaign arms are recorded as excluded, with no new savings claim and no Fast substitution.

## Current coverage

| Route | State |
| --- | --- |
| `jevto run -- PROGRAM ARGS...` | Implemented for directly wrapped commands. The child runs with the caller's permissions. Output within the capture limit is stored with separate stdout and stderr; larger output streams through without a capture. The child exit status is returned in either case. Cursor CLI `2026.09.23-86fc751` on exact `grok-4.7-xhigh` also completed this instructed route in a [tracked disposable Git project](../benchmarks/cursor-tracked-explicit-report.md), with exact recall; it did not automatically intercept other shell calls. |
| Deterministic selection | Implemented for supported UTF-8 Rust test inventory lines, successful Cargo `Checking`/`Compiling` progress, and consecutive `rg` results with the same path and exact long match text. Deferred passing test lines now carry an explicit total count. The Cargo route keeps the first and last two lines in runs of at least 16, plus warnings, final status, and task-mentioned crates; failed builds pass through. The search route keeps the first and last two locations and defers only the middle of runs of at least 12 hits. Warning-like test names and malformed inventory lines stay visible, as do warnings, failures, assertions, and status. Already lean, ambiguous, unsupported, binary, or oversized results pass through. |
| `jevto recall` | Byte-exact retrieval of retained historical stdout/stderr, a deferred section, or a line range. It does not rerun a command or verify the current working tree. |
| `jevto review` / `jevto advice-status` | Read-only Git diff advice with a candidate hash and staleness check. Findings are questions, not edit or test verdicts. |
| `jevto mcp` | By default, exposes only local `jevto_recall`, `jevto_review`, and `jevto_status`. Status reports coverage counts and per-run details for sessions of up to eight runs in both text and structured content; larger sessions return aggregates and mark details omitted. Codex CLI `0.158.0-alpha.2.1` completed model-driven status, exact recall, and advisory review calls under a scoped grant. Cursor CLI `2026.09.26-dd393fe` completed a model-driven status call through the current registration on exact Grok 4.7 xhigh under force mode. Cursor recall/review and Claude model-driven calls remain unverified. Recall records a local access receipt. |
| `jevto mcp --run-policy PATH` | Opt-in `jevto_run` for exact command IDs fixed in an external JSON policy. The file must live outside its workspace; the tool accepts no shell string or agent-supplied argv. Disposable Codex CLI 0.158 and Cursor CLI 2026.09.23 checks exercised successful and failed children with matching capture IDs and byte-exact full recall. Codex denied automatic approval, then allowed a specific per-tool grant; its read-only shell sandbox did **not** constrain the granted MCP child. Cursor required targeted project-server approval and was tested with force mode and its Windows sandbox disabled. The user-scoped `init HOST` installer does not expose or grant this tool; use only with a reviewed command policy and host grant. No paired provider-token saving was measured for this route. |
| `jevto init-cursor-runner --workspace PATH --policy POLICY.json` / `disable-cursor-runner` | Previewed, reversible project setup for the separate `jevto_exec` server. The installer validates the external exact-command policy, pins its SHA-256 in `.cursor/mcp.json`, and leaves the normal three-tool registration alone. Apply is explicit; Cursor approval is separate. Disposable CLI checks exercised apply, a real MCP command, exact recall, policy-change refusal, and disable. Cursor CLI `2026.09.23-86fc751` completed model-driven passing and failing verifier calls in disposable non-Git directories on exact `grok-4.7-xhigh`. In four tracked Git project turns, the CLI listed the runner ready but the model could not discover its namespace; none produced a receipt or terminal usage result. Tracked-project coding support and a paired token benefit are unverified. |
| `jevto init-cursor-rule --workspace PATH -- PROGRAM ARG...` / `disable-cursor-rule` | Previewed, reversible project rule for one exact verifier command, with an external store and ownership record. In tracked Git projects on Cursor CLI `2026.09.26-dd393fe`, exact Grok 4.7 xhigh followed installer-generated rules for passing and failing fixtures: it reported 80 passed with exit 0, then 79 passed and one failed assertion with exit 101, each with a capture ID. Local full recall matched both native fixtures, and owned rules were removed. This is agent guidance, not automatic interception. A later [matched coding pilot](../benchmarks/cursor-rule-coding-pilot.md) completed both host turns, but the JevTO edit failed the frozen holdout; there is no equal-quality token result or unforced permission check. [Route evidence](../benchmarks/cursor-route-pair-report.md). |
| `jevto init HOST` / `jevto disable HOST` | Previewed, reversible user configuration for the MCP server in `codex`, `claude`, or `cursor`. Apply is explicit. Codex setup allows only the three current tools and grants each of them per-tool approval; it leaves the user's shell approval policy unchanged. This does not route ordinary agent commands through JevTO. |
| `jevto init-codex-hook --workspace PATH` / `disable-codex-hook` | Previewed, reversible Codex project `PostToolUse` hook. Codex must review and trust it. On Codex CLI 0.144.4 for Windows, a successful Bash tool response containing a passing Rust test inventory was captured and the agent used the shorter evidence pack. On 0.158.0-alpha.2.1, the hook ran but model-visible replacement was not established. Exact recall covers the text the hook received. Child exit and completeness of the underlying process output are unavailable. |
| `jevto init-codex-pre-hook --workspace PATH` / `disable-codex-pre-hook` | Previewed, reversible Windows Codex project `PreToolUse` hook for plain `cargo test`, `cargo check`, `cargo build`, and simple `rg -n -H` commands. The hook rewrites an eligible command to the explicit wrapper before execution. On CLI 0.158.0-alpha.2.1, an agent saw selected output on passing Rust tests, a successful 24-crate check and build, and a repeated search. Failed checks and builds retained diagnostics; the current hook propagated child exit 101 through PowerShell. Exact recall worked, and Cargo targets outside the workspace were denied. One [small edit-and-verify pair](../benchmarks/codex-coding-pair-report.md) completed with the same passing diff and fewer session-reported input tokens in the hooked arm, which took longer. Codex hook trust is required. Other shells, commands, approval prompts, and client versions remain unverified. |
| `jevto init-claude-hook --workspace PATH` / `disable-claude-hook` | Previewed, reversible Claude project `PostToolUse` hook for successful simple `rg` calls through Bash or PowerShell and content-mode Grep results. It clones Claude's result shape and replaces only the text field when compact output is smaller. On Claude Code `2.1.283`, a Bash replacement reached pinned Haiku and exact recall matched the hook-visible text. Claude had already removed one trailing CRLF from the native child output. The post-fix Grep path passed replay of the exact native event shape; a new native delivery is pending. Failed/interrupted calls and other tools stay outside this route. |
| Other native Cursor, Codex, or Claude calls | Unverified or unsupported. The observed Codex `PostToolUse` hook was not invoked for a nonzero command; use the pre-hook route where supported or explicit `jevto run`. |
| Adaptive Jev selection | Opt-in for simple successful line-numbered repository searches. OpenRouter Jev uses one Choice ranking plus one Noul existence check over 9–24 consecutive same-file search sections and retains the top four only when the existence probability is at least 0.9. The request requires an explicit public/synthetic outbound policy, rejects secret-like content, is capped at 16 KiB, makes at most one call without retries, and uses a content-addressed local decision cache. Protected warnings, failures, assertions, constraints, and status remain local authority. The default path needs no account or network. |

`jevto doctor --json` reports the static, version-specific coverage matrix plus a read-only check of the three user-scoped MCP registrations. A registration can be `current`, `different_executable`, `missing_executable`, `missing`, `unmanaged`, `conflict`, or `unreadable`. `current` means the owned host config names this exact executable; it does not prove that a running host reloaded the config or that an agent used a tool.

## Build and try

Use Rust and Cargo from a current stable toolchain:

```sh
cargo test --workspace
cargo build -p jevto
```

On Windows PowerShell:

```powershell
.\target\debug\jevto.exe doctor --json
.\target\debug\jevto.exe run -- cargo test --workspace
.\target\debug\jevto.exe recall CAPTURE_ID --full
```

On macOS or Linux, use `./target/debug/jevto` in place of `.\target\debug\jevto.exe`. `run` accepts an executable and arguments directly after `--`; it does not interpret shell operators. A failed child command keeps its failed exit. The terminal response includes a capture ID when JevTO selects a shorter view; `jevto report --session SESSION_ID --json` reads local receipts for a named session. The CLI checks the final text including store-specific recall commands against the byte budget and original output size; if it is too large, it delivers the original result and records the bypass. `jevto purge` removes expired captures and their packs.

To register the three default MCP tools for a host, build the binary you intend to keep at a stable path, preview the change, then apply it:

```powershell
.\target\release\jevto.exe init codex
.\target\release\jevto.exe init codex --apply
.\target\release\jevto.exe disable codex
.\target\release\jevto.exe disable codex --apply
```

Replace `codex` with `claude` or `cursor` as needed. `init` refuses to overwrite a different existing JevTO entry. If the exact same entry already exists, `--adopt-existing` previews or explicitly adopts it without editing the host config. JevTO keeps local ownership records and configuration backups; `disable` removes only the matching owned entry and keeps other host settings and captures. Codex's user-scoped registration grants `approve` to the three listed JevTO tools; it does not grant future JevTO tools or change shell permissions. Review this access before applying the installer, especially if your local captures may contain sensitive output. These commands change MCP registration only. For output selection, an agent must explicitly call `jevto run -- PROGRAM ARGS...` in a permitted shell. On PowerShell, use `; exit $LASTEXITCODE` when the outer shell must propagate the child's exact numeric exit; the JevTO pack itself always displays the recorded child exit.

After changing the executable path, run `disable HOST` and `init HOST` with the new binary, previewing before each `--apply`. The owned entry must still match for removal; unrelated host settings and captures remain. Then use `doctor --json` to check that the registration says `current` and restart or reload the host if it had already launched its MCP process. This check covers configuration only; a host tool call is separate runtime proof.

The separate `--run-policy` mode reads a JSON object with `schema_version: 1`, an absolute `workspace`, a `workspace_id`, and a nonempty `commands` array. Each command has a unique `id`, an absolute existing `program`, and its exact `argv` list. Put this policy outside the workspace and start a separate MCP server process with `jevto --store-dir PATH mcp --run-policy POLICY.json`; the normal `init` command never enables it. For a manually configured server, `--run-policy-sha256 HEX` additionally refuses startup if the policy bytes change. The Cursor project installer below supplies this pin automatically. Grant `jevto_run` explicitly in the host before use. The child's status appears separately from MCP transport errors. Text results do not duplicate their bytes as base64; binary results include base64 in structured content. Output above 1 MiB returns an explicit MCP error after the command may have run, so do not retry blindly. This mode has no per-command timeout, and the tested Codex read-only shell sandbox did not constrain an approved MCP child.

For a Cursor project, review the exact policy and preview the owned `jevto_exec` entry before applying it. Use the same `--store-dir` for setup and removal if you override the default. Keep the executable at a stable path. Cursor must separately approve the project execution server; setup itself runs no policy command.

```powershell
.\target\release\jevto.exe init-cursor-runner --workspace . --policy C:\path\outside-project\policy.json
.\target\release\jevto.exe init-cursor-runner --workspace . --policy C:\path\outside-project\policy.json --apply
.\target\release\jevto.exe disable-cursor-runner --workspace .
.\target\release\jevto.exe disable-cursor-runner --workspace . --apply
```

Disable removes only the unchanged owned server entry. It leaves other Cursor settings, the policy file, and retained captures. If the policy changes after setup, the pinned server refuses to start until its reviewed configuration is replaced. The installer does not grant `jevto_run` or intercept ordinary Cursor shell calls.

To guide Cursor toward one exact verifier command in a trusted project, keep the capture store outside that project, preview the generated rule, then apply it. Cursor loads project rules as agent instructions. This setup does not install an execution tool or change shell permissions. Use the same store for removal:

```powershell
.\target\release\jevto.exe --store-dir C:\path\outside-project\jevto-store init-cursor-rule --workspace . -- python verify_inventory.py
.\target\release\jevto.exe --store-dir C:\path\outside-project\jevto-store init-cursor-rule --workspace . --apply -- python verify_inventory.py
.\target\release\jevto.exe --store-dir C:\path\outside-project\jevto-store disable-cursor-rule --workspace . --apply
```

The installer refuses an existing or changed owned rule and leaves other rules and captures in place. Keep the executable at a stable path. The supported argv grammar is bounded plain text; use explicit `jevto run` for commands outside it.

To route plain Windows PowerShell Rust tests, Cargo checks and builds, and simple line-numbered searches in one Codex project, use the binary at a stable path and review the preview before applying it:

```powershell
.\target\debug\jevto.exe init-codex-pre-hook --workspace .
.\target\debug\jevto.exe init-codex-pre-hook --workspace . --apply
.\target\debug\jevto.exe disable-codex-pre-hook --workspace .
.\target\debug\jevto.exe disable-codex-pre-hook --workspace . --apply
```

The pre-hook routes only simple `cargo test`, `cargo check`, and `cargo build` invocations with a small set of flags and package names, plus unquoted `rg -n -H PATTERN RELATIVE_PATH` searches with a small set of flags. Pipelines, redirection, globs, parent paths, other programs, and unrecognized syntax stay native. Codex must review and trust the project hook. Its installer preserves other hook groups and creates `.jevto-store/.gitignore` so raw project-local captures are ignored by Git. The store remains after disabling the hook until its captures expire or you purge them with the same `--store-dir`. The pack prints exact recall commands with that store path; a pass-through result may have a receipt without printing a capture ID. A valid Codex session ID is carried into the receipt for `report --session`. The hook appends `exit $LASTEXITCODE` so PowerShell reports the wrapped child's numeric exit. The observed Codex hook event exposed only the command field of the tool input, so this filter cannot inspect every tool-call permission field; the tested workspace sandbox remained enforced for Rust tests, a Cargo check, and a Cargo build. Native approval prompts have not been exercised.

The older `PostToolUse` hook remains available for the narrower CLI 0.144.4 passing-output path:

```powershell
.\target\debug\jevto.exe init-codex-hook --workspace .
.\target\debug\jevto.exe init-codex-hook --workspace . --apply
.\target\debug\jevto.exe disable-codex-hook --workspace .
.\target\debug\jevto.exe disable-codex-hook --workspace . --apply
```

For either installer, use the same `JEVTO_STORE_DIR` for setup and disable if you override the default, because the ownership record lives there. The post-hook only selects complete-looking, successful Rust test inventories from the Codex `Bash` response. Its recall handle is exact for the text Codex passed to the hook, which may differ from the child process's full output. The post-hook did not replace a nested code-mode result on CLI 0.158.

For one adaptive search, edit the [example task frame](../examples/task-frame.json) and [outbound policy](../examples/remote-search-policy.json), set `OPENROUTER_API_KEY` through your secret manager, and explicitly opt in. The policy's allowed root must resolve to the current workspace:

```powershell
.\target\debug\jevto.exe run --mode adaptive --task-file .\examples\task-frame.json --remote-policy .\examples\remote-search-policy.json --jev-preview -- rg -n -H FIXME_JEVTO src
.\target\debug\jevto.exe run --mode adaptive --task-file .\examples\task-frame.json --remote-policy .\examples\remote-search-policy.json --allow-remote-jev -- rg -n -H FIXME_JEVTO src
```

When the goal or trusted constraints change for the same session and workspace, increase the task frame's `revision`. JevTO stores a local revision and content fingerprint, not the goal text, for the capture retention window. It checks again after the child command finishes, so a newer revision accepted while that child runs makes the older result lose task-specific selection and remote Jev. The child still completes and the receipt records `task_frame_fallback`. This does not serialize every concurrent action after that check.

The preview executes the command and prints the bounded outbound request without contacting Jev. The second command can make at most one OpenRouter Decisions request for that wrapped result. It sends the short goal, query, paths, line ranges, and up to 24 bounded search sections; it does not send stderr or unrelated capture bytes. Each run requires the opt-in flag and policy. If input is unsuitable, private, unreducible, or Jev fails, selection falls back to deterministic mode and the receipt records why. A successful decision keeps four sections and renders compact output by default; use `--format verbose` for the audit-oriented form. The response-reported model, usage, cost, and latency are in `jevto report --json`. There is no enforced account-wide spend cap in this build.

For the Claude project hook, preview before applying and use the same store path for removal. The hook preserves unrelated Claude settings and refuses changed owned entries:

```powershell
.\target\debug\jevto.exe --store-dir C:\path\to\store init-claude-hook --workspace .
.\target\debug\jevto.exe --store-dir C:\path\to\store init-claude-hook --workspace . --apply
.\target\debug\jevto.exe --store-dir C:\path\to\store disable-claude-hook --workspace . --apply
```

Deterministic hook use needs no environment variables. Adaptive hook use additionally requires `JEVTO_ADAPTIVE=1`, `JEVTO_TASK_FILE`, `JEVTO_REMOTE_POLICY`, and `OPENROUTER_API_KEY` in Claude's process environment. The installer does not write these values or enable adaptive mode. Exact recall covers the text Claude supplied to `PostToolUse`; it does not prove byte identity with the original child process.

The static website is in [`site/`](../site/). From this directory, `python -m http.server 4173 --directory site` serves a local preview. Its evidence viewer is a labeled synthetic illustration, not a live command runner.

## Storage and privacy

The deterministic path makes no remote Jev request and emits no JevTO telemetry. Adaptive mode has a separate outbound opt-in and a conservative local filter for paths and secret-looking text; that filter is not a guarantee that all sensitive text will be recognized. By default, captures, selected packs, receipts, and task revision fingerprints live under `%LOCALAPPDATA%\JevTO` on Windows or `$XDG_DATA_HOME/jevto` (otherwise `~/.local/share/jevto`) on Unix. Selected packs can contain trusted constraints shown in the first view. The Codex pre-hook instead uses the installing project's ignored `.jevto-store` so sandboxed commands can write captures. `--store-dir` or `JEVTO_STORE_DIR` overrides the default, and `JEVTO_MAX_STORE_BYTES` overrides the default 1 GiB store limit. Raw command arguments, environment variables, and the OpenRouter key are not stored; captured output itself may contain secrets, so review what you wrap. JevTO does not add encryption at rest.

Recall expires after 24 hours. Expired captures and their packs are removed on the next capture or by `jevto purge`. A valid capture ID permits recall from the same user store; the current interface does not authenticate the caller against the capture's recorded session or workspace. `jevto run` buffers up to 64 MiB of combined stdout and stderr by default (or less when the store ceiling is smaller). `JEVTO_MAX_CAPTURE_BYTES` changes that memory limit. When output exceeds it, JevTO immediately forwards the buffered prefix and streams the rest, records `capture_limit_exceeded`, and retains no recallable capture for that run. Output within the limit is selected after the child exits. A failed store write falls back to delivering the child's original output and status.

## Evidence and benchmark

The Rust suite checks schema versioning, separate stream hashes, exact recall, partial and expired captures, protected warnings and failures, overflow, the default MCP tool boundary, and stale diff advice. A separate MCP smoke checks exact-command allowlisting, nonzero child status, byte-exact recall, binary content, and oversized output. These are local correctness checks, not savings evidence. The [benchmark harness](../benchmarks/README.md) prepares isolated synthetic checkouts and pins the exact Cursor model ID `grok-4.7-xhigh`; Grok Fast and other models are forbidden for benchmark arms.

The [bounded pilot report](../benchmarks/pilot-report.md) records four exact-model runs across two tiny fixtures. The already-lean pair passed with JevTO passing output through. Both quiet-warning changes passed the original checks but failed a stronger adversarial holdout added after the pair. Cursor did not expose provider session usage or a bill. The harness supplied the selected evidence in the prompt; it did not intercept Cursor's native tools. These runs support no cost, time, or quality equivalence claim.

In a disposable Codex CLI 0.144.4 smoke, a local instruction led the agent to run both a passing and a failing verifier through the explicit shell wrapper and to recall one deferred section. The first view was 561 bytes from 2,268 captured bytes for the passing command and 638 from 2,346 for the failing command. The agent recalled the failing command's omitted 2,176-byte section. A second run under stricter managed policy blocked JevTO and the verifier before execution; no capture was created by those blocked calls.

A separate disposable Codex project used the installed `PostToolUse` hook on a successful verifier. Codex passed 2,268 bytes to the hook and showed the agent a 607-byte selection; JevTO stored exactly those 2,268 hook-visible bytes for recall. The installer was then disabled and left an unrelated hook in place. In the observed nonzero run, Codex did not invoke this hook, despite the host documentation describing nonzero `PostToolUse` behavior. These first-view byte reductions are not net token-saving measurements: there is no paired provider usage, and recall or hook overhead can offset the saved input.

A [Codex post-hook diagnostic](../benchmarks/codex-hook-diagnostic.md) found that the hook emitted a 642-byte pack from the same 2,268-byte result, but the model did not report its capture ID. The nested code-mode tool result contained the original output. In one paired run, Codex reported 53,497 input tokens with the hook installed versus 49,490 without it. This pair does not demonstrate savings. Hook receipts distinguish a replacement request from confirmed host delivery. A separate [pre-hook host check](../benchmarks/codex-prehook-evidence.md) documents the working CLI 0.158 route and its limits.

In a later [Codex pre-hook pair](../benchmarks/codex-prehook-pair-report.md), two matched runs on a synthetic 500-test command each reported 3,178–3,392 fewer Codex input tokens with the pre-hook than with native output. All four runs passed the test suite; the agent saw both JevTO capture IDs. This is a measured reduction for one narrow command journey, not evidence of lower cost or equal quality across coding tasks. The earlier post-hook result and the new pre-hook result apply to different routes.

A separate [Codex search host check](../benchmarks/codex-rg-prehook-evidence.md) routed one `rg -n -H` command through the pre-hook: the agent saw 945 bytes from 5,671 captured bytes, reported all 80 matches and the capture ID, and exact full recall matched the native command. A missing-file command stayed failed with its error intact. This is host-path evidence, not a paired provider-token result.

A [Codex Cargo check host check](../benchmarks/codex-cargo-check-prehook-evidence.md) routed one 24-crate check through the pre-hook: the agent saw 1,142 bytes from 2,424 captured bytes, reported the correct crate count and capture ID, and full recall matched exactly. A compile error kept exit 101 and its diagnostic; an outside-workspace Cargo target was denied. This is another host-path observation, not a paired provider-token result.

A [Codex Cargo build host check](../benchmarks/codex-cargo-build-prehook-evidence.md) routed a 24-crate build through the same pre-hook. The debug build delivered 1,142 bytes from 2,424 captured bytes; a retest with the then-current versioned executable delivered 1,170 bytes because its recall commands were longer. The agent reported the capture ID, and full recall returned the captured stderr. A compile failure kept its diagnostic and outer exit 101, and an outside-workspace Cargo target was denied. This is a host-path check, not a provider-token pair.

A [Codex MCP host check](../benchmarks/codex-mcp-host-evidence.md) exercised the installed server on CLI `0.158.0-alpha.2.1`: the model called status, recalled 18 exact bytes, observed an invalid capture error, and received an advisory `new_file` finding from a disposable Git project. The scoped MCP approval grant was needed under the user's noninteractive approval policy. These calls did not replace ordinary shell output or measure a saving.

A [Cursor MCP status check](../benchmarks/cursor-mcp-transport-diagnostic.md) on exact `grok-4.7-xhigh` produced a completed model-driven tool call through the current registered binary. Cursor forwarded JevTO's bounded text summary, and the final host check matched the local receipt: one run, 22 raw and delivered bytes, exit 0, `already_lean`. It used force mode and a disabled Windows sandbox in an empty disposable workspace; it did not route ordinary Cursor commands or measure a coding-task saving.

A later [Cursor explicit-command pilot](../benchmarks/cursor-route-pair-report.md) completed a native/JevTO pair on a synthetic 80-test command with the same exact model. Both arms correctly reported exit 0 and 80 passed tests. Cursor reported 28,599 native input tokens versus 14,231 with the explicit wrapper, while the JevTO arm took 4.884 seconds longer. The native arm made two additional Cursor tool calls, and there is no matching provider bill; the pair is evidence of a narrow observed session difference, not a causal estimate of payload savings or a realistic coding-task benefit. A preceding diagnostic exposed an incorrect two-test inference from a shortened log; JevTO now prints the exact retained-plus-deferred test count, and the corrected route was rechecked with a completed Cursor turn.

A separate [Cursor exact-command MCP check](../benchmarks/cursor-mcp-run-evidence.md) used the same exact model in two disposable projects. The model called the policy-bound `jevto_run` tool once for 80 passing tests and once for a failed assertion with child exit 101. It reported each result accurately; local full recall matched native stdout and stderr. The project execution servers were disabled afterward. This verifies a narrow opted-in host route, not automatic shell interception or a paired token saving.

A separate [OpenRouter Jev probe](../benchmarks/jev-probe-report.md) made five bounded, synthetic decisions with the pinned Jev 1.13 model. One relevant warning received a 0.44 relevance probability, reinforcing the local rule that remote scores cannot demote protected evidence. After integration, one opt-in synthetic CLI run returned the resolved model `typesafe/jev-1.13-20260917`, 1,112 Jev input tokens, and a response-reported cost of $0.000046704. It delivered 809 bytes from 2,341 raw bytes while retaining the warning and failure. This is local payload reduction, not measured coding-agent token savings.

JevTO's value must eventually be tested against native agent operation and compatible existing tools on verified task completion, total cost, wall time, and code quality. Shorter command output alone does not establish a better outcome.

## License

Apache-2.0. See [LICENSE](../LICENSE). JevTO is independent of TypeSafe AI.
