use crate::integration::{read_optional, sha256, write_atomic};
use jevto_core::Store;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};
use uuid::Uuid;

#[derive(Serialize, Deserialize)]
struct HookManifest {
    schema_version: u32,
    workspace: PathBuf,
    config_path: PathBuf,
    command: PathBuf,
    store_root: PathBuf,
    original_sha256: Option<String>,
}

fn paths(workspace: &Path, store: &Store) -> Result<(PathBuf, PathBuf, PathBuf), Box<dyn Error>> {
    let workspace = fs::canonicalize(workspace)?;
    if !workspace.is_dir() {
        return Err("Claude hook workspace must be a directory".into());
    }
    let directory = workspace.join(".claude");
    if fs::symlink_metadata(&directory).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err(".claude is a symlink; refusing to edit hook configuration".into());
    }
    let config = directory.join("settings.local.json");
    if fs::symlink_metadata(&config).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
        return Err("Claude settings.local.json is a symlink; refusing to edit it".into());
    }
    let identity = sha256(workspace.to_string_lossy().as_bytes());
    let manifest = store
        .root
        .join("integrations")
        .join("claude-hooks")
        .join(format!("{identity}.json"));
    Ok((workspace, config, manifest))
}

fn parse_settings(bytes: Option<&[u8]>) -> Result<(Value, bool), Box<dyn Error>> {
    let Some(bytes) = bytes else {
        return Ok((json!({}), false));
    };
    let bom = bytes.starts_with(&[0xef, 0xbb, 0xbf]);
    let content = if bom { &bytes[3..] } else { bytes };
    let value: Value = serde_json::from_slice(content)?;
    if !value.is_object() {
        return Err("Claude settings.local.json must be an object".into());
    }
    Ok((value, bom))
}

fn hook_group(executable: &Path, workspace: &Path, store_root: &Path) -> Value {
    json!({
        "matcher":"Bash|PowerShell|Grep",
        "hooks":[{
            "type":"command",
            "command":executable,
            "args":["--store-dir",store_root,"claude-hook","--workspace",workspace],
            "timeout":10,
            "statusMessage":"Selecting compact JevTO evidence"
        }]
    })
}

fn contains_jevto_hook(group: &Value) -> bool {
    group
        .get("hooks")
        .and_then(Value::as_array)
        .is_some_and(|hooks| {
            hooks.iter().any(|hook| {
                hook.get("args")
                    .and_then(Value::as_array)
                    .is_some_and(|args| args.iter().any(|arg| arg.as_str() == Some("claude-hook")))
            })
        })
}

