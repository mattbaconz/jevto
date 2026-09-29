use serde_json::{json, Value};
use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use uuid::Uuid;

const MAX_HOOK_INPUT_BYTES: u64 = 1024 * 1024;

pub fn serve(workspace: &Path) -> Result<(), Box<dyn std::error::Error>> {
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
    let executable = std::env::current_exe()?;
    if let Some(rewrite) = process_event(workspace, &executable, &event) {
        serde_json::to_writer(io::stdout(), &rewrite)?;
        io::stdout().write_all(b"\n")?;
    }
    Ok(())
}

fn process_event(workspace: &Path, executable: &Path, event: &Value) -> Option<Value> {
    if !cfg!(windows)
        || event.get("hook_event_name")?.as_str()? != "PreToolUse"
        || event.get("tool_name")?.as_str()? != "Bash"
    {
        return None;
    }
    let workspace = fs::canonicalize(workspace).ok()?;
    let codex_dir = workspace.join(".codex");
    let store_dir = workspace.join(".jevto-store");
    if fs::symlink_metadata(&codex_dir)
        .ok()?
        .file_type()
        .is_symlink()
        || fs::symlink_metadata(&store_dir)
            .ok()?
            .file_type()
            .is_symlink()
    {
        return None;
    }
    let cwd = fs::canonicalize(event.get("cwd")?.as_str()?).ok()?;
    if !cwd.starts_with(&workspace) {
        return None;
    }
    let input = event.get("tool_input")?.as_object()?;
    if input.contains_key("sandbox_permissions") || input.contains_key("justification") {
        return None;
    }
    if let Some(shell) = input.get("shell") {
        let shell = shell.as_str()?.to_ascii_lowercase();
        if shell != "powershell"
            && shell != "pwsh"
            && shell != "powershell.exe"
            && shell != "pwsh.exe"
        {
            return None;
        }
    }
    if let Some(workdir) = input.get("workdir") {
        let workdir = PathBuf::from(workdir.as_str()?);
        let workdir = if workdir.is_absolute() {
            workdir
        } else {
            cwd.join(workdir)
        };
        if !fs::canonicalize(workdir).ok()?.starts_with(&workspace) {
            return None;
        }
    }
    let original_command = input.get("command")?.as_str()?;
    let command = cargo_verifier_command(original_command)
        .or_else(|| rg_search_command(original_command))
        .or_else(|| {
            // Other allowlisted programs, with no parent or absolute paths.
            crate::claude_auto::routable_powershell(original_command)
                .filter(|command| {
                    command.split_ascii_whitespace().all(|token| {
                        let token = token.trim_matches(['\'', '"']);
                        !token.split(['/', '\\']).any(|part| part == "..")
                            && !Path::new(token).is_absolute()
                            && !token.starts_with(['/', '\\'])
                            && !token.contains(":\\")
                    })
                })
                .map(str::to_owned)
        })?;
    let session_arg = event
        .get("session_id")
        .and_then(Value::as_str)
        .and_then(|session| Uuid::parse_str(session).ok())
        .map(|session| format!(" --session {session}"))
        .unwrap_or_default();
    let mut updated = input.clone();
    updated.insert(
        "command".into(),
        json!(format!(
            "& '{}' --store-dir '{}' run{session_arg} -- {command}; exit $LASTEXITCODE",
            executable.to_string_lossy().replace('\'', "''"),
            store_dir.to_string_lossy().replace('\'', "''"),
        )),
    );
    Some(json!({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated,
        }
    }))
}

fn cargo_verifier_command(command: &str) -> Option<String> {
    if command.len() > 512 || command.contains('\r') || command.contains('\n') {
        return None;
    }
    let tokens: Vec<_> = command.split_ascii_whitespace().collect();
    if tokens.first() != Some(&"cargo")
        || !matches!(tokens.get(1), Some(&"test" | &"check" | &"build"))
    {
        return None;
    }
    let mut index = 2;
    while index < tokens.len() {
        match tokens[index] {
            "--workspace" | "--all-targets" | "--locked" | "--offline" | "--lib" | "--doc"
            | "--quiet" | "-q" => index += 1,
            "-p" | "--package" => {
                let package = *tokens.get(index + 1)?;
                if package.is_empty()
                    || !package.bytes().all(|byte| {
                        byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.')
                    })
                {
                    return None;
                }
                index += 2;
            }
            _ => return None,
        }
    }
    Some(tokens.join(" "))
}

