# Codex edit-and-verify pair — 2026-09-28

This is one exploratory **Codex** coding-task pair. It is separate from the Cursor benchmark, which remains pinned to `grok-4.7-xhigh`. Both arms finished the same fix and passed independent checks. The result is evidence for the tested Codex pre-hook path, not a general token, cost, or quality claim. The raw JSONL traces, manifests, diffs, and captures are local in ignored `.bench-runs/codex-coding-pair-20260927T202349Z/`.

## Setup and validity

- Desktop-bundled Codex CLI `0.158.0-alpha.2.1` (SHA256 `8f0554ede25bbc5450921897c468b2e84635aa513c5017457997af0954581f49`) requested `gpt-6-sol` with `max` reasoning, `workspace-write` shell sandbox, and a fresh ephemeral session for each arm. The JSONL stream reports usage but does not independently identify the resolved model. The approved local hook used `--dangerously-bypass-hook-trust`; shell sandbox and ordinary approval settings were not bypassed.
- The JevTO arm used versioned executable SHA256 `bee26c2ee80921121ef644c5d17f5fb52cdc2b7a41bc2e8a6b10cc312398a2e0`. OpenRouter and Cursor were not called. The script removed the hook afterward; `.codex/hooks.json` had an empty `PreToolUse` list. Historical captures remained in the ignored disposable project store.
- The two disposable Git projects had the same initial tree `f2a40e2e5d5611fa74d630c2ef82b0c76d29e718`, the same prompt, and the same initially failing test. The fixture first ran 499 passing inventory tests, then failed a critical case because `total` accepted `value=` inside a warning. Each arm read the same source and critical test, edited only `src/lib.rs`, and ran `cargo test --workspace` once after the edit. The final source SHA256 was identical in both arms (`b72b14c374d235b2d3502b736811b7b44d3a86b7825322822850c47009eb3dcb`). Both final runs reported 500 passed, 0 failed; an independent three-case Rust holdout passed for each candidate.
- The harness ran JevTO first, then native. Its exact prompt and fixture generator are in [codex_coding_pair.py](codex_coding_pair.py). Reproduce from `repo` with `python benchmarks/codex_coding_pair.py --codex-cli C:\path\to\codex.exe --jevto-cli C:\path\to\jevto.exe`. It stops after an invalid JevTO arm and retains traces. After the first run, its validation was tightened to require a source edit before the final Cargo call, unchanged tests, only the source file modified, and final-answer counts; the saved traces passed the new ordering check without another model run.

## Observed pair

| Arm | Codex input tokens | Cached input | Output tokens | Final Cargo tool result | Wall time | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| JevTO pre-hook | 113,144 | 96,000 | 1,242 | 2,191 bytes | 58.762 s | 500 visible + 3 holdout tests passed |
| Native shell | 116,957 | 96,128 | 1,278 | 11,354 bytes | 55.605 s | 500 visible + 3 holdout tests passed |

Codex reported **3,813 fewer input tokens** in the JevTO session (3.26% of native input) and the JevTO session took **3.157 seconds longer**. Each arm had three completed shell calls, one of them Cargo. The JevTO receipt recorded 11,353 raw and 2,191 delivered bytes for its successful Cargo call, with capture `83106ce5-4694-448a-977c-711a9a6e5ecc`; the model included that ID in its final answer. The native result came from a separate execution, so its bytes are not an exact raw-output control for the hooked execution.

The order was fixed, cached input differed by 128 tokens, and the provider did not expose a matching bill. One small fixture with identical final diffs does not establish general savings, faster work, quality equivalence, or adaptive Jev value. It also does not repair Cursor's tracked-project MCP discovery failure or substitute another model for its required Grok benchmark.
