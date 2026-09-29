use crate::contracts::{DiffAdvice, Finding, SCHEMA_VERSION};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::Read;
use std::path::Path;
use std::process::{Command, Stdio};
use thiserror::Error;

const REVIEW_LIMIT: u64 = 2 * 1024 * 1024;

#[derive(Debug, Error)]
pub enum ReviewError {
    #[error("not_repository: git could not inspect this workspace")]
    NotRepository,
    #[error("git: {0}")]
    Git(String),
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
}

fn git(root: &Path, args: &[&str]) -> Result<Vec<u8>, ReviewError> {
    let output = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .env("GIT_OPTIONAL_LOCKS", "0")
        .stdin(Stdio::null())
        .output()?;
    if !output.status.success() {
        return Err(ReviewError::Git(
            String::from_utf8_lossy(&output.stderr).trim().into(),
        ));
    }
    Ok(output.stdout)
}

fn null_paths(bytes: &[u8]) -> Vec<String> {
    bytes
        .split(|byte| *byte == 0)
        .filter(|part| !part.is_empty())
        .map(|part| String::from_utf8_lossy(part).into_owned())
        .collect()
}

/// Base commit, candidate hash, and the changed, untracked, and deleted paths.
type Snapshot = (String, String, Vec<String>, Vec<String>, Vec<String>);

fn candidate_snapshot(root: &Path, base: &str) -> Result<Snapshot, ReviewError> {
    git(root, &["rev-parse", "--is-inside-work-tree"]).map_err(|_| ReviewError::NotRepository)?;
    let base_commit = String::from_utf8(git(root, &["rev-parse", "--verify", base])?)
        .map_err(|_| ReviewError::NotRepository)?
        .trim()
        .to_string();
    let diff = git(
        root,
        &[
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            &base_commit,
            "--",
        ],
    )?;
    let mut tracked = null_paths(&git(
        root,
        &[
            "diff",
            "--no-ext-diff",
            "--name-only",
            "-z",
            &base_commit,
            "--",
        ],
    )?);
    let mut untracked = null_paths(&git(
        root,
        &["ls-files", "--others", "--exclude-standard", "-z", "--"],
    )?);
    tracked.sort();
    untracked.sort();
    let mut manifest = Sha256::new();
    manifest.update(b"jevto-candidate-manifest-v1\0");
    manifest.update(base_commit.as_bytes());
    manifest.update((diff.len() as u64).to_be_bytes());
    manifest.update(&diff);
    let mut reviewed: BTreeSet<String> = tracked.iter().cloned().collect();
    let mut unreviewed = Vec::new();
    let mut new_files = Vec::new();
    for path in &untracked {
        manifest.update((path.len() as u64).to_be_bytes());
        manifest.update(path.as_bytes());
        let full = root.join(path);
        let metadata = fs::symlink_metadata(&full)?;
        if !metadata.file_type().is_file() {
            manifest.update(b"nonregular\0");
            unreviewed.push(path.clone());
            continue;
        }
        manifest.update(metadata.len().to_be_bytes());
        let mut file = File::open(&full)?;
        let mut buffer = [0u8; 64 * 1024];
        let mut text_sample = Vec::new();
        loop {
            let n = file.read(&mut buffer)?;
            if n == 0 {
                break;
            }
            manifest.update(&buffer[..n]);
            if text_sample.len() < 4096 {
                text_sample.extend_from_slice(&buffer[..n.min(4096 - text_sample.len())]);
            }
        }
        if metadata.len() > REVIEW_LIMIT
            || text_sample.contains(&0)
            || std::str::from_utf8(&text_sample).is_err()
        {
            unreviewed.push(path.clone());
        } else {
            reviewed.insert(path.clone());
            new_files.push(path.clone());
        }
    }
    let diff_text = String::from_utf8_lossy(&diff).into_owned();
    Ok((
        hex::encode(manifest.finalize()),
        diff_text,
        reviewed.into_iter().collect(),
        unreviewed,
        new_files,
    ))
}

