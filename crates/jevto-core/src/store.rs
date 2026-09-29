use crate::contracts::{
    canonical_hash, Completeness, EvidenceCapture, EvidencePack, ProcessStatus, Receipt, Stream,
    TaskFrame, SCHEMA_VERSION,
};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use thiserror::Error;
use uuid::Uuid;

const DEFAULT_MAX_BYTES: u64 = 1024 * 1024 * 1024;
const DEFAULT_RETENTION_HOURS: i64 = 24;

#[derive(Debug, Error)]
pub enum StoreError {
    #[error("missing: capture or section does not exist")]
    Missing,
    #[error("expired: capture retention ended")]
    Expired,
    #[error("corrupt: capture failed its integrity check")]
    Corrupt,
    #[error("partial: the requested full original was not captured")]
    Partial,
    #[error("unsupported_version: record schema is not supported")]
    UnsupportedVersion,
    #[error("store_full: capture would exceed the configured store limit")]
    Full,
    #[error("invalid_range: requested bytes are outside the historical stream")]
    InvalidRange,
    #[error("invalid_id: expected an opaque capture identifier")]
    InvalidId,
    #[error("conflict: immutable pack ID already stores different content")]
    Conflict,
    #[error("stale_task_revision: task revision is older than the last accepted revision")]
    StaleTaskRevision,
    #[error(
        "task_revision_conflict: goal or trusted constraints changed without a revision increase"
    )]
    TaskRevisionConflict,
    #[error("task_state_busy: another run is updating this task revision")]
    TaskStateBusy,
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    #[error("json: {0}")]
    Json(#[from] serde_json::Error),
}

/// Latest goal a host session submitted, used as its automatic task frame.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct SessionGoal {
    pub schema_version: u32,
    pub session_id: String,
    pub goal: String,
    pub revision: u64,
    pub updated_at: DateTime<Utc>,
}

pub struct CaptureBytes {
    pub record: EvidenceCapture,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
}

#[derive(Serialize, Deserialize)]
struct TaskRevisionState {
    schema_version: u32,
    revision: u64,
    content_sha256: String,
    updated_at: DateTime<Utc>,
}

struct TaskStateLock(PathBuf);

