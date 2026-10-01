# Contributing to JevTO

Thanks for helping. JevTO's promise is narrow and strict: a smaller view **without losing a fact the agent needs**, with every hidden byte recallable. Contributions are judged against that promise first and against token savings second.

## Ground rules

- **Never paraphrase.** Views contain exact lines from the capture, plus clearly marked JevTO summary lines (`jevto exit=…`, `tests=… ok`, gap markers). Do not rewrite, truncate inside a line, or summarize source, assertions, or diagnostics.
- **Protect runtime facts.** Lines that report failures, panics, assertions, warnings, or exit status stay visible. If you add a rule that can hide such a line, it needs a test showing why the fact is still visible (for example, an identical copy remains).
- **Unknown means untouched.** Recognizers must be conservative. If a format is ambiguous, leave the line visible.
- **The model is advisory.** Jev may rank and prioritize; it may not remove protected facts, decide correctness, or widen permissions. All arithmetic, budgets, and privacy checks stay in code.
- **Claims need evidence.** Don't describe a host route as supported without a named host version and exercised success, failure, and permission paths. Benchmark numbers come from `benchmarks/token_bench.py` or the agent-level harnesses, not from estimates.

## Layout

| Path | What lives there |
| --- | --- |
| `crates/jevto-core/src/reduce.rs` | Line-level recognizers and the reduction plan |
| `crates/jevto-core/src/pack.rs` | Evidence packs, adaptive section candidates, rendering |
| `crates/jevto-core/src/review.rs` | Advisory diff review |
| `crates/jevto-core/src/store.rs` | Captures, recall, receipts, session goals |
| `crates/jevto-cli/src/jev.rs` | Jev requests, validation, cache, scope review |
| `crates/jevto-cli/src/claude_auto.rs` | Claude Code auto-route and installer |
| `benchmarks/token_bench.py` | Payload benchmark against native and RTK |
| `tests/*.py` | End-to-end smoke tests against the built binary |

## Adding a recognizer

1. Capture a real output of the tool (redact anything private) and note the command and version.
2. Add a focused function in `reduce.rs` (for example, a new passing-test pattern in `passing_test_name`) and wire it into `plan`.
3. Add unit tests that show: the noise is deferred, every failure/warning line survives, and a near-miss line (a failure that looks similar) stays visible.
4. If the tool is common in agent workflows, add a scenario to `benchmarks/token_bench.py` with needles that are format-neutral facts, and, if needed, add the program to `eligible` in `claude_auto.rs`.
5. Run the checks below and include before/after token counts in the pull request.

## Checks

```sh
cargo fmt --all -- --check
cargo clippy --workspace --all-targets
cargo test --workspace
cargo build -p jevto
python tests/cli_smoke.py
python tests/claude_auto_smoke.py
python tests/mcp_run_smoke.py
python tests/accounting_smoke.py        # fabricated responses on loopback; no provider calls
python tests/fresh_tasks_smoke.py       # offline task controls; no coding-agent runs
python benchmarks/token_bench.py        # optional; needs cargo/go/python, RTK optional
```

Benchmark scenarios generate their workspaces from scratch, so results are comparable across machines, but runner timings differ. Commit `benchmarks/results/` only when a change affects selection.

## Pull requests

Keep changes focused, explain the user-visible effect, and list what you ran. There is no CLA; contributions are accepted under Apache-2.0. Please follow the [code of conduct](CODE_OF_CONDUCT.md).
