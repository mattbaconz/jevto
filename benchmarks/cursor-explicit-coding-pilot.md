# Cursor edit-and-verify pilot with an explicit wrapper — 2026-09-28

This exploratory pair used staged Cursor CLI `2026.09.23-86fc751` with exact `grok-4.7-xhigh`, reported in both streams as `Grok 4.7 256K Extra High`. No Fast or substitute model was used. Both arms began at the same disposable tracked Git tree SHA256 `0ff680a80a093a926c7da82c9d6704df53086b0858e87fff48a434a49cc09a55` and received the same fix request. Only the verification route differed: native `python verify_once.py` versus explicitly instructed `jevto run -- python verify_once.py`. Both used fresh sessions, `--force --trust --sandbox disabled` on Windows, and the same set of project-disabled user MCP servers. The JevTO arm used versioned binary SHA256 `bee26c2ee80921121ef644c5d17f5fb52cdc2b7a41bc2e8a6b10cc312398a2e0`.

The task was to make `summarize.total` count only lines **beginning** with `value=`, ignoring warnings and notes that mention that marker. The visible verifier printed 80 generated passing cases plus one summary case. The independent holdout originally checked warnings, notes, negative values, and the empty case. `cursor_runner_coding_probe.py` retains the prompts, fixture preparation, exact model preflight, terminal traces, source hashes, verifier marker, diff, independent verification, receipts, and exact-recall check. Raw artifacts are local in ignored `.bench-runs/cursor-runner-coding-explicit-20260927T204622Z/` and `.bench-runs/cursor-runner-coding-native-20260927T205159Z/`.

## What happened

| Observation | JevTO explicit wrapper | Native shell |
| --- | --- | --- |
| Initial tree | Same SHA256 | Same SHA256 |
| Agent edit | Trimmed each line, then checked `value=` prefix | Checked `value=` at the start of the original line |
| Agent verifier | Ran through `jevto run`, child exit 0 | Ran natively, exit 0 |
| Original visible and holdout checks | Passed | Passed |
| Final source verifier marker | Matched final source | Matched final source |
| Original headless turn | CLI exit 1 after 188.910 s; no terminal result or usage | Terminal success after 172.869 s |
| Recovery | Same Cursor session resumed; terminal success in 35.932 s without another tool call | Not needed |
| Provider usage | Original turn unavailable; resume-only 350 input, 360 output, 24,960 cache-read tokens | 24,926 input, 2,549 output, 151,808 cache-read tokens |

The JevTO CLI receipt recorded 2,392 raw bytes, 748 delivered bytes, child exit 0, and capture `34b3a3eb-2487-45e8-9959-af4bfcd4981d`. The completed tool result contained the capture ID. The local harness's full recall matched a fresh verifier run on the **same final source** byte for byte on stdout and stderr. That harness recall, not an agent recall, incremented the receipt's recall count. Cursor had read the completed result and was preparing its final answer when the original CLI process exited with `RetriableError: [aborted] read ECONNRESET`. A single `--resume` of that session returned the correct 81 passing lines, child exit 0, and matching capture ID without edits or tool calls. The resume usage is not the original task's missing usage.

## Stronger semantic check after the pair

Inspecting the diffs exposed an untested interpretation of “beginning”: the JevTO-arm candidate calls `strip()` before checking the prefix, so it counts `" value=7"`. The native candidate checks `line.startswith("value=")` and ignores that line. A new holdout assertion requiring `total([" value=7", "value=2"]) == 2` was added **after** the model runs. Re-evaluating the saved checkouts with that disclosed posthoc assertion made the JevTO candidate fail and the native candidate pass. The original results remain in their `result.json` files; `.bench-runs/cursor-coding-posthoc-leading-space.json` records the stronger check and its hash.

The stricter check is supported by the literal task wording, but it was not preregistered. It prevents a claim that these two edits have equal quality; it is not a confirmatory failure-rate estimate. JevTO selected verifier output **after** its agent made the incorrect edit, so this run does not show that deferred evidence caused that mistake. The paired token difference is **unavailable** because the original JevTO turn emitted no usage record. The native usage, smaller JevTO tool result, and resume-only usage cannot be subtracted to invent one. No matching provider bill or OpenRouter Jev call was involved.

This pilot establishes that the explicit wrapper can run during a tracked Git coding task and that the current evaluator needed a literal-start edge case. It does not establish whole-task savings, speed, quality equivalence, automatic Cursor interception, or tracked-project MCP runner support. A future main benchmark needs frozen holdouts before runs and a complete usage trace for every compared arm.
