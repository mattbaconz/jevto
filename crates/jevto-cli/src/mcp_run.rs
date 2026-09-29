//! An opt-in MCP bridge to the explicit CLI wrapper. The policy fixes every
//! executable and argument before the agent can call the tool.

use crate::bounded_output::{self, ChildOutput, OverflowBehavior};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use jevto_core::Store;
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use uuid::Uuid;

const MCP_OUTPUT_LIMIT: usize = 1024 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RunPolicyFile {
    schema_version: u32,
    workspace: PathBuf,
    workspace_id: String,
    commands: Vec<AllowedCommand>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AllowedCommand {
    id: String,
    program: PathBuf,
    argv: Vec<String>,
}

pub struct RunPolicy {
    workspace: PathBuf,
    workspace_id: String,
    commands: Vec<AllowedCommand>,
}

pub fn load_pinned(
    path: &Path,
    expected_sha256: Option<&str>,
) -> Result<RunPolicy, Box<dyn Error>> {
    let policy_path = fs::canonicalize(path)?;
    let bytes = fs::read(&policy_path)?;
    if let Some(expected) = expected_sha256 {
        if expected.len() != 64 || !expected.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err("MCP run policy SHA-256 must be 64 hexadecimal characters".into());
        }
        let actual = hex::encode(Sha256::digest(&bytes));
        if !actual.eq_ignore_ascii_case(expected) {
            return Err("MCP run policy changed since approval; refusing to start".into());
        }
    }
    let file: RunPolicyFile = serde_json::from_slice(&bytes)?;
    if file.schema_version != 1 {
        return Err("unsupported MCP run policy schema version".into());
    }
    let workspace = fs::canonicalize(&file.workspace)?;
    if !workspace.is_dir() || policy_path.starts_with(&workspace) {
        return Err("MCP run policy must be outside its existing workspace".into());
    }
    if file.workspace_id.is_empty() || file.workspace_id.len() > 128 || file.commands.is_empty() {
        return Err("MCP run policy needs a workspace ID and at least one command".into());
    }
    let mut ids = HashSet::new();
    let mut commands = Vec::with_capacity(file.commands.len());
    for mut command in file.commands {
        if command.id.is_empty()
            || command.id.len() > 64
            || !command
                .id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
            || !ids.insert(command.id.clone())
        {
            return Err("MCP run command IDs must be unique ASCII words".into());
        }
        if !command.program.is_absolute()
            || command.argv.len() > 32
            || command
                .argv
                .iter()
                .any(|arg| arg.len() > 4096 || arg.contains('\0'))
        {
            return Err("MCP run command must use an absolute program and bounded argv".into());
        }
        command.program = fs::canonicalize(&command.program)?;
        if !command.program.is_file() {
            return Err("MCP run program must be an existing file".into());
        }
        commands.push(command);
    }
    Ok(RunPolicy {
        workspace,
        workspace_id: file.workspace_id,
        commands,
    })
}

impl RunPolicy {
    pub fn workspace(&self) -> &Path {
        &self.workspace
    }
}

pub fn tool_definition(policy: &RunPolicy) -> Value {
    let ids = policy
        .commands
        .iter()
        .map(|command| command.id.as_str())
        .collect::<Vec<_>>();
    json!({
        "name":"jevto_run",
        "description":format!("Run one exact command authorized by an external workspace policy. Available command IDs: {}. The child may edit files, and a host shell sandbox may not constrain this MCP process. Its nonzero exit is a result, not an MCP transport error. Output is bounded and locally recallable when captured.", ids.join(", ")),
        "annotations":{"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":false},
        "inputSchema":{"type":"object","properties":{"command_id":{"type":"string","enum":ids}},"required":["command_id"],"additionalProperties":false}
    })
}

fn tool_error(message: String) -> Value {
    json!({"content":[{"type":"text","text":message}],"isError":true})
}

