use crate::jev;
use jevto_core::{
    make_pack_with_selection, render_pack_formatted, AdaptiveSelection, Completeness, Coverage,
    Mode, PackCoverage, PackFormat, ProcessStatus, Receipt, Store, TaskFrame, SCHEMA_VERSION,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::error::Error;
use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::time::Instant;
use uuid::Uuid;

const MAX_HOOK_INPUT_BYTES: u64 = 1024 * 1024;
const MAX_RESULT_BYTES: usize = 512 * 1024;
const PACK_BUDGET_BYTES: usize = 64 * 1024;

#[derive(Clone, Copy)]
enum ResponseSlot {
    Whole,
    Field(&'static str),
}

struct Eligible<'a> {
    session: &'a str,
    cwd: &'a str,
    stdout: &'a str,
    stderr: &'a str,
    program: Vec<String>,
    default_search_path: Option<String>,
    response_slot: ResponseSlot,
}

pub fn serve(store: &Store, workspace: &Path) -> Result<(), Box<dyn Error>> {
    let mut input = Vec::new();
    io::stdin()
        .take(MAX_HOOK_INPUT_BYTES + 1)
        .read_to_end(&mut input)?;
    if input.len() as u64 > MAX_HOOK_INPUT_BYTES {
        return Ok(());
    }
    let Ok(event) = serde_json::from_slice::<Value>(&input) else {
        return Ok(());
    };
    match process_event(store, workspace, &event) {
        Ok(Some(replacement)) => {
            io::stdout().write_all(serde_json::to_string(&replacement)?.as_bytes())?;
            io::stdout().write_all(b"\n")?;
        }
        Ok(None) => {}
        Err(error) => eprintln!("jevto: Claude hook bypassed ({error})"),
    }
    Ok(())
}

fn simple_rg(command: &str) -> Option<Vec<String>> {
    if command.is_empty()
        || !command.is_ascii()
        || command.bytes().any(|byte| {
            matches!(
                byte,
                b'\'' | b'"' | b';' | b'|' | b'&' | b'`' | b'$' | b'>' | b'<'
            )
        })
    {
        return None;
    }
    let tokens = command
        .split_ascii_whitespace()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    if tokens.len() < 4 {
        return None;
    }
    let executable = Path::new(&tokens[0])
        .file_name()
        .and_then(|name| name.to_str())?;
    if (!executable.eq_ignore_ascii_case("rg") && !executable.eq_ignore_ascii_case("rg.exe"))
        || !matches!(tokens[1].as_str(), "-n" | "--line-number")
        || !matches!(tokens[2].as_str(), "-H" | "--with-filename")
        || tokens[3].starts_with('-')
        || tokens[4..].iter().any(|token| {
            token.starts_with('-')
                || token == ".."
                || token.starts_with("../")
                || token.starts_with("..\\")
                // Windows roots and drives are outside paths on every platform.
                || token.starts_with('\\')
                || token.as_bytes().get(1) == Some(&b':')
                || Path::new(token).is_absolute()
        })
    {
        return None;
    }
    Some(tokens)
}

fn response_text<'a>(tool: &str, response: &'a Value) -> Option<(&'a str, &'a str, ResponseSlot)> {
    match tool {
        "Bash" | "PowerShell" => {
            let object = response.as_object()?;
            let stdout = object.get("stdout")?.as_str()?;
            let stderr = object.get("stderr")?.as_str()?;
            if object.get("interrupted").and_then(Value::as_bool) != Some(false)
                || object.get("isImage").and_then(Value::as_bool) != Some(false)
            {
                return None;
            }
            Some((stdout, stderr, ResponseSlot::Field("stdout")))
        }
        "Grep" => {
            if let Some(text) = response.as_str() {
                return Some((text, "", ResponseSlot::Whole));
            }
            for field in ["content", "stdout", "output"] {
                if let Some(text) = response.get(field).and_then(Value::as_str) {
                    return Some((text, "", ResponseSlot::Field(field)));
                }
            }
            None
        }
        _ => None,
    }
}