fn rg_search_command(command: &str) -> Option<String> {
    if command.len() > 512 || command.contains('\r') || command.contains('\n') {
        return None;
    }
    let tokens: Vec<_> = command.split_ascii_whitespace().collect();
    if !matches!(
        tokens.first()?.to_ascii_lowercase().as_str(),
        "rg" | "rg.exe"
    ) {
        return None;
    }
    let mut line_numbered = false;
    let mut with_filename = false;
    let mut index = 1;
    while let Some(flag) = tokens.get(index) {
        match *flag {
            "-n" | "--line-number" => line_numbered = true,
            "-H" | "--with-filename" => with_filename = true,
            "--color=never" | "--no-heading" => {}
            _ => break,
        }
        index += 1;
    }
    if !line_numbered || !with_filename {
        return None;
    }
    let pattern = *tokens.get(index)?;
    if pattern.is_empty()
        || !pattern
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b'-'))
        || pattern.starts_with('-')
    {
        return None;
    }
    let paths = tokens.get(index + 1..)?;
    if paths.is_empty()
        || paths.iter().any(|path| {
            path.is_empty()
                || path.starts_with(['-', '/', '\\'])
                || !path.bytes().all(|byte| {
                    byte.is_ascii_alphanumeric()
                        || matches!(byte, b'_' | b'-' | b'.' | b'/' | b'\\')
                })
                || path.split(['/', '\\']).any(|segment| segment == "..")
        })
    {
        return None;
    }
    Some(tokens.join(" "))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rewrites_only_plain_local_powershell_cargo_commands_and_rg_searches() {
        let workspace = tempfile::tempdir().unwrap();
        fs::create_dir_all(workspace.path().join(".codex")).unwrap();
        fs::create_dir_all(workspace.path().join(".jevto-store")).unwrap();
        let cwd = workspace.path().to_string_lossy().into_owned();
        let event = json!({
            "hook_event_name":"PreToolUse", "tool_name":"Bash", "cwd":cwd,
            "session_id":"11111111-2222-3333-4444-555555555555",
            "tool_input":{"command":"cargo test --workspace -p jevto-core", "shell":"powershell", "workdir":workspace.path(), "max_output_tokens":10000}
        });
        let executable = Path::new(r"C:\JevTO\jevto.exe");
        let output = process_event(workspace.path(), executable, &event).unwrap();
        assert_eq!(output["hookSpecificOutput"]["permissionDecision"], "allow");
        let updated = &output["hookSpecificOutput"]["updatedInput"];
        assert_eq!(updated["shell"], "powershell");
        assert_eq!(updated["max_output_tokens"], 10000);
        assert!(updated["command"]
            .as_str()
            .unwrap()
            .contains("run --session 11111111-2222-3333-4444-555555555555 -- cargo test --workspace -p jevto-core"));
        assert!(updated["command"]
            .as_str()
            .unwrap()
            .ends_with("; exit $LASTEXITCODE"));

        let mut check = event.clone();
        check["tool_input"]["command"] = json!("cargo check --workspace");
        let check_output = process_event(workspace.path(), executable, &check).unwrap();
        assert!(
            check_output["hookSpecificOutput"]["updatedInput"]["command"]
                .as_str()
                .unwrap()
                .contains(
                    "run --session 11111111-2222-3333-4444-555555555555 -- cargo check --workspace"
                )
        );

        let mut build = event.clone();
        build["tool_input"]["command"] = json!("cargo build --workspace");
        let build_output = process_event(workspace.path(), executable, &build).unwrap();
        assert!(
            build_output["hookSpecificOutput"]["updatedInput"]["command"]
                .as_str()
                .unwrap()
                .contains(
                    "run --session 11111111-2222-3333-4444-555555555555 -- cargo build --workspace"
                )
        );

        let mut search = event.clone();
        search["tool_input"]["command"] = json!("rg -n -H --color=never needle src/cache.rs");
        let search_output = process_event(workspace.path(), executable, &search).unwrap();
        assert!(search_output["hookSpecificOutput"]["updatedInput"]["command"]
            .as_str()
            .unwrap()
            .contains("run --session 11111111-2222-3333-4444-555555555555 -- rg -n -H --color=never needle src/cache.rs"));

        for command in [
            "cargo test --workspace; whoami",
            "cargo test --workspace | Out-File x",
            "cargo test -p bad$name",
            "cargo test && echo ok",
            "cargo build --workspace; whoami",
            "cargo build -p bad$name",
            "cargo check --workspace; whoami",
            "cargo check -p bad$name",
            "jevto run -- cargo test",
            "rg -n -H needle ../private",
            "rg -n -H needle C:\\private\\file.rs",
            "python -m unittest discover -s ../other",
            "rg -n -H needle src; whoami",
            "python script.py",
            "npm run dev",
        ] {
            let mut changed = event.clone();
            changed["tool_input"]["command"] = json!(command);
            assert!(
                process_event(workspace.path(), executable, &changed).is_none(),
                "{command}"
            );
        }
        for command in [
            "python -m unittest -v",
            "go test ./...",
            "npm test",
            "cargo test -- --nocapture",
            "rg -n -H 'needle' src",
        ] {
            let mut routed = event.clone();
            routed["tool_input"]["command"] = json!(command);
            let output = process_event(workspace.path(), executable, &routed)
                .unwrap_or_else(|| panic!("{command} should route"));
            assert!(output["hookSpecificOutput"]["updatedInput"]["command"]
                .as_str()
                .unwrap()
                .contains(&format!("-- {command}; exit $LASTEXITCODE")));
        }
        let mut elevated = event.clone();
        elevated["tool_input"]["sandbox_permissions"] = json!("require_escalated");
        assert!(process_event(workspace.path(), executable, &elevated).is_none());
        let mut other_shell = event.clone();
        other_shell["tool_input"]["shell"] = json!("cmd");
        assert!(process_event(workspace.path(), executable, &other_shell).is_none());
        let mut omitted_shell = event.clone();
        omitted_shell["tool_input"]
            .as_object_mut()
            .unwrap()
            .remove("shell");
        assert!(process_event(workspace.path(), executable, &omitted_shell).is_some());
        let outside = tempfile::tempdir().unwrap();
        let mut other_cwd = event.clone();
        other_cwd["tool_input"]["workdir"] = json!(outside.path());
        assert!(process_event(workspace.path(), executable, &other_cwd).is_none());
    }
}
