use crate::HostArg;
use jevto_core::Store;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::error::Error;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use toml_edit::{value, Array, DocumentMut, Item, Table, Value as TomlValue};
use uuid::Uuid;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Host {
    Codex,
    Claude,
    Cursor,
}

impl From<HostArg> for Host {
    fn from(host: HostArg) -> Self {
        match host {
            HostArg::Codex => Self::Codex,
            HostArg::Claude => Self::Claude,
            HostArg::Cursor => Self::Cursor,
        }
    }
}

impl Host {
    fn name(self) -> &'static str {
        match self {
            Self::Codex => "codex",
            Self::Claude => "claude",
            Self::Cursor => "cursor",
        }
    }

    fn config_path(self, home: &Path) -> PathBuf {
        match self {
            Self::Codex => home.join(".codex").join("config.toml"),
            Self::Claude => home.join(".claude.json"),
            Self::Cursor => home.join(".cursor").join("mcp.json"),
        }
    }
}

#[derive(Serialize, Deserialize)]
struct ManagedEntry {
    schema_version: u32,
    host: Host,
    config_path: PathBuf,
    command: String,
    original_sha256: Option<String>,
    backup_path: Option<PathBuf>,
    adopted: bool,
}

#[derive(Clone, Debug, Serialize)]
pub(crate) struct HostRegistration {
    pub(crate) host: &'static str,
    pub(crate) state: &'static str,
}

fn home_dir() -> Result<PathBuf, Box<dyn Error>> {
    let name = if cfg!(windows) { "USERPROFILE" } else { "HOME" };
    Ok(PathBuf::from(
        std::env::var_os(name).ok_or("user home unavailable")?,
    ))
}

fn manifest_path(store: &Store, host: Host) -> PathBuf {
    store
        .root
        .join("integrations")
        .join(format!("{}.json", host.name()))
}

pub(crate) fn read_optional(path: &Path) -> Result<Option<Vec<u8>>, Box<dyn Error>> {
    match fs::read(path) {
        Ok(bytes) => Ok(Some(bytes)),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.into()),
    }
}

pub(crate) fn sha256(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn desired_json(host: Host, command: &str) -> Value {
    match host {
        Host::Claude => json!({"type":"stdio","command":command,"args":["mcp"],"env":{}}),
        Host::Cursor => json!({"command":command,"args":["mcp"]}),
        Host::Codex => unreachable!(),
    }
}

pub(crate) fn parse_json(bytes: Option<&[u8]>) -> Result<(Value, bool), Box<dyn Error>> {
    let Some(bytes) = bytes else {
        return Ok((json!({"mcpServers":{}}), false));
    };
    let bom = bytes.starts_with(&[0xef, 0xbb, 0xbf]);
    let content = if bom { &bytes[3..] } else { bytes };
    let value: Value = serde_json::from_slice(content)?;
    if !value.is_object() {
        return Err("host JSON config must be an object".into());
    }
    Ok((value, bom))
}

fn json_entry(root: &Value) -> Option<&Value> {
    root.get("mcpServers")?.get("jevto")
}

fn edit_json(
    host: Host,
    original: Option<&[u8]>,
    command: &str,
    install: bool,
) -> Result<Vec<u8>, Box<dyn Error>> {
    let (mut root, bom) = parse_json(original)?;
    let servers = root
        .as_object_mut()
        .ok_or("host JSON config must be an object")?
        .entry("mcpServers")
        .or_insert_with(|| json!({}));
    let servers = servers
        .as_object_mut()
        .ok_or("mcpServers must be an object")?;
    let expected = desired_json(host, command);
    if install {
        if servers.contains_key("jevto") {
            return Err(
                "jevto already exists in host config; existing entry was not changed".into(),
            );
        }
        servers.insert("jevto".into(), expected);
    } else if servers.get("jevto") == Some(&expected) {
        servers.remove("jevto");
    } else {
        return Err("jevto entry changed or missing; refusing to remove it".into());
    }
    let mut rendered = serde_json::to_vec_pretty(&root)?;
    rendered.push(b'\n');
    if bom {
        let mut with_bom = vec![0xef, 0xbb, 0xbf];
        with_bom.extend(rendered);
        Ok(with_bom)
    } else {
        Ok(rendered)
    }
}

fn codex_entry(document: &DocumentMut) -> Option<&Item> {
    document.get("mcp_servers")?.get("jevto")
}

fn codex_entry_matches(item: &Item, command: &str) -> bool {
    let Some(table) = item.as_table() else {
        return false;
    };
    let command_matches = table.get("command").and_then(Item::as_str) == Some(command)
        && table
            .get("args")
            .and_then(Item::as_array)
            .is_some_and(|args| {
                args.len() == 1 && args.get(0).and_then(TomlValue::as_str) == Some("mcp")
            });
    if !command_matches {
        return false;
    }
    if table.len() == 2 {
        return true; // Entries installed before scoped Codex tool grants remain removable.
    }
    let tool_names = ["jevto_recall", "jevto_review", "jevto_status"];
    if table.len() != 4
        || !table
            .get("enabled_tools")
            .and_then(Item::as_array)
            .is_some_and(|enabled| {
                enabled.len() == tool_names.len()
                    && tool_names.iter().enumerate().all(|(index, name)| {
                        enabled.get(index).and_then(TomlValue::as_str) == Some(*name)
                    })
            })
    {
        return false;
    }
    table
        .get("tools")
        .and_then(Item::as_table)
        .is_some_and(|tools| {
            tools.len() == tool_names.len()
                && tool_names.iter().all(|name| {
                    tools
                        .get(name)
                        .and_then(Item::as_table)
                        .is_some_and(|tool| {
                            tool.len() == 1
                                && tool.get("approval_mode").and_then(Item::as_str)
                                    == Some("approve")
                        })
                })
        })
}

fn edit_codex(
    original: Option<&[u8]>,
    command: &str,
    install: bool,
) -> Result<Vec<u8>, Box<dyn Error>> {
    let source = original.map(std::str::from_utf8).transpose()?.unwrap_or("");
    let mut document: DocumentMut = source.parse()?;
    if document.get("mcp_servers").is_none() {
        if !install {
            return Err("mcp_servers is missing; refusing to remove JevTO".into());
        }
        document["mcp_servers"] = Item::Table(Table::new());
    }
    let servers = document["mcp_servers"]
        .as_table_mut()
        .ok_or("mcp_servers must be a table")?;
    if install {
        if servers.contains_key("jevto") {
            return Err(
                "jevto already exists in Codex config; existing entry was not changed".into(),
            );
        }
        let mut entry = Table::new();
        entry.insert("command", value(command));
        let mut args = Array::new();
        args.push("mcp");
        entry.insert("args", Item::Value(TomlValue::Array(args)));
        let tool_names = ["jevto_recall", "jevto_review", "jevto_status"];
        let mut enabled_tools = Array::new();
        let mut tools = Table::new();
        for name in tool_names {
            enabled_tools.push(name);
            let mut tool = Table::new();
            tool.insert("approval_mode", value("approve"));
            tools.insert(name, Item::Table(tool));
        }
        entry.insert(
            "enabled_tools",
            Item::Value(TomlValue::Array(enabled_tools)),
        );
        entry.insert("tools", Item::Table(tools));
        servers.insert("jevto", Item::Table(entry));
    } else if servers
        .get("jevto")
        .is_some_and(|item| codex_entry_matches(item, command))
    {
        servers.remove("jevto");
    } else {
        return Err("jevto entry changed or missing; refusing to remove it".into());
    }
    Ok(document.to_string().into_bytes())
}

fn entry_state(
    host: Host,
    bytes: Option<&[u8]>,
    command: &str,
) -> Result<Option<bool>, Box<dyn Error>> {
    match host {
        Host::Codex => {
            let source = bytes.map(std::str::from_utf8).transpose()?.unwrap_or("");
            let document: DocumentMut = source.parse()?;
            Ok(codex_entry(&document).map(|item| codex_entry_matches(item, command)))
        }
        Host::Claude | Host::Cursor => {
            let (root, _) = parse_json(bytes)?;
            Ok(json_entry(&root).map(|entry| entry == &desired_json(host, command)))
        }
    }
}

fn registration_state(host: Host, home: &Path, store: &Store, executable: &Path) -> &'static str {
    let config = host.config_path(home);
    let config_bytes = match read_optional(&config) {
        Ok(bytes) => bytes,
        Err(_) => return "unreadable",
    };
    let manifest_bytes = match read_optional(&manifest_path(store, host)) {
        Ok(bytes) => bytes,
        Err(_) => return "unreadable",
    };
    let current_command = executable.to_string_lossy();
    let observed = match entry_state(host, config_bytes.as_deref(), &current_command) {
        Ok(state) => state,
        Err(_) => return "unreadable",
    };
    let Some(manifest_bytes) = manifest_bytes else {
        return if observed.is_some() {
            "unmanaged"
        } else {
            "missing"
        };
    };
    let Ok(manifest) = serde_json::from_slice::<ManagedEntry>(&manifest_bytes) else {
        return "conflict";
    };
    if manifest.schema_version != 1 || manifest.host != host || manifest.config_path != config {
        return "conflict";
    }
    match entry_state(host, config_bytes.as_deref(), &manifest.command) {
        Ok(Some(true)) => {}
        _ => return "conflict",
    }
    if !Path::new(&manifest.command).is_file() {
        return "missing_executable";
    }
    if Path::new(&manifest.command) == executable {
        "current"
    } else {
        "different_executable"
    }
}