fn eligible<'a>(workspace: &Path, event: &'a Value) -> Option<Eligible<'a>> {
    if event.get("hook_event_name")?.as_str()? != "PostToolUse" {
        return None;
    }
    let tool = event.get("tool_name")?.as_str()?;
    let session = event.get("session_id")?.as_str()?;
    let cwd = event.get("cwd")?.as_str()?;
    if session.is_empty() || cwd.is_empty() {
        return None;
    }
    let workspace = fs::canonicalize(workspace).ok()?;
    let observed_cwd = fs::canonicalize(cwd).ok()?;
    if !observed_cwd.starts_with(&workspace) {
        return None;
    }
    let (program, default_search_path) = match tool {
        "Bash" | "PowerShell" => (
            simple_rg(event.pointer("/tool_input/command")?.as_str()?)?,
            None,
        ),
        "Grep" => {
            let input = event.get("tool_input")?;
            if input
                .get("output_mode")
                .and_then(Value::as_str)
                .unwrap_or("files_with_matches")
                != "content"
                || input.get("multiline").and_then(Value::as_bool) == Some(true)
            {
                return None;
            }
            let pattern = input.get("pattern")?.as_str()?;
            let default_path = match input.get("path").and_then(Value::as_str) {
                None => None,
                Some(raw) if !raw.is_empty() && raw.len() <= 1024 => {
                    let requested = Path::new(raw);
                    let target = if requested.is_absolute() {
                        requested.to_path_buf()
                    } else {
                        observed_cwd.join(requested)
                    };
                    let target = fs::canonicalize(target).ok()?;
                    if !target.starts_with(&workspace) {
                        return None;
                    }
                    target.is_file().then(|| raw.to_owned())
                }
                _ => return None,
            };
            (
                vec!["rg".into(), "-n".into(), "-H".into(), pattern.into()],
                default_path,
            )
        }
        _ => return None,
    };
    let (stdout, stderr, response_slot) = response_text(tool, event.get("tool_response")?)?;
    if stdout.len() + stderr.len() > MAX_RESULT_BYTES
        || stdout.is_empty()
        || [stdout, stderr].iter().any(|text| {
            let lower = text.to_ascii_lowercase();
            lower.contains("truncated output")
                || lower.contains("output truncated")
                || lower.contains("... truncated")
        })
    {
        return None;
    }
    Some(Eligible {
        session,
        cwd,
        stdout,
        stderr,
        program,
        default_search_path,
        response_slot,
    })
}

/// Task frame, outbound policy, and optional key for the adaptive search hook.
type AdaptiveInputs = (TaskFrame, jev::OutboundPolicy, Option<String>);

fn adaptive_inputs(cwd: &Path) -> Result<Option<AdaptiveInputs>, &'static str> {
    if std::env::var("JEVTO_ADAPTIVE").as_deref() != Ok("1") {
        return Ok(None);
    }
    let task_path = std::env::var_os("JEVTO_TASK_FILE").ok_or("missing_task_file")?;
    let policy_path = std::env::var_os("JEVTO_REMOTE_POLICY").ok_or("missing_remote_policy")?;
    let key = std::env::var("OPENROUTER_API_KEY").ok();
    let task: TaskFrame = serde_json::from_slice(
        &fs::read(PathBuf::from(task_path)).map_err(|_| "task_frame_unreadable")?,
    )
    .map_err(|_| "task_frame_invalid")?;
    let policy = jev::OutboundPolicy::load(Path::new(&policy_path), cwd)?;
    Ok(Some((task, policy, key)))
}

