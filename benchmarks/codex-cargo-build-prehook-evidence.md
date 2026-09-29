# Codex `cargo build` pre-hook host check — 2026-09-27

This checks a named agent route, byte selection, exact recall, failure status, and workspace permissions. It is not a paired token or cost benchmark. No Cursor or OpenRouter Jev request was made.

## Setup

- Host: desktop-bundled Codex CLI `0.158.0-alpha.2.1`, `gpt-6-luna`, Windows PowerShell, `workspace-write` sandbox, and the existing shell approval policy. The disposable project was `D:\jevto\.tooling\cargo-progress-probe`, with 24 local no-dependency Rust crates.
- JevTO host-probed debug executable SHA256: `b7649f14acdf94767da29dfc3a20eda539ed575279ade5ad6a1992f451a102e4`. The project `PreToolUse` hook rewrote the plain `cargo build --workspace` command to the explicit wrapper. Unattended automation used `--dangerously-bypass-hook-trust` for this reviewed disposable hook; it did not bypass the workspace sandbox or shell approval policy. A normal user must review and trust the project hook.
- The reducer accepts formatted successful `Compiling` and `Checking` lines from a `cargo` child, keeps the first and last two lines in a run of at least 16, and retains warnings, failures, and final status. Failed builds pass through. Shell operators, other commands, and unrecognized flags are not rewritten.

## Observations

| Check | Observed result |
| --- | --- |
| Direct wrapper | A fresh native `cargo build --workspace` produced 2,424 captured stderr bytes. JevTO delivered 1,142 bytes, deferred 20 progress lines, and returned child exit 0. Full recall returned all 2,424 stderr bytes. |
| Model-driven success | Codex ran one rewritten build, saw the 1,142-byte pack, and reported 24 compiled crates, child exit 0, and capture `a3f37f70-9cb6-49e1-bf8b-cd8b72c1f5e8`. The receipt recorded 2,424 raw bytes, 1,142 delivered bytes, and `replaced: true`. Full recall returned 2,424 stderr bytes. Session: `01a0e3a6-c9db-7691-a418-7a4d97fadc4f`. |
| Compiler failure | A disposable crate contained `let x = ;`. Codex received `error: expected expression, found ';'` with the exact file location, and its outer shell reported exit 101. The result stayed native-looking at 357 raw and delivered bytes with `already_lean`; no capture ID was visibly printed, and the agent reported none. The fixture source was restored. Session: `01a0e3a7-c64a-7612-ab4e-c534f13445a8`. |
| Workspace permission | With `CARGO_TARGET_DIR` set outside the disposable project, Cargo received `Access is denied. (os error 5)` and the outer shell reported exit 101. The target directory was absent afterward. The receipt recorded 111 raw and delivered bytes and `already_lean`. Session: `01a0e3a8-8632-7411-bb78-fd14c5f6557d`. |
| Disable | Preview changed nothing. The final disable removed the owned `PreToolUse` group, leaving zero groups and retaining ignored local captures. |

The local traces are `D:\jevto\.tooling\cargo-build-{success,failure,permission}-trace.jsonl`. The observed 1,282-byte first-view reduction applies to the debug-build command. It does not prove lower provider tokens, lower billed cost, faster work, quality equivalence, or broad agent integration. Other shells, client versions, and approval prompts remain unverified.

## Versioned release retest

The final registered executable, SHA256 `ca8a1820cbf8555896d6ab4d6b9a0bb55a9b09b1dd01df03afe78a7edeb2eb2c`, installed the same project hook in the disposable workspace. Under Codex CLI `0.158.0-alpha.2.1`, `gpt-6-luna`, Windows PowerShell, and `workspace-write`, the agent ran one fresh `cargo build --workspace` through that exact executable. It reported 24 crates, child exit 0, and capture `f2be1e0d-4dcd-4cea-aa6b-2b085e8a4c8e`. The receipt recorded 2,424 raw and **1,170 delivered bytes**; the longer release path lengthened the printed recall commands compared with the debug build. Full recall returned all 2,424 captured stderr bytes. The hook was disabled afterward, leaving zero `PreToolUse` groups. Session: `01a0e3b7-e6a4-75f3-8c87-321cd947ad75`; trace: `D:\jevto\.tooling\cargo-build-release-host-trace.jsonl`.
