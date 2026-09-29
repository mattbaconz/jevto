//! Preview-first setup for one exact Cursor project-rule command.

use crate::integration::{read_optional, sha256};
use jevto_core::Store;
use serde::{Deserialize, Serialize};
use std::error::Error;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

const RULE_NAME: &str = "jevto-verifier.mdc";

#[derive(Serialize, Deserialize)]
struct ManagedRule {
    schema_version: u32,
    workspace: PathBuf,
    rule_path: PathBuf,
    rule_sha256: String,
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
        return Err("Cursor rule store must be outside its workspace".into());
    }
    Ok(root)
}

fn paths(workspace: &Path, store: &Store) -> Result<(PathBuf, PathBuf, PathBuf), Box<dyn Error>> {
    let workspace = fs::canonicalize(workspace)?;
    if !workspace.is_dir() {
        return Err("Cursor rule workspace must be a directory".into());
    }
    let cursor = workspace.join(".cursor");
    let rules = cursor.join("rules");
    let rule = rules.join(RULE_NAME);
    for path in [&cursor, &rules, &rule] {
        if fs::symlink_metadata(path).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
            return Err("Cursor rule path contains a symlink; refusing to edit it".into());
        }
    }
    let root = store_root(store, &workspace)?;
    let identity = sha256(workspace.to_string_lossy().as_bytes());
    let manifest = root
        .join("integrations")
        .join("cursor-rules")
        .join(format!("{identity}.json"));
    Ok((rule, manifest, root))
}

fn validate_command(program: &[String]) -> Result<(), Box<dyn Error>> {
    if program.is_empty() || program.len() > 33 {
        return Err("Cursor rule needs one program and at most 32 arguments".into());
    }
    let total = program.iter().map(String::len).sum::<usize>();
    if total > 1024
        || program.iter().any(|token| {
            token.is_empty()
                || token.len() > 256
                || !token.bytes().all(|byte| {
                    byte.is_ascii_alphanumeric()
                        || matches!(byte, b'_' | b'-' | b'.' | b':' | b'/' | b'\\' | b'=')
                })
                || [
                    "password", "secret", "api_key", "apikey", "token", "bearer", "sk-",
                ]
                .iter()
                .any(|needle| token.to_ascii_lowercase().contains(needle))
        })
    {
        return Err(
            "Cursor rule command must be bounded plain argv without secret-looking text".into(),
        );
    }
    Ok(())
}

fn ps_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "''"))
}

fn shell_path(path: &Path) -> String {
    let value = path.to_string_lossy();
    if let Some(stripped) = value.strip_prefix(r"\\?\") {
        if let Some(unc) = stripped.strip_prefix("UNC\\") {
            return format!(r"\\{unc}");
        }
        return stripped.to_owned();
    }
    value.into_owned()
}

fn render_rule(executable: &Path, root: &Path, workspace: &Path, program: &[String]) -> String {
    let workspace_id = format!(
        "cursor-rule-{}",
        &sha256(workspace.to_string_lossy().as_bytes())[..12]
    );
    let plain = program.join(" ");
    let mut command = format!(
        "& {} --store-dir {} run --workspace-id {} --",
        ps_quote(&shell_path(executable)),
        ps_quote(&shell_path(root)),
        ps_quote(&workspace_id),
    );
    for token in program {
        command.push(' ');
        command.push_str(&ps_quote(token));
    }
    format!(
        "---\ndescription: Use JevTO for this project's {name} verifier\nalwaysApply: true\n---\n\
         When verifying this project with `{plain}`, run this exact shell command once:\n\n\
         `{command}`\n\n\
         It runs the same child argv and returns its exit status. Wait for the command to finish, \
         report the real result and capture ID, and do not run the plain command separately. \
         If successful test lines are deferred, report the printed total; recall only when the \
         needed result is absent. This rule is \
         agent guidance; it does not change Cursor's shell permissions or intercept other calls.\n",
        name = program[0].rsplit(['/', '\\']).next().unwrap_or(&program[0]),
    )
}