fn replace_response(response: &Value, slot: ResponseSlot, rendered: String) -> Option<Value> {
    match slot {
        ResponseSlot::Whole => Some(Value::String(rendered)),
        ResponseSlot::Field(field) => {
            let mut replacement = response.clone();
            *replacement.get_mut(field)? = Value::String(rendered);
            Some(replacement)
        }
    }
}

fn recall_prefix(store: &Store) -> Result<String, Box<dyn Error>> {
    let executable = std::env::current_exe()?;
    let root = fs::canonicalize(&store.root)?;
    if cfg!(windows) {
        Ok(format!(
            "& '{}' --store-dir '{}'",
            executable.to_string_lossy().replace('\'', "''"),
            root.to_string_lossy().replace('\'', "''")
        ))
    } else {
        Ok(format!(
            "'{}' --store-dir '{}'",
            executable.to_string_lossy().replace('\'', "'\\''"),
            root.to_string_lossy().replace('\'', "'\\''")
        ))
    }
}

#[allow(clippy::too_many_arguments)]
fn save_receipt(
    store: &Store,
    eligible: &Eligible<'_>,
    capture_id: &str,
    mode: Mode,
    raw_bytes: usize,
    delivered_bytes: usize,
    requested: bool,
    fallback: Option<&str>,
    jev_usage: Option<jevto_core::ObservedUsage>,
    jev_metadata: Option<(&str, f64, bool, bool, u64)>,
) -> Result<(), Box<dyn Error>> {
    let mut extra = serde_json::Map::new();
    extra.insert("capture_id".into(), json!(capture_id));
    extra.insert("capture_scope".into(), json!("claude_hook_visible_text"));
    extra.insert("replacement_requested".into(), json!(requested));
    extra.insert("host_delivery_confirmed".into(), json!(false));
    if let Some(reason) = fallback {
        extra.insert("adaptive_fallback".into(), json!(reason));
    }
    if let Some((model, exists, cache_hit, attempted, elapsed_ms)) = jev_metadata {
        extra.insert("jev_model".into(), json!(model));
        extra.insert("jev_exists".into(), json!(exists));
        extra.insert("jev_cache_hit".into(), json!(cache_hit));
        extra.insert("jev_call_attempted".into(), json!(attempted));
        extra.insert("jev_elapsed_ms".into(), json!(elapsed_ms));
    }
    store.save_receipt(&Receipt {
        schema_version: SCHEMA_VERSION,
        session_id: eligible.session.into(),
        run_id: Uuid::new_v4().to_string(),
        mode,
        coverage: vec![Coverage {
            tool_path: "claude_post_tool_use".into(),
            captured: true,
            replaced: false,
            bypass_reason: (!requested).then(|| fallback.unwrap_or("no_net_reduction").into()),
        }],
        raw_bytes: raw_bytes as u64,
        delivered_bytes: delivered_bytes as u64,
        recalls: 0,
        provider_usage: None,
        jev_usage,
        verification: None,
        extra: extra.into_iter().collect(),
    })?;
    Ok(())
}