impl Drop for TaskStateLock {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

#[derive(Clone)]
pub struct Store {
    pub root: PathBuf,
    pub max_bytes: u64,
    pub retention_hours: i64,
}

impl Store {
    pub fn default_root() -> PathBuf {
        if let Some(explicit) = std::env::var_os("JEVTO_STORE_DIR") {
            return PathBuf::from(explicit);
        }
        #[cfg(windows)]
        {
            let base =
                std::env::var_os("LOCALAPPDATA").expect("LOCALAPPDATA is required on Windows");
            PathBuf::from(base).join("JevTO")
        }
        #[cfg(not(windows))]
        {
            let base = std::env::var_os("XDG_DATA_HOME")
                .map(PathBuf::from)
                .or_else(|| {
                    std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".local/share"))
                })
                .expect("XDG_DATA_HOME or HOME is required");
            base.join("jevto")
        }
    }

    pub fn default_local() -> Self {
        let max_bytes = std::env::var("JEVTO_MAX_STORE_BYTES")
            .ok()
            .and_then(|n| n.parse().ok())
            .unwrap_or(DEFAULT_MAX_BYTES);
        Self::with_limits(Self::default_root(), max_bytes, DEFAULT_RETENTION_HOURS)
    }

    pub fn with_limits(root: PathBuf, max_bytes: u64, retention_hours: i64) -> Self {
        Self {
            root,
            max_bytes,
            retention_hours,
        }
    }

    fn captures(&self) -> PathBuf {
        self.root.join("captures")
    }
    fn packs(&self) -> PathBuf {
        self.root.join("packs")
    }
    fn receipts(&self) -> PathBuf {
        self.root.join("receipts")
    }
    fn tasks(&self) -> PathBuf {
        self.root.join("tasks")
    }
    fn goals(&self) -> PathBuf {
        self.root.join("goals")
    }

    pub fn initialize(&self) -> Result<(), StoreError> {
        for path in [
            self.captures(),
            self.packs(),
            self.receipts(),
            self.tasks(),
            self.goals(),
        ] {
            fs::create_dir_all(&path)?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&path, fs::Permissions::from_mode(0o700))?;
            }
        }
        Ok(())
    }

    pub fn used_bytes(&self) -> Result<u64, StoreError> {
        let mut total = 0u64;
        for directory in [
            self.captures(),
            self.packs(),
            self.receipts(),
            self.tasks(),
            self.goals(),
        ] {
            if !directory.exists() {
                continue;
            }
            for entry in fs::read_dir(directory)? {
                let entry = entry?;
                if entry.file_type()?.is_file() {
                    total = total.saturating_add(entry.metadata()?.len());
                }
            }
        }
        Ok(total)
    }

    pub fn observe_task_frame(&self, task: &TaskFrame) -> Result<(), StoreError> {
        self.initialize()?;
        let identity = canonical_hash(task.session_id.as_bytes(), task.workspace_id.as_bytes());
        let path = self.tasks().join(format!("{identity}.json"));
        let lock_path = self.tasks().join(format!("{identity}.lock"));
        let lock_file = match OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&lock_path)
        {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                return Err(StoreError::TaskStateBusy);
            }
            Err(error) => return Err(error.into()),
        };
        drop(lock_file);
        let _lock = TaskStateLock(lock_path);
        let content_sha256 = canonical_hash(
            task.goal.as_bytes(),
            &serde_json::to_vec(&task.trusted_constraints)?,
        );
        let current = match fs::read(&path) {
            Ok(bytes) => {
                let state: TaskRevisionState = serde_json::from_slice(&bytes)?;
                if state.schema_version != SCHEMA_VERSION {
                    return Err(StoreError::UnsupportedVersion);
                }
                if state.content_sha256.len() != 64
                    || !state
                        .content_sha256
                        .bytes()
                        .all(|byte| byte.is_ascii_hexdigit())
                {
                    return Err(StoreError::Corrupt);
                }
                Some(state)
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
            Err(error) => return Err(error.into()),
        };
        if let Some(state) = &current {
            if Utc::now() <= state.updated_at + Duration::hours(self.retention_hours) {
                if task.revision < state.revision {
                    return Err(StoreError::StaleTaskRevision);
                }
                if task.revision == state.revision && content_sha256 != state.content_sha256 {
                    return Err(StoreError::TaskRevisionConflict);
                }
            }
        }
        let next = TaskRevisionState {
            schema_version: SCHEMA_VERSION,
            revision: task.revision,
            content_sha256,
            updated_at: Utc::now(),
        };
        let data = serde_json::to_vec_pretty(&next)?;
        let old_len = fs::metadata(&path)
            .map(|metadata| metadata.len())
            .unwrap_or(0);
        if self
            .used_bytes()?
            .saturating_sub(old_len)
            .saturating_add(data.len() as u64)
            > self.max_bytes
        {
            return Err(StoreError::Full);
        }
        write_atomic(&path, &data)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn capture(
        &self,
        session_id: &str,
        workspace_id: &str,
        tool: &str,
        command_display: Option<String>,
        stdout: &[u8],
        stderr: &[u8],
        completeness: Completeness,
        status: ProcessStatus,
        exit_code: Option<i32>,
    ) -> Result<EvidenceCapture, StoreError> {
        self.initialize()?;
        self.purge_expired()?;
        let projected = self
            .used_bytes()?
            .saturating_add(stdout.len() as u64)
            .saturating_add(stderr.len() as u64)
            .saturating_add(4096);
        if projected > self.max_bytes {
            return Err(StoreError::Full);
        }
        let id = Uuid::new_v4().to_string();
        let record = EvidenceCapture {
            schema_version: SCHEMA_VERSION,
            capture_id: id.clone(),
            session_id: session_id.into(),
            workspace_id: workspace_id.into(),
            created_at: Utc::now(),
            tool: tool.into(),
            command_display,
            command_hash: None,
            raw_sha256: canonical_hash(stdout, stderr),
            stdout_bytes: stdout.len() as u64,
            stderr_bytes: stderr.len() as u64,
            completeness,
            status,
            exit_code,
            repo_revision: None,
            extra: Default::default(),
        };
        let out_path = self.capture_path(&id, "stdout")?;
        let err_path = self.capture_path(&id, "stderr")?;
        let meta_path = self.capture_path(&id, "json")?;
        write_atomic(&out_path, stdout)?;
        if let Err(error) = write_atomic(&err_path, stderr) {
            let _ = fs::remove_file(&out_path);
            return Err(error);
        }
        if let Err(error) = write_atomic(&meta_path, &serde_json::to_vec_pretty(&record)?) {
            let _ = fs::remove_file(&out_path);
            let _ = fs::remove_file(&err_path);
            return Err(error);
        }
        Ok(record)
    }

    fn capture_path(&self, id: &str, extension: &str) -> Result<PathBuf, StoreError> {
        if Uuid::parse_str(id).is_err() {
            return Err(StoreError::InvalidId);
        }
        Ok(self.captures().join(format!("{id}.{extension}")))
    }

    /// Full capture ID for a full ID or a unique prefix of at least 8 hex
    /// characters (packs print the short form to save context).
    pub fn resolve_capture_id(&self, id: &str) -> Result<String, StoreError> {
        if Uuid::parse_str(id).is_ok() {
            return Ok(id.into());
        }
        if id == "last" {
            return self.last_capture_id();
        }
        if id.len() < 8
            || !id
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() || byte == b'-')
        {
            return Err(StoreError::InvalidId);
        }
        let prefix = id.to_ascii_lowercase();
        let mut found = None;
        if self.captures().exists() {
            for entry in fs::read_dir(self.captures())? {
                let name = entry?.file_name();
                let Some(stem) = name.to_str().and_then(|name| name.strip_suffix(".json")) else {
                    continue;
                };
                if stem.starts_with(&prefix)
                    && Uuid::parse_str(stem).is_ok()
                    && found.replace(stem.to_string()).is_some()
                {
                    return Err(StoreError::InvalidId);
                }
            }
        }
        found.ok_or(StoreError::Missing)
    }

    /// Most recently written capture (`jevto recall last`).
    fn last_capture_id(&self) -> Result<String, StoreError> {
        let mut newest: Option<(std::time::SystemTime, String)> = None;
        if self.captures().exists() {
            for entry in fs::read_dir(self.captures())? {
                let entry = entry?;
                let name = entry.file_name();
                let Some(stem) = name.to_str().and_then(|name| name.strip_suffix(".json")) else {
                    continue;
                };
                if Uuid::parse_str(stem).is_err() {
                    continue;
                }
                let modified = entry.metadata()?.modified()?;
                if newest.as_ref().is_none_or(|(time, _)| modified > *time) {
                    newest = Some((modified, stem.to_string()));
                }
            }
        }
        newest.map(|(_, id)| id).ok_or(StoreError::Missing)
    }

    pub fn read_capture(&self, id: &str) -> Result<CaptureBytes, StoreError> {
        let path = self.capture_path(id, "json")?;
        let record_bytes = fs::read(path).map_err(missing_if_not_found)?;
        let record: EvidenceCapture =
            serde_json::from_slice(&record_bytes).map_err(version_or_json)?;
        if record.capture_id != id {
            return Err(StoreError::Corrupt);
        }
        if Utc::now() > record.created_at + Duration::hours(self.retention_hours) {
            return Err(StoreError::Expired);
        }
        let stdout = fs::read(self.capture_path(id, "stdout")?).map_err(corrupt_if_not_found)?;
        let stderr = fs::read(self.capture_path(id, "stderr")?).map_err(corrupt_if_not_found)?;
        if stdout.len() as u64 != record.stdout_bytes
            || stderr.len() as u64 != record.stderr_bytes
            || canonical_hash(&stdout, &stderr) != record.raw_sha256
        {
            return Err(StoreError::Corrupt);
        }
        Ok(CaptureBytes {
            record,
            stdout,
            stderr,
        })
    }

    pub fn recall(
        &self,
        id: &str,
        stream: Stream,
        range: Option<(u64, u64)>,
        full: bool,
    ) -> Result<Vec<u8>, StoreError> {
        let capture = self.read_capture(id)?;
        if full && capture.record.completeness == Completeness::Partial {
            return Err(StoreError::Partial);
        }
        let bytes = match stream {
            Stream::Stdout => &capture.stdout,
            Stream::Stderr => &capture.stderr,
        };
        let (start, end) = range.unwrap_or((0, bytes.len() as u64));
        if start > end || end > bytes.len() as u64 {
            return Err(StoreError::InvalidRange);
        }
        Ok(bytes[start as usize..end as usize].to_vec())
    }

    pub fn recall_lines(
        &self,
        id: &str,
        stream: Stream,
        start: usize,
        end: usize,
    ) -> Result<Vec<u8>, StoreError> {
        if start == 0 || end < start {
            return Err(StoreError::InvalidRange);
        }
        let capture = self.read_capture(id)?;
        let bytes = match stream {
            Stream::Stdout => &capture.stdout,
            Stream::Stderr => &capture.stderr,
        };
        std::str::from_utf8(bytes).map_err(|_| StoreError::InvalidRange)?;
        let mut positions = vec![0];
        for (index, byte) in bytes.iter().enumerate() {
            if *byte == b'\n' {
                positions.push(index + 1);
            }
        }
        if *positions.last().unwrap() != bytes.len() {
            positions.push(bytes.len());
        }
        let line_count = positions.len() - 1;
        if end > line_count {
            return Err(StoreError::InvalidRange);
        }
        Ok(bytes[positions[start - 1]..positions[end]].to_vec())
    }

    pub fn save_pack(&self, pack: &EvidencePack) -> Result<(), StoreError> {
        self.initialize()?;
        if Uuid::parse_str(&pack.capture_id).is_err()
            || !pack.pack_id.chars().all(|c| c.is_ascii_hexdigit())
        {
            return Err(StoreError::InvalidId);
        }
        let data = serde_json::to_vec_pretty(pack)?;
        let path = self
            .packs()
            .join(format!("{}-{}.json", pack.capture_id, pack.pack_id));
        match fs::read(&path) {
            Ok(existing) if existing == data => return Ok(()),
            Ok(_) => return Err(StoreError::Conflict),
            Err(error) if error.kind() != std::io::ErrorKind::NotFound => {
                return Err(error.into());
            }
            Err(_) => {}
        }
        if self.used_bytes()?.saturating_add(data.len() as u64) > self.max_bytes {
            return Err(StoreError::Full);
        }
        write_new_atomic(&path, &data)
    }

    pub fn find_section(
        &self,
        capture_id: &str,
        section_id: &str,
    ) -> Result<(Stream, u64, u64), StoreError> {
        let capture = self.read_capture(capture_id)?;
        if !self.packs().exists() {
            return Err(StoreError::Missing);
        }
        for entry in fs::read_dir(self.packs())? {
            let entry = entry?;
            if !entry.file_type()?.is_file() {
                continue;
            }
            let pack: EvidencePack =
                serde_json::from_slice(&fs::read(entry.path())?).map_err(version_or_json)?;
            if pack.capture_id != capture_id || pack.raw_sha256 != capture.record.raw_sha256 {
                continue;
            }
            if let Some(omission) = pack
                .omissions
                .into_iter()
                .find(|section| section.section_id == section_id)
            {
                return Ok((omission.stream, omission.byte_start, omission.byte_end));
            }
        }
        Err(StoreError::Missing)
    }

    pub fn save_receipt(&self, receipt: &Receipt) -> Result<(), StoreError> {
        self.initialize()?;
        if Uuid::parse_str(&receipt.run_id).is_err() {
            return Err(StoreError::InvalidId);
        }
        let data = serde_json::to_vec_pretty(receipt)?;
        if self.used_bytes()?.saturating_add(data.len() as u64) > self.max_bytes {
            return Err(StoreError::Full);
        }
        write_atomic(
            &self.receipts().join(format!("{}.json", receipt.run_id)),
            &data,
        )
    }

    pub fn receipts_for_session(&self, session_id: &str) -> Result<Vec<Receipt>, StoreError> {
        if !self.receipts().exists() {
            return Ok(Vec::new());
        }
        let mut receipts = Vec::new();
        for entry in fs::read_dir(self.receipts())? {
            let entry = entry?;
            if entry.file_type()?.is_file()
                && entry
                    .path()
                    .extension()
                    .is_some_and(|extension| extension == "json")
            {
                let receipt: Receipt =
                    serde_json::from_slice(&fs::read(entry.path())?).map_err(version_or_json)?;
                if receipt.session_id == session_id {
                    receipts.push(receipt);
                }
            }
        }
        Ok(receipts)
    }

    pub fn record_recall(&self, capture_id: &str, bytes: usize) -> Result<bool, StoreError> {
        if Uuid::parse_str(capture_id).is_err() {
            return Err(StoreError::InvalidId);
        }
        if !self.receipts().exists() {
            return Ok(false);
        }
        for entry in fs::read_dir(self.receipts())? {
            let entry = entry?;
            if !entry.file_type()?.is_file()
                || entry
                    .path()
                    .extension()
                    .is_none_or(|extension| extension != "json")
            {
                continue;
            }
            let path = entry.path();
            let mut receipt: Receipt =
                serde_json::from_slice(&fs::read(&path)?).map_err(version_or_json)?;
            if receipt.extra.get("capture_id").and_then(|id| id.as_str()) != Some(capture_id) {
                continue;
            }
            receipt.recalls = receipt.recalls.saturating_add(1);
            let previous = receipt
                .extra
                .get("recalled_bytes")
                .and_then(|value| value.as_u64())
                .unwrap_or(0);
            receipt.extra.insert(
                "recalled_bytes".into(),
                serde_json::json!(previous.saturating_add(bytes as u64)),
            );
            write_atomic(&path, &serde_json::to_vec_pretty(&receipt)?)?;
            return Ok(true);
        }
        Ok(false)
    }

    fn goal_path(&self, session_id: &str) -> PathBuf {
        let identity = canonical_hash(b"session-goal-v1", session_id.as_bytes());
        self.goals().join(format!("{identity}.json"))
    }

    /// Records the latest user goal for a host session. The revision advances
    /// only when the goal text changes. The goal stays local; it is sent to Jev
    /// only when a run explicitly opts into adaptive mode.
    pub fn record_session_goal(
        &self,
        session_id: &str,
        goal: &str,
    ) -> Result<SessionGoal, StoreError> {
        self.initialize()?;
        let path = self.goal_path(session_id);
        let previous = self.session_goal(session_id)?;
        let revision = match &previous {
            Some(previous) if previous.goal == goal => previous.revision,
            Some(previous) => previous.revision.saturating_add(1),
            None => 1,
        };
        let record = SessionGoal {
            schema_version: SCHEMA_VERSION,
            session_id: session_id.into(),
            goal: goal.into(),
            revision,
            updated_at: Utc::now(),
        };
        let data = serde_json::to_vec_pretty(&record)?;
        if self.used_bytes()?.saturating_add(data.len() as u64) > self.max_bytes {
            return Err(StoreError::Full);
        }
        write_atomic(&path, &data)?;
        Ok(record)
    }

    /// The unexpired goal recorded for a host session, if any.
    pub fn session_goal(&self, session_id: &str) -> Result<Option<SessionGoal>, StoreError> {
        let bytes = match fs::read(self.goal_path(session_id)) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error.into()),
        };
        let record: SessionGoal = serde_json::from_slice(&bytes).map_err(version_or_json)?;
        if record.session_id != session_id
            || Utc::now() > record.updated_at + Duration::hours(self.retention_hours)
        {
            return Ok(None);
        }
        Ok(Some(record))
    }

    /// All receipts in the store, newest first.
    pub fn all_receipts(&self) -> Result<Vec<Receipt>, StoreError> {
        if !self.receipts().exists() {
            return Ok(Vec::new());
        }
        let mut receipts = Vec::new();
        for entry in fs::read_dir(self.receipts())? {
            let entry = entry?;
            if entry.file_type()?.is_file()
                && entry
                    .path()
                    .extension()
                    .is_some_and(|extension| extension == "json")
            {
                if let Ok(receipt) = serde_json::from_slice::<Receipt>(&fs::read(entry.path())?) {
                    receipts.push(receipt);
                }
            }
        }
        Ok(receipts)
    }

    pub fn purge_expired(&self) -> Result<usize, StoreError> {
        let mut expired = HashSet::new();
        if self.captures().exists() {
            for entry in fs::read_dir(self.captures())? {
                let entry = entry?;
                if entry.path().extension().is_none_or(|ext| ext != "json") {
                    continue;
                }
                let record: EvidenceCapture =
                    serde_json::from_slice(&fs::read(entry.path())?).map_err(version_or_json)?;
                if Utc::now() > record.created_at + Duration::hours(self.retention_hours) {
                    expired.insert(record.capture_id.clone());
                    for extension in ["json", "stdout", "stderr"] {
                        let path = self.capture_path(&record.capture_id, extension)?;
                        if path.exists() {
                            fs::remove_file(path)?;
                        }
                    }
                }
            }
        }
        if !expired.is_empty() && self.packs().exists() {
            for entry in fs::read_dir(self.packs())? {
                let entry = entry?;
                if !entry.file_type()?.is_file() {
                    continue;
                }
                let path = entry.path();
                if path.extension().is_none_or(|ext| ext != "json") {
                    continue;
                }
                let value: serde_json::Value = serde_json::from_slice(&fs::read(&path)?)?;
                if value
                    .get("capture_id")
                    .and_then(|value| value.as_str())
                    .is_some_and(|id| expired.contains(id))
                {
                    fs::remove_file(path)?;
                }
            }
        }
        if self.tasks().exists() {
            for entry in fs::read_dir(self.tasks())? {
                let entry = entry?;
                let path = entry.path();
                if !entry.file_type()?.is_file()
                    || path.extension().is_none_or(|extension| extension != "lock")
                {
                    continue;
                }
                let modified = DateTime::<Utc>::from(entry.metadata()?.modified()?);
                if Utc::now() > modified + Duration::hours(self.retention_hours) {
                    fs::remove_file(path)?;
                }
            }
            for entry in fs::read_dir(self.tasks())? {
                let entry = entry?;
                let path = entry.path();
                if !entry.file_type()?.is_file()
                    || path.extension().is_none_or(|extension| extension != "json")
                    || path.with_extension("lock").exists()
                {
                    continue;
                }
                let Ok(state) = serde_json::from_slice::<TaskRevisionState>(&fs::read(&path)?)
                else {
                    continue;
                };
                if Utc::now() > state.updated_at + Duration::hours(self.retention_hours) {
                    fs::remove_file(path)?;
                }
            }
        }
        if self.goals().exists() {
            for entry in fs::read_dir(self.goals())? {
                let entry = entry?;
                let path = entry.path();
                if !entry.file_type()?.is_file() {
                    continue;
                }
                let expired_goal = serde_json::from_slice::<SessionGoal>(&fs::read(&path)?)
                    .map_or(true, |record| {
                        Utc::now() > record.updated_at + Duration::hours(self.retention_hours)
                    });
                if expired_goal {
                    fs::remove_file(path)?;
                }
            }
        }
        Ok(expired.len())
    }
}

