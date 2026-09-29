# JevTO token benchmark

Generated 2026-09-30 with `jevto.exe` (jevto 0.1.0), RTK rtk 0.48.0, on win32.

**Method.** Every scenario builds a small real workspace and runs one real command. Each arm is chosen by that tool's own Claude Code hook (`rtk hook claude`, `jevto hook claude-pre`), so an arm is whatever the tool actually does when an agent types the command. Tokens are bytes/4 estimates of what reaches the agent for that one command. **Facts** are exact strings the task needs (needles), counted when visible without a recall. **Effective tokens** add the native payload whenever a fact is missing, because the agent then has to go back for the full output; the same penalty applies to every tool.

**Suites.** *dev*: Dev set (rules were tuned on these). *holdout*: Holdout set (written after the rules froze; run once). *semantic*: Semantic set (goal and answer share no words; the Jev cases).

## Summary

| Suite | Scenarios | Native | RTK | JevTO | JevTO + Jev | Effective tokens (Native → RTK → JevTO) |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| dev | 11 | **99,738** · 20/20 | **71,320 (−28%)** · 18/20 | **4,991 (−95%)** · 20/20 | **4,361 (−96%)** · 20/20 | 99,738 → 91,163 → 4,991 |
| holdout | 9 | **123,099** · 23/23 | **118,503 (−4%)** · 23/23 | **7,371 (−94%)** · 23/23 | **7,371 (−94%)** · 23/23 | 123,099 → 118,503 → 7,371 |
| semantic | 3 | **46,899** · 3/3 | **46,712 (−0%)** · 3/3 | **4,617 (−90%)** · 2/3 | **1,896 (−96%)** · 3/3 | 46,899 → 46,712 → 5,749 |
| **All** | 23 | **269,736** · 46/46 | **236,535 (−12%)** · 44/46 | **16,979 (−94%)** · 45/46 | **13,628 (−95%)** · 46/46 | 269,736 → 256,378 → 18,111 |

## Dev set (rules were tuned on these)

| Scenario | Command | Native | RTK | JevTO | JevTO + Jev |
| --- | --- | ---: | ---: | ---: | ---: |
| rust-test-failure | `cargo test` | 1,245 · 3/3 | 126 (−90%) · 3/3 | 155 (−88%) · 3/3 | 155 (−88%) · 3/3 |
| go-test-failure | `go test -v -count=1 ./...` | 2,651 · 2/2 | 64 (−98%) · 2/2 | 78 (−97%) · 2/2 | 78 (−97%) · 2/2 |
| python-unittest-failure | `python -m unittest -v` | 3,572 · 3/3 | 3,572 (−0%) · 3/3 (not routed) | 200 (−94%) · 3/3 | 200 (−94%) · 3/3 |
| python-quiet-warning | `python -m unittest -v` | 3,397 · 2/2 | 3,397 (−0%) · 2/2 (not routed) | 89 (−97%) · 2/2 | 89 (−97%) · 2/2 |
| node-test-failure | `node --test` | 1,576 · 2/2 | 1,574 (−0%) · 2/2 (not routed) | 197 (−88%) · 2/2 | 197 (−88%) · 2/2 |
| rust-test-lean | `cargo test` | 119 · 1/1 | 10 (−92%) · 1/1 | 61 (−49%) · 1/1 | 61 (−49%) · 1/1 |
| service-log-triage | `python print_log.py` | 52,163 · 2/2 | 52,163 (−0%) · 2/2 (not routed) | 2,075 (−96%) · 2/2 (explicit `jevto run`) | 2,075 (−96%) · 2/2 (explicit `jevto run`) |
| search-many-hits | `rg -n -H retry_budget src --sort=path` | 3,688 · 1/1 | 3,688 (−0%) · 1/1 | 356 (−90%) · 1/1 | 233 (−94%) · 1/1 |
| git-diff-lockfile | `git diff` | 11,484 · 2/2 | 766 (−93%) · 2/2 | 120 (−99%) · 2/2 | 120 (−99%) · 2/2 |
| git-diff-multi-file | `git diff` | 7,225 · 1/1 | 5,738 (−21%) · 0/1 ✗ | 1,109 (−85%) · 1/1 | 602 (−92%) · 1/1 |
| git-log-history | `git log` | 12,618 · 1/1 | 222 (−98%) · 0/1 ✗ | 551 (−96%) · 1/1 | 551 (−96%) · 1/1 |

