# Changelog

All notable changes to this project are recorded here. JevTO is experimental; benchmark results are exploratory and failures are published alongside them.

## Unreleased

### Added
- One-command PowerShell and shell installers at jevto.xyz, with platform selection, pinned release checksums, executable verification, and user PATH setup. No Rust toolchain or administrator access is required.
- Installer checks for clean installs, replacement, repeat installs, profile preservation, and rejected downloads; CI also installs the published binary on Windows, Linux, and both macOS architectures.

### Changed
- The website and README lead with the one-command install; the docs put Claude Code setup next and retain manual downloads, source builds, and removal instructions. The installers currently distribute the existing v0.1.1 release.

## 0.1.1 - 2026-10-01

### Added
- Doctor reports configured and effective modes and conditional network eligibility without revealing API keys or making a request.
- Run and Claude search-hook receipts record request outcomes separately from cache hits and fallback reasons. Gain/report and benchmark summaries expose unknown outcomes and incomplete costs; valid response usage survives a rejected decision or failed cache write. Existing receipt and summary fields remain available.
- Six fresh offline task fixtures have separately exported evaluators and positive/negative controls, file hashes, and explicit native/rules/rules+Jev arms. No agent runs, isolation proof, or savings results are implied by preparation.
- CI runs the offline request-accounting and fresh-task control smokes.

### Changed
- README distinguishes harness-valid sessions from passing hidden holdouts in the earlier Claude/Codex pilot and attributes the Claude 0/12 holdout result to the model (Haiku 4.5; every arm, native included, broke the same stated rule).
- README documents installing prebuilt release binaries and `cargo install --git`.
- Release notes contain only the tagged version's CHANGELOG section; a tag without one fails the release.
- Host evidence notes use `<workdir>`/`<workspace>` placeholders instead of local machine paths.

## 0.1.0 - 2026-09-30

### Added
- Reduction plan with per-line reasons (`reduce.rs`): passing-test recognition for Rust libtest, Go `-v`, Python unittest and pytest, node:test, Jest/Vitest, and TAP; runner boilerplate; terminal progress redraws; identical and near-identical line runs; lockfile and minified-file diffs; long-output windows that keep head, tail, protected facts with context, goal identifiers, and rare "anomaly" lines.
- Adaptive Jev ranking for any output type (search groups, per-file diffs, long-output chunks) using Choice, Noul, and Score in one bounded request; need-driven keep count; request sizing that shrinks section text to fit; policy flags `allow_output_snippets` and `allow_diff_snippets`.
- `jevto review` findings for weakened, skipped, and deleted tests and for npm, pip/pyproject, Go, and Bundler dependency additions; optional Jev scope review (per-file Noul plus a scope Score).
- Claude Code auto-route: `init-claude-auto` / `disable-claude-auto` install a `PreToolUse` rewrite for allowlisted simple commands and a `UserPromptSubmit` hook that records the session goal locally.
- `jevto gain`: bytes and estimated tokens saved, recalls, and Jev calls and cost from local receipts.
- `benchmarks/token_bench.py`: eleven-scenario payload benchmark comparing native, RTK, and JevTO through each tool's own hook, with answerability needles.
- `benchmarks/claude_ponytail_pilot.py`: Claude Code session pilot comparing native, Ponytail, JevTO auto-route, and both, with hidden holdout checks.
- OpenCode plugin (`integrations/opencode/jevto.js`) and `benchmarks/agent_pilot.py` for Codex CLI and OpenCode.
- Codex pre-hook routes any allowlisted single PowerShell command (for example `python -m unittest`, `go test`, `npm test`), not only `cargo` and `rg`.
- Reduction policy v9.1: search results keep the first matches, every definition, and goal matches; a `git diff` that repeats one mechanical edit across files shows it once and keeps every file header; runtime-internal and dependency stack frames are deferred; failure recaps that echo visible lines are dropped silently; more runner chrome (`running N tests`, Cargo `Finished`, pytest headers, node:test zero counters) and dangling blank lines are silent.
- Short recall IDs: views print the first 8 characters of the capture ID, and `jevto recall` resolves them, as well as `last` for the newest capture.
- Jev requests send long-output candidates as line-shape digests (rare lines verbatim, repeats counted), and receipts record the prepared request size (`jev_request`) even when no key is set.
- Benchmark v2: 23 scenarios in dev, holdout (written after the rules froze, `benchmarks/holdout_scenarios.py`), and semantic suites; effective tokens charge a missed fact the native payload; a Jev request-size and cost table; `--suite` and `--render-only`.
- Brand: flat JevTO mark and lockups (`assets/brand/`), reproducible renders (`render.py`, `compare.py`), a VHS terminal demo (`assets/demo/`), big-type README banner, numbers and comparison posters (`numbers.py`, `compare.py`), rebranded charts with suite grouping, a caveman-voice README, and the made-by-mattbaconz banner.
- First live Jev benchmark (`typesafe/jev-1.13`): the adaptive arm kept 46/46 facts at 13,628 estimated tokens (rules alone: 45/46 at 16,979); cached decisions in `benchmarks/jev-cache/`.
- Release workflow: tagged pushes build Linux, macOS (arm64, x86_64), and Windows binaries with SHA256 checksums into a draft GitHub release.
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, CI workflow, issue templates.

### Changed
- **Full Jev v0:** `--mode` now defaults to `auto`. With `OPENROUTER_API_KEY` set, runs with a goal use Jev ranking under an implicit current-workspace policy (no flags or policy file); without a key, rules only. `JEVTO_MODE=rules` opts out; `rules` and `jev` are aliases for `deterministic` and `adaptive`. Receipts record `jev_auto`.
- Compact view shows inline gap markers with section IDs and prints a short capture ID once; the separate `tests=N ok` line is gone because the gap marker states the count. Passing inventories are deferred from three lines up.
- `jevto recall ID` returns the full capture when no narrower selection is given.
- Passing-test lines whose names contain words such as "error" are treated as passes; malformed lines still stay visible.
- Adaptive runs check the local decision cache before requiring an API key.
- Detailed host observations moved from the README to `docs/EVIDENCE.md`.

### Fixed
- On Linux and macOS, output streamed through after the capture limit could lose a trailing partial line; bypassed output is now flushed as it passes and before exit.
- Path checks for search targets refuse Windows drives, roots, and `..\` escapes on every platform.
- Jev request timeout raised from 4 s to 8 s; requests near the 64 KiB cap timed out in live runs.
- Workspace is clippy-clean with `-D warnings`; CI clippy is now blocking.
- Whitespace-only lines no longer count as goal-relevant, which had fragmented long outputs.
- `jevto run` resolves bare program names through PATH and PATHEXT on Windows, so `.cmd` shims such as `tsc`, `npm`, `npx`, and `eslint` start instead of failing with "program not found" (found by the holdout benchmark).
- Goal words that match many lines of a long output (a service name, a timestamp) no longer overflow the view budget and force full passthrough (found by the holdout benchmark).
- Adaptive search no longer re-protects matched `Err(`/`assert!` source lines, and a Jev need level sets a minimum number of kept sections (policy v9.2; found by the first live Jev run).
- Gaps smaller than their marker stay visible, and Go's interleaved `=== RUN` lines no longer produce one marker per test.