pub fn call(store: &Store, policy: &RunPolicy, args: &Value) -> Value {
    let Some(id) = args.get("command_id").and_then(Value::as_str) else {
        return tool_error("missing command_id; command was not run".into());
    };
    if args.as_object().is_none_or(|object| object.len() != 1) {
        return tool_error("unexpected argument; command was not run".into());
    }
    let Some(allowed) = policy.commands.iter().find(|command| command.id == id) else {
        return tool_error("command_id is not allowed; command was not run".into());
    };
    if let Err(error) = store.initialize() {
        return tool_error(format!(
            "JevTO store unavailable; command was not run: {error}"
        ));
    }
    let store_path = match fs::canonicalize(&store.root) {
        Ok(path) => path,
        Err(error) => {
            return tool_error(format!(
                "JevTO store unavailable; command was not run: {error}"
            ))
        }
    };
    let executable = match std::env::current_exe() {
        Ok(path) => path,
        Err(error) => {
            return tool_error(format!(
                "JevTO executable unavailable; command was not run: {error}"
            ))
        }
    };
    let session = Uuid::new_v4().to_string();
    let mut child = Command::new(executable);
    child
        .arg("--store-dir")
        .arg(store_path)
        .arg("run")
        .arg("--session")
        .arg(&session)
        .arg("--workspace-id")
        .arg(&policy.workspace_id)
        .arg("--budget")
        .arg("65536")
        .arg("--")
        .arg(&allowed.program)
        .args(&allowed.argv)
        .current_dir(&policy.workspace)
        .env("JEVTO_MAX_CAPTURE_BYTES", MCP_OUTPUT_LIMIT.to_string());
    let output = bounded_output::run_command(child, MCP_OUTPUT_LIMIT, OverflowBehavior::Discard);
    let output = match output {
        Ok(ChildOutput::Captured(output)) => output,
        Ok(ChildOutput::Bypassed { status, raw_bytes }) => {
            return tool_error(format!(
                "JevTO command {id} ran, but its MCP output exceeded {MCP_OUTPUT_LIMIT} bytes ({raw_bytes} observed); wrapper exit={:?}; session_id={session}. Do not rerun blindly.",
                status.code()
            ));
        }
        Err(error) => return tool_error(format!("JevTO command {id} may have started, but output collection failed: {error}; session_id={session}. Do not rerun blindly.")),
    };
    let receipts = match store.receipts_for_session(&session) {
        Ok(receipts) => receipts,
        Err(error) => return tool_error(format!("JevTO command {id} ran, but its receipt is unavailable: {error}; wrapper exit={:?}; session_id={session}. Do not rerun blindly.", output.status.code())),
    };
    let [receipt] = receipts.as_slice() else {
        return tool_error(format!("JevTO command {id} may have run, but exactly one receipt was not found; wrapper exit={:?}; session_id={session}. Do not rerun blindly.", output.status.code()));
    };
    let child_exit = receipt.extra.get("child_exit").and_then(Value::as_i64);
    if child_exit != output.status.code().map(i64::from) {
        return tool_error(format!("JevTO command {id} ran, but wrapper/receipt exit disagreed; session_id={session}. Do not rerun blindly."));
    }
    let capture_id = receipt.extra.get("capture_id").and_then(Value::as_str);
    let bypass = receipt
        .coverage
        .iter()
        .filter_map(|coverage| coverage.bypass_reason.as_deref())
        .collect::<Vec<_>>()
        .join(", ");
    let stdout_text = std::str::from_utf8(&output.stdout);
    let stderr_text = std::str::from_utf8(&output.stderr);
    let stdout = stdout_text.unwrap_or("[binary stdout; use structuredContent.stdout_base64]");
    let stderr = stderr_text.unwrap_or("[binary stderr; use structuredContent.stderr_base64]");
    let response_text = format!(
        "JevTO command {id}: child_exit={}; session_id={session}; capture_id={}; raw_bytes={}; delivered_bytes={}; bypass_reason={}\nUntrusted child stdout:\n{stdout}\nUntrusted child stderr:\n{stderr}",
        child_exit.map_or_else(|| "interrupted".to_string(), |exit| exit.to_string()),
        capture_id.unwrap_or("unavailable"),
        receipt.raw_bytes,
        receipt.delivered_bytes,
        if bypass.is_empty() { "none" } else { &bypass },
    );
    let mut structured = json!({
        "command_id":id,
        "session_id":session,
        "capture_id":capture_id,
        "child_exit":child_exit,
        "raw_bytes":receipt.raw_bytes,
        "delivered_bytes":receipt.delivered_bytes,
        "coverage":receipt.coverage,
        "provider_usage":null
    });
    if stdout_text.is_err() {
        structured["stdout_base64"] = json!(STANDARD.encode(&output.stdout));
    }
    if stderr_text.is_err() {
        structured["stderr_base64"] = json!(STANDARD.encode(&output.stderr));
    }
    json!({
        "content":[{"type":"text","text":response_text}],
        "structuredContent":structured,
        "isError":false
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn policy_must_live_outside_workspace_and_use_exact_commands() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = temp.path().join("workspace");
        fs::create_dir(&workspace).unwrap();
        let program = std::env::current_exe().unwrap();
        let policy = json!({"schema_version":1,"workspace":workspace,"workspace_id":"test","commands":[{"id":"verify","program":program,"argv":["--version"]}]});
        let inside = workspace.join("policy.json");
        fs::write(&inside, policy.to_string()).unwrap();
        assert!(load_pinned(&inside, None).is_err());
        let outside = temp.path().join("policy.json");
        fs::write(&outside, policy.to_string()).unwrap();
        let loaded = load_pinned(&outside, None).unwrap();
        assert_eq!(loaded.commands.len(), 1);
        assert_eq!(
            tool_definition(&loaded)["inputSchema"]["properties"]["command_id"]["enum"],
            json!(["verify"])
        );
        let denied = call(
            &Store::with_limits(temp.path().join("store"), 1024 * 1024, 24),
            &loaded,
            &json!({"command_id":"other"}),
        );
        assert_eq!(denied["isError"], true);
        assert!(!temp.path().join("store").exists());
    }

    #[test]
    fn pinned_policy_refuses_changed_bytes_before_exposing_tools() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = temp.path().join("workspace");
        fs::create_dir(&workspace).unwrap();
        let policy_path = temp.path().join("policy.json");
        let program = std::env::current_exe().unwrap();
        let first = json!({"schema_version":1,"workspace":workspace,"workspace_id":"test","commands":[{"id":"verify","program":program,"argv":[]}]}).to_string();
        fs::write(&policy_path, &first).unwrap();
        let digest = hex::encode(Sha256::digest(first.as_bytes()));
        assert!(load_pinned(&policy_path, Some(&digest)).is_ok());
        fs::write(&policy_path, format!("{first}\n")).unwrap();
        assert!(load_pinned(&policy_path, Some(&digest)).is_err());
        assert!(load_pinned(&policy_path, Some("invalid")).is_err());
    }
}
