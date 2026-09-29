use chrono::{DateTime, Utc};
use serde::{de::Error as _, Deserialize, Deserializer, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

pub const SCHEMA_VERSION: u32 = 1;

fn version_one<'de, D: Deserializer<'de>>(deserializer: D) -> Result<u32, D::Error> {
    let version = u32::deserialize(deserializer)?;
    if version == SCHEMA_VERSION {
        Ok(version)
    } else {
        Err(D::Error::custom(format!("unsupported_version: {version}")))
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Completeness {
    Complete,
    Partial,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ProcessStatus {
    Completed,
    Interrupted,
    ToolError,
    Unknown,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    Passthrough,
    Deterministic,
    Adaptive,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum PackCoverage {
    Complete,
    Partial,
    Overflow,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Stream {
    Stdout,
    Stderr,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TaskFrame {
    #[serde(deserialize_with = "version_one")]
    pub schema_version: u32,
    pub session_id: String,
    pub workspace_id: String,
    pub revision: u64,
    pub goal: String,
    pub trusted_constraints: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub repo_revision: Option<String>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

impl TaskFrame {
    pub fn revise(
        &mut self,
        goal: String,
        trusted_constraints: Vec<String>,
    ) -> Result<(), &'static str> {
        if self.goal != goal || self.trusted_constraints != trusted_constraints {
            let next_revision = self
                .revision
                .checked_add(1)
                .ok_or("task_revision_overflow")?;
            self.revision = next_revision;
            self.goal = goal;
            self.trusted_constraints = trusted_constraints;
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EvidenceCapture {
    #[serde(deserialize_with = "version_one")]
    pub schema_version: u32,
    pub capture_id: String,
    pub session_id: String,
    pub workspace_id: String,
    pub created_at: DateTime<Utc>,
    pub tool: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub command_display: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub command_hash: Option<String>,
    pub raw_sha256: String,
    pub stdout_bytes: u64,
    pub stderr_bytes: u64,
    pub completeness: Completeness,
    pub status: ProcessStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub exit_code: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub repo_revision: Option<String>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ProtectedFact {
    pub kind: String,
    pub value: String,
    pub source: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stream: Option<Stream>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub byte_start: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub byte_end: Option<u64>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ExactBlock {
    pub stream: Stream,
    pub byte_start: u64,
    pub byte_end: u64,
    pub sha256: String,
    pub rendered_text: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Omission {
    pub section_id: String,
    pub stream: Stream,
    pub byte_start: u64,
    pub byte_end: u64,
    pub factual_count: u64,
    pub reason: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RecallRef {
    #[serde(deserialize_with = "version_one")]
    pub schema_version: u32,
    pub capture_id: String,
    pub raw_sha256: String,
    pub expires_at: DateTime<Utc>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub section_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stream: Option<Stream>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub byte_start: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub byte_end: Option<u64>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EvidencePack {
    #[serde(deserialize_with = "version_one")]
    pub schema_version: u32,
    pub pack_id: String,
    pub capture_id: String,
    pub raw_sha256: String,
    pub task_revision: u64,
    pub mode: Mode,
    pub coverage: PackCoverage,
    pub protected_facts: Vec<ProtectedFact>,
    pub blocks: Vec<ExactBlock>,
    pub omissions: Vec<Omission>,
    pub recall: Vec<RecallRef>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Finding {
    pub kind: String,
    pub path: String,
    pub evidence: String,
    pub question: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DiffAdvice {
    #[serde(deserialize_with = "version_one")]
    pub schema_version: u32,
    pub workspace_id: String,
    pub task_revision: u64,
    pub base_ref: String,
    pub candidate_sha256: String,
    pub reviewed_paths: Vec<String>,
    pub unreviewed_paths: Vec<String>,
    pub findings: Vec<Finding>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Coverage {
    pub tool_path: String,
    pub captured: bool,
    pub replaced: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bypass_reason: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ObservedUsage {
    pub provider: String,
    pub model: String,
    pub source: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total_input_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fresh_input_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cached_input_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reasoning_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub billed_cost: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub currency: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct VerificationObservation {
    pub check: String,
    pub candidate_sha256: String,
    pub result: String,
    pub observed_at: DateTime<Utc>,
    pub origin: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Receipt {
    #[serde(deserialize_with = "version_one")]
    pub schema_version: u32,
    pub session_id: String,
    pub run_id: String,
    pub mode: Mode,
    pub coverage: Vec<Coverage>,
    pub raw_bytes: u64,
    pub delivered_bytes: u64,
    pub recalls: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provider_usage: Option<ObservedUsage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub jev_usage: Option<ObservedUsage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub verification: Option<Vec<VerificationObservation>>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

pub fn canonical_hash(stdout: &[u8], stderr: &[u8]) -> String {
    let mut hash = Sha256::new();
    hash.update((stdout.len() as u64).to_be_bytes());
    hash.update(stdout);
    hash.update((stderr.len() as u64).to_be_bytes());
    hash.update(stderr);
    hex::encode(hash.finalize())
}

pub fn slice_hash(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_hash_preserves_stream_boundaries() {
        assert_ne!(canonical_hash(b"a", b"bc"), canonical_hash(b"ab", b"c"));
    }

    #[test]
    fn version_and_unknown_optional_fields() {
        let json = r#"{"schema_version":1,"session_id":"s","workspace_id":"w","revision":0,"goal":"x","trusted_constraints":[],"future_field":42}"#;
        let task: TaskFrame = serde_json::from_str(json).unwrap();
        assert_eq!(task.extra["future_field"], 42);
        assert_eq!(serde_json::to_value(&task).unwrap()["future_field"], 42);
        let unsupported = json.replace("\"schema_version\":1", "\"schema_version\":2");
        assert!(serde_json::from_str::<TaskFrame>(&unsupported).is_err());
    }

    #[test]
    fn task_revision_changes_only_for_trusted_edits() {
        let mut task: TaskFrame = serde_json::from_str(r#"{"schema_version":1,"session_id":"s","workspace_id":"w","revision":0,"goal":"fix","trusted_constraints":[]}"#).unwrap();
        task.revise("fix".into(), vec![]).unwrap();
        assert_eq!(task.revision, 0);
        task.revise("fix safely".into(), vec![]).unwrap();
        assert_eq!(task.revision, 1);
        task.revision = u64::MAX;
        assert_eq!(
            task.revise("different goal".into(), vec![]),
            Err("task_revision_overflow")
        );
        assert_eq!(task.goal, "fix safely");
    }
}
