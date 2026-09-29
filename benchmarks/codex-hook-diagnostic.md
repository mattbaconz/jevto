# Codex project hook diagnostic — 2026-09-27

This is a small host integration diagnostic, **not** a Cursor benchmark or a whole-task savings result. No Grok model was used in these Codex runs. The Cursor benchmark remains pinned to `grok-4.7-xhigh` and never uses Fast.

## Setup

- Host: Codex desktop-bundled CLI `0.158.0-alpha.2.1` on Windows, with its configured default model. The JSONL usage records do not identify a resolved model ID.
- Workspace: disposable Git fixture with `noisy.py`, which prints 80 passing Rust-style test lines and a passing summary.
- Both arms: same prompt, sandbox `workspace-write`, one successful `python noisy.py` command, final answer `80`.
- Hook arm: `jevto init-codex-hook --workspace . --apply`, then disabled after the run. The unrelated project hook remained. The fixture used `--dangerously-bypass-hook-trust` after local review so an automated run could proceed; ordinary use requires Codex's hook trust review.
- No OpenRouter call or key was used.

| Observed quantity | No JevTO hook | JevTO hook installed |
| --- | ---: | ---: |
| CLI exit | 0 | 0 |
| Command tool calls | 1 | 1 |
| Final answer | `80` | `80` |
| Codex reported input tokens | 49,490 | 53,497 |
| Of those, cached input tokens | 37,248 | 39,168 |
| Derived uncached input tokens | 12,242 | 14,329 |
| Codex reported output tokens | 266 | 389 |
| Hook-visible input bytes | — | 2,268 |
| JevTO pack bytes emitted to hook stdout | — | 642 |

The hook ran and stored the 2,268-byte host response. Its local receipt recorded a 642-byte pack, but that receipt cannot establish delivery to the model. A separate visibility probe asked the agent to report the emitted `capture_id`; it replied `NONE`. The nested code-mode `tools.exec_command` result in Codex's rollout contained the original output and no capture ID. This establishes that replacement **did not reach that code-mode call**. It does not establish behavior for a direct, non-code-mode tool call. [OpenAI Codex issue #36940](https://github.com/openai/codex/issues/36940) describes the same code-mode `PostToolUse` result path.

The hook-installed arm used 4,007 more reported input tokens and 123 more output tokens in this single pair. Cache state, model behavior, and host delivery were not controlled enough to attribute that difference to the hook. It certainly does not demonstrate net token savings. The earlier Codex CLI `0.144.4` fixture did show an agent using a JevTO capture ID from an installed hook; that observation is version-specific and does not establish this newer CLI's behavior.

The project records hook receipt `replaced: false` and `host_delivery_confirmed: false` until a host-visible acknowledgement is available. The emitted pack size is a local payload measurement, not provider usage. A separate [pre-hook route](codex-prehook-evidence.md) uses a reviewed command rewrite before execution and has agent-visible passing and failing results on this CLI version.