fn install(
    workspace: &Path,
    program: &[String],
    apply: bool,
    store: &Store,
    executable: &Path,
) -> Result<String, Box<dyn Error>> {
    validate_command(program)?;
    let workspace = fs::canonicalize(workspace)?;
    let (rule, manifest_file, root) = paths(&workspace, store)?;
    if read_optional(&rule)?.is_some() || read_optional(&manifest_file)?.is_some() {
        return Err(
            "Cursor JevTO rule or ownership record already exists; refusing to replace it".into(),
        );
    }
    let executable = fs::canonicalize(executable)?;
    let rendered = render_rule(&executable, &root, &workspace, program);
    let preview = format!(
        "Cursor project rule: {}\nExact child argv: {}\nJevTO store: {}\nThe rule guides the agent; it does not grant execution permission or intercept shell calls. Review and trust this project rule in Cursor before use.",
        rule.display(), program.join(" "), root.display()
    );
    if !apply {
        return Ok(format!(
            "Preview only; no files changed.\n{preview}\n\n{rendered}"
        ));
    }
    fs::create_dir_all(rule.parent().ok_or("rule has no parent")?)?;
    let (checked_rule, checked_manifest, _) = paths(&workspace, store)?;
    if checked_rule != rule
        || checked_manifest != manifest_file
        || read_optional(&rule)?.is_some()
        || read_optional(&manifest_file)?.is_some()
    {
        return Err("Cursor rule path changed during setup; retry".into());
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&rule)?;
    if let Err(error) = file
        .write_all(rendered.as_bytes())
        .and_then(|_| file.sync_all())
    {
        drop(file);
        let _ = fs::remove_file(&rule);
        return Err(error.into());
    }
    drop(file);
    let manifest = ManagedRule {
        schema_version: 1,
        workspace,
        rule_path: rule.clone(),
        rule_sha256: sha256(rendered.as_bytes()),
    };
    let manifest_bytes = serde_json::to_vec_pretty(&manifest)?;
    if let Err(error) = crate::integration::write_atomic(&manifest_file, &manifest_bytes) {
        let _ = fs::remove_file(&rule);
        return Err(error);
    }
    if sha256(&fs::read(&rule)?) != manifest.rule_sha256 {
        return Err("Cursor rule setup verification failed; inspect the changed file".into());
    }
    Ok(format!("Installed project-scoped Cursor rule.\n{preview}"))
}

fn uninstall(workspace: &Path, apply: bool, store: &Store) -> Result<String, Box<dyn Error>> {
    let workspace = fs::canonicalize(workspace)?;
    let (rule, manifest_file, root) = paths(&workspace, store)?;
    let manifest_bytes = fs::read(&manifest_file).map_err(|_| "no owned Cursor rule here")?;
    let manifest: ManagedRule = serde_json::from_slice(&manifest_bytes)?;
    if manifest.schema_version != 1 || manifest.workspace != workspace || manifest.rule_path != rule
    {
        return Err("Cursor rule ownership record does not match this project".into());
    }
    let original = fs::read(&rule)?;
    if sha256(&original) != manifest.rule_sha256 {
        return Err("Cursor rule changed since setup; refusing to remove it".into());
    }
    let preview = format!(
        "Cursor project rule: {}\nRemove only the unchanged owned rule. Keep other rules and captures at {}.",
        rule.display(), root.display()
    );
    if !apply {
        return Ok(format!("Preview only; no files changed.\n{preview}"));
    }
    if fs::read(&rule)? != original || fs::read(&manifest_file)? != manifest_bytes {
        return Err("Cursor rule or ownership record changed during removal; retry".into());
    }
    fs::remove_file(&rule)?;
    if let Err(error) = fs::remove_file(&manifest_file) {
        let _ = crate::integration::write_atomic(&rule, &original);
        return Err(error.into());
    }
    Ok(format!("Disabled project-scoped Cursor rule.\n{preview}"))
}

pub fn init(
    workspace: &Path,
    program: &[String],
    apply: bool,
    store: &Store,
) -> Result<(), Box<dyn Error>> {
    println!(
        "{}",
        install(workspace, program, apply, store, &std::env::current_exe()?)?
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
    fn preview_apply_disable_preserves_other_rules_and_rejects_changed_rule() {
        let temp = tempfile::tempdir().unwrap();
        let workspace = temp.path().join("workspace");
        let rules = workspace.join(".cursor/rules");
        fs::create_dir_all(&rules).unwrap();
        let other = rules.join("other.mdc");
        fs::write(&other, b"unrelated").unwrap();
        let store = Store::with_limits(temp.path().join("store"), 1024 * 1024, 24);
        let program = vec!["python".into(), "verify_inventory.py".into()];
        let exe = std::env::current_exe().unwrap();
        assert!(install(&workspace, &program, false, &store, &exe)
            .unwrap()
            .contains("Preview only"));
        assert!(!rules.join(RULE_NAME).exists());
        assert!(!store.root.exists());
        install(&workspace, &program, true, &store, &exe).unwrap();
        let rule = rules.join(RULE_NAME);
        let bytes = fs::read(&rule).unwrap();
        assert!(String::from_utf8_lossy(&bytes).contains("alwaysApply: true"));
        assert!(String::from_utf8_lossy(&bytes).contains("-- 'python' 'verify_inventory.py'"));
        assert!(!String::from_utf8_lossy(&bytes).contains(r"\\?\"));
        assert!(install(&workspace, &program, true, &store, &exe).is_err());
        fs::write(&rule, b"user edit").unwrap();
        assert!(uninstall(&workspace, true, &store).is_err());
        assert_eq!(fs::read(&rule).unwrap(), b"user edit");
        fs::write(&rule, bytes).unwrap();
        assert!(uninstall(&workspace, false, &store)
            .unwrap()
            .contains("Preview only"));
        uninstall(&workspace, true, &store).unwrap();
        assert!(!rule.exists());
        assert_eq!(fs::read(&other).unwrap(), b"unrelated");
    }

    #[test]
    fn rejects_secret_like_and_shell_syntax() {
        assert!(validate_command(&["python".into(), "verify.py".into()]).is_ok());
        assert!(validate_command(&["python".into(), "verify.py;evil".into()]).is_err());
        assert!(validate_command(&["python".into(), "--api_key=secret".into()]).is_err());
    }
}