fn registrations_for(home: &Path, store: &Store, executable: &Path) -> Vec<HostRegistration> {
    [Host::Codex, Host::Cursor, Host::Claude]
        .into_iter()
        .map(|host| HostRegistration {
            host: host.name(),
            state: registration_state(host, home, store, executable),
        })
        .collect()
}

pub(crate) fn host_registrations(store: &Store) -> Vec<HostRegistration> {
    match (home_dir(), std::env::current_exe()) {
        (Ok(home), Ok(executable)) => registrations_for(&home, store, &executable),
        _ => [Host::Codex, Host::Cursor, Host::Claude]
            .into_iter()
            .map(|host| HostRegistration {
                host: host.name(),
                state: "unreadable",
            })
            .collect(),
    }
}

fn edit(
    host: Host,
    original: Option<&[u8]>,
    command: &str,
    install: bool,
) -> Result<Vec<u8>, Box<dyn Error>> {
    match host {
        Host::Codex => edit_codex(original, command, install),
        Host::Claude | Host::Cursor => edit_json(host, original, command, install),
    }
}

pub(crate) fn write_atomic(path: &Path, bytes: &[u8]) -> Result<(), Box<dyn Error>> {
    fs::create_dir_all(path.parent().ok_or("path has no parent")?)?;
    let temporary = path.with_file_name(format!(
        ".{}.jevto-{}.tmp",
        path.file_name()
            .ok_or("path has no filename")?
            .to_string_lossy(),
        Uuid::new_v4()
    ));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)?;
    if let Ok(metadata) = fs::metadata(path) {
        file.set_permissions(metadata.permissions())?;
    }
    let result = (|| -> Result<(), Box<dyn Error>> {
        file.write_all(bytes)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn backup(store: &Store, host: Host, bytes: &[u8]) -> Result<PathBuf, Box<dyn Error>> {
    let path = store
        .root
        .join("integrations")
        .join("backups")
        .join(format!("{}-{}.bak", host.name(), Uuid::new_v4()));
    write_atomic(&path, bytes)?;
    Ok(path)
}

fn install(
    host: Host,
    apply: bool,
    adopt_existing: bool,
    home: &Path,
    store: &Store,
    executable: &Path,
) -> Result<String, Box<dyn Error>> {
    let config = host.config_path(home);
    let manifest_file = manifest_path(store, host);
    let command = executable.to_string_lossy().into_owned();
    let original = read_optional(&config)?;
    if read_optional(&manifest_file)?.is_some() {
        return Err("a JevTO ownership record already exists; inspect or disable it first".into());
    }
    let existing = entry_state(host, original.as_deref(), &command)?;
    let adopted = match existing {
        Some(false) => return Err("jevto is already configured differently; existing entry was not changed".into()),
        Some(true) if adopt_existing => true,
        Some(true) => return Err("matching JevTO entry exists; use --adopt-existing to manage it without changing the host config".into()),
        None => false,
    };
    let rendered = if adopted {
        None
    } else {
        Some(edit(host, original.as_deref(), &command, true)?)
    };
    let operation = if adopted {
        "Adopt the matching JevTO MCP entry; leave host config unchanged"
    } else {
        "Add only the JevTO MCP entry"
    };
    let preview = format!(
        "{} user config: {}\n{} -> {} mcp\nOwnership record: {}",
        host.name(),
        config.display(),
        operation,
        executable.display(),
        manifest_file.display()
    );
    if !apply {
        return Ok(format!("Preview only; no files changed.\n{preview}"));
    }
    if read_optional(&config)? != original {
        return Err("host config changed during setup; retry after inspecting it".into());
    }
    let backup_path = original
        .as_deref()
        .map(|bytes| backup(store, host, bytes))
        .transpose()?;
    let manifest = ManagedEntry {
        schema_version: 1,
        host,
        config_path: config.clone(),
        command: command.clone(),
        original_sha256: original.as_deref().map(sha256),
        backup_path,
        adopted,
    };
    write_atomic(&manifest_file, &serde_json::to_vec_pretty(&manifest)?)?;
    if let Some(rendered) = rendered {
        if let Err(error) = write_atomic(&config, &rendered) {
            let _ = fs::remove_file(&manifest_file);
            return Err(error);
        }
    }
    if entry_state(host, read_optional(&config)?.as_deref(), &command)? != Some(true) {
        if let Some(bytes) = original {
            let _ = write_atomic(&config, &bytes);
        } else {
            let _ = fs::remove_file(&config);
        }
        let _ = fs::remove_file(&manifest_file);
        return Err("host config verification failed; original restored".into());
    }
    Ok(format!(
        "{} JevTO read-only MCP for {}.\n{preview}",
        if adopted { "Adopted" } else { "Installed" },
        host.name()
    ))
}

fn uninstall(
    host: Host,
    apply: bool,
    home: &Path,
    store: &Store,
) -> Result<String, Box<dyn Error>> {
    let config = host.config_path(home);
    let manifest_file = manifest_path(store, host);
    let manifest_bytes =
        fs::read(&manifest_file).map_err(|_| "no JevTO-owned entry for this host")?;
    let manifest: ManagedEntry = serde_json::from_slice(&manifest_bytes)?;
    if manifest.schema_version != 1 || manifest.host != host || manifest.config_path != config {
        return Err("ownership record does not match this host configuration".into());
    }
    let original = fs::read(&config)?;
    if entry_state(host, Some(&original), &manifest.command)? != Some(true) {
        return Err("jevto entry changed or missing; refusing to remove it".into());
    }
    let rendered = edit(host, Some(&original), &manifest.command, false)?;
    let preview = format!(
        "{} user config: {}\nRemove only the JevTO MCP entry matching {} mcp\nKeep all other host settings and JevTO captures",
        host.name(),
        config.display(),
        manifest.command
    );
    if !apply {
        return Ok(format!("Preview only; no files changed.\n{preview}"));
    }
    if fs::read(&config)? != original {
        return Err("host config changed during removal; retry after inspecting it".into());
    }
    let _backup = backup(store, host, &original)?;
    write_atomic(&config, &rendered)?;
    if entry_state(host, read_optional(&config)?.as_deref(), &manifest.command)?.is_some() {
        let _ = write_atomic(&config, &original);
        return Err("host config verification failed; previous config restored".into());
    }
    fs::remove_file(&manifest_file)?;
    Ok(format!(
        "Disabled JevTO MCP for {}.\n{preview}",
        host.name()
    ))
}

pub fn init(
    host: HostArg,
    apply: bool,
    adopt_existing: bool,
    store: &Store,
) -> Result<(), Box<dyn Error>> {
    let result = install(
        host.into(),
        apply,
        adopt_existing,
        &home_dir()?,
        store,
        &std::env::current_exe()?,
    )?;
    println!("{result}");
    Ok(())
}

pub fn disable(host: HostArg, apply: bool, store: &Store) -> Result<(), Box<dyn Error>> {
    println!("{}", uninstall(host.into(), apply, &home_dir()?, store)?);
    Ok(())
}

fn codex_hook_group(executable: &Path) -> Value {
    let path = executable.to_string_lossy();
    let unix = format!("'{}' codex-hook", path.replace('\'', "'\\''"));
    let windows = format!("& '{}' codex-hook", path.replace('\'', "''"));
    json!({
        "matcher": "^Bash$",
        "hooks": [{
            "type": "command",
            "command": unix,
            "commandWindows": windows,
            "timeout": 10,
            "statusMessage": "Selecting JevTO evidence"
        }]
    })
}

fn codex_hook_paths(workspace: &Path, store: &Store) -> Result<(PathBuf, PathBuf), Box<dyn Error>> {
    let workspace = fs::canonicalize(workspace)?;
    if !workspace.is_dir() {
        return Err("hook workspace must be a directory".into());
    }
    let codex_dir = workspace.join(".codex");
    if fs::symlink_metadata(&codex_dir).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err(".codex is a symlink; refusing to edit hook configuration".into());
    }
    let config = codex_dir.join("hooks.json");
    if fs::symlink_metadata(&config).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err("hooks.json is a symlink; refusing to edit it".into());
    }
    let identity = sha256(workspace.to_string_lossy().as_bytes());
    let manifest = store
        .root
        .join("integrations")
        .join("codex-hooks")
        .join(format!("{identity}.json"));
    Ok((config, manifest))
}

