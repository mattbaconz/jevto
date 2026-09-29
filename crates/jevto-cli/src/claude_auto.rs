//! Automatic Claude Code route.
//!
//! * `PreToolUse` (Bash/PowerShell): rewrites an eligible, simple command to
//!   `jevto run ... -- <original command>` through `updatedInput`. It never sets
//!   a permission decision, so Claude's normal permission flow still applies.
//! * `UserPromptSubmit`: records the prompt as the session's local goal, which
//!   `jevto run --session` uses as its task frame for goal-aware selection.
//!
//! Commands with pipes, redirection, substitution, background execution, or an
//! unrecognized program are left untouched.

use crate::integration::{read_optional, sha256, write_atomic};
use jevto_core::Store;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::error::Error;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use uuid::Uuid;

const MAX_GOAL_CHARS: usize = 600;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Shell {
    Bash,
    PowerShell,
}

struct Word {
    start: usize,
    text: String,
}

/// Splits a command into top-level `&&` segments of words. Returns `None` for
/// any syntax this route does not handle.
fn parse(command: &str, shell: Shell) -> Option<Vec<Vec<Word>>> {
    let bytes = command.as_bytes();
    let mut segments = vec![Vec::new()];
    let mut index = 0;
    let mut word: Option<Word> = None;
    let finish = |word: &mut Option<Word>, segments: &mut Vec<Vec<Word>>| {
        if let Some(done) = word.take() {
            segments.last_mut().unwrap().push(done);
        }
    };
    while index < bytes.len() {
        let character = bytes[index] as char;
        match character {
            ' ' | '\t' => {
                finish(&mut word, &mut segments);
                index += 1;
            }
            '&' if shell == Shell::Bash && bytes.get(index + 1) == Some(&b'&') => {
                finish(&mut word, &mut segments);
                if segments.last().unwrap().is_empty() {
                    return None;
                }
                segments.push(Vec::new());
                index += 2;
            }
            '|' | ';' | '&' | '>' | '<' | '`' | '(' | ')' | '{' | '}' | '\n' | '\r' => return None,
            '$' if bytes.get(index + 1) == Some(&b'(') => return None,
            '@' | '$' if shell == Shell::PowerShell => return None,
            '\'' | '"' => {
                let quote = bytes[index];
                let start = word.as_ref().map_or(index, |word| word.start);
                let mut text = word.take().map_or_else(String::new, |word| word.text);
                let mut cursor = index + 1;
                loop {
                    let next = *bytes.get(cursor)?;
                    if next == quote {
                        break;
                    }
                    if quote == b'"'
                        && (next == b'`' || (next == b'$' && bytes.get(cursor + 1) == Some(&b'(')))
                    {
                        return None;
                    }
                    if quote == b'"' && next == b'\\' && shell == Shell::Bash {
                        cursor += 1;
                    }
                    cursor += 1;
                }
                text.push_str(&command[index + 1..cursor]);
                word = Some(Word { start, text });
                index = cursor + 1;
            }
            '\\' if shell == Shell::Bash => {
                // A backslash escape outside quotes; keep the next character.
                let start = word.as_ref().map_or(index, |word| word.start);
                let mut text = word.take().map_or_else(String::new, |word| word.text);
                let next = command[index + 1..].chars().next()?;
                text.push(next);
                word = Some(Word { start, text });
                index += 1 + next.len_utf8();
            }
            _ => {
                let next = command[index..].chars().next()?;
                match &mut word {
                    Some(word) => word.text.push(next),
                    None => {
                        word = Some(Word {
                            start: index,
                            text: next.to_string(),
                        })
                    }
                }
                index += next.len_utf8();
            }
        }
    }
    finish(&mut word, &mut segments);
    if segments.iter().any(Vec::is_empty) {
        return None;
    }
    Some(segments)
}

fn is_assignment(word: &str) -> bool {
    word.split_once('=').is_some_and(|(name, _)| {
        !name.is_empty()
            && name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
            && !name.as_bytes()[0].is_ascii_digit()
    })
}

fn program_name(word: &str) -> String {
    let base = word.rsplit(['/', '\\']).next().unwrap_or(word);
    let base = base.to_ascii_lowercase();
    for suffix in [".exe", ".cmd", ".bat"] {
        if let Some(stripped) = base.strip_suffix(suffix) {
            return stripped.into();
        }
    }
    base
}

const LONG_RUNNING_SCRIPTS: &[&str] = &["dev", "start", "serve", "watch", "preview"];