/// Per-file summary of a candidate change, used for advisory scope review.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ChangedFileSummary {
    pub path: String,
    pub added: usize,
    pub removed: usize,
    pub deleted: bool,
    pub new_untracked: bool,
    /// First changed lines, exactly as they appear in the diff.
    pub excerpt: String,
}

const SOURCE_EXTENSIONS: &[&str] = &[
    ".rs", ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".java", ".kt", ".cs", ".c", ".cc",
    ".cpp", ".h", ".hpp", ".rb", ".php", ".swift", ".scala",
];

fn is_source(path: &str) -> bool {
    SOURCE_EXTENSIONS
        .iter()
        .any(|extension| path.ends_with(extension))
}

fn is_test_path(path: &str) -> bool {
    let lower = path.to_ascii_lowercase();
    lower.contains("test") || lower.contains("spec") || lower.contains("__tests__")
}

fn is_assertion(line: &str) -> bool {
    let lower = line.to_ascii_lowercase();
    [
        "assert",
        "expect(",
        ".should",
        "#[test]",
        "def test",
        "func test",
        "it(",
        "test(",
        "@test",
        "t.error",
        "t.fatal",
    ]
    .iter()
    .any(|needle| lower.contains(needle))
}

fn is_skip_marker(line: &str) -> bool {
    let lower = line.to_ascii_lowercase();
    [
        "#[ignore",
        "pytest.mark.skip",
        "unittest.skip",
        ".skip(",
        "t.skip(",
        "@disabled",
        "xit(",
        "xdescribe(",
        "@ignore",
    ]
    .iter()
    .any(|needle| lower.contains(needle))
}

fn dependency_line(path: &str, line: &str) -> bool {
    let base = path.rsplit('/').next().unwrap_or(path).to_ascii_lowercase();
    let body = line[1..].trim();
    if body.is_empty() || body.starts_with('#') || body.starts_with("//") {
        return false;
    }
    match base.as_str() {
        "cargo.toml" => {
            body.contains('=') && !body.starts_with("version") && !body.starts_with('[')
        }
        "package.json" => {
            // `"name": "^1.2.3"` style entries.
            let Some((name, version)) = body.split_once("\":") else {
                return false;
            };
            let version = version.trim().trim_start_matches('"');
            name.starts_with('"')
                && !matches!(
                    name.trim_matches('"'),
                    "version" | "name" | "main" | "description" | "license" | "type"
                )
                && version.chars().next().is_some_and(|character| {
                    character.is_ascii_digit() || matches!(character, '^' | '~' | '*' | '>' | '<')
                })
        }
        "go.mod" => body.contains(" v") && !body.starts_with("go ") && !body.starts_with("module "),
        "pyproject.toml" => {
            body.starts_with('"')
                && ["==", ">=", "~=", "<=", "^"]
                    .iter()
                    .any(|op| body.contains(op))
        }
        _ if base.starts_with("requirements") && base.ends_with(".txt") => !body.starts_with('-'),
        "gemfile" => body.starts_with("gem "),
        _ => false,
    }
}

/// Parses a unified Git diff into per-file change summaries.
pub fn changed_files(diff: &str) -> Vec<ChangedFileSummary> {
    let mut files: Vec<ChangedFileSummary> = Vec::new();
    for line in diff.lines() {
        if let Some(rest) = line.strip_prefix("diff --git a/") {
            let path = rest
                .split_once(" b/")
                .map_or(rest, |(_, path)| path)
                .to_string();
            files.push(ChangedFileSummary {
                path,
                added: 0,
                removed: 0,
                deleted: false,
                new_untracked: false,
                excerpt: String::new(),
            });
            continue;
        }
        let Some(file) = files.last_mut() else {
            continue;
        };
        if line.starts_with("deleted file mode") {
            file.deleted = true;
        } else if line.starts_with("+++") || line.starts_with("---") {
            continue;
        } else if line.starts_with('+') || line.starts_with('-') {
            if line.starts_with('+') {
                file.added += 1;
            } else {
                file.removed += 1;
            }
            if file.excerpt.lines().count() < 12 {
                file.excerpt.push_str(line);
                file.excerpt.push('\n');
            }
        }
    }
    files
}

