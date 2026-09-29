//! Preview-first, project-scoped setup for Cursor's opt-in exact-command MCP runner.

use crate::integration::{parse_json, read_optional, sha256, write_atomic};
use crate::mcp_run;
use jevto_core::Store;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};
use uuid::Uuid;

const ENTRY_NAME: &str = "jevto_exec";

#[derive(Serialize, Deserialize)]
struct ManagedRunner {
    schema_version: u32,
    workspace: PathBuf,
    config_path: PathBuf,
    policy_path: PathBuf,
    policy_sha256: String,
    entry: Value,
}

fn store_root(store: &Store, workspace: &Path) -> Result<PathBuf, Box<dyn Error>> {
    let absolute = std::path::absolute(&store.root)?;
    let root = if absolute.exists() {
        fs::canonicalize(&absolute)?
    } else {
        let parent = fs::canonicalize(absolute.parent().ok_or("store has no parent")?)?;
        parent.join(absolute.file_name().ok_or("store has no directory name")?)
    };
    if root.starts_with(workspace) {
        return Err("Cursor runner store must be outside its workspace".into());
    }
    Ok(root)
}

fn paths(workspace: &Path, store: &Store) -> Result<(PathBuf, PathBuf, PathBuf), Box<dyn Error>> {
    let workspace = fs::canonicalize(workspace)?;
    if !workspace.is_dir() {
        return Err("Cursor runner workspace must be a directory".into());
    }
    let cursor_dir = workspace.join(".cursor");
    if fs::symlink_metadata(&cursor_dir).is_ok_and(|m| m.file_type().is_symlink()) {
        return Err(".cursor is a symlink; refusing to edit its configuration".into());
    }
    let config = cursor_dir.join("mcp.json");
    if fs::symlink_metadata(&config).is_ok_and(|m| m.file_type().is_symlink()) {
        return Err("Cursor mcp.json is a symlink; refusing to edit it".into());
    }
    let root = store_root(store, &workspace)?;
    let identity = sha256(workspace.to_string_lossy().as_bytes());
    let manifest = root
        .join("integrations")
        .join("cursor-runners")
        .join(format!("{identity}.json"));
    Ok((config, manifest, root))
}