fn parse_hook_json(bytes: Option<&[u8]>) -> Result<(Value, bool), Box<dyn Error>> {
    let Some(bytes) = bytes else {
        return Ok((json!({"hooks": {}}), false));
    };
    let bom = bytes.starts_with(&[0xef, 0xbb, 0xbf]);
    let content = if bom { &bytes[3..] } else { bytes };
    let root: Value = serde_json::from_slice(content)?;
    if !root.is_object() {
        return Err("Codex hooks.json must be an object".into());
    }
    Ok((root, bom))
}

fn edit_codex_hook(
    original: Option<&[u8]>,
    executable: &Path,
    install: bool,
) -> Result<Vec<u8>, Box<dyn Error>> {
    let (mut root, bom) = parse_hook_json(original)?;
    let hooks = root
        .as_object_mut()
        .ok_or("Codex hooks.json must be an object")?
        .entry("hooks")
        .or_insert_with(|| json!({}));
    let post_tool_use = hooks
        .as_object_mut()
        .ok_or("hooks must be an object")?
        .entry("PostToolUse")
        .or_insert_with(|| json!([]));
    let groups = post_tool_use
        .as_array_mut()
        .ok_or("PostToolUse must be an array")?;
    let expected = codex_hook_group(executable);
    let matches = groups.iter().filter(|group| *group == &expected).count();
    if install {
        if matches != 0 || groups.iter().any(contains_codex_hook_command) {
            return Err(
                "a codex-hook command already exists; refusing to duplicate or replace it".into(),
            );
        }
        groups.push(expected);
    } else if matches == 1 {
        groups.retain(|group| group != &expected);
    } else {
        return Err("JevTO hook changed, duplicated, or missing; refusing to remove it".into());
    }
    let mut rendered = serde_json::to_vec_pretty(&root)?;
    rendered.push(b'\n');
    if bom {
        let mut with_bom = vec![0xef, 0xbb, 0xbf];
        with_bom.extend(rendered);
        Ok(with_bom)
    } else {
        Ok(rendered)
    }
}

