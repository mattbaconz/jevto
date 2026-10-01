# Cursor headless coding pilot attempt — 2026-09-28 local time

This is an **invalid paired pilot**, kept as a transport and harness record. It gives no token-saving, cost, time, or task-quality comparison.

The harness used staged Cursor CLI `2026.09.23-86fc751`, exact requested ID `grok-4.7-xhigh`, and `Grok 4.7 256K Extra High` in both stream init events. JevTO was the registered local release `<workdir>\bin\jevto-0.1.0-aac1ba3e26c4.exe` (SHA256 `aac1ba3e26c446f52cec915d32f21427cb3c04da1886ae550e6ba98d1c62bd0c`). Both arms began from the same `quiet_warning` tree hash `e2afe18b3baacec9f8088091c1416de3c181e723805226c48a86839e1b013862` in separate disposable Git checkouts. All seven configured MCP servers were disabled per checkout, `--force --trust --sandbox disabled` and headless `stream-json` were used in both, and each had a 180-second cap. No Fast model, substitute model, OpenRouter Jev request, or benchmark credit purchase was used.

| Arm | Cursor stream and command observations | Final state |
| --- | --- | --- |
| Native | 75 JSON events, 9 tool events, exact model init. A `git log --oneline -20 && git show HEAD --stat` shell call started but had no completion event before the cap. No terminal `result` event or usage fields. | Timed out at 180.808 s; no file diff; visible and strengthened holdout checks failed. Ineligible. |
| JevTO deterministic | 100 JSON events, 22 tool events, exact model init. The agent ran JevTO `--help`, then `run --help` and `recall --help`, but did not invoke `jevto run -- python verify.py`. No JevTO receipt, terminal `result` event, or usage fields. | Timed out at 180.809 s; no file diff; visible and strengthened holdout checks failed. Ineligible. |

The absence of a terminal usage event means usage is **unknown**, not zero. A pending native shell call does not establish that all Cursor shell calls fail; the JevTO arm's help calls completed. The responding MCP status call in [the separate host diagnostic](cursor-mcp-transport-diagnostic.md) proves only that status tool path. Neither coding arm reached a verified completion or an agent-controlled JevTO capture, so comparing elapsed times or bytes would be misleading.

Artifacts are local and ignored: `.bench-runs/pilot-20260927T170927Z/` contains the manifest, both stream traces, result JSON, candidate diffs, and verifier output. The harness now accepts explicit Cursor and JevTO executable paths, checks the exact model label and terminal success, requires a JevTO receipt for that arm, and stops future runs after an invalid arm with exit 2. `python -m py_compile benchmarks/pilot.py` and `python benchmarks/pilot.py --self-check` passed. A future coding pair needs a responding Cursor shell/edit path and a completed final candidate under the same model before any session-token comparison.

## Pager-controlled follow-up

One further native attempt set `GIT_PAGER=cat`, `PAGER=cat`, and `GIT_TERMINAL_PROMPT=0` for the disposable Cursor process and raised the cap to 240 seconds. Its exact-model init, 198 JSON events, and 26 tool events are in `.bench-runs/pilot-20260927T172549Z/`. Cursor started and completed a `python verify.py` shell tool call, but that call was marked background. It later inspected that process tree. The candidate changed by 14 diff lines and passed both the visible and strengthened holdout verifiers when the harness checked it afterward. Cursor still emitted **no terminal result event or usage fields** before the 240.975-second cap. The arm is therefore ineligible despite the passing candidate. The updated harness stopped with exit 2 before launching the JevTO arm.

This follow-up changed both the pager environment and time cap, so it does not identify what allowed the edit. It shows that a passing file state can exist without a completed agent turn; treating the file state as a valid timing or token result would overstate the evidence. No further Grok arm was run after this bounded stop.