## Holdout set (written after the rules froze; run once)

**Holdout run log.** Run 1 (policy v9, [`token-bench-run1-v9.json`](token-bench-run1-v9.json)) found two real JevTO bugs: `jevto run` could not start Windows `.cmd` shims, so the routed `tsc` command failed with exit 2 instead of 1 (`npm`, `npx`, `eslint` were equally affected); and goal words that match hundreds of log lines (`checkout`, `10:42`) overflowed the view budget, so the 434 KB JSON log fell back to full passthrough (0% cut). Both cargo scenarios did not run in any arm because the harness shell lacked the Rust toolchain (a harness error, not a result). Fixes: PATH/PATHEXT resolution for bare program names, and a stricter goal-word frequency cap (policy v9.1). Run 2 (below) is the first run with all nine scenarios valid; no rule was changed in response to a specific holdout output other than these two bug fixes. Live Jev run 1 (2026-09-30, [`token-bench-live1-v9.1.json`](token-bench-live1-v9.1.json)) exposed two adaptive-policy bugs: the adaptive search view re-protected matched `Err(`/`assert!` code lines (search-many-hits grew from 356 to 1,778 tokens), and a high need score (3.4 of 4) still kept only the single top-ranked file, dropping the rename in git-diff-rename-plus-fix. Fixed in policy v9.2 (search matches are not runtime facts; need levels set a minimum keep count); the stale cached decision was deleted and re-requested. The tables below are live run 2.

| Scenario | Command | Native | RTK | JevTO | JevTO + Jev |
| --- | --- | ---: | ---: | ---: | ---: |
| cargo-multi-failure | `cargo test` | 4,311 · 6/6 | 205 (−95%) · 6/6 | 280 (−94%) · 6/6 | 280 (−94%) · 6/6 |
| tsc-type-errors | `tsc -p .` | 70 · 3/3 | 90 (−-29%) · 3/3 | 70 (−0%) · 3/3 | 70 (−0%) · 3/3 |
| go-package-failure | `go test -count=1 ./...` | 129 · 2/2 | 58 (−55%) · 2/2 | 99 (−23%) · 2/2 | 99 (−23%) · 2/2 |
| node-all-pass | `node --test` | 2,571 · 2/2 | 2,574 (−-0%) · 2/2 (not routed) | 48 (−98%) · 2/2 | 48 (−98%) · 2/2 |
| python-stdlib-traceback | `python -m unittest -v` | 1,261 · 3/3 | 1,261 (−0%) · 3/3 (not routed) | 389 (−69%) · 3/3 | 389 (−69%) · 3/3 |
| rg-todo-sweep | `rg -n -H TODO src --sort=path` | 1,913 · 1/1 | 1,913 (−0%) · 1/1 | 215 (−89%) · 1/1 | 215 (−89%) · 1/1 |
| git-diff-rename-plus-fix | `git diff` | 2,233 · 2/2 | 1,849 (−17%) · 2/2 | 835 (−63%) · 2/2 | 835 (−63%) · 2/2 |
| cargo-build-warnings | `cargo build` | 2,084 · 3/3 | 2,026 (−3%) · 3/3 | 2,084 (−0%) · 3/3 | 2,084 (−0%) · 3/3 |
| json-log-triage | `python emit_log.py` | 108,527 · 1/1 | 108,527 (−0%) · 1/1 (not routed) | 3,351 (−97%) · 1/1 (explicit `jevto run`) | 3,351 (−97%) · 1/1 (explicit `jevto run`) |