fn contains_codex_hook_command(group: &Value) -> bool {
    group
        .get("hooks")
        .and_then(Value::as_array)
        .is_some_and(|hooks| {
            hooks.iter().any(|hook| {
                ["command", "commandWindows"]
                    .iter()
                    .filter_map(|key| hook.get(key).and_then(Value::as_str))
                    .any(|command| command.split_whitespace().any(|part| part == "codex-hook"))
            })
        })
}

fn codex_hook_matches(bytes: Option<&[u8]>, executable: &Path) -> Result<usize, Box<dyn Error>> {
    let (root, _) = parse_hook_json(bytes)?;
    Ok(root
        .pointer("/hooks/PostToolUse")
        .and_then(Value::as_array)
        .map(|groups| {
            groups
                .iter()
                .filter(|group| *group == &codex_hook_group(executable))
                .count()
        })
        .unwrap_or(0))
}

fn install_codex_hook(
    workspace: &Path,
    apply: bool,
    store: &Store,
    executable: &Path,
) -> Result<String, Box<dyn Error>> {
    let (config, manifest_file) = codex_hook_paths(workspace, store)?;
    if read_optional(&manifest_file)?.is_some() {
        return Err("a JevTO ownership record already exists for this Codex hook".into());
    }
    let original = read_optional(&config)?;
    let rendered = edit_codex_hook(original.as_deref(), executable, true)?;
    let preview = format!(
        "Codex project hook: {}\nAdd JevTO only to PostToolUse Bash hooks\nExecutable: {}\nOwnership record: {}\nCodex must review and trust the new hook before it runs.",
        config.display(), executable.display(), manifest_file.display()
    );
    if !apply {
        return Ok(format!("Preview only; no files changed.\n{preview}"));
    }
    if read_optional(&config)? != original {
        return Err("Codex hook config changed during setup; retry after inspecting it".into());
    }
    let backup_path = original
        .as_deref()
        .map(|bytes| backup(store, Host::Codex, bytes))
        .transpose()?;
    let manifest = ManagedEntry {
        schema_version: 1,
        host: Host::Codex,
        config_path: config.clone(),
        command: executable.to_string_lossy().into_owned(),
        original_sha256: original.as_deref().map(sha256),
        backup_path,
        adopted: false,
    };
    write_atomic(&manifest_file, &serde_json::to_vec_pretty(&manifest)?)?;
    if let Err(error) = write_atomic(&config, &rendered) {
        let _ = fs::remove_file(&manifest_file);
        return Err(error);
    }
    if codex_hook_matches(read_optional(&config)?.as_deref(), executable)? != 1 {
        if let Some(original) = original {
            let _ = write_atomic(&config, &original);
        } else {
            let _ = fs::remove_file(&config);
        }
        let _ = fs::remove_file(&manifest_file);
        return Err("Codex hook verification failed; original restored".into());
    }
    Ok(format!("Installed project-scoped Codex hook.\n{preview}"))
}

fn uninstall_codex_hook(
    workspace: &Path,
    apply: bool,
    store: &Store,
) -> Result<String, Box<dyn Error>> {
    let (config, manifest_file) = codex_hook_paths(workspace, store)?;
    let manifest_bytes = fs::read(&manifest_file).map_err(|_| "no JevTO-owned Codex hook here")?;
    let manifest: ManagedEntry = serde_json::from_slice(&manifest_bytes)?;
    if manifest.schema_version != 1
        || manifest.host != Host::Codex
        || manifest.config_path != config
    {
        return Err("Codex hook ownership record does not match this workspace".into());
    }
    let executable = Path::new(&manifest.command);
    let original = fs::read(&config)?;
    let rendered = edit_codex_hook(Some(&original), executable, false)?;
    let preview = format!(
        "Codex project hook: {}\nRemove only the owned JevTO PostToolUse Bash hook\nKeep other hooks and JevTO captures",
        config.display()
    );
    if !apply {
        return Ok(format!("Preview only; no files changed.\n{preview}"));
    }
    if fs::read(&config)? != original {
        return Err("Codex hook config changed during removal; retry after inspecting it".into());
    }
    let _backup = backup(store, Host::Codex, &original)?;
    write_atomic(&config, &rendered)?;
    if codex_hook_matches(read_optional(&config)?.as_deref(), executable)? != 0 {
        let _ = write_atomic(&config, &original);
        return Err("Codex hook removal verification failed; previous config restored".into());
    }
    fs::remove_file(&manifest_file)?;
    Ok(format!("Disabled project-scoped Codex hook.\n{preview}"))
}

pub fn init_codex_hook(workspace: &Path, apply: bool, store: &Store) -> Result<(), Box<dyn Error>> {
    println!(
        "{}",
        install_codex_hook(workspace, apply, store, &std::env::current_exe()?)?
    );
    Ok(())
}

pub fn disable_codex_hook(
    workspace: &Path,
    apply: bool,
    store: &Store,
) -> Result<(), Box<dyn Error>> {
    println!("{}", uninstall_codex_hook(workspace, apply, store)?);
    Ok(())
}

fn codex_pre_hook_group(executable: &Path, workspace: &Path) -> Value {
    let exe = executable.to_string_lossy();
    let root = workspace.to_string_lossy();
    let unix = format!(
        "'{}' codex-pre-hook --workspace '{}'",
        exe.replace('\'', "'\\''"),
        root.replace('\'', "'\\''")
    );
    let windows = format!(
        "& '{}' codex-pre-hook --workspace '{}'",
        exe.replace('\'', "''"),
        root.replace('\'', "''")
    );
    json!({
        "matcher": "^Bash$",
        "hooks": [{
            "type": "command",
            "command": unix,
            "commandWindows": windows,
            "timeout": 10,
            "statusMessage": "Checking JevTO Rust verifier route"
        }]
    })
}

fn codex_pre_hook_paths(
    workspace: &Path,
    store: &Store,
) -> Result<(PathBuf, PathBuf, PathBuf, PathBuf), Box<dyn Error>> {
    let workspace = fs::canonicalize(workspace)?;
    let (config, _) = codex_hook_paths(&workspace, store)?;
    let local_store = workspace.join(".jevto-store");
    if fs::symlink_metadata(&local_store).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err("Codex JevTO store is a symlink; refusing to use it".into());
    }
    let ignore_file = local_store.join(".gitignore");
    if fs::symlink_metadata(&ignore_file).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err("Codex JevTO store .gitignore is a symlink; refusing to edit it".into());
    }
    let identity = sha256(workspace.to_string_lossy().as_bytes());
    let manifest = store
        .root
        .join("integrations")
        .join("codex-pre-hooks")
        .join(format!("{identity}.json"));
    Ok((config, manifest, local_store, ignore_file))
}

