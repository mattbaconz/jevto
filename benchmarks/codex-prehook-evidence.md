# Codex pre-hook integration check — 2026-09-27

This is a bounded host-path check, not a whole-task token benchmark. No Grok or paid Jev model was used. The Cursor benchmark remains pinned to `grok-4.7-xhigh` and never uses Fast.

## Environment and route

- Host: Codex desktop-bundled CLI `0.158.0-alpha.2.1` on Windows PowerShell, model `gpt-6-luna`, `workspace-write` sandbox.
- Workspace: a disposable local Git project with 80 trivial Rust tests. The installed project `PreToolUse` `Bash` hook rewrote one plain `cargo test --workspace` call to `jevto --store-dir <project>\.jevto-store run -- cargo test --workspace`. `--dangerously-bypass-hook-trust` was used only for this reviewed disposable hook in unattended automation; the sandbox and approval settings were not bypassed. Normal users must review and trust the project hook.
- The observed hook event exposed `tool_input.command` but omitted the shell and workdir fields supplied to the nested shell tool. The implementation therefore accepts an omitted shell on the tested Windows PowerShell route. A different default shell or host event shape is unverified.
- The first store location under `.codex` could not write a capture from Codex's sandbox. The installer now creates a project-root `.jevto-store` with an internal `.gitignore` that ignores captures. `git check-ignore` confirmed a capture path is ignored. The installed hook uses this root; the old failed run is not counted as a successful capture.

## Observations

| Check | Observed result |
| --- | --- |
| Passing run | One wrapped `cargo test --workspace` produced 80 passed, 0 failed. JevTO captured 1,818 stdout bytes and 197 stderr bytes, and delivered a 1,176-byte selected shell result. The agent reported capture `7d0f975f-155a-493d-ac1f-a93331fa1a48` and the passing status. |
| Exact historical recall | `jevto --store-dir <project>\.jevto-store recall <capture> --full` returned 1,818 stdout bytes identical to the stored stdout for the passing run. A deferred section returned its named test lines. |
| Failing run | Changing one fixture assertion yielded 79 passed, 1 failed. The agent received capture `a86f5a4c-6d23-48bd-9a6f-3edb63d3a375`, the failed `case_79` assertion (`left: 2`, `right: 3`), and JevTO's child exit 101. Codex's outer PowerShell tool reported exit 1. The 1,962-byte failed stdout recalled byte-for-byte from the stored capture. The assertion was restored afterward. |
| Sandbox check | A temporary 81st Rust test asserted that writing a sentinel outside the fixture failed. The hooked Codex run reported 81 passed, 0 failed and a JevTO capture ID; the sentinel did not exist after the run. The temporary test was removed afterward. This observes filesystem sandbox preservation for this route, not every approval scenario. |
| Session receipts | A later passing run carried validated Codex session `01a0e2ee-3dfe-75a1-9a3d-fe3c7d0c7366` into the wrapper. The agent reported capture `5f184fb1-ddab-4c33-98f6-477b9eadbcef`; `jevto --store-dir <project>\.jevto-store report --session <session> --json` returned a receipt containing that capture. |
| Installer/disable | Setup preview changed no files. Unit tests cover BOM preservation, unrelated hooks, exact-owned removal, refusal to remove a changed entry, and refusal to hide existing unignored store files. A live disable/reinstall cycle preserved the unrelated `PostToolUse apply_patch` group. Final disable left zero JevTO pre-hook groups, one unrelated post-hook group, the capture files, and the store's ignore file. |

The passing shell result was 839 bytes shorter than the captured stdout plus stderr (2,015 bytes). This is a first-view payload observation. Codex's provider usage for one successful hooked run was 53,413 input tokens, 34,304 cached input tokens, and 311 output tokens, but there is no controlled, equivalent baseline for that run. The earlier [post-hook diagnostic](codex-hook-diagnostic.md) showed a non-saving pair and a model-visible delivery failure in code mode. Neither check proves net token, cost, time, or quality benefit.

The pre-hook is opt-in, Windows PowerShell only, and limited to plain `cargo test` commands with supported flags. It does not cover pipelines, shell redirection, arbitrary verifiers, Cursor native tools, or Claude Code. The tested sandbox denied an outside write; host approval prompts, explicit escalation requests, and other Codex versions remain unverified. A hook rewrite must be reviewed because Codex's hook protocol requires `permissionDecision: "allow"` alongside `updatedInput`; the [Codex hook docs](https://learn.chatgpt.com/docs/hooks) describe the contract. An [OpenAI Codex issue](https://github.com/openai/codex/issues/36940) describes the separate code-mode `PostToolUse` replacement failure found in the earlier diagnostic.