/// Whether JevTO has useful selection for this program invocation.
pub fn eligible(program: &str, args: &[&str]) -> bool {
    // `cargo +nightly test`: skip a rustup toolchain override.
    let args = match args.first() {
        Some(first) if program == "cargo" && first.starts_with('+') => &args[1..],
        _ => args,
    };
    let first = args.first().copied().unwrap_or("");
    let has = |flag: &str| args.contains(&flag);
    match program {
        "cargo" => matches!(
            first,
            "test" | "build" | "check" | "clippy" | "nextest" | "doc" | "t" | "b" | "c"
        ),
        "go" => matches!(first, "test" | "build" | "vet"),
        "python" | "python3" | "py" => args.windows(2).any(|pair| {
            pair[0] == "-m" && matches!(pair[1], "pytest" | "unittest" | "mypy" | "ruff")
        }),
        "pytest" | "jest" | "vitest" | "tsc" | "eslint" | "ruff" | "mypy" | "pyright" | "mocha"
        | "rspec" | "phpunit" | "rg" | "make" | "ninja" | "ctest" => true,
        "node" => has("--test"),
        "npm" | "pnpm" | "yarn" | "bun" => match first {
            "test" | "t" | "install" | "i" | "ci" | "build" | "lint" | "typecheck" | "tsc" => true,
            "run" => args.get(1).is_some_and(|script| {
                !LONG_RUNNING_SCRIPTS
                    .iter()
                    .any(|long| script.starts_with(long))
            }),
            "exec" | "dlx" | "x" => args.get(1).is_some_and(|tool| {
                matches!(*tool, "jest" | "vitest" | "tsc" | "eslint" | "mocha")
            }),
            _ => false,
        },
        "npx" | "bunx" => matches!(first, "jest" | "vitest" | "tsc" | "eslint" | "mocha"),
        "grep" => args.iter().any(|arg| {
            arg.starts_with('-')
                && !arg.starts_with("--")
                && (arg.contains('r') || arg.contains('R'))
        }),
        "git" => matches!(first, "log" | "diff" | "show" | "blame"),
        "cmake" => has("--build"),
        "gradle" | "gradlew" | "mvn" | "mvnw" => args.iter().any(|arg| {
            matches!(
                *arg,
                "test" | "build" | "check" | "verify" | "compile" | "package" | "assemble"
            )
        }),
        "dotnet" => matches!(first, "test" | "build"),
        "mix" => matches!(first, "test" | "compile"),
        "bundle" => {
            first == "exec"
                && args
                    .get(1)
                    .is_some_and(|tool| matches!(*tool, "rspec" | "rake"))
        }
        "docker" => first == "build",
        "kubectl" => matches!(first, "logs" | "describe"),
        "terraform" | "tofu" => first == "plan",
        _ => false,
    }
}

fn quote_bash(text: &str) -> String {
    format!("'{}'", text.replace('\'', "'\\''"))
}

fn quote_powershell(text: &str) -> String {
    format!("'{}'", text.replace('\'', "''"))
}

fn portable(path: &Path) -> String {
    let text = path.to_string_lossy().into_owned();
    if cfg!(windows) {
        text.replace('\\', "/")
    } else {
        text
    }
}

