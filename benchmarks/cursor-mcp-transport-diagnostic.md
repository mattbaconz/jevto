# Cursor xhigh MCP transport diagnostic — 2026-09-27

This is a bounded host check, not a benchmark arm or token-saving result. Every model probe requested the exact `grok-4.7-xhigh` ID and showed the Extra High label. No Fast model was used, and no OpenRouter Jev request was made.

## Versions and MCP state

- The PATH Cursor CLI `2026.06.04-5fd875e` listed `grok-4.7-xhigh` but showed `jevto: not loaded (needs approval)` even after `mcp enable jevto` said it was already approved.
- The separately staged CLI `2026.09.23-86fc751`, used for the earlier four-arm pilot, listed the same exact model and showed `jevto: ready` with the current registered JevTO executable.
- In a disposable workspace, the staged CLI initially showed six other MCP servers in their existing ready/authentication/error states and JevTO ready. Six other servers were disabled for an isolated JevTO probe, then restored. A plain-prompt probe also ran with all seven disabled. Afterward, all seven statuses in the disposable workspace matched their initial values; the normal `<workspace>` workspace statuses were unchanged. Cursor documents project and global MCP configuration and CLI enable/disable behavior in its [MCP CLI guide](https://prod.cursor.com/docs/cli/mcp).

## Bounded probes

| Transport and scope | Observation |
| --- | --- |
| Staged CLI headless `--print --output-format stream-json --mode ask --trust`, exact model, JevTO the only enabled MCP | No stream event or answer after 90 seconds. The same live process was interrupted. |
| Staged CLI interactive terminal, exact model, JevTO the only enabled MCP, status-tool prompt | The Extra High label and submitted prompt were visible. No tool result, approval prompt, or final answer appeared within 75 seconds. |
| Staged CLI interactive terminal, exact model, plain one-line answer prompt, JevTO the only enabled MCP | The prompt was submitted but no final answer appeared within 50 seconds. |
| Same interactive plain prompt with every MCP disabled in the disposable workspace | No final answer appeared within 40 seconds. This shows the observed stall was not caused solely by JevTO or another MCP server. |
| Same interactive plain prompt with `--sandbox disabled` and normal MCP state, without `--force` | No final answer appeared within 53 seconds. |

The interactive traces show the exact model label and a persistent working indicator. The first headless trace has zero events. Those probes exposed no matching provider usage or bill, so no token or cost figure is inferred from them. The earlier successful four-arm pilot used the staged CLI with a different prompt, force/sandbox settings, and all project MCPs disabled; it remains a separate historical observation. The initial probes did not prove the cause of the stall.

## Follow-up on 2026-09-28 local time

The same staged CLI responded when a disposable workspace used `--force --sandbox disabled` and all seven MCP servers were disabled. A read-only interactive prompt on exact `grok-4.7-xhigh` displayed `Grok 4.7  Extra High` and returned `FORCE_PROBE_DONE` after 67.5 seconds from submission. The workspace's MCP status text matched before and after the probe. This isolates a responding model path but does not identify which setting or transient host condition changed the earlier result. Cursor's [headless CLI guide](https://docs.cursor.com/en/cli/headless) documents `--force` and streaming JSON; its [parameter reference](https://docs.cursor.com/en/cli/reference/parameters) describes force as allowing commands unless explicitly denied. We used a disposable workspace and a no-tool prompt for this check.

With six other MCP servers disabled and JevTO left ready, an interactive prompt on the same exact model returned `none` mode and zero runs for an empty session. A subsequent headless `stream-json` probe created one explicit local JevTO run, then asked the model to call `jevto_status` for that session. The stream had a `system/init` event naming `Grok 4.7 256K Extra High`, a completed `mcpToolCall` to `jevto_status`, and a successful result. Cursor forwarded only JevTO's `content` text, omitting its `structuredContent`. The old text reported one deterministic run but omitted raw bytes, child exit, and bypass reason; the model correctly said those fields were unavailable. This was a real integration gap.

JevTO's status text now includes aggregate bytes and bounded per-run mode, bytes, child exit, and bypass reason. On release SHA256 `aac1ba3e26c446f52cec915d32f21427cb3c04da1886ae550e6ba98d1c62bd0c`, a second headless run on the exact model produced 31 JSON events, including a completed `mcpToolCall` to `jevto_status` and a successful final result. The tool text and the model's answer both reported **one deterministic run, 26 raw and delivered bytes, child exit 0, and `already_lean`**. The local receipt independently recorded those values. The final CLI elapsed 91.03 seconds, with 66.09 seconds in its result event. Its stream reported `inputTokens: 19832`, `outputTokens: 346`, and `cacheReadTokens: 38784` for this status task; those are CLI fields, not a matched bill or evidence of savings. No Fast model, OpenRouter Jev request, or substitute model was used. Six other MCP statuses in the disposable workspace matched their initial text after restoration.

This proves model-driven **status** on Cursor CLI `2026.09.23-86fc751` under the tested force and disabled-Windows-sandbox settings. It does not prove Cursor MCP recall/review, approval behavior without force, ordinary shell-result interception, or a coding-task token benefit. The local artifacts are `.tooling/cursor-force-transport.ansi.txt`, `.tooling/cursor-force-mcp.ansi.txt`, `.tooling/cursor-force-mcp-payload.jsonl`, and `.tooling/cursor-force-mcp-payload-final.jsonl`.

## Current registered binary check — 2026-09-28 local time

The user-scoped Cursor entry was later refreshed to `<workdir>\bin\jevto-0.1.0-469b14ed1dc6.exe`, SHA256 `469b14ed1dc601ce37a623edf3801ff9bf682a8207ab7f1ff998db65b538495a`. `jevto doctor --json` reported its Cursor registration `current`, and `cursor-agent mcp list` reported `jevto: ready`. Cursor CLI `2026.09.26-dd393fe` listed the exact `grok-4.7-xhigh` model. A local explicit JevTO run in an empty disposable workspace created one receipt for session `cursor-current-32cd098f1243`: 22 raw and delivered bytes, child exit 0, `already_lean`.

A fresh headless Cursor turn requested `grok-4.7-xhigh` with `--force --trust --sandbox disabled`, `stream-json`, and that empty workspace. Its init event reported `Grok 4.7 256K Extra High`. The trace contains one completed MCP discovery call, one completed `jevto_status` call, no shell call, and one successful terminal result. The MCP text and final answer reported one run, 22/22 bytes, exit 0, and `already_lean`, matching the local receipt. The terminal CLI reported input 20,140, output 560, cache read 42,752, cache write 0; these are usage fields for a status probe, with no native pair or bill. The 36-event trace, stderr, and summary are under `<workdir>\cursor-current-host-check\20260927T225201Z`. The workspace remained empty. This verifies status through the **current** Cursor registration; recall/review, unforced approval, ordinary shell interception, and savings remain unverified. No Fast model or OpenRouter Jev call was used.