## Semantic set (goal and answer share no words; the Jev cases)

| Scenario | Command | Native | RTK | JevTO | JevTO + Jev |
| --- | --- | ---: | ---: | ---: | ---: |
| semantic-log-signout | `python print_log.py` | 42,783 · 1/1 | 42,783 (−0%) · 1/1 (not routed) | 1,411 (−97%) · 1/1 (explicit `jevto run`) | 1,411 (−97%) · 1/1 (explicit `jevto run`) |
| semantic-search-deadline | `rg -n -H timeout src --sort=path` | 1,132 · 1/1 | 1,132 (−0%) · 1/1 | 222 (−80%) · 0/1 ✗ | 68 (−94%) · 1/1 |
| semantic-diff-rounding | `git diff` | 2,984 · 1/1 | 2,797 (−6%) · 1/1 | 2,984 (−0%) · 1/1 | 417 (−86%) · 1/1 |

## Jev requests (adaptive arm)

What JevTO would send to Jev for each scenario, measured before any network call. Long-output candidates are sent as line-shape digests (rare lines verbatim, repeats counted), so a 200 KB log becomes a small request. Cost uses $0.042 per million input tokens (bytes/4).

| Scenario | Kind | Candidates | Request | Est. cost per decision | Calls | Outcome |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| rust-test-failure |  |  |  |  | 0 | no_rankable_sections |
| go-test-failure |  |  |  |  | 0 | no_rankable_sections |
| python-unittest-failure |  |  |  |  | 0 | no_rankable_sections |
| python-quiet-warning |  |  |  |  | 0 | no_rankable_sections |
| node-test-failure |  |  |  |  | 0 | no_rankable_sections |
| rust-test-lean |  |  |  |  | 0 | no_rankable_sections |
| service-log-triage | command_output | 60 | 12.2 KB | $0.00013 | 0 | jev_no_relevant_evidence |
| search-many-hits | search_results | 28 | 20.2 KB | $0.00022 | 0 | kept 2/28, need 1.9 |
| git-diff-lockfile |  |  |  |  | 0 | no_reducible_sections |
| git-diff-multi-file | diff_files | 14 | 33.2 KB | $0.00036 | 0 | kept 1/14, need 1.1 |
| git-log-history | command_output | 63 | 28.2 KB | $0.00030 | 0 | jev_http_failed |
| cargo-multi-failure |  |  |  |  | 0 | no_rankable_sections |
| tsc-type-errors |  |  |  |  | 0 | no_rankable_sections |
| go-package-failure |  |  |  |  | 0 | no_rankable_sections |
| node-all-pass |  |  |  |  | 0 | no_rankable_sections |
| python-stdlib-traceback |  |  |  |  | 0 | no_rankable_sections |
| rg-todo-sweep |  |  |  |  | 0 | search_candidate_limit |
| git-diff-rename-plus-fix | diff_files | 21 | 12.6 KB | $0.00014 | 0 | kept 4/21, need 3.4 |
| cargo-build-warnings |  |  |  |  | 0 | no_rankable_sections |
| json-log-triage | command_output | 60 | 17.0 KB | $0.00018 | 0 | jev_no_relevant_evidence |
| semantic-log-signout | command_output | 61 | 45.2 KB | $0.00049 | 0 | jev_http_failed |
| semantic-search-deadline | search_results | 25 | 9.0 KB | $0.00010 | 0 | kept 1/25, need 1.5 |
| semantic-diff-rounding | diff_files | 13 | 14.4 KB | $0.00016 | 0 | kept 1/13, need 1.5 |

`missing_jev_key` means the request was prepared and sized but not sent. `no_rankable_sections` / `no_reducible_sections` mean the deterministic view already had nothing left to rank, so Jev is never called.

Reproduce: `cargo build --release -p jevto && python benchmarks/token_bench.py`. Committed Jev decisions in `benchmarks/jev-cache/` replay without a key; `--live-jev` with `OPENROUTER_API_KEY` set makes fresh calls for cache misses only.