fn valid_session(session: &str) -> bool {
    !session.is_empty()
        && session.len() <= 128
        && session
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

/// A single, simple PowerShell command whose program JevTO understands.
/// Returns the trimmed command to wrap, or `None` to leave it native.
pub fn routable_powershell(command: &str) -> Option<&str> {
    let trimmed = command.trim();
    if trimmed.is_empty() || trimmed.len() > 4000 {
        return None;
    }
    let segments = parse(trimmed, Shell::PowerShell)?;
    let [words] = segments.as_slice() else {
        return None;
    };
    let program = program_name(&words.first()?.text);
    if matches!(program.as_str(), "jevto" | "rtk") {
        return None;
    }
    let args = words[1..]
        .iter()
        .map(|word| word.text.as_str())
        .collect::<Vec<_>>();
    eligible(&program, &args).then_some(trimmed)
}

/// Rewrites one command, or returns `None` to leave it native.
pub fn rewrite(
    command: &str,
    shell: Shell,
    executable: &Path,
    store_root: Option<&Path>,
    session: &str,
) -> Option<String> {
    let trimmed = command.trim();
    if trimmed.is_empty() || trimmed.len() > 4000 || !valid_session(session) {
        return None;
    }
    let segments = parse(trimmed, shell)?;
    let last = segments.last()?;
    let program_index = last.iter().position(|word| !is_assignment(&word.text))?;
    if shell == Shell::PowerShell && (program_index != 0 || segments.len() != 1) {
        return None;
    }
    let program = program_name(&last[program_index].text);
    if matches!(program.as_str(), "jevto" | "rtk") {
        return None;
    }
    let args = last[program_index + 1..]
        .iter()
        .map(|word| word.text.as_str())
        .collect::<Vec<_>>();
    if !eligible(&program, &args) {
        return None;
    }
    let head = &trimmed[..last[program_index].start];
    let tail = &trimmed[last[program_index].start..];
    let store = store_root.map(portable);
    let wrapped = match shell {
        Shell::Bash => {
            let mut prefix = quote_bash(&portable(executable));
            if let Some(store) = &store {
                prefix.push_str(&format!(" --store-dir {}", quote_bash(store)));
            }
            format!(
                "{head}{prefix} run --host claude --session {session} --workspace-id claude -- {tail}"
            )
        }
        Shell::PowerShell => {
            let mut prefix = format!("& {}", quote_powershell(&executable.to_string_lossy()));
            if let Some(store) = store_root {
                prefix.push_str(&format!(
                    " --store-dir {}",
                    quote_powershell(&store.to_string_lossy())
                ));
            }
            format!(
                "{prefix} run --host claude --session {session} --workspace-id claude -- {tail}; exit $LASTEXITCODE"
            )
        }
    };
    Some(wrapped)
}

fn read_stdin_json() -> Result<Value, Box<dyn Error>> {
    let mut input = Vec::new();
    std::io::stdin().take(1024 * 1024).read_to_end(&mut input)?;
    Ok(serde_json::from_slice(&input)?)
}

/// Builds the PreToolUse hook response for one event, if it applies.
pub fn pre_tool_use_response(
    event: &Value,
    executable: &Path,
    store_root: Option<&Path>,
) -> Option<Value> {
    let shell = match event.get("tool_name").and_then(Value::as_str)? {
        "Bash" => Shell::Bash,
        "PowerShell" => Shell::PowerShell,
        _ => return None,
    };
    let input = event.get("tool_input")?.as_object()?;
    if input
        .get("run_in_background")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return None;
    }
    let command = input.get("command")?.as_str()?;
    let session = event.get("session_id").and_then(Value::as_str)?;
    let rewritten = rewrite(command, shell, executable, store_root, session)?;
    let mut updated = input.clone();
    updated.insert("command".into(), Value::String(rewritten));
    Some(json!({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecisionReason": "JevTO: goal-aware output selection with exact recall",
            "updatedInput": updated
        }
    }))
}

/// `jevto hook claude-pre`: never fails the tool call; on any problem it
/// prints nothing and exits 0 so the command runs natively.
pub fn pre_tool_use(store: &Store, explicit_store: bool) -> Result<(), Box<dyn Error>> {
    let Ok(event) = read_stdin_json() else {
        return Ok(());
    };
    let executable = std::env::current_exe()?;
    let root = explicit_store.then_some(store.root.as_path());
    if let Some(response) = pre_tool_use_response(&event, &executable, root) {
        println!("{response}");
    }
    Ok(())
}

/// Extracts a goal from a submitted prompt. Slash commands and empty prompts
/// do not replace the current goal.
pub fn goal_from_prompt(prompt: &str) -> Option<String> {
    let trimmed = prompt.trim();
    if trimmed.is_empty() || trimmed.starts_with('/') {
        return None;
    }
    Some(trimmed.chars().take(MAX_GOAL_CHARS).collect())
}