fn diff_findings(diff: &str) -> Vec<Finding> {
    let mut findings = Vec::new();
    let mut path = String::new();
    let mut removed_assertions = 0usize;
    let mut added_assertions = 0usize;
    let flush = |path: &str, removed: usize, added: usize, findings: &mut Vec<Finding>| {
        if !path.is_empty() && is_test_path(path) && removed > added {
            findings.push(Finding {
                kind: "weakened_test".into(),
                path: path.into(),
                evidence: format!(
                    "{removed} assertion or test lines removed, {added} added"
                ),
                question: "Does the change still test the original behavior, or were checks removed to make it pass?".into(),
            });
        }
    };
    for line in diff.lines() {
        if let Some(rest) = line.strip_prefix("diff --git a/") {
            flush(&path, removed_assertions, added_assertions, &mut findings);
            removed_assertions = 0;
            added_assertions = 0;
            path = rest
                .split_once(" b/")
                .map_or(rest, |(_, path)| path)
                .to_string();
            continue;
        }
        if line.starts_with("deleted file mode") && is_test_path(&path) && is_source(&path) {
            findings.push(Finding {
                kind: "deleted_test".into(),
                path: path.clone(),
                evidence: format!("Test file deleted: {path}"),
                question: "Is removing this test required, and is its behavior still covered?"
                    .into(),
            });
            continue;
        }
        if line.starts_with("+++") || line.starts_with("---") {
            continue;
        }
        if line.starts_with('+') {
            if is_test_path(&path) && is_assertion(line) {
                added_assertions += 1;
            }
            if is_skip_marker(line) {
                findings.push(Finding {
                    kind: "skipped_test".into(),
                    path: path.clone(),
                    evidence: line.into(),
                    question: "Why is this test skipped, and does the task allow it?".into(),
                });
            }
            if dependency_line(&path, line) {
                findings.push(Finding {
                    kind: "dependency_change".into(),
                    path: path.clone(),
                    evidence: line.into(),
                    question: "Is this dependency needed, and is an existing dependency or standard library feature sufficient?".into(),
                });
            }
        } else if line.starts_with('-') && is_test_path(&path) && is_assertion(line) {
            removed_assertions += 1;
        }
    }
    flush(&path, removed_assertions, added_assertions, &mut findings);
    findings
}

pub fn review_git(
    root: &Path,
    workspace_id: &str,
    task_revision: u64,
    base: &str,
) -> Result<DiffAdvice, ReviewError> {
    review_git_detailed(root, workspace_id, task_revision, base).map(|(advice, _)| advice)
}

