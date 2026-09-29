# JevTO bounded Cursor pilot — 2026-09-25

This report is an exploratory harness result, not a product savings claim. Four paired-arm runs used Cursor CLI `2026.09.23-86fc751`, the exact requested model ID `grok-4.7-xhigh`, and the interactive label `Grok 4.7 256K Extra High`. The staged launcher SHA-256 was `11169ef90694ac10c60e685761b25be5848f92d44cfe5ee6a89010ed5735eff7`; the JevTO binary SHA-256 was `49fc9c724039ac9919bc13c6efe9c7128fe8fb41f583deb879df3562475c7846`. Each run used a new isolated Git checkout and Cursor session. The paired initial tree hashes matched within each task. Windows sandbox mode was disabled because Cursor reported its sandbox unavailable on Windows; force mode and the same six project MCP disable settings were used in all four checkouts. Cursor's cache policy and provider usage were not observable.

The harness ran the initial failing verifier itself. Native arms received that output; JevTO arms received `jevto run -- python verify.py` output. The model was asked to edit with file tools and leave command execution to the harness. This is **harness-mediated payload selection**, with zero agent-controlled JevTO calls and zero native host interception coverage. A final response marker ended the measured turn, followed by visible and holdout verification on the edited checkout. The intervention changes the evidence text and its label; it does not establish end-to-end integration.

| Fixture | Arm | Original → shown prompt bytes | Cursor submit-to-marker | Initial visible / holdout | Later quality audit |
| --- | --- | ---: | ---: | --- | --- |
| Quiet warning | Native | 1,304 → 1,304 | 107.270 s | pass / pass | fail |
| Quiet warning | JevTO deterministic | 1,340 → 1,221 | 127.381 s | pass / pass | fail |
| Already lean | JevTO deterministic | 367 → 367 | 112.157 s | pass / pass | pass |
| Already lean | Native | 349 → 349 | 79.699 s | pass / pass | pass |

The quiet-warning JevTO receipt recorded 1,373 raw bytes, 1,232 delivered bytes, `captured=true`, `replaced=true`, and zero recalls. The already-lean receipt recorded 372 raw and 372 delivered bytes with `replaced=false` and `bypass_reason=already_lean`. Receipt bytes include the child's original CRLF bytes; prompt byte fields above count UTF-8 text after Python newline conversion. The checkouts have different absolute path lengths in Python tracebacks. Replacing those path prefixes with the same placeholder for descriptive comparison gives 1,150 original and 1,031 selected prompt bytes for quiet warning, and 273 bytes in both already-lean arms. The model saw the unnormalized paths; this is a small uncontrolled input difference.

Both quiet-warning agents made the same one-line change, `if "value=" in line`. It passed the original visible and hidden checks but violated the task's instruction to ignore other log lines: a later holdout with `warning: expected value=99, got 2` made both candidates raise `ValueError`. This was found **after** the pair, so `.bench-runs/pilot-injected-20260925T124000Z/posthoc-quality-audit.json` is kept separate from the original verification record. The strengthened holdout is now in the harness for future runs. The already-lean pair made the same one-line uppercase fix and passed an independent final rerun, recorded in its own `posthoc-quality-audit.json`.

The local artifacts are ignored by Git and remain only in this workspace:

- `.bench-runs/pilot-injected-20260925T124000Z/` — quiet-warning pair, original manifests, terminal traces, diffs, receipts, and later audit.
- `.bench-runs/pilot-injected-20260925T124652Z/` — already-lean pair.
- `.bench-runs/pilot-pty-20260925T122016Z/` — a direct interactive xhigh arm that edited and passed the then-current checks, but Cursor kept a finished verifier shell task active past the 240-second cap. It is excluded from paired timing.
- `.bench-runs/pilot-20260925T113503Z/` — headless stream-json attempts that emitted no model init/result before timeout. They are excluded.

A separate ACP transport probe started with the xhigh CLI flag but reported `grok-4.7[context=256k,reasoning_effort=high,fast=true]` in session metadata. It sent one trivial `READY` request before that mismatch was inspected. It is **excluded**, was never used for a benchmark arm, and the ACP probe now refuses a session whose model metadata differs from the pinned xhigh ID. No further Fast request was sent. This is a model-pin failure of that transport, not evidence about JevTO.

Cursor did not expose provider input, cached input, output tokens, or a matching bill in these terminal traces. Tool-call and retry counts are unknown. These four tiny tasks cannot support a cost, time, or quality equivalence claim. The slower JevTO-arm times here are observations, not a causal estimate. Adaptive Jev, RTK, Headroom, Ponytail, and host read/write ablations were not run in this bounded pilot.