fn edit_codex_pre_hook(
    original: Option<&[u8]>,
    executable: &Path,
    workspace: &Path,
    install: bool,
) -> Result<Vec<u8>, Box<dyn Error>> {
    let (mut root, bom) = parse_hook_json(original)?;
    let hooks = root
        .as_object_mut()
        .ok_or("Codex hooks.json must be an object")?
        .entry("hooks")
        .or_insert_with(|| json!({}));
    let pre = hooks
        .as_object_mut()
        .ok_or("hooks must be an object")?
        .entry("PreToolUse")
        .or_insert_with(|| json!([]));
    let groups = pre.as_array_mut().ok_or("PreToolUse must be an array")?;
    let expected = codex_pre_hook_group(executable, workspace);
    let matches = groups.iter().filter(|group| *group == &expected).count();
    if install {
        if matches != 0 || groups.iter().any(contains_codex_pre_hook_command) {
            return Err(
                "a codex-pre-hook command already exists; refusing to duplicate or replace it"
                    .into(),
            );
        }
        groups.push(expected);
    } else if matches == 1 {
        groups.retain(|group| group != &expected);
    } else {
        return Err("JevTO pre-hook changed, duplicated, or missing; refusing to remove it".into());
    }
    let mut rendered = serde_json::to_vec_pretty(&root)?;
    rendered.push(b'\n');
    if bom {
        let mut with_bom = vec![0xef, 0xbb, 0xbf];
        with_bom.extend(rendered);
        Ok(with_bom)
    } else {
        Ok(rendered)
    }
}

fn contains_codex_pre_hook_command(group: &Value) -> bool {
    group
        .get("hooks")
        .and_then(Value::as_array)
        .is_some_and(|hooks| {
            hooks.iter().any(|hook| {
                ["command", "commandWindows"]
                    .iter()
                    .filter_map(|key| hook.get(key).and_then(Value::as_str))
                    .any(|command| {
                        command
                            .split_whitespace()
                            .any(|part| part == "codex-pre-hook")
                    })
            })
        })
}

fn codex_pre_hook_matches(
    bytes: Option<&[u8]>,
    executable: &Path,
    workspace: &Path,
) -> Result<usize, Box<dyn Error>> {
    let (root, _) = parse_hook_json(bytes)?;
    Ok(root
        .pointer("/hooks/PreToolUse")
        .and_then(Value::as_array)
        .map(|groups| {
            groups
                .iter()
                .filter(|group| *group == &codex_pre_hook_group(executable, workspace))
                .count()
        })
        .unwrap_or(0))
}

fn install_codex_pre_hook(
    workspace: &Path,
    apply: bool,
    store: &Store,
    executable: &Path,
) -> Result<String, Box<dyn Error>> {
    if !cfg!(windows) {
        return Err("the Codex pre-hook is currently supported on Windows PowerShell only".into());
    }
    let workspace = fs::canonicalize(workspace)?;
    let (config, manifest_file, local_store, ignore_file) =
        codex_pre_hook_paths(&workspace, store)?;
    if read_optional(&manifest_file)?.is_some() {
        return Err("a JevTO ownership record already exists for this Codex pre-hook".into());
    }
    let old_ignore = read_optional(&ignore_file)?;
    if old_ignore.is_none() && local_store.is_dir() && fs::read_dir(&local_store)?.next().is_some()
    {
        return Err("Codex JevTO store already contains files without its .gitignore; refusing to hide them".into());
    }
    if old_ignore.as_deref().is_some_and(|bytes| bytes != b"*\n") {
        return Err("Codex JevTO store has a different .gitignore; refusing to replace it".into());
    }
    let original = read_optional(&config)?;
    let rendered = edit_codex_pre_hook(original.as_deref(), executable, &workspace, true)?;
    let preview = format!(
        "Codex project pre-hook: {}\nRoute plain PowerShell cargo test/check/build commands and simple line-numbered rg searches through JevTO\nProject-local ignored capture store: {}\nExecutable: {}\nOwnership record: {}\nCodex must review and trust the hook before it runs. The observed hook input exposed only the command, so approval-request metadata may be unavailable to this filter.",
        config.display(), ignore_file.parent().unwrap().display(), executable.display(), manifest_file.display()
    );
    if !apply {
        return Ok(format!("Preview only; no files changed.\n{preview}"));
    }
    if read_optional(&config)? != original || read_optional(&ignore_file)? != old_ignore {
        return Err(
            "Codex hook config or local store changed during setup; retry after inspecting it"
                .into(),
        );
    }
    let backup_path = original
        .as_deref()
        .map(|bytes| backup(store, Host::Codex, bytes))
        .transpose()?;
    let manifest = ManagedEntry {
        schema_version: 1,
        host: Host::Codex,
        config_path: config.clone(),
        command: executable.to_string_lossy().into_owned(),
        original_sha256: original.as_deref().map(sha256),
        backup_path,
        adopted: false,
    };
    write_atomic(&manifest_file, &serde_json::to_vec_pretty(&manifest)?)?;
    let result = (|| -> Result<(), Box<dyn Error>> {
        if old_ignore.is_none() {
            write_atomic(&ignore_file, b"*\n")?;
        }
        write_atomic(&config, &rendered)?;
        if codex_pre_hook_matches(read_optional(&config)?.as_deref(), executable, &workspace)? != 1
        {
            return Err("Codex pre-hook verification failed".into());
        }
        Ok(())
    })();
    if let Err(error) = result {
        if let Some(bytes) = original {
            let _ = write_atomic(&config, &bytes);
        } else {
            let _ = fs::remove_file(&config);
        }
        if old_ignore.is_none() {
            let _ = fs::remove_file(&ignore_file);
        }
        let _ = fs::remove_file(&manifest_file);
        return Err(error);
    }
    Ok(format!(
        "Installed project-scoped Codex pre-hook.\n{preview}"
    ))
}

