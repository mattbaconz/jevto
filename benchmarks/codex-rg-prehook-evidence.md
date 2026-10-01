# Codex line-numbered search route — 2026-09-27

This is a host integration check for a narrow `rg` search path, not a token-saving benchmark. It is separate from the Grok 4.7 xhigh Cursor pilot. No OpenRouter or Jev call occurred.

## Setup

- Host: Codex desktop-bundled CLI `0.158.0-alpha.2.1`, Windows PowerShell, model `gpt-6-luna`, `workspace-write` sandbox.
- Disposable Git workspace: `<workdir>\codex-rg-fixture`. It contained one file with 80 identical, long search-match lines. A project `PreToolUse` hook installed by `jevto init-codex-pre-hook --workspace ... --apply` rewrote only a simple `rg -n -H needle search.txt` command to the explicit JevTO wrapper. The installer preview was inspected first. For unattended runs, `--dangerously-bypass-hook-trust` trusted only this reviewed local hook; no sandbox-bypass or approval-bypass flag was set. Normal use still requires Codex hook trust review.
- Raw host traces remain in the disposable workspace as `rg-success.jsonl` and `rg-failure.jsonl`. The currently built JevTO debug executable and its product installer were used.

## Observed paths

| Check | Observation |
| --- | --- |
| Matching search | Codex ran one rewritten shell command. `rg` exited 0. JevTO captured 5,671 stdout bytes and delivered a 945-byte result containing the first two and last two locations, the fact that 76 middle lines were deferred, a capture ID, and recall commands. The agent reported 80 matches, the capture ID `9ffa45c5-1d40-44cc-9abb-1c8beb533227`, and the omission count. |
| Exact recall | `jevto --store-dir <workspace>\.jevto-store recall <capture> --full` returned 5,671 stdout bytes and empty stderr. These matched a direct native `rg -n -H needle search.txt` byte for byte; the stdout SHA256 was `f712df02f5e6640a470850dc844833a33ed5ee9e70389d0af4356194b67e10e5`. |
| Missing file | Codex ran one rewritten `rg -n -H needle missing.txt` command. The original `rg` error remained agent-visible; its child exit was 2 in the JevTO capture and the outer PowerShell tool reported exit 1. The receipt recorded `captured: true`, `replaced: false`, and bypass reason `search_nonzero_status`. The agent correctly reported a failed command. |
| Disable | `disable-codex-pre-hook --apply` removed the owned hook. `PreToolUse` had zero groups afterward; the two captures and `.jevto-store/.gitignore` remained, and `git check-ignore` confirmed captures were ignored. The ownership record was removed. |

The parser reduces only runs of at least 12 consecutive line-numbered results with the same path and exact content. It keeps the first two and last two locations, protects warning/failure-looking text, and passes through ambiguous, unsupported, binary, and nonzero search results. The Codex hook accepts a small unquoted command grammar; shell operators, globs, quoted patterns, absolute or parent paths, and unsupported flags bypass it. The wrapped process remains subject to the host's permissions. An `rg`-specific permission denial and approval prompt were not exercised. No general Cursor or Claude interception is implied.
