# Claude adaptive search pilot

Date: 2026-09-28

## Scope

This pilot covers the project-local Claude Code `PostToolUse` hook and the frozen adaptive-search benchmark. Claude is pinned to `claude-haiku-4-5-20251001`. Cursor remains a separate benchmark pinned to `grok-4.7-xhigh`; Fast is not an accepted substitute.

The hook handles successful `Bash`, `PowerShell`, and `Grep` search results. It captures the text Claude supplies to the hook, stores it for exact recall, and requests a shape-preserving `updatedToolOutput` only when the compact result is smaller. Adaptive selection is opt in and requires a task frame, an outbound policy, and an OpenRouter key supplied only to that process.

## Hook evidence

Claude Code CLI `2.1.283` ran on the signed-in Pro account with terminal `modelUsage` containing only the pinned Haiku ID.

- A native Bash `rg` turn delivered a compact JevTO result to Claude: 6,069 hook-visible bytes became 583 bytes. Claude reported the correct first selected line. Full recall matched all 6,069 hook-visible bytes. The operating-system command had 6,071 bytes because Claude removed the final CRLF before invoking the hook, so this proves hook-visible recall rather than raw child-process recall. Claude's list-price estimate was `$0.0071402`.
- A pre-fix native Grep turn exposed Claude's actual object result: `content` used `line:text` for a single requested file and kept `mode`, `numFiles`, `filenames`, `numLines`, and `totalLines`. The older parser passed it through. That turn's list-price estimate was `$0.0254563`.
- After the parser fix, the saved native Grep event was replayed byte for byte through the final rebuilt hook. The hook preserved every object field, replaced only `content`, reduced 5,110 hook-visible bytes to 534, and produced capture `75be007d-e3d3-4d91-8d5d-b55d35409406`. Full recall matched all 5,110 input bytes. This is exact-event local proof; a post-fix native Grep delivery remains pending.
- The owned hook was removed from the disposable project after the checks. The remaining `PostToolUse` list is empty.

Artifacts are under `.bench-runs/claude-hook-probe-20260928T182909/`. The native Bash stream is `claude-stream-repeated.jsonl`; the native pre-fix Grep stream is `claude-stream-grep.jsonl`; the post-fix replay input and final output are `grep-hook-replay-input.json` and `grep-hook-replay-final-output.json`.

## Frozen coding campaign

[`claude_adaptive_search_benchmark.py`](claude_adaptive_search_benchmark.py) freezes six synthetic Python search tasks, three arms, and two repeats for 36 planned sessions. Every fixture has 24 candidate files and 120 matching lines. Each run uses a fresh Git checkout, one exact search command, one visible verifier, a holdout frozen before the model call, a file-scope check, a trace-grounded final-answer review, and a scoped diff review.

The harness rejects missing usage, a model ID other than the pinned Haiku snapshot, incomplete turns, a missing JevTO receipt, failed visible or holdout tests, changed tests, excess file scope, malformed capture claims, and missing exact recall. It records fresh input, cache creation, cache reads, output, their sum, Claude's list-price estimate, elapsed time, tool calls, diff, verifier outputs, and Jev usage. Savings are calculated only for equal verified outcomes.

Limits are 300 seconds, 12 turns, and `$0.20` in Claude's CLI estimate per session, plus `$1.00` in response-reported OpenRouter cost for the campaign. `--safe-mode` is not used because it disables project hooks. Isolation instead uses project-local settings only, a strict empty MCP configuration, disabled slash commands, and a fixed tool allowlist.

## Current campaign result

No coding arm ran. A fresh Claude turn reported five-hour status `allowed_warning`, utilization `0.9`, no overage, and reset timestamp `1790600400`. The approved protocol stops at that boundary. The final harness recorded all 36 planned arms as excluded with `pro_allowance_boundary` in [the local manifest](../.bench-runs/claude-adaptive-search-20260928T121848Z/manifest.json); [the generated report](../.bench-runs/claude-adaptive-search-20260928T121848Z/report.md) lists every arm and every missing usage field.

No OpenRouter request was made during this campaign setup, and the credential was not written to a prompt, trace, manifest, or repository file. There is no fresh whole-session token comparison and no new savings claim. The earlier five-arm instructed-wrapper pilot remains the only Claude coding result; it showed about five percent fewer combined tokens on one equal-outcome Rust task and no clear adaptive advantage.

## Conclusion

The hook and frozen benchmark are implemented, and compact delivery is proven for a native Bash result. The Grep parser is proven against Claude's exact saved event shape. The larger campaign must run after the Pro allowance resets before JevTO can claim that adaptive semantic selection is clearly useful. Its preregistered bar is at least 20% below native and 10% below deterministic whole-session tokens on equal verified outcomes, with lower estimated total cost and median elapsed time within 10%.