fn uninstall_codex_pre_hook(
    workspace: &Path,
    apply: bool,
    store: &Store,
) -> Result<String, Box<dyn Error>> {
    let workspace = fs::canonicalize(workspace)?;
    let (config, manifest_file, local_store, _) = codex_pre_hook_paths(&workspace, store)?;
    let manifest_bytes =
        fs::read(&manifest_file).map_err(|_| "no JevTO-owned Codex pre-hook here")?;
    let manifest: ManagedEntry = serde_json::from_slice(&manifest_bytes)?;
    if manifest.schema_version != 1
        || manifest.host != Host::Codex
        || manifest.config_path != config
    {
        return Err("Codex pre-hook ownership record does not match this workspace".into());
    }
    let executable = Path::new(&manifest.command);
    let original = fs::read(&config)?;
    let rendered = edit_codex_pre_hook(Some(&original), executable, &workspace, false)?;
    let preview = format!(
        "Codex project pre-hook: {}\nRemove only the owned JevTO PreToolUse Bash hook\nKeep other hooks and local captures at {}",
        config.display(), local_store.display()
    );
    if !apply {
        return Ok(format!("Preview only; no files changed.\n{preview}"));
    }
    if fs::read(&config)? != original {
        return Err("Codex hook config changed during removal; retry after inspecting it".into());
    }
    let _backup = backup(store, Host::Codex, &original)?;
    write_atomic(&config, &rendered)?;
    if codex_pre_hook_matches(read_optional(&config)?.as_deref(), executable, &workspace)? != 0 {
        let _ = write_atomic(&config, &original);
        return Err("Codex pre-hook removal verification failed; previous config restored".into());
    }
    fs::remove_file(&manifest_file)?;
    Ok(format!(
        "Disabled project-scoped Codex pre-hook.\n{preview}"
    ))
}

pub fn init_codex_pre_hook(
    workspace: &Path,
    apply: bool,
    store: &Store,
) -> Result<(), Box<dyn Error>> {
    println!(
        "{}",
        install_codex_pre_hook(workspace, apply, store, &std::env::current_exe()?)?
    );
    Ok(())
}

