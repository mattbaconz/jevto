// JevTO plugin for OpenCode.
//
// Routes eligible shell commands (tests, builds, lints, searches, git diff/log)
// through `jevto run` before they execute, and records each user message as
// the session's local goal. The rewrite rules are JevTO's own (the same ones
// its Claude Code hook uses); anything JevTO does not recognize, or any error,
// leaves the command untouched.
//
// Install: copy this file to `.opencode/plugins/jevto.js` in a project (or
// your global OpenCode plugin directory). Set JEVTO_BIN if `jevto` is not on
// PATH. Remove the file to disable.

import { execFileSync } from "node:child_process";

const JEVTO = process.env.JEVTO_BIN || "jevto";

function hook(event, payload) {
  try {
    return execFileSync(JEVTO, ["hook", event], {
      input: JSON.stringify(payload),
      encoding: "utf8",
      timeout: 5000,
      windowsHide: true,
    }).trim();
  } catch {
    return "";
  }
}

function sessionId(value) {
  const id = String(value || "").replace(/[^A-Za-z0-9_-]/g, "");
  return id.slice(0, 128) || "opencode";
}

export const JevtoPlugin = async () => ({
  "tool.execute.before": async (input, output) => {
    if (input.tool !== "bash" || typeof output?.args?.command !== "string") return;
    if (output.args.run_in_background) return;
    const reply = hook("claude-pre", {
      session_id: sessionId(input.sessionID),
      tool_name: "Bash",
      tool_input: { command: output.args.command },
    });
    if (!reply) return;
    try {
      const command = JSON.parse(reply)?.hookSpecificOutput?.updatedInput?.command;
      if (typeof command === "string" && command.length > 0) output.args.command = command;
    } catch {
      // Leave the command native.
    }
  },
  "chat.message": async (input, output) => {
    const text = (output?.parts || [])
      .filter((part) => part?.type === "text" && typeof part.text === "string")
      .map((part) => part.text)
      .join("\n");
    if (!text.trim()) return;
    hook("claude-prompt", { session_id: sessionId(input?.sessionID), prompt: text });
  },
});