fn edit_config(
    original: Option<&[u8]>,
    expected: &Value,
    install: bool,
) -> Result<Vec<u8>, Box<dyn Error>> {
    let (mut root, bom) = parse_json(original)?;
    let servers = root
        .as_object_mut()
        .ok_or("Cursor mcp.json must be an object")?
        .entry("mcpServers")
        .or_insert_with(|| json!({}));
    let servers = servers
        .as_object_mut()
        .ok_or("Cursor mcpServers must be an object")?;
    if install {
        if servers.contains_key(ENTRY_NAME) {
            return Err("jevto_exec already exists; refusing to replace it".into());
        }
        servers.insert(ENTRY_NAME.into(), expected.clone());
    } else if servers.get(ENTRY_NAME) == Some(expected) {
        servers.remove(ENTRY_NAME);
    } else {
        return Err("jevto_exec changed or missing; refusing to remove it".into());
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

fn backup(root: &Path, bytes: &[u8]) -> Result<(), Box<dyn Error>> {
    let path = root
        .join("integrations")
        .join("backups")
        .join(format!("cursor-runner-{}.bak", Uuid::new_v4()));
    write_atomic(&path, bytes)
}

fn install(
    workspace: &Path,
    policy_path: &Path,
    apply: bool,
    store: &Store,
    executable: &Path,
) -> Result<String, Box<dyn Error>> {
    let workspace = fs::canonicalize(workspace)?;
    let (config, manifest_file, root) = paths(&workspace, store)?;
    let policy_path = fs::canonicalize(policy_path)?;
    let policy_bytes = fs::read(&policy_path)?;
    let policy_sha256 = sha256(&policy_bytes);
    let policy = mcp_run::load_pinned(&policy_path, Some(&policy_sha256))?;
    if policy.workspace() != workspace {
        return Err("MCP run policy workspace does not match the Cursor project".into());
    }
    if read_optional(&manifest_file)?.is_some() {
        return Err("a Cursor runner ownership record already exists; disable it first".into());
    }
    let executable = fs::canonicalize(executable)?;
    let entry = json!({
        "command":executable,
        "args":["--store-dir",root,"mcp","--run-policy",policy_path,"--run-policy-sha256",policy_sha256]
    });
    let original = read_optional(&config)?;
    let rendered = edit_config(original.as_deref(), &entry, true)?;
    let preview = format!(
        "Cursor project config: {}\nAdd only mcpServers.{}:\n{}\nPolicy: {}\nCursor must separately approve this execution server. Its child may write files outside a host shell sandbox; review the exact policy before approval. No command is run during setup.",
        config.display(), ENTRY_NAME, serde_json::to_string_pretty(&entry)?, policy_path.display()
    );
    if !apply {
        return Ok(format!("Preview only; no files changed.\n{preview}"));
    }
    if read_optional(&config)? != original || sha256(&fs::read(&policy_path)?) != policy_sha256 {
        return Err(
            "Cursor config or policy changed during setup; retry after inspecting it".into(),
        );
    }
    if let Some(bytes) = original.as_deref() {
        backup(&root, bytes)?;
    }
    let manifest = ManagedRunner {
        schema_version: 1,
        workspace,
        config_path: config.clone(),
        policy_path,
        policy_sha256,
        entry: entry.clone(),
    };
    write_atomic(&manifest_file, &serde_json::to_vec_pretty(&manifest)?)?;
    if let Err(error) = write_atomic(&config, &rendered) {
        let _ = fs::remove_file(&manifest_file);
        return Err(error);
    }
    let after = read_optional(&config)?;
    if parse_json(after.as_deref())?.0["mcpServers"][ENTRY_NAME] != entry {
        if let Some(bytes) = original {
            let _ = write_atomic(&config, &bytes);
        } else {
            let _ = fs::remove_file(&config);
        }
        let _ = fs::remove_file(&manifest_file);
        return Err("Cursor runner setup verification failed; original restored".into());
    }
    Ok(format!(
        "Installed project-scoped Cursor runner.\n{preview}"
    ))
}

fn uninstall(workspace: &Path, apply: bool, store: &Store) -> Result<String, Box<dyn Error>> {
    let workspace = fs::canonicalize(workspace)?;
    let (config, manifest_file, root) = paths(&workspace, store)?;
    let manifest_bytes = fs::read(&manifest_file).map_err(|_| "no owned Cursor runner here")?;
    let manifest: ManagedRunner = serde_json::from_slice(&manifest_bytes)?;
    if manifest.schema_version != 1
        || manifest.workspace != workspace
        || manifest.config_path != config
    {
        return Err("Cursor runner ownership record does not match this project".into());
    }
    let original = fs::read(&config)?;
    let rendered = edit_config(Some(&original), &manifest.entry, false)?;
    let preview = format!(
        "Cursor project config: {}\nRemove only mcpServers.{} matching the owned entry\nKeep other servers, the external policy, and captures at {}",
        config.display(), ENTRY_NAME, root.display()
    );
    if !apply {
        return Ok(format!("Preview only; no files changed.\n{preview}"));
    }
    if fs::read(&config)? != original || fs::read(&manifest_file)? != manifest_bytes {
        return Err("Cursor config or ownership record changed during removal; retry".into());
    }
    backup(&root, &original)?;
    write_atomic(&config, &rendered)?;
    let after = read_optional(&config)?;
    if parse_json(after.as_deref())?.0["mcpServers"]
        .get(ENTRY_NAME)
        .is_some()
    {
        let _ = write_atomic(&config, &original);
        return Err("Cursor runner removal verification failed; previous config restored".into());
    }
    if let Err(error) = fs::remove_file(&manifest_file) {
        let _ = write_atomic(&config, &original);
        return Err(error.into());
    }
    Ok(format!("Disabled project-scoped Cursor runner.\n{preview}"))
}

pub fn init(
    workspace: &Path,
    policy: &Path,
    apply: bool,
    store: &Store,
) -> Result<(), Box<dyn Error>> {
    println!(
        "{}",
        install(workspace, policy, apply, store, &std::env::current_exe()?)?
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

    #[test]
    fn preview_install_and_remove_preserve_other_server_and_bom() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = temp.path().join("workspace");
        fs::create_dir_all(workspace.join(".cursor")).unwrap();
        let config = workspace.join(".cursor/mcp.json");
        let original =
            b"\xef\xbb\xbf{\"mcpServers\":{\"other\":{\"command\":\"keep\"}},\"unrelated\":true}";
        fs::write(&config, original).unwrap();
        let program = std::env::current_exe().unwrap();
        let policy_path = temp.path().join("policy.json");
        fs::write(&policy_path, json!({"schema_version":1,"workspace":workspace,"workspace_id":"fixture","commands":[{"id":"verify","program":program,"argv":["--version"]}]}).to_string()).unwrap();
        let store = Store::with_limits(temp.path().join("store"), 1024 * 1024, 24);
        let preview = install(&workspace, &policy_path, false, &store, &program).unwrap();
        assert!(preview.contains("Preview only"));
        assert_eq!(fs::read(&config).unwrap(), original);
        assert!(!store.root.exists());
        install(&workspace, &policy_path, true, &store, &program).unwrap();
        let installed = fs::read(&config).unwrap();
        assert!(installed.starts_with(&[0xef, 0xbb, 0xbf]));
        let parsed = parse_json(Some(&installed)).unwrap().0;
        assert_eq!(parsed["mcpServers"]["other"]["command"], "keep");
        assert_eq!(parsed["unrelated"], true);
        assert_eq!(
            parsed["mcpServers"][ENTRY_NAME]["args"][6],
            sha256(&fs::read(&policy_path).unwrap())
        );
        assert!(uninstall(&workspace, false, &store)
            .unwrap()
            .contains("Preview only"));
        assert_eq!(fs::read(&config).unwrap(), installed);
        uninstall(&workspace, true, &store).unwrap();
        let after = fs::read(&config).unwrap();
        assert!(after.starts_with(&[0xef, 0xbb, 0xbf]));
        let parsed = parse_json(Some(&after)).unwrap().0;
        assert!(parsed["mcpServers"].get(ENTRY_NAME).is_none());
        assert_eq!(parsed["mcpServers"]["other"]["command"], "keep");
        assert_eq!(parsed["unrelated"], true);
    }

    #[test]
    fn mismatched_policy_and_changed_owned_entry_are_refused() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = temp.path().join("workspace");
        let other = temp.path().join("other");
        fs::create_dir(&workspace).unwrap();
        fs::create_dir(&other).unwrap();
        let program = std::env::current_exe().unwrap();
        let policy_path = temp.path().join("policy.json");
        let store = Store::with_limits(temp.path().join("store"), 1024 * 1024, 24);
        fs::write(&policy_path, json!({"schema_version":1,"workspace":other,"workspace_id":"fixture","commands":[{"id":"verify","program":program,"argv":[]}]}).to_string()).unwrap();
        assert!(install(&workspace, &policy_path, true, &store, &program).is_err());
        assert!(!workspace.join(".cursor").exists());
        fs::write(&policy_path, json!({"schema_version":1,"workspace":workspace,"workspace_id":"fixture","commands":[{"id":"verify","program":program,"argv":[]}]}).to_string()).unwrap();
        install(&workspace, &policy_path, true, &store, &program).unwrap();
        let config = workspace.join(".cursor/mcp.json");
        let mut parsed = parse_json(Some(&fs::read(&config).unwrap())).unwrap().0;
        parsed["mcpServers"][ENTRY_NAME]["args"][0] = json!("changed");
        fs::write(&config, serde_json::to_vec(&parsed).unwrap()).unwrap();
        assert!(uninstall(&workspace, true, &store).is_err());
        assert_eq!(
            parse_json(Some(&fs::read(&config).unwrap())).unwrap().0,
            parsed
        );
    }
}
