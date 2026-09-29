# Opt-in Jev CLI smoke — 2026-09-27

This was one synthetic `jevto run --mode adaptive --allow-remote-jev` invocation on Windows. The source harness is [`adaptive_smoke.py`](adaptive_smoke.py). The task goal was “Investigate storage reconnect behavior”; the child printed 80 passing Rust-style test names, a warning, a failed test line, and an assertion, then exited 101. Only the goal and at most 24 passing test names were eligible for the outbound Choice request. The OpenRouter key was supplied to the process environment for this run and was not written to the repository or receipt.

| Observation | Value |
| --- | ---: |
| Requested model | `typesafe/jev-1.13` |
| Resolved response model | `typesafe/jev-1.13-20260917` |
| Jev input tokens in response | 1,112 |
| Jev response-reported cost | $0.000046704 |
| Jev round trip | 1,162 ms |
| Raw captured output | 2,341 bytes |
| CLI-delivered view | 809 bytes |
| Child exit | 101, preserved |
| Warning and failed assertion | Visible |
| Selected passing test | `storage::reconnect`, visible |

The byte difference is a local payload measurement for one deliberately wrapped command. The Jev input tokens and cost belong to the decision call, not the coding agent. No Cursor, Codex, or Claude provider usage was observed for this run, and there is no measured whole-task saving or quality conclusion. The [earlier Noul probe](jev-probe-report.md) reported $0.00007875 across five synthetic calls; all six response-reported costs together are $0.000125454 under the user's $1 task cap.
