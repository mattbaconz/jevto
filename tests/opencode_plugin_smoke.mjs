// Exercises integrations/opencode/jevto.js without a model:
//   node tests/opencode_plugin_smoke.mjs   (JEVTO_BIN points at a built jevto)
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";

const store = fs.mkdtempSync(path.join(os.tmpdir(), "jevto-opencode-"));
process.env.JEVTO_STORE_DIR = store;
const plugin = await import(pathToFileURL(path.resolve("integrations/opencode/jevto.js")).href);
const hooks = await plugin.JevtoPlugin({});

const routed = { args: { command: "python -m unittest -v", description: "tests" } };
await hooks["tool.execute.before"]({ tool: "bash", sessionID: "ses_abc123" }, routed);
assert.match(routed.args.command, / run --host claude --session ses_abc123 --workspace-id claude -- python -m unittest -v$/);
assert.equal(routed.args.description, "tests");

for (const command of ["ls -la", "python -m unittest -v | tail -3", "cat README.md"]) {
  const native = { args: { command } };
  await hooks["tool.execute.before"]({ tool: "bash", sessionID: "ses_abc123" }, native);
  assert.equal(native.args.command, command);
}
const other = { args: { filePath: "x" } };
await hooks["tool.execute.before"]({ tool: "read", sessionID: "ses_abc123" }, other);
assert.deepEqual(other.args, { filePath: "x" });

await hooks["chat.message"]({ sessionID: "ses_abc123" }, { parts: [{ type: "text", text: "Fix the parser test" }] });
assert.equal(fs.readdirSync(path.join(store, "goals")).length, 1);
fs.rmSync(store, { recursive: true, force: true });
console.log("OpenCode plugin smoke passed: rewrite, native passthrough, non-bash untouched, goal capture");