/// `jevto hook claude-prompt`: records the session goal locally.
pub fn user_prompt_submit(store: &Store) -> Result<(), Box<dyn Error>> {
    let Ok(event) = read_stdin_json() else {
        return Ok(());
    };
    let (Some(session), Some(prompt)) = (
        event.get("session_id").and_then(Value::as_str),
        event.get("prompt").and_then(Value::as_str),
    ) else {
        return Ok(());
    };
    if !valid_session(session) {
        return Ok(());
    }
    if let Some(goal) = goal_from_prompt(prompt) {
        if let Err(error) = store.record_session_goal(session, &goal) {
            eprintln!("jevto: session goal not recorded ({error})");
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Reversible project installer.

#[derive(Serialize, Deserialize)]
struct Manifest {
    schema_version: u32,
    workspace: PathBuf,
    config_path: PathBuf,
    command: PathBuf,
    store_root: PathBuf,
}

const EVENTS: [(&str, &str, Option<&str>); 2] = [
    ("PreToolUse", "claude-pre", Some("Bash|PowerShell")),
    ("UserPromptSubmit", "claude-prompt", None),
];

fn group(executable: &Path, store_root: &Path, hook: &str, matcher: Option<&str>) -> Value {
    let mut group = json!({
        "hooks": [{
            "type": "command",
            "command": executable,
            "args": ["--store-dir", store_root, "hook", hook],
            "timeout": 10
        }]
    });
    if let Some(matcher) = matcher {
        group["matcher"] = json!(matcher);
    }
    group
}

fn owns(group: &Value) -> bool {
    group
        .pointer("/hooks/0/args")
        .and_then(Value::as_array)
        .is_some_and(|args| {
            args.iter()
                .any(|arg| matches!(arg.as_str(), Some("claude-pre" | "claude-prompt")))
        })
}

fn edit(
    original: Option<&[u8]>,
    executable: &Path,
    store_root: &Path,
    install: bool,
) -> Result<Vec<u8>, Box<dyn Error>> {
    let (bom, mut root) = match original {
        None => (false, json!({})),
        Some(bytes) => {
            let bom = bytes.starts_with(&[0xef, 0xbb, 0xbf]);
            let value: Value = serde_json::from_slice(if bom { &bytes[3..] } else { bytes })?;
            if !value.is_object() {
                return Err("Claude settings must be a JSON object".into());
            }
            (bom, value)
        }
    };
    let hooks = root
        .as_object_mut()
        .unwrap()
        .entry("hooks")
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or("Claude hooks must be an object")?;
    for (event, hook, matcher) in EVENTS {
        let groups = hooks
            .entry(event)
            .or_insert_with(|| json!([]))
            .as_array_mut()
            .ok_or("Claude hook event must be an array")?;
        let expected = group(executable, store_root, hook, matcher);
        let exact = groups.iter().filter(|group| **group == expected).count();
        if install {
            if groups.iter().any(owns) {
                return Err(
                    "a JevTO Claude auto hook already exists; refusing to duplicate it".into(),
                );
            }
            groups.push(expected);
        } else if exact == 1 && groups.iter().filter(|group| owns(group)).count() == 1 {
            groups.retain(|group| *group != expected);
        } else {
            return Err(
                "the JevTO Claude auto hook changed or is missing; refusing to remove it".into(),
            );
        }
    }
    let mut rendered = serde_json::to_vec_pretty(&root)?;
    rendered.push(b'\n');
    if bom {
        let mut with_bom = vec![0xef, 0xbb, 0xbf];
        with_bom.extend(rendered);
        return Ok(with_bom);
    }
    Ok(rendered)
}

fn locate(workspace: &Path, store: &Store) -> Result<(PathBuf, PathBuf, PathBuf), Box<dyn Error>> {
    let workspace = fs::canonicalize(workspace)?;
    let directory = workspace.join(".claude");
    if fs::symlink_metadata(&directory).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err(".claude is a symlink; refusing to edit it".into());
    }
    let config = directory.join("settings.local.json");
    if fs::symlink_metadata(&config).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err("settings.local.json is a symlink; refusing to edit it".into());
    }
    let manifest = store
        .root
        .join("integrations")
        .join("claude-auto")
        .join(format!(
            "{}.json",
            sha256(workspace.to_string_lossy().as_bytes())
        ));
    Ok((workspace, config, manifest))
}

fn rtk_warning(workspace: &Path) -> Option<String> {
    let mut candidates = vec![workspace.join(".claude").join("settings.json")];
    if let Some(home) = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")) {
        candidates.push(PathBuf::from(home).join(".claude").join("settings.json"));
    }
    candidates.into_iter().find_map(|path| {
        let text = fs::read_to_string(&path).ok()?;
        text.contains("rtk hook").then(|| {
            format!(
                "Note: {} also rewrites Bash commands (RTK). Claude does not chain rewrites from two PreToolUse hooks; disable one of them for predictable routing.",
                path.display()
            )
        })
    })
}

