# Cursor usage fields in JevTO's local pilot

Cursor's [TypeScript SDK token-usage reference](https://cursor.com/docs/sdk/typescript#token-usage) defines `inputTokens` as prompt tokens sent to the model, `outputTokens` as generated tokens, `cacheReadTokens` as tokens served from prompt cache, and `cacheWriteTokens` as tokens written there. It defines `totalTokens` as their sum and says reported token counts alone do not establish billed cost. The [Python SDK reference](https://prod.cursor.com/docs/sdk/python#token-usage) specifies the same formula and says its wire JSON uses camelCase field names.

The completed `grok-4.7-xhigh` project-rule coding pair's raw CLI terminal events are in the ignored `.bench-runs/cursor-rule-coding-native_rule-20260927T220719Z/cursor-stream.jsonl` and `.bench-runs/cursor-rule-coding-jevto_rule-20260927T221150Z/cursor-stream.jsonl`. They contain the four named usage fields but no `totalTokens`. `pilot.stream_observations` preserves the four reported fields and separately derives a sum only when all four are present as nonnegative integers. This assumes the CLI fields have the same semantics as the SDK fields; Cursor's CLI stream does not itself confirm that assumption or expose a bill.

| Arm | Input | Output | Cache read | Cache write | Derived sum | Frozen holdout |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Native rule | 66,542 | 7,254 | 203,776 | 0 | 277,572 | Passed |
| JevTO rule | 120,060 | 3,935 | 87,040 | 0 | 211,035 | Failed |

The JevTO arm has 53,518 more input tokens and 66,537 fewer in the derived sum, largely because cache-read usage differs. The outcomes are unequal and the cache conditions were not controlled tightly enough to infer a savings effect. See [the full pair report](cursor-rule-coding-pilot.md) for fixture identity, route evidence, and exclusions. Do not treat this as a cost or quality win.