/// Review plus per-file change summaries (tracked diffs and new untracked
/// text files) for optional scope questions.
pub fn review_git_detailed(
    root: &Path,
    workspace_id: &str,
    task_revision: u64,
    base: &str,
) -> Result<(DiffAdvice, Vec<ChangedFileSummary>), ReviewError> {
    let (hash, diff, reviewed_paths, unreviewed_paths, new_files) = candidate_snapshot(root, base)?;
    let mut findings = Vec::new();
    let mut summaries = changed_files(&diff);
    for path in new_files {
        if is_source(&path) {
            findings.push(Finding {
                kind: "new_file".into(),
                path: path.clone(),
                evidence: format!("Non-ignored untracked source file: {path}"),
                question: "Does this file serve a requirement that existing code cannot cover?"
                    .into(),
            });
        }
        let text = fs::read_to_string(root.join(&path)).unwrap_or_default();
        summaries.push(ChangedFileSummary {
            added: text.lines().count(),
            removed: 0,
            deleted: false,
            new_untracked: true,
            excerpt: text
                .lines()
                .take(12)
                .map(|line| format!("+{line}\n"))
                .collect(),
            path,
        });
    }
    findings.extend(diff_findings(&diff));
    Ok((
        DiffAdvice {
            schema_version: SCHEMA_VERSION,
            workspace_id: workspace_id.into(),
            task_revision,
            base_ref: base.into(),
            candidate_sha256: hash,
            reviewed_paths,
            unreviewed_paths,
            findings,
            extra: Default::default(),
        },
        summaries,
    ))
}
pub fn stale_advice(
    root: &Path,
    advice: &DiffAdvice,
    current_task_revision: u64,
) -> Result<bool, ReviewError> {
    if current_task_revision != advice.task_revision {
        return Ok(true);
    }
    let (hash, _, _, _, _) = candidate_snapshot(root, &advice.base_ref)?;
    Ok(hash != advice.candidate_sha256)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    fn run(root: &Path, args: &[&str]) {
        let status = Command::new("git")
            .arg("-C")
            .arg(root)
            .args(args)
            .status()
            .unwrap();
        assert!(status.success());
    }

    #[test]
    fn advice_is_read_only_advisory_and_becomes_stale() {
        let dir = tempfile::tempdir().unwrap();
        run(dir.path(), &["init", "-q"]);
        run(dir.path(), &["config", "user.name", "Test"]);
        run(
            dir.path(),
            &["config", "user.email", "test@example.invalid"],
        );
        fs::write(dir.path().join("Cargo.toml"), "[dependencies]\n").unwrap();
        run(dir.path(), &["add", "Cargo.toml"]);
        run(dir.path(), &["commit", "-qm", "base"]);
        fs::write(
            dir.path().join("Cargo.toml"),
            "[dependencies]\nserde = \"1\"\n",
        )
        .unwrap();
        fs::write(dir.path().join("new.rs"), "pub fn x() {}\n").unwrap();
        let advice = review_git(dir.path(), "w", 1, "HEAD").unwrap();
        assert!(advice
            .findings
            .iter()
            .any(|f| f.kind == "dependency_change"));
        assert!(advice.findings.iter().any(|f| f.kind == "new_file"));
        assert!(!stale_advice(dir.path(), &advice, 1).unwrap());
        assert!(stale_advice(dir.path(), &advice, 2).unwrap());
        fs::write(
            dir.path().join("new.rs"),
            "pub fn x() { println!(\"changed\") }\n",
        )
        .unwrap();
        assert!(stale_advice(dir.path(), &advice, 1).unwrap());
    }

    #[test]
    fn diff_findings_flag_weakened_skipped_and_dependency_changes() {
        let diff = r#"diff --git a/tests/parse_test.py b/tests/parse_test.py
--- a/tests/parse_test.py
+++ b/tests/parse_test.py
-    assert parse(' value=7') is None
-    assert parse('value=7') == 7
+    assert parse('value=7') == 7
+@pytest.mark.skip(reason='flaky')
diff --git a/package.json b/package.json
--- a/package.json
+++ b/package.json
+    "left-pad": "^1.3.0",
+  "version": "2.0.0",
diff --git a/src/old_test.rs b/src/old_test.rs
deleted file mode 100644
"#;
        let findings = diff_findings(diff);
        let kinds = findings.iter().map(|f| f.kind.as_str()).collect::<Vec<_>>();
        assert!(kinds.contains(&"weakened_test"), "{kinds:?}");
        assert!(kinds.contains(&"skipped_test"));
        assert!(kinds.contains(&"deleted_test"));
        assert_eq!(
            kinds
                .iter()
                .filter(|kind| **kind == "dependency_change")
                .count(),
            1
        );
        let files = changed_files(diff);
        assert_eq!(files.len(), 3);
        assert_eq!((files[0].added, files[0].removed), (2, 2));
        assert!(files[2].deleted);
    }
}