pub fn disable_codex_pre_hook(
    workspace: &Path,
    apply: bool,
    store: &Store,
) -> Result<(), Box<dyn Error>> {
    println!("{}", uninstall_codex_pre_hook(workspace, apply, store)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn setup() -> (tempfile::TempDir, Store, PathBuf) {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::with_limits(temp.path().join("store"), 1024 * 1024, 24);
        let home = temp.path().join("home");
        fs::create_dir_all(&home).unwrap();
        (temp, store, home)
    }

    #[test]
    fn doctor_registration_states_are_read_only_and_detect_stale_paths() {
        let (temp, store, home) = setup();
        let current = temp.path().join("current-jevto.exe");
        let previous = temp.path().join("previous-jevto.exe");
        fs::write(&current, b"current").unwrap();
        fs::write(&previous, b"previous").unwrap();

        for host in [Host::Codex, Host::Cursor, Host::Claude] {
            assert_eq!(registration_state(host, &home, &store, &current), "missing");
            install(host, true, false, &home, &store, &current).unwrap();
            let config = host.config_path(&home);
            let before = fs::read(&config).unwrap();
            let manifest = fs::read(manifest_path(&store, host)).unwrap();
            assert_eq!(registration_state(host, &home, &store, &current), "current");
            assert_eq!(fs::read(&config).unwrap(), before);
            assert_eq!(fs::read(manifest_path(&store, host)).unwrap(), manifest);
        }

        let host = Host::Codex;
        let config = host.config_path(&home);
        uninstall(host, true, &home, &store).unwrap();
        install(host, true, false, &home, &store, &previous).unwrap();
        assert_eq!(
            registration_state(host, &home, &store, &current),
            "different_executable"
        );
        fs::remove_file(&previous).unwrap();
        assert_eq!(
            registration_state(host, &home, &store, &current),
            "missing_executable"
        );
        fs::write(&previous, b"previous").unwrap();
        let owned = fs::read(&config).unwrap();
        let without = edit_codex(Some(&owned), previous.to_str().unwrap(), false).unwrap();
        let changed = edit_codex(Some(&without), current.to_str().unwrap(), true).unwrap();
        fs::write(&config, changed).unwrap();
        assert_eq!(
            registration_state(host, &home, &store, &current),
            "conflict"
        );
        fs::write(&config, &owned).unwrap();
        fs::remove_file(manifest_path(&store, host)).unwrap();
        assert_eq!(
            registration_state(host, &home, &store, &current),
            "unmanaged"
        );
        fs::write(&config, b"not valid TOML").unwrap();
        assert_eq!(
            registration_state(host, &home, &store, &current),
            "unreadable"
        );
    }

    #[test]
    fn preview_is_read_only_and_round_trip_preserves_other_entries() {
        for host in [Host::Codex, Host::Claude, Host::Cursor] {
            let (_temp, store, home) = setup();
            let config = host.config_path(&home);
            fs::create_dir_all(config.parent().unwrap()).unwrap();
            let source = match host {
                Host::Codex => b"# keep comment\n[mcp_servers.other]\ncommand = 'other'\nargs = ['serve']\n".to_vec(),
                Host::Claude => br#"{"mcpServers":{"other":{"command":"other","args":["serve"]}},"unrelated":{"on":true}}"#.to_vec(),
                Host::Cursor => {
                    let mut bytes = vec![0xef, 0xbb, 0xbf];
                    bytes.extend(br#"{"mcpServers":{"other":{"command":"other","args":["serve"]}},"unrelated":true}"#);
                    bytes
                }
            };
            fs::write(&config, &source).unwrap();
            let exe = Path::new(r"C:\JevTO\jevto.exe");
            assert!(install(host, false, false, &home, &store, exe)
                .unwrap()
                .contains("Preview only"));
            assert_eq!(fs::read(&config).unwrap(), source);
            assert!(!manifest_path(&store, host).exists());
            install(host, true, false, &home, &store, exe).unwrap();
            assert_eq!(
                entry_state(
                    host,
                    Some(&fs::read(&config).unwrap()),
                    r"C:\JevTO\jevto.exe"
                )
                .unwrap(),
                Some(true)
            );
            assert!(uninstall(host, false, &home, &store)
                .unwrap()
                .contains("Preview only"));
            uninstall(host, true, &home, &store).unwrap();
            assert_eq!(
                entry_state(
                    host,
                    Some(&fs::read(&config).unwrap()),
                    r"C:\JevTO\jevto.exe"
                )
                .unwrap(),
                None
            );
            assert!(!manifest_path(&store, host).exists());
            let after = fs::read(&config).unwrap();
            match host {
                Host::Codex => {
                    assert!(String::from_utf8(after).unwrap().contains("# keep comment"))
                }
                Host::Claude => assert_eq!(
                    parse_json(Some(&after)).unwrap().0["unrelated"],
                    json!({"on":true})
                ),
                Host::Cursor => assert!(after.starts_with(&[0xef, 0xbb, 0xbf])),
            }
        }
    }

    #[test]
    fn codex_grants_only_the_three_current_tools_and_detects_policy_changes() {
        let (_temp, store, home) = setup();
        let host = Host::Codex;
        let config = host.config_path(&home);
        let command = Path::new(r"C:\JevTO\jevto.exe");
        install(host, true, false, &home, &store, command).unwrap();
        let installed = fs::read_to_string(&config).unwrap();
        let document: DocumentMut = installed.parse().unwrap();
        let entry = codex_entry(&document).unwrap();
        let enabled = entry["enabled_tools"].as_array().unwrap();
        assert_eq!(enabled.len(), 3);
        for name in ["jevto_recall", "jevto_review", "jevto_status"] {
            assert!(enabled.iter().any(|tool| tool.as_str() == Some(name)));
            assert_eq!(
                entry["tools"][name]["approval_mode"].as_str(),
                Some("approve")
            );
        }
        assert!(entry["tools"].get("jevto_run").is_none());

        let changed = installed.replace("approval_mode = \"approve\"", "approval_mode = \"auto\"");
        fs::write(&config, &changed).unwrap();
        assert!(uninstall(host, true, &home, &store).is_err());
        assert_eq!(fs::read_to_string(&config).unwrap(), changed);
        fs::write(&config, &installed).unwrap();
        uninstall(host, true, &home, &store).unwrap();
    }

    #[test]
    fn codex_legacy_entry_is_still_removable() {
        let command = r"C:\JevTO\jevto.exe";
        let legacy = format!("[mcp_servers.jevto]\ncommand = '{command}'\nargs = ['mcp']\n");
        assert_eq!(
            entry_state(Host::Codex, Some(legacy.as_bytes()), command).unwrap(),
            Some(true)
        );
        let removed = edit_codex(Some(legacy.as_bytes()), command, false).unwrap();
        assert_eq!(
            entry_state(Host::Codex, Some(&removed), command).unwrap(),
            None
        );
    }

    #[test]
    fn conflict_and_user_change_block_mutation() {
        let (_temp, store, home) = setup();
        let host = Host::Cursor;
        let config = host.config_path(&home);
        fs::create_dir_all(config.parent().unwrap()).unwrap();
        let existing = br#"{"mcpServers":{"jevto":{"command":"someone-else","args":["mcp"]}}}"#;
        fs::write(&config, existing).unwrap();
        assert!(install(host, true, false, &home, &store, Path::new("jevto.exe")).is_err());
        assert_eq!(fs::read(&config).unwrap(), existing);
        fs::remove_file(&config).unwrap();
        install(host, true, false, &home, &store, Path::new("jevto.exe")).unwrap();
        let changed = br#"{"mcpServers":{"jevto":{"command":"different.exe","args":["mcp"]},"other":{"command":"keep"}}}"#;
        fs::write(&config, changed).unwrap();
        assert!(uninstall(host, true, &home, &store).is_err());
        assert_eq!(fs::read(&config).unwrap(), changed);
    }

    #[test]
    fn matching_existing_entry_requires_explicit_adoption() {
        let (_temp, store, home) = setup();
        let host = Host::Claude;
        let config = host.config_path(&home);
        let command = r"C:\JevTO\jevto.exe";
        let original = serde_json::to_vec_pretty(&json!({
            "mcpServers": {
                "other": {"type":"stdio","command":"other","args":["serve"]},
                "jevto": desired_json(host, command)
            },
            "unrelated": {"keep":true}
        }))
        .unwrap();
        fs::write(&config, &original).unwrap();
        assert!(install(host, true, false, &home, &store, Path::new(command)).is_err());
        let preview = install(host, false, true, &home, &store, Path::new(command)).unwrap();
        assert!(preview.contains("leave host config unchanged"));
        install(host, true, true, &home, &store, Path::new(command)).unwrap();
        assert_eq!(fs::read(&config).unwrap(), original);
        uninstall(host, true, &home, &store).unwrap();
        let (after, _) = parse_json(Some(&fs::read(&config).unwrap())).unwrap();
        assert!(json_entry(&after).is_none());
        assert_eq!(after["unrelated"], json!({"keep":true}));
        assert!(after["mcpServers"].get("other").is_some());
    }

    #[test]
    fn disable_keeps_unrelated_changes_made_after_install() {
        for host in [Host::Codex, Host::Claude, Host::Cursor] {
            let (_temp, store, home) = setup();
            let config = host.config_path(&home);
            let command = Path::new(r"C:\JevTO\jevto.exe");
            install(host, true, false, &home, &store, command).unwrap();
            let current = fs::read(&config).unwrap();
            let edited = match host {
                Host::Codex => {
                    let mut text = String::from_utf8(current).unwrap();
                    text.push_str("\n# added by user\n[other]\nkeep = true\n");
                    text.into_bytes()
                }
                Host::Claude | Host::Cursor => {
                    let (mut root, _) = parse_json(Some(&current)).unwrap();
                    root.as_object_mut()
                        .unwrap()
                        .insert("addedByUser".into(), json!({"keep": true}));
                    serde_json::to_vec_pretty(&root).unwrap()
                }
            };
            fs::write(&config, &edited).unwrap();
            uninstall(host, true, &home, &store).unwrap();
            let after = fs::read(&config).unwrap();
            assert_eq!(
                entry_state(host, Some(&after), command.to_str().unwrap()).unwrap(),
                None
            );
            match host {
                Host::Codex => {
                    let text = String::from_utf8(after).unwrap();
                    assert!(text.contains("# added by user"));
                    assert!(text.contains("keep = true"));
                }
                Host::Claude | Host::Cursor => {
                    let (root, _) = parse_json(Some(&after)).unwrap();
                    assert_eq!(root["addedByUser"], json!({"keep": true}));
                }
            }
        }
    }

    #[test]
    fn codex_project_hook_preview_round_trip_and_user_changes() {
        let (_temp, store, workspace) = setup();
        let config = workspace.join(".codex").join("hooks.json");
        fs::create_dir_all(config.parent().unwrap()).unwrap();
        let mut original = vec![0xef, 0xbb, 0xbf];
        original.extend(br#"{"description":"keep","hooks":{"PostToolUse":[{"matcher":"^Bash$","hooks":[{"type":"command","command":"other"}]}],"SessionStart":[]}}"#);
        fs::write(&config, &original).unwrap();
        let exe = Path::new(r"C:\JevTO\jevto.exe");
        assert!(install_codex_hook(&workspace, false, &store, exe)
            .unwrap()
            .contains("Preview only"));
        assert_eq!(fs::read(&config).unwrap(), original);
        install_codex_hook(&workspace, true, &store, exe).unwrap();
        assert_eq!(
            codex_hook_matches(Some(&fs::read(&config).unwrap()), exe).unwrap(),
            1
        );
        let (mut current, _) = parse_hook_json(Some(&fs::read(&config).unwrap())).unwrap();
        assert_eq!(
            current["hooks"]["PostToolUse"][1]["hooks"][0]["commandWindows"],
            json!(r"& 'C:\JevTO\jevto.exe' codex-hook")
        );
        current["userAdded"] = json!({"keep":true});
        let mut edited = vec![0xef, 0xbb, 0xbf];
        edited.extend(serde_json::to_vec_pretty(&current).unwrap());
        fs::write(&config, edited).unwrap();
        assert!(uninstall_codex_hook(&workspace, false, &store)
            .unwrap()
            .contains("Preview only"));
        uninstall_codex_hook(&workspace, true, &store).unwrap();
        let after_bytes = fs::read(&config).unwrap();
        assert!(after_bytes.starts_with(&[0xef, 0xbb, 0xbf]));
        let (after, _) = parse_hook_json(Some(&after_bytes)).unwrap();
        assert_eq!(after["description"], "keep");
        assert_eq!(after["userAdded"], json!({"keep":true}));
        assert_eq!(after["hooks"]["PostToolUse"].as_array().unwrap().len(), 1);
        assert_eq!(after["hooks"]["SessionStart"], json!([]));
    }

    #[test]
    fn changed_codex_project_hook_blocks_removal() {
        let (_temp, store, workspace) = setup();
        let config = workspace.join(".codex").join("hooks.json");
        let exe = Path::new(r"C:\JevTO\jevto.exe");
        install_codex_hook(&workspace, true, &store, exe).unwrap();
        let (mut root, _) = parse_hook_json(Some(&fs::read(&config).unwrap())).unwrap();
        root["hooks"]["PostToolUse"][0]["hooks"][0]["commandWindows"] =
            json!("changed.exe codex-hook");
        let changed = serde_json::to_vec_pretty(&root).unwrap();
        fs::write(&config, &changed).unwrap();
        assert!(uninstall_codex_hook(&workspace, true, &store).is_err());
        assert_eq!(fs::read(&config).unwrap(), changed);
    }

    #[test]
    fn changed_codex_hook_without_ownership_blocks_install() {
        let (_temp, store, workspace) = setup();
        let config = workspace.join(".codex").join("hooks.json");
        fs::create_dir_all(config.parent().unwrap()).unwrap();
        let original = br#"{"hooks":{"PostToolUse":[{"matcher":"^Bash$","hooks":[{"type":"command","commandWindows":"& 'C:\\Old\\jevto.exe' codex-hook"}]}]}}"#;
        fs::write(&config, original).unwrap();
        let exe = Path::new(r"C:\JevTO\jevto.exe");
        assert!(install_codex_hook(&workspace, true, &store, exe).is_err());
        assert_eq!(fs::read(&config).unwrap(), original);
    }

    #[test]
    fn codex_pre_hook_preview_round_trip_and_changed_entry() {
        let (_temp, store, workspace) = setup();
        let config = workspace.join(".codex").join("hooks.json");
        fs::create_dir_all(config.parent().unwrap()).unwrap();
        let mut original = vec![0xef, 0xbb, 0xbf];
        original.extend(br#"{"description":"keep","hooks":{"PostToolUse":[{"matcher":"^apply_patch$","hooks":[{"type":"command","command":"other"}]}]}}"#);
        fs::write(&config, &original).unwrap();
        let exe = Path::new(r"C:\JevTO\jevto.exe");
        let local_store = workspace.join(".jevto-store");
        assert!(install_codex_pre_hook(&workspace, false, &store, exe)
            .unwrap()
            .contains("Preview only"));
        assert_eq!(fs::read(&config).unwrap(), original);
        assert!(!local_store.exists());

        install_codex_pre_hook(&workspace, true, &store, exe).unwrap();
        assert_eq!(fs::read(local_store.join(".gitignore")).unwrap(), b"*\n");
        assert_eq!(
            codex_pre_hook_matches(
                Some(&fs::read(&config).unwrap()),
                exe,
                &fs::canonicalize(&workspace).unwrap()
            )
            .unwrap(),
            1
        );
        let (mut current, _) = parse_hook_json(Some(&fs::read(&config).unwrap())).unwrap();
        current["userAdded"] = json!({"keep":true});
        let changed = serde_json::to_vec_pretty(&current).unwrap();
        fs::write(&config, &changed).unwrap();
        uninstall_codex_pre_hook(&workspace, true, &store).unwrap();
        let (after, _) = parse_hook_json(Some(&fs::read(&config).unwrap())).unwrap();
        assert_eq!(after["description"], "keep");
        assert_eq!(after["userAdded"], json!({"keep":true}));
        assert_eq!(after["hooks"]["PostToolUse"].as_array().unwrap().len(), 1);
        assert!(after["hooks"]["PreToolUse"].as_array().unwrap().is_empty());
        assert!(local_store.join(".gitignore").exists());

        install_codex_pre_hook(&workspace, true, &store, exe).unwrap();
        let (mut altered, _) = parse_hook_json(Some(&fs::read(&config).unwrap())).unwrap();
        altered["hooks"]["PreToolUse"][0]["hooks"][0]["commandWindows"] =
            json!("changed.exe codex-pre-hook");
        let changed = serde_json::to_vec_pretty(&altered).unwrap();
        fs::write(&config, &changed).unwrap();
        assert!(uninstall_codex_pre_hook(&workspace, true, &store).is_err());
        assert_eq!(fs::read(&config).unwrap(), changed);
    }

    #[test]
    fn codex_pre_hook_refuses_to_hide_existing_unignored_files() {
        let (_temp, store, workspace) = setup();
        let local_store = workspace.join(".jevto-store");
        fs::create_dir_all(&local_store).unwrap();
        fs::write(local_store.join("user-notes.txt"), b"keep visible").unwrap();
        let exe = Path::new(r"C:\JevTO\jevto.exe");
        assert!(install_codex_pre_hook(&workspace, true, &store, exe).is_err());
        assert_eq!(
            fs::read(local_store.join("user-notes.txt")).unwrap(),
            b"keep visible"
        );
        assert!(!workspace.join(".codex/hooks.json").exists());
        assert!(!local_store.join(".gitignore").exists());
    }
}