fn missing_if_not_found(error: std::io::Error) -> StoreError {
    if error.kind() == std::io::ErrorKind::NotFound {
        StoreError::Missing
    } else {
        StoreError::Io(error)
    }
}

fn corrupt_if_not_found(error: std::io::Error) -> StoreError {
    if error.kind() == std::io::ErrorKind::NotFound {
        StoreError::Corrupt
    } else {
        StoreError::Io(error)
    }
}

fn version_or_json(error: serde_json::Error) -> StoreError {
    if error.to_string().contains("unsupported_version") {
        StoreError::UnsupportedVersion
    } else {
        StoreError::Json(error)
    }
}

fn write_atomic(path: &Path, data: &[u8]) -> Result<(), StoreError> {
    let name = format!(
        ".{}.tmp-{}",
        path.file_name().unwrap().to_string_lossy(),
        Uuid::new_v4()
    );
    let temp = path.with_file_name(name);
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&temp)?;
    file.write_all(data)?;
    file.sync_all()?;
    drop(file);
    if let Err(error) = fs::rename(&temp, path) {
        let _ = fs::remove_file(&temp);
        return Err(error.into());
    }
    Ok(())
}

fn write_new_atomic(path: &Path, data: &[u8]) -> Result<(), StoreError> {
    let name = format!(
        ".{}.tmp-{}",
        path.file_name().unwrap().to_string_lossy(),
        Uuid::new_v4()
    );
    let temp = path.with_file_name(name);
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&temp)?;
    file.write_all(data)?;
    file.sync_all()?;
    drop(file);
    let linked = fs::hard_link(&temp, path);
    let _ = fs::remove_file(&temp);
    match linked {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            if fs::read(path)? == data {
                Ok(())
            } else {
                Err(StoreError::Conflict)
            }
        }
        Err(error) => Err(error.into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_recall_and_integrity() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::with_limits(dir.path().to_path_buf(), 1024 * 1024, 24);
        let capture = store
            .capture(
                "s",
                "w",
                "run",
                None,
                b"a\r\nb\n",
                b"warn\n",
                Completeness::Complete,
                ProcessStatus::Completed,
                Some(2),
            )
            .unwrap();
        assert_eq!(
            store.resolve_capture_id(&capture.capture_id[..8]).unwrap(),
            capture.capture_id
        );
        assert_eq!(
            store.resolve_capture_id("last").unwrap(),
            capture.capture_id
        );
        assert!(store.resolve_capture_id("abc").is_err());
        assert!(store.resolve_capture_id("../../etc").is_err());
        assert_eq!(
            store
                .recall(&capture.capture_id, Stream::Stdout, Some((1, 4)), false)
                .unwrap(),
            b"\r\nb"
        );
        assert_eq!(
            store
                .recall_lines(&capture.capture_id, Stream::Stdout, 2, 2)
                .unwrap(),
            b"b\n"
        );
        assert_eq!(
            store
                .recall(&capture.capture_id, Stream::Stderr, None, true)
                .unwrap(),
            b"warn\n"
        );
        fs::write(
            store.capture_path(&capture.capture_id, "stderr").unwrap(),
            b"evil\n",
        )
        .unwrap();
        assert!(matches!(
            store.read_capture(&capture.capture_id),
            Err(StoreError::Corrupt)
        ));
    }

    #[test]
    fn partial_and_quota_are_explicit() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::with_limits(dir.path().to_path_buf(), 4500, 24);
        let capture = store
            .capture(
                "s",
                "w",
                "hook",
                None,
                b"partial",
                b"",
                Completeness::Partial,
                ProcessStatus::Unknown,
                None,
            )
            .unwrap();
        assert!(matches!(
            store.recall(&capture.capture_id, Stream::Stdout, None, true),
            Err(StoreError::Partial)
        ));
        assert!(matches!(
            store.capture(
                "s",
                "w",
                "run",
                None,
                &vec![b'x'; 500],
                b"",
                Completeness::Complete,
                ProcessStatus::Completed,
                Some(0)
            ),
            Err(StoreError::Full)
        ));
    }

    #[test]
    fn expiry_removes_captures_and_associated_packs() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::with_limits(dir.path().to_path_buf(), 1024 * 1024, 24);
        let mut output = Vec::new();
        for n in 0..80 {
            output.extend_from_slice(format!("test suite::{n} ... ok\n").as_bytes());
        }
        let capture = store
            .capture(
                "s",
                "w",
                "run",
                None,
                &output,
                b"",
                Completeness::Complete,
                ProcessStatus::Completed,
                Some(0),
            )
            .unwrap();
        let pack = crate::make_pack(
            &capture,
            &output,
            b"",
            None,
            crate::Mode::Deterministic,
            4096,
        )
        .pack
        .unwrap();
        let capture_bytes = store.used_bytes().unwrap();
        store.save_pack(&pack).unwrap();
        assert!(store.used_bytes().unwrap() > capture_bytes);
        let expired_store = Store::with_limits(dir.path().to_path_buf(), 1024 * 1024, -1);
        assert!(matches!(
            expired_store.read_capture(&capture.capture_id),
            Err(StoreError::Expired)
        ));
        assert_eq!(expired_store.purge_expired().unwrap(), 1);
        assert!(matches!(
            store.read_capture(&capture.capture_id),
            Err(StoreError::Missing)
        ));
        assert_eq!(fs::read_dir(store.packs()).unwrap().count(), 0);
        assert_eq!(store.used_bytes().unwrap(), 0);
    }

    #[test]
    fn recall_updates_only_the_matching_run_receipt() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::with_limits(dir.path().to_path_buf(), 1024 * 1024, 24);
        let capture = store
            .capture(
                "session",
                "workspace",
                "run",
                None,
                b"test output\n",
                b"",
                Completeness::Complete,
                ProcessStatus::Completed,
                Some(0),
            )
            .unwrap();
        let mut extra = std::collections::BTreeMap::new();
        extra.insert("capture_id".into(), serde_json::json!(capture.capture_id));
        let receipt = Receipt {
            schema_version: SCHEMA_VERSION,
            session_id: "session".into(),
            run_id: Uuid::new_v4().to_string(),
            mode: crate::Mode::Deterministic,
            coverage: Vec::new(),
            raw_bytes: 12,
            delivered_bytes: 5,
            recalls: 0,
            provider_usage: None,
            jev_usage: None,
            verification: None,
            extra,
        };
        store.save_receipt(&receipt).unwrap();
        assert!(store.record_recall(&capture.capture_id, 7).unwrap());
        assert!(store.record_recall(&capture.capture_id, 3).unwrap());
        assert!(!store.record_recall(&Uuid::new_v4().to_string(), 9).unwrap());
        let saved = store.receipts_for_session("session").unwrap();
        assert_eq!(saved.len(), 1);
        assert_eq!(saved[0].recalls, 2);
        assert_eq!(saved[0].extra["recalled_bytes"], serde_json::json!(10));
    }

    #[test]
    fn saved_pack_is_immutable_and_idempotent() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::with_limits(dir.path().to_path_buf(), 1024 * 1024, 24);
        let output = (0..80)
            .map(|n| format!("test suite::{n} ... ok\n"))
            .collect::<String>()
            .into_bytes();
        let capture = store
            .capture(
                "session",
                "workspace",
                "run",
                None,
                &output,
                b"",
                Completeness::Complete,
                ProcessStatus::Completed,
                Some(0),
            )
            .unwrap();
        let pack = crate::make_pack(
            &capture,
            &output,
            b"",
            None,
            crate::Mode::Deterministic,
            8192,
        )
        .pack
        .unwrap();
        store.save_pack(&pack).unwrap();
        let path = store
            .packs()
            .join(format!("{}-{}.json", pack.capture_id, pack.pack_id));
        let original = fs::read(&path).unwrap();
        store.save_pack(&pack).unwrap();
        assert_eq!(fs::read(&path).unwrap(), original);
        let mut conflicting = pack.clone();
        conflicting.omissions[0].factual_count += 1;
        assert!(matches!(
            store.save_pack(&conflicting),
            Err(StoreError::Conflict)
        ));
        assert_eq!(fs::read(path).unwrap(), original);
    }

    #[test]
    fn task_revision_state_rejects_changed_or_stale_intent_and_expires() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::with_limits(dir.path().to_path_buf(), 1024 * 1024, 24);
        let mut task: TaskFrame = serde_json::from_value(serde_json::json!({
            "schema_version": 1,
            "session_id": "task-session",
            "workspace_id": "workspace-a",
            "revision": 0,
            "goal": "Fix the parser",
            "trusted_constraints": ["Keep errors visible"]
        }))
        .unwrap();
        store.observe_task_frame(&task).unwrap();
        store.observe_task_frame(&task).unwrap();
        let state_files: Vec<_> = fs::read_dir(store.tasks())
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .collect();
        assert_eq!(state_files.len(), 1);
        let state_bytes = fs::read(&state_files[0]).unwrap();
        assert!(!state_bytes
            .windows(b"Fix the parser".len())
            .any(|part| part == b"Fix the parser"));
        assert!(!state_bytes
            .windows(b"Keep errors visible".len())
            .any(|part| part == b"Keep errors visible"));

        task.goal = "Fix the cache".into();
        assert!(matches!(
            store.observe_task_frame(&task),
            Err(StoreError::TaskRevisionConflict)
        ));
        task.goal = "Fix the parser".into();
        task.trusted_constraints.push("Keep public API".into());
        assert!(matches!(
            store.observe_task_frame(&task),
            Err(StoreError::TaskRevisionConflict)
        ));
        task.trusted_constraints.pop();
        task.goal = "Fix the cache".into();
        task.revision = 1;
        store.observe_task_frame(&task).unwrap();
        task.revision = 0;
        assert!(matches!(
            store.observe_task_frame(&task),
            Err(StoreError::StaleTaskRevision)
        ));
        task.workspace_id = "workspace-b".into();
        store.observe_task_frame(&task).unwrap();
        let identity = canonical_hash(task.session_id.as_bytes(), task.workspace_id.as_bytes());
        fs::write(store.tasks().join(format!("{identity}.lock")), b"").unwrap();
        assert!(matches!(
            store.observe_task_frame(&task),
            Err(StoreError::TaskStateBusy)
        ));

        let expired = Store::with_limits(dir.path().to_path_buf(), 1024 * 1024, -1);
        assert_eq!(expired.purge_expired().unwrap(), 0);
        assert_eq!(fs::read_dir(store.tasks()).unwrap().count(), 0);
    }
}