fn edit_settings(
    original: Option<&[u8]>,
    executable: &Path,
    workspace: &Path,
    store_root: &Path,
    install: bool,
) -> Result<Vec<u8>, Box<dyn Error>> {
    let (mut root, bom) = parse_settings(original)?;
    let hooks = root
        .as_object_mut()
        .ok_or("Claude settings.local.json must be an object")?
        .entry("hooks")
        .or_insert_with(|| json!({}));
    let post = hooks
        .as_object_mut()
        .ok_or("Claude hooks must be an object")?
        .entry("PostToolUse")
        .or_insert_with(|| json!([]));
    let groups = post
        .as_array_mut()
        .ok_or("Claude PostToolUse hooks must be an array")?;
    let expected = hook_group(executable, workspace, store_root);
    let matches = groups.iter().filter(|group| *group == &expected).count();
    if install {
        if matches != 0 || groups.iter().any(contains_jevto_hook) {
            return Err(
                "a JevTO Claude hook already exists; refusing to duplicate or replace it".into(),
            );
        }
        groups.push(expected);
    } else if matches == 1 {
        groups.retain(|group| group != &expected);
    } else {
        return Err(
            "JevTO Claude hook changed, duplicated, or missing; refusing to remove it".into(),
        );
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

fn matches(
    bytes: Option<&[u8]>,
    executable: &Path,
    workspace: &Path,
    store_root: &Path,
) -> Result<usize, Box<dyn Error>> {
    let (root, _) = parse_settings(bytes)?;
    Ok(root
        .pointer("/hooks/PostToolUse")
        .and_then(Value::as_array)
        .map(|groups| {
            groups
                .iter()
                .filter(|group| *group == &hook_group(executable, workspace, store_root))
                .count()
        })
        .unwrap_or(0))
}

fn backup(store: &Store, bytes: &[u8]) -> Result<(), Box<dyn Error>> {
    let path = store
        .root
        .join("integrations")
        .join("backups")
        .join(format!("claude-hook-{}.bak", Uuid::new_v4()));
    write_atomic(&path, bytes)
}

fn install(
    workspace: &Path,
    apply: bool,
    store: &Store,
    executable: &Path,
) -> Result<String, Box<dyn Error>> {
    let (workspace, config, manifest_file) = paths(workspace, store)?;
    let store_root = if store.root.is_absolute() {
        store.root.clone()
    } else {
        std::env::current_dir()?.join(&store.root)
    };
    if read_optional(&manifest_file)?.is_some() {
        return Err("a JevTO ownership record already exists for this Claude hook".into());
    }
    let original = read_optional(&config)?;
    let rendered = edit_settings(
        original.as_deref(),
        executable,
        &workspace,
        &store_root,
        true,
    )?;
    let preview = format!(
        "Claude project hook: {}\nAdd JevTO only to successful Bash, PowerShell, and Grep PostToolUse results\nExecutable: {}\nOwnership record: {}",
        config.display(),
        executable.display(),
        manifest_file.display()
    );
    if !apply {
        return Ok(format!("Preview only; no files changed.\n{preview}"));
    }
    if read_optional(&config)? != original {
        return Err("Claude hook config changed during setup; retry after inspecting it".into());
    }
    if let Some(bytes) = original.as_deref() {
        backup(store, bytes)?;
    }
    let manifest = HookManifest {
        schema_version: 1,
        workspace: workspace.clone(),
        config_path: config.clone(),
        command: executable.to_path_buf(),
        store_root: store_root.clone(),
        original_sha256: original.as_deref().map(sha256),
    };
    write_atomic(&manifest_file, &serde_json::to_vec_pretty(&manifest)?)?;
    if let Err(error) = write_atomic(&config, &rendered) {
        let _ = fs::remove_file(&manifest_file);
        return Err(error);
    }
    if matches(
        read_optional(&config)?.as_deref(),
        executable,
        &workspace,
        &store_root,
    )? != 1
    {
        if let Some(original) = original {
            let _ = write_atomic(&config, &original);
        } else {
            let _ = fs::remove_file(&config);
        }
        let _ = fs::remove_file(&manifest_file);
        return Err("Claude hook verification failed; original restored".into());
    }
    Ok(format!("Installed project-scoped Claude hook.\n{preview}"))
}

fn uninstall(workspace: &Path, apply: bool, store: &Store) -> Result<String, Box<dyn Error>> {
    let (workspace, config, manifest_file) = paths(workspace, store)?;
    let manifest: HookManifest = serde_json::from_slice(
        &fs::read(&manifest_file).map_err(|_| "no JevTO-owned Claude hook here")?,
    )?;
    if manifest.schema_version != 1
        || manifest.workspace != workspace
        || manifest.config_path != config
    {
        return Err("Claude hook ownership record does not match this workspace".into());
    }
    let original = fs::read(&config)?;
    let rendered = edit_settings(
        Some(&original),
        &manifest.command,
        &workspace,
        &manifest.store_root,
        false,
    )?;
    let preview = format!(
        "Claude project hook: {}\nRemove only the owned JevTO PostToolUse hook\nKeep other Claude settings and JevTO captures",
        config.display()
    );
    if !apply {
        return Ok(format!("Preview only; no files changed.\n{preview}"));
    }
    if fs::read(&config)? != original {
        return Err("Claude hook config changed during removal; retry after inspecting it".into());
    }
    backup(store, &original)?;
    write_atomic(&config, &rendered)?;
    if matches(
        read_optional(&config)?.as_deref(),
        &manifest.command,
        &workspace,
        &manifest.store_root,
    )? != 0
    {
        let _ = write_atomic(&config, &original);
        return Err("Claude hook removal verification failed; previous config restored".into());
    }
    fs::remove_file(&manifest_file)?;
    Ok(format!("Disabled project-scoped Claude hook.\n{preview}"))
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

    #[test]
    fn preview_apply_disable_preserves_unrelated_settings() {
        let workspace = tempfile::tempdir().unwrap();
        let store = Store::with_limits(workspace.path().join("store"), 1024 * 1024, 24);
        let executable = workspace.path().join("jevto.exe");
        fs::write(&executable, b"fixture").unwrap();
        let config = workspace.path().join(".claude").join("settings.local.json");
        fs::create_dir_all(config.parent().unwrap()).unwrap();
        fs::write(&config, b"{\"permissions\":{\"allow\":[\"Read\"]}}\n").unwrap();
        let before = fs::read(&config).unwrap();
        let preview = install(workspace.path(), false, &store, &executable).unwrap();
        assert!(preview.starts_with("Preview only"));
        assert_eq!(fs::read(&config).unwrap(), before);
        install(workspace.path(), true, &store, &executable).unwrap();
        let installed: Value = serde_json::from_slice(&fs::read(&config).unwrap()).unwrap();
        assert_eq!(installed["permissions"]["allow"][0], "Read");
        assert_eq!(
            installed["hooks"]["PostToolUse"].as_array().unwrap().len(),
            1
        );
        let mut with_user_change = installed;
        with_user_change["model"] = json!("claude-haiku-4-5-20251001");
        fs::write(
            &config,
            serde_json::to_vec_pretty(&with_user_change).unwrap(),
        )
        .unwrap();
        uninstall(workspace.path(), true, &store).unwrap();
        let removed: Value = serde_json::from_slice(&fs::read(&config).unwrap()).unwrap();
        assert_eq!(removed["permissions"]["allow"][0], "Read");
        assert_eq!(removed["model"], "claude-haiku-4-5-20251001");
        assert!(removed["hooks"]["PostToolUse"]
            .as_array()
            .unwrap()
            .is_empty());
    }

    #[test]
    fn changed_owned_hook_blocks_removal() {
        let workspace = tempfile::tempdir().unwrap();
        let store = Store::with_limits(workspace.path().join("store"), 1024 * 1024, 24);
        let executable = workspace.path().join("jevto.exe");
        fs::write(&executable, b"fixture").unwrap();
        install(workspace.path(), true, &store, &executable).unwrap();
        let config = workspace.path().join(".claude").join("settings.local.json");
        let mut changed: Value = serde_json::from_slice(&fs::read(&config).unwrap()).unwrap();
        changed["hooks"]["PostToolUse"][0]["hooks"][0]["timeout"] = json!(11);
        fs::write(&config, serde_json::to_vec_pretty(&changed).unwrap()).unwrap();
        assert!(uninstall(workspace.path(), true, &store).is_err());
        assert_eq!(
            serde_json::from_slice::<Value>(&fs::read(&config).unwrap()).unwrap(),
            changed
        );
    }
}