fn process_event(
    store: &Store,
    workspace: &Path,
    event: &Value,
) -> Result<Option<Value>, Box<dyn Error>> {
    let Some(eligible) = eligible(workspace, event) else {
        return Ok(None);
    };
    let stdout = eligible.stdout.as_bytes();
    let stderr = eligible.stderr.as_bytes();
    let raw_bytes = stdout.len() + stderr.len();
    let workspace_id = hex::encode(Sha256::digest(eligible.cwd.as_bytes()));
    let capture = store.capture(
        eligible.session,
        &workspace_id,
        "claude_post_tool_use",
        Some("rg [arguments redacted]".into()),
        stdout,
        stderr,
        Completeness::Complete,
        ProcessStatus::Completed,
        Some(0),
    )?;

    let mut mode = Mode::Deterministic;
    let mut task = None;
    let mut selection: Option<AdaptiveSelection> = None;
    let mut fallback = None;
    let mut usage = None;
    let mut metadata: Option<(String, f64, bool, bool, u64)> = None;
    match adaptive_inputs(Path::new(eligible.cwd)) {
        Ok(Some((frame, policy, key))) => {
            task = Some(frame);
            match jev::prepare(
                task.as_ref().unwrap(),
                &eligible.program,
                &capture,
                stdout,
                stderr,
                eligible.default_search_path.as_deref(),
                &policy,
            ) {
                Ok(prepared) => {
                    let attempted = key.is_some() && !prepared.has_cache_candidate(&store.root);
                    let started = Instant::now();
                    match jev::decide_cached(&prepared, key.as_deref(), &store.root) {
                        Ok(decision) => {
                            let elapsed =
                                started.elapsed().as_millis().min(u64::MAX as u128) as u64;
                            mode = Mode::Adaptive;
                            selection = Some(decision.selection);
                            usage = decision.usage;
                            metadata = Some((
                                decision.model,
                                decision.exists,
                                decision.cache_hit,
                                attempted,
                                elapsed,
                            ));
                        }
                        Err(reason) => fallback = Some(reason.to_owned()),
                    }
                }
                Err(reason) => fallback = Some(reason.to_owned()),
            }
        }
        Ok(None) => {}
        Err(reason) => fallback = Some(reason.to_owned()),
    }

    let deterministic_selection = (mode != Mode::Adaptive
        && eligible.default_search_path.is_some())
    .then(|| AdaptiveSelection {
        stderr_ranges: Vec::new(),
        stdout_ranges: Vec::new(),
        default_search_path: eligible.default_search_path.clone(),
    });
    let decision = make_pack_with_selection(
        &capture,
        stdout,
        stderr,
        task.as_ref(),
        mode.clone(),
        PACK_BUDGET_BYTES,
        None,
        if mode == Mode::Adaptive {
            selection.as_ref()
        } else {
            deterministic_selection.as_ref()
        },
    );
    let Some(pack) = decision
        .pack
        .filter(|pack| pack.coverage == PackCoverage::Complete)
    else {
        let reason = decision
            .bypass_reason
            .as_deref()
            .or(fallback.as_deref())
            .unwrap_or("no_reducible_output");
        save_receipt(
            store,
            &eligible,
            &capture.capture_id,
            mode,
            raw_bytes,
            raw_bytes,
            false,
            Some(reason),
            usage,
            metadata
                .as_ref()
                .map(|item| (item.0.as_str(), item.1, item.2, item.3, item.4)),
        )?;
        return Ok(None);
    };
    let rendered =
        render_pack_formatted(&capture, &pack, &recall_prefix(store)?, PackFormat::Compact);
    if rendered.len() >= raw_bytes || rendered.len() > PACK_BUDGET_BYTES {
        save_receipt(
            store,
            &eligible,
            &capture.capture_id,
            mode,
            raw_bytes,
            raw_bytes,
            false,
            Some("rendered_pack_no_net_reduction"),
            usage,
            metadata
                .as_ref()
                .map(|item| (item.0.as_str(), item.1, item.2, item.3, item.4)),
        )?;
        return Ok(None);
    }
    store.save_pack(&pack)?;
    let replacement = replace_response(
        event.get("tool_response").ok_or("missing tool response")?,
        eligible.response_slot,
        rendered.clone(),
    )
    .ok_or("unsupported Claude response shape")?;
    save_receipt(
        store,
        &eligible,
        &capture.capture_id,
        mode,
        raw_bytes,
        rendered.len(),
        true,
        fallback.as_deref(),
        usage,
        metadata
            .as_ref()
            .map(|item| (item.0.as_str(), item.1, item.2, item.3, item.4)),
    )?;
    Ok(Some(json!({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": replacement
        }
    })))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> (tempfile::TempDir, Store) {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::with_limits(temp.path().join("store"), 1024 * 1024, 24);
        (temp, store)
    }

    fn bash_event(cwd: &Path, output: &str) -> Value {
        json!({
            "hook_event_name":"PostToolUse",
            "tool_name":"Bash",
            "session_id":"claude-session",
            "cwd":cwd,
            "tool_input":{"command":"rg -n -H needle src"},
            "tool_response":{"stdout":output,"stderr":"","interrupted":false,"isImage":false}
        })
    }

    #[test]
    fn simple_rg_parser_rejects_shell_syntax_flags_and_outside_paths() {
        assert!(simple_rg("rg -n -H needle src").is_some());
        for command in [
            "rg needle src",
            "rg -n -H needle ../private",
            "rg -n -H needle C:\\private",
            "rg -n -H --glob src",
            "rg -n -H 'needle' src",
            "rg -n -H needle src; whoami",
        ] {
            assert!(simple_rg(command).is_none(), "{command}");
        }
    }

    #[test]
    fn bash_shape_is_preserved_and_full_output_is_recallable() {
        let (workspace, store) = fixture();
        let content = "let needle = cache.lookup(user_id).unwrap_or_default();";
        let raw = (1..=80)
            .map(|line| format!("src/cache.rs:{line}:{content}\n"))
            .collect::<String>();
        let replacement = process_event(
            &store,
            workspace.path(),
            &bash_event(workspace.path(), &raw),
        )
        .unwrap()
        .unwrap();
        let updated = &replacement["hookSpecificOutput"]["updatedToolOutput"];
        assert_eq!(updated["stderr"], "");
        assert_eq!(updated["interrupted"], false);
        assert_eq!(updated["isImage"], false);
        let text = updated["stdout"].as_str().unwrap();
        assert!(text.len() < raw.len());
        assert!(text.contains("jevto exit=0"));
        let receipt = store
            .receipts_for_session("claude-session")
            .unwrap()
            .pop()
            .unwrap();
        assert_eq!(receipt.coverage[0].tool_path, "claude_post_tool_use");
        assert_eq!(receipt.extra["replacement_requested"], true);
        let capture_id = receipt.extra["capture_id"].as_str().unwrap();
        assert_eq!(
            store
                .recall(capture_id, jevto_core::Stream::Stdout, None, true)
                .unwrap(),
            raw.as_bytes()
        );
    }

    #[test]
    fn grep_object_shape_and_line_only_content_are_preserved() {
        let (workspace, store) = fixture();
        fs::write(workspace.path().join("fixture.py"), "# fixture\n").unwrap();
        let content = "let needle = cache.lookup(user_id).unwrap_or_default();";
        let raw = (1..=80)
            .map(|line| format!("{line}:{content}\n"))
            .collect::<String>();
        let grep = json!({
            "hook_event_name":"PostToolUse","tool_name":"Grep",
            "session_id":"claude-session","cwd":workspace.path(),
            "tool_input":{"pattern":"needle","path":"fixture.py","output_mode":"content"},
            "tool_response":{
                "mode":"content","numFiles":0,"filenames":[],
                "content":raw,"numLines":80,"totalLines":80
            }
        });
        let replacement = process_event(&store, workspace.path(), &grep)
            .unwrap()
            .unwrap();
        let updated = &replacement["hookSpecificOutput"]["updatedToolOutput"];
        assert_eq!(updated["mode"], "content");
        assert_eq!(updated["numLines"], 80);
        assert!(updated["content"]
            .as_str()
            .unwrap()
            .contains("jevto exit=0"));
        let mut files_only = grep;
        files_only["tool_input"]["output_mode"] = json!("files_with_matches");
        assert!(process_event(&store, workspace.path(), &files_only)
            .unwrap()
            .is_none());
        let outside = tempfile::tempdir().unwrap();
        let event = bash_event(outside.path(), "src/a.rs:1:needle\n");
        assert!(process_event(&store, workspace.path(), &event)
            .unwrap()
            .is_none());
    }
}