fn install(
    workspace: &Path,
    apply: bool,
    store: &Store,
    executable: &Path,
) -> Result<String, Box<dyn Error>> {
    let (workspace, config, manifest_file) = locate(workspace, store)?;
    let store_root = std::path::absolute(&store.root)?;
    if read_optional(&manifest_file)?.is_some() {
        return Err("a JevTO ownership record already exists for this workspace".into());
    }
    let original = read_optional(&config)?;
    let rendered = edit(original.as_deref(), executable, &store_root, true)?;
    let mut preview = format!(
        "Claude project settings: {}\nPreToolUse (Bash|PowerShell): route test, build, lint, search, and diff commands through `jevto run`\nUserPromptSubmit: record each prompt locally as the session goal (never sent anywhere unless adaptive mode is opted in)\nExecutable: {}\nStore: {}\nOwnership record: {}",
        config.display(),
        executable.display(),
        store_root.display(),
        manifest_file.display()
    );
    if let Some(warning) = rtk_warning(&workspace) {
        preview.push('\n');
        preview.push_str(&warning);
    }
    if !apply {
        return Ok(format!("Preview only; no files changed.\n{preview}"));
    }
    if let Some(bytes) = original.as_deref() {
        write_atomic(
            &store
                .root
                .join("integrations")
                .join("backups")
                .join(format!("claude-auto-{}.bak", Uuid::new_v4())),
            bytes,
        )?;
    }
    write_atomic(
        &manifest_file,
        &serde_json::to_vec_pretty(&Manifest {
            schema_version: 1,
            workspace: workspace.clone(),
            config_path: config.clone(),
            command: executable.to_path_buf(),
            store_root: store_root.clone(),
        })?,
    )?;
    if let Err(error) = write_atomic(&config, &rendered) {
        let _ = fs::remove_file(&manifest_file);
        return Err(error);
    }
    Ok(format!(
        "Installed JevTO automatic Claude route.\n{preview}"
    ))
}

fn uninstall(workspace: &Path, apply: bool, store: &Store) -> Result<String, Box<dyn Error>> {
    let (workspace, config, manifest_file) = locate(workspace, store)?;
    let manifest: Manifest = serde_json::from_slice(
        &fs::read(&manifest_file).map_err(|_| "no JevTO-owned Claude auto hook here")?,
    )?;
    if manifest.workspace != workspace || manifest.config_path != config {
        return Err("ownership record does not match this workspace".into());
    }
    let original = fs::read(&config)?;
    let rendered = edit(
        Some(&original),
        &manifest.command,
        &manifest.store_root,
        false,
    )?;
    let preview = format!(
        "Claude project settings: {}\nRemove only the owned JevTO PreToolUse and UserPromptSubmit hooks; keep captures and other settings",
        config.display()
    );
    if !apply {
        return Ok(format!("Preview only; no files changed.\n{preview}"));
    }
    write_atomic(&config, &rendered)?;
    fs::remove_file(&manifest_file)?;
    Ok(format!("Disabled JevTO automatic Claude route.\n{preview}"))
}

pub fn init(workspace: &Path, apply: bool, store: &Store) -> Result<(), Box<dyn Error>> {
    println!(
        "{}",
        install(workspace, apply, store, &std::env::current_exe()?)?
    );
    Ok(())
}

