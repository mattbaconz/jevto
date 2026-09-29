# Codex `cargo check` pre-hook host check — 2026-09-27

This is a host-path and exact-recall check, not a paired token or cost benchmark. It used Codex CLI `0.158.0-alpha.2.1`, `gpt-6-luna`, Windows PowerShell, and the `workspace-write` sandbox. No Cursor model or OpenRouter Jev request was used.

## Fixture and route

The disposable workspace `D:\jevto\.tooling\cargo-progress-probe` contains 24 local Rust library crates with no external dependencies. Each has a one-line `lib.rs`. A reviewed project `PreToolUse` hook invoked the local debug build and rewrote the plain command `cargo check --workspace` to an explicit JevTO wrapper in that workspace. Unattended automation used `--dangerously-bypass-hook-trust` for this disposable hook only; the sandbox and shell approval policy remained active. A normal user must review and trust the project hook. The host event exposed `tool_input.command` but omitted shell and workdir fields, as in the earlier Rust-test check. The later versioned release was built from this source and registered for MCP, but its `cargo check` hook was not separately host-retested.

The reducer recognizes only Cargo's formatted `Checking` and `Compiling` progress lines on a successful `cargo` child. It defers the middle of a run of at least 16 such lines, keeping the first two, last two, warnings, final summary, and task-mentioned crate names. A failed check passes through intact. The hook accepts only the existing strict PowerShell command grammar, now including `cargo check`; pipelines, redirection, unknown flags, and other commands remain native.

## Observations

| Check | Observed result |
| --- | --- |
| Successful check | Codex ran one rewritten `cargo check --workspace`. The child checked 24 crates and exited 0. JevTO captured 2,424 stderr bytes, delivered a 1,142-byte agent-visible pack, and deferred 20 progress lines. The agent reported 24 crates and capture `cd796b74-8e4d-4d42-93a8-f7f789f3bec5`. Full recall matched all 2,424 stored stderr bytes. |
| Compiler failure | With one disposable `lib.rs` containing `let x = ;`, the wrapper retained `error: expected expression, found ';'` and the file location. After the hook appended `exit $LASTEXITCODE`, Codex's outer shell reported exit 101 and the agent reported 101. JevTO's receipt recorded 342 raw and delivered bytes, `already_lean`, and child exit 101. No capture ID was printed because no shortened pack was delivered; the agent correctly said none when asked to report only a visible ID. The fixture source was restored. |
| Workspace permission | With `CARGO_TARGET_DIR` set to an explicitly named path outside the disposable workspace, Cargo failed with `Access is denied. (os error 5)` and exit 101. The agent reported that failure, the receipt retained the 105-byte diagnostic, and the outside target directory was absent afterward. An earlier attempt to create a deliberate outside-write build script was rejected by automatic execution policy before it ran; no script or sentinel was created. |
| Disable | The installer preview changed no files. After the host checks, `disable-codex-pre-hook --apply` removed the only JevTO `PreToolUse` group. Four ignored captures remained for inspection. |

The first failed host probe, before the exit propagation repair, had kept the diagnostic but Codex reported outer exit 1. The agent invented a short capture ID after a prompt asked for one. The later run with explicit `exit $LASTEXITCODE` corrected the outer status, and a prompt conditioned on a visible ID avoided that unsupported claim. For pass-through failures, the receipt has the historical capture ID while the native-looking tool output need not show it.

The successful check reduced agent-visible bytes for this one command. It does not establish a Codex token saving, lower bill, whole-task benefit, or quality equivalence. Approval prompts, other shells/client versions, and a Cargo check with private dependencies remain unverified.