pub fn disable(workspace: &Path, apply: bool, store: &Store) -> Result<(), Box<dyn Error>> {
    println!("{}", uninstall(workspace, apply, store)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn exe() -> PathBuf {
        PathBuf::from("/opt/jevto/bin/jevto")
    }

    fn bash(command: &str) -> Option<String> {
        rewrite(command, Shell::Bash, &exe(), None, "sess-1")
    }

    #[test]
    fn rewrites_eligible_simple_commands_and_keeps_prefixes() {
        assert_eq!(
            bash("cargo test -p core").unwrap(),
            "'/opt/jevto/bin/jevto' run --host claude --session sess-1 --workspace-id claude -- cargo test -p core"
        );
        assert_eq!(
            bash("cd crates/core && RUST_BACKTRACE=1 cargo test").unwrap(),
            "cd crates/core && RUST_BACKTRACE=1 '/opt/jevto/bin/jevto' run --host claude --session sess-1 --workspace-id claude -- cargo test"
        );
        assert!(bash("python -m pytest -q tests/").is_some());
        assert!(bash("npm run test:unit").is_some());
        assert!(bash("rg -n \"fn parse\" src").is_some());
        assert!(bash("git diff HEAD~1").is_some());
        assert!(bash("go test ./...").is_some());
        assert!(bash("cargo +1.95.0 test --workspace").is_some());
    }

    #[test]
    fn leaves_complex_interactive_or_unknown_commands_native() {
        for command in [
            "cargo test | tail -5",
            "cargo test > out.txt",
            "cargo run",
            "npm run dev",
            "echo $(cargo test)",
            "ls -la",
            "cat src/main.rs",
            "rtk cargo test",
            "jevto run -- cargo test",
            "cargo test; echo done",
            "cargo test &",
            "git status",
            "vim file",
        ] {
            assert_eq!(bash(command), None, "{command}");
        }
        assert_eq!(
            rewrite("cargo test", Shell::Bash, &exe(), None, "bad session"),
            None
        );
    }

    #[test]
    fn powershell_route_is_single_command_and_propagates_exit() {
        let rewritten = rewrite(
            "cargo test --workspace",
            Shell::PowerShell,
            Path::new("C:\\Tools\\jevto.exe"),
            None,
            "s",
        )
        .unwrap();
        assert!(rewritten.starts_with("& 'C:\\Tools\\jevto.exe' run"));
        assert!(rewritten.ends_with("-- cargo test --workspace; exit $LASTEXITCODE"));
        assert!(rewrite("$env:X=1; cargo test", Shell::PowerShell, &exe(), None, "s").is_none());
    }

    #[test]
    fn hook_response_preserves_other_input_fields_and_sets_no_permission() {
        let event = json!({
            "session_id": "abc",
            "tool_name": "Bash",
            "tool_input": {"command": "cargo test", "description": "Run tests", "timeout": 60000}
        });
        let response = pre_tool_use_response(&event, &exe(), None).unwrap();
        let output = &response["hookSpecificOutput"];
        assert_eq!(output["hookEventName"], "PreToolUse");
        assert!(output.get("permissionDecision").is_none());
        assert_eq!(output["updatedInput"]["description"], "Run tests");
        assert_eq!(output["updatedInput"]["timeout"], 60000);
        let background = json!({
            "session_id": "abc",
            "tool_name": "Bash",
            "tool_input": {"command": "cargo test", "run_in_background": true}
        });
        assert!(pre_tool_use_response(&background, &exe(), None).is_none());
        let other =
            json!({"session_id": "abc", "tool_name": "Read", "tool_input": {"file_path": "x"}});
        assert!(pre_tool_use_response(&other, &exe(), None).is_none());
    }

    #[test]
    fn prompt_goal_skips_slash_commands_and_is_bounded() {
        assert_eq!(goal_from_prompt("/compact"), None);
        assert_eq!(goal_from_prompt("   "), None);
        assert_eq!(
            goal_from_prompt("Fix the parser").as_deref(),
            Some("Fix the parser")
        );
        assert_eq!(
            goal_from_prompt(&"x".repeat(2000)).unwrap().len(),
            MAX_GOAL_CHARS
        );
    }

    #[test]
    fn installer_round_trip_preserves_unrelated_settings() {
        let workspace = tempfile::tempdir().unwrap();
        let store = Store::with_limits(workspace.path().join("store"), 1024 * 1024, 24);
        let executable = workspace.path().join("jevto.exe");
        let config = workspace.path().join(".claude").join("settings.local.json");
        fs::create_dir_all(config.parent().unwrap()).unwrap();
        fs::write(&config, b"{\"permissions\":{\"allow\":[\"Read\"]}}\n").unwrap();
        let before = fs::read(&config).unwrap();
        assert!(install(workspace.path(), false, &store, &executable)
            .unwrap()
            .starts_with("Preview only"));
        assert_eq!(fs::read(&config).unwrap(), before);
        install(workspace.path(), true, &store, &executable).unwrap();
        let installed: Value = serde_json::from_slice(&fs::read(&config).unwrap()).unwrap();
        assert_eq!(installed["permissions"]["allow"][0], "Read");
        assert_eq!(
            installed["hooks"]["PreToolUse"][0]["matcher"],
            "Bash|PowerShell"
        );
        assert!(
            installed["hooks"]["UserPromptSubmit"][0]["hooks"][0]["args"]
                .to_string()
                .contains("claude-prompt")
        );
        assert!(install(workspace.path(), true, &store, &executable).is_err());
        uninstall(workspace.path(), true, &store).unwrap();
        let removed: Value = serde_json::from_slice(&fs::read(&config).unwrap()).unwrap();
        assert_eq!(removed["permissions"]["allow"][0], "Read");
        assert!(removed["hooks"]["PreToolUse"]
            .as_array()
            .unwrap()
            .is_empty());
        assert!(removed["hooks"]["UserPromptSubmit"]
            .as_array()
            .unwrap()
            .is_empty());
    }
}
