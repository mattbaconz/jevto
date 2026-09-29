use crate::contracts::{
    slice_hash, Completeness, EvidenceCapture, EvidencePack, ExactBlock, Mode, Omission,
    PackCoverage, ProcessStatus, ProtectedFact, RecallRef, Stream, TaskFrame, SCHEMA_VERSION,
};
use crate::reduce::{self, fact_kind, passing_test_name};
use chrono::Duration;
use sha2::{Digest, Sha256};

const POLICY_VERSION: &str = "reduction-plan-and-inline-markers-v9.2";
/// A gap marker costs about this many bytes; smaller gaps stay visible.
const MIN_OMISSION_BYTES: usize = 72;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SearchSection {
    pub id: String,
    pub path: String,
    pub first_line: u64,
    pub last_line: u64,
    pub byte_start: u64,
    pub byte_end: u64,
    pub text: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct AdaptiveSelection {
    pub stdout_ranges: Vec<(u64, u64)>,
    pub stderr_ranges: Vec<(u64, u64)>,
    pub default_search_path: Option<String>,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum PackFormat {
    #[default]
    Compact,
    Verbose,
}

fn hash_text(hasher: &mut Sha256, text: &str) {
    hasher.update((text.len() as u64).to_be_bytes());
    hasher.update(text.as_bytes());
}

pub struct PackDecision {
    pub pack: Option<EvidencePack>,
    pub bypass_reason: Option<String>,
}

struct Line<'a> {
    start: usize,
    end: usize,
    bytes: &'a [u8],
}

fn lines(bytes: &[u8]) -> Vec<Line<'_>> {
    let mut result = Vec::new();
    let mut start = 0;
    for (index, byte) in bytes.iter().enumerate() {
        if *byte == b'\n' {
            result.push(Line {
                start,
                end: index + 1,
                bytes: &bytes[start..index + 1],
            });
            start = index + 1;
        }
    }
    if start < bytes.len() {
        result.push(Line {
            start,
            end: bytes.len(),
            bytes: &bytes[start..],
        });
    }
    result
}

/// Line ranges of each file section in a unified Git diff. The first line of
/// each run is its `diff --git` header.
pub(crate) fn diff_file_runs(texts: &[&str]) -> Vec<(usize, usize)> {
    let mut runs = Vec::new();
    let mut start = None;
    for (index, text) in texts.iter().enumerate() {
        if text.starts_with("diff --git a/") {
            if let Some(begin) = start.replace(index) {
                runs.push((begin, index));
            }
        }
    }
    if let Some(begin) = start {
        runs.push((begin, texts.len()));
    }
    runs
}

fn is_rg_capture(capture: &EvidenceCapture) -> bool {
    capture
        .command_display
        .as_deref()
        .and_then(|display| display.split_once(" ["))
        .is_some_and(|(name, _)| {
            name.eq_ignore_ascii_case("rg") || name.eq_ignore_ascii_case("rg.exe")
        })
}

fn is_cargo_capture(capture: &EvidenceCapture) -> bool {
    capture
        .command_display
        .as_deref()
        .and_then(|display| display.split_once(" ["))
        .is_some_and(|(name, _)| {
            name.eq_ignore_ascii_case("cargo") || name.eq_ignore_ascii_case("cargo.exe")
        })
}

fn cargo_progress_identity(line: &[u8]) -> Option<&str> {
    let text = std::str::from_utf8(line)
        .ok()?
        .trim_end_matches(['\r', '\n']);
    if text.contains('\u{1b}') || fact_kind(text).is_some() {
        return None;
    }
    let progress = text
        .strip_prefix("   Compiling ")
        .or_else(|| text.strip_prefix("    Checking "))?;
    let (name, rest) = progress.split_once(" v")?;
    if name.is_empty()
        || !name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        return None;
    }
    let (version, suffix) = rest.split_once(' ').unwrap_or((rest, ""));
    if !version.starts_with(|character: char| character.is_ascii_digit())
        || !version
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'+'))
        || !(suffix.is_empty() || (suffix.starts_with('(') && suffix.ends_with(')')))
    {
        return None;
    }
    Some(name)
}

fn cargo_progress_defer_flags(lines: &[Line<'_>], relevance_text: &[String]) -> Vec<bool> {
    let candidates = lines
        .iter()
        .map(|line| {
            cargo_progress_identity(line.bytes).is_some_and(|name| {
                let name = name.to_ascii_lowercase();
                !relevance_text.iter().any(|text| text.contains(&name))
            })
        })
        .collect::<Vec<_>>();
    let mut defer = vec![false; lines.len()];
    let mut start = 0;
    while start < candidates.len() {
        if !candidates[start] {
            start += 1;
            continue;
        }
        let mut end = start + 1;
        while end < candidates.len() && candidates[end] {
            end += 1;
        }
        if end - start >= 16 {
            defer[start + 2..end - 2].fill(true);
        }
        start = end;
    }
    defer
}

fn search_match(line: &[u8]) -> Option<(&str, u64, &str)> {
    let text = std::str::from_utf8(line)
        .ok()?
        .trim_end_matches(['\r', '\n']);
    if text.contains('\u{1b}') {
        return None;
    }
    let mut parsed = None;
    let mut numeric_delimiters = 0;
    for (separator, _) in text
        .char_indices()
        .filter(|(_, character)| *character == ':')
    {
        let after_path = &text[separator + 1..];
        let Some(number_end) = after_path.find(':') else {
            continue;
        };
        let number = &after_path[..number_end];
        if number.is_empty() || !number.bytes().all(|byte| byte.is_ascii_digit()) {
            continue;
        }
        numeric_delimiters += 1;
        if numeric_delimiters > 1 {
            return None;
        }
        let path = &text[..separator];
        let content = &after_path[number_end + 1..];
        if path.is_empty() || content.is_empty() {
            continue;
        }
        if let Some((drive, rest)) = path.split_once(':') {
            if drive.len() != 1
                || !drive.as_bytes()[0].is_ascii_alphabetic()
                || !(rest.starts_with('\\') || rest.starts_with('/'))
                || rest.contains(':')
            {
                continue;
            }
        }
        let Some(line_number) = number.parse::<u64>().ok().filter(|number| *number > 0) else {
            continue;
        };
        if parsed.is_some() {
            return None;
        }
        parsed = Some((path, line_number, content));
    }
    parsed
}

fn search_match_with_default<'a>(
    line: &'a [u8],
    default_path: Option<&'a str>,
) -> Option<(&'a str, u64, &'a str)> {
    if let Some(found) = search_match(line) {
        return Some(found);
    }
    let path = default_path?;
    let text = std::str::from_utf8(line)
        .ok()?
        .trim_end_matches(['\r', '\n']);
    if text.contains('\u{1b}') {
        return None;
    }
    let (number, content) = text.split_once(':')?;
    if path.is_empty()
        || number.is_empty()
        || content.is_empty()
        || !number.bytes().all(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    let line_number = number.parse::<u64>().ok().filter(|number| *number > 0)?;
    Some((path, line_number, content))
}

pub fn search_sections(
    capture: &EvidenceCapture,
    stdout: &[u8],
) -> Result<Vec<SearchSection>, &'static str> {
    search_sections_with_default_path(capture, stdout, None)
}

pub fn search_sections_with_default_path(
    capture: &EvidenceCapture,
    stdout: &[u8],
    default_path: Option<&str>,
) -> Result<Vec<SearchSection>, &'static str> {
    if !is_rg_capture(capture) || capture.exit_code != Some(0) {
        return Err("not_successful_rg_output");
    }
    let parsed = lines(stdout)
        .into_iter()
        .map(|line| search_match_with_default(line.bytes, default_path).map(|item| (line, item)))
        .collect::<Option<Vec<_>>>()
        .ok_or("unsupported_search_format")?;
    if parsed.is_empty() {
        return Err("no_search_candidates");
    }
    let mut sections = Vec::new();
    let mut start = 0;
    while start < parsed.len() {
        let path = parsed[start].1 .0;
        let first_line = parsed[start].1 .1;
        let mut end = start + 1;
        while end < parsed.len()
            && parsed[end].1 .0 == path
            && parsed[end].1 .1 == parsed[end - 1].1 .1 + 1
        {
            end += 1;
        }
        let byte_start = parsed[start].0.start;
        let byte_end = parsed[end - 1].0.end;
        sections.push(SearchSection {
            id: format!("section_{:02}_{byte_start}_{byte_end}", sections.len()),
            path: path.into(),
            first_line,
            last_line: parsed[end - 1].1 .1,
            byte_start: byte_start as u64,
            byte_end: byte_end as u64,
            text: std::str::from_utf8(&stdout[byte_start..byte_end])
                .map_err(|_| "unsupported_search_format")?
                .into(),
        });
        start = end;
    }
    Ok(sections)
}

fn search_defer_flags(
    lines: &[Line<'_>],
    relevance_text: &[String],
    default_path: Option<&str>,
) -> Option<Vec<bool>> {
    let matches = lines
        .iter()
        .map(|line| search_match_with_default(line.bytes, default_path))
        .collect::<Option<Vec<_>>>()?;
    let mut defer = vec![false; matches.len()];
    let mut start = 0;
    while start < matches.len() {
        let (path, _, content) = matches[start];
        let mut end = start + 1;
        while end < matches.len()
            && matches[end].0 == path
            && matches[end].2 == content
            && matches[end].1 > matches[end - 1].1
        {
            end += 1;
        }
        let path_lower = path.to_ascii_lowercase();
        let content_lower = content.to_ascii_lowercase();
        let trusted_relevance = relevance_text
            .iter()
            .any(|text| text.contains(&path_lower) || text.contains(&content_lower));
        if end - start >= 12
            && content.len() >= 32
            && fact_kind(content).is_none()
            && !trusted_relevance
        {
            defer[start + 2..end - 2].fill(true);
        }
        start = end;
    }
    Some(defer)
}

fn exact_block(stream: Stream, bytes: &[u8], start: usize, end: usize) -> ExactBlock {
    ExactBlock {
        stream,
        byte_start: start as u64,
        byte_end: end as u64,
        sha256: slice_hash(&bytes[start..end]),
        rendered_text: std::str::from_utf8(&bytes[start..end]).unwrap().into(),
    }
}

fn whole_blocks(stdout: &[u8], stderr: &[u8]) -> Vec<ExactBlock> {
    let mut blocks = Vec::new();
    if !stdout.is_empty() {
        blocks.push(exact_block(Stream::Stdout, stdout, 0, stdout.len()));
    }
    if !stderr.is_empty() {
        blocks.push(exact_block(Stream::Stderr, stderr, 0, stderr.len()));
    }
    blocks
}

fn push_contiguous(
    blocks: &mut Vec<ExactBlock>,
    stream: Stream,
    bytes: &[u8],
    start: usize,
    end: usize,
) {
    if let Some(previous) = blocks.last_mut() {
        if previous.stream == stream && previous.byte_end == start as u64 {
            previous.byte_end = end as u64;
            previous.sha256 = slice_hash(&bytes[previous.byte_start as usize..end]);
            previous
                .rendered_text
                .push_str(std::str::from_utf8(&bytes[start..end]).unwrap());
            return;
        }
    }
    blocks.push(exact_block(stream, bytes, start, end));
}

struct StreamPlan<'a> {
    segments: Vec<Line<'a>>,
    texts: Vec<&'a str>,
    reasons: Vec<Option<&'static str>>,
    is_diff: bool,
}

#[allow(clippy::too_many_arguments)]
fn stream_plan<'a>(
    capture: &EvidenceCapture,
    stream: &Stream,
    bytes: &'a [u8],
    relevance_text: &[String],
    tokens: &[String],
    mode: &Mode,
    preferred_success_test: Option<&str>,
    adaptive_selection: Option<&AdaptiveSelection>,
) -> Result<StreamPlan<'a>, &'static str> {
    let stream = stream.clone();
    let is_search = is_rg_capture(capture);
    let is_cargo = is_cargo_capture(capture);
    let segments = lines(bytes);
    let texts = segments
        .iter()
        .map(|line| std::str::from_utf8(line.bytes).unwrap())
        .collect::<Vec<_>>();
    let mut reasons: Vec<Option<&'static str>> = vec![None; segments.len()];
    let semantic_search = is_search
        && stream == Stream::Stdout
        && *mode == Mode::Adaptive
        && adaptive_selection.is_some();
    if is_search && stream == Stream::Stdout && !segments.is_empty() {
        if semantic_search {
            let selection = adaptive_selection.expect("checked above");
            let parsed = segments
                .iter()
                .map(|line| {
                    search_match_with_default(line.bytes, selection.default_search_path.as_deref())
                })
                .collect::<Option<Vec<_>>>();
            let Some(parsed) = parsed else {
                return Err("unsupported_search_format");
            };
            for (index, (line, (path, _, content))) in segments.iter().zip(parsed).enumerate() {
                let selected = selection
                    .stdout_ranges
                    .iter()
                    .any(|(start, end)| line.start as u64 >= *start && line.end as u64 <= *end);
                let path_lower = path.to_ascii_lowercase();
                let content_lower = content.to_ascii_lowercase();
                let trusted = relevance_text
                    .iter()
                    .any(|text| text.contains(&path_lower) || text.contains(&content_lower));
                // Matched source text is not a runtime fact (`Err(` or
                // `assert!` in code); definitions stay, as in deterministic mode.
                if !selected && !trusted && !reduce::is_definition(content) {
                    reasons[index] = Some("adaptive_search_nonselected");
                }
            }
        } else {
            match search_defer_flags(
                &segments,
                relevance_text,
                adaptive_selection.and_then(|selection| selection.default_search_path.as_deref()),
            ) {
                Some(flags) => {
                    for (reason, flag) in reasons.iter_mut().zip(flags) {
                        if flag {
                            *reason = Some("repeated_search_match_content");
                        }
                    }
                }
                None => {
                    return Err("unsupported_search_format");
                }
            }
        }
    }
    if is_cargo && capture.exit_code == Some(0) && stream == Stream::Stderr {
        let flags = cargo_progress_defer_flags(&segments, relevance_text);
        for (reason, flag) in reasons.iter_mut().zip(flags) {
            if flag {
                *reason = Some("successful_cargo_progress");
            }
        }
    }
    let is_diff = !is_search && texts.iter().any(|text| text.starts_with("diff --git a/"));
    reduce::plan(
        &reduce::PlanInput {
            lines: &texts,
            relevance_text,
            relevance_tokens: tokens,
            preferred_test: preferred_success_test,
            is_search,
            is_diff,
        },
        &mut reasons,
    );
    Ok(StreamPlan {
        segments,
        texts,
        reasons,
        is_diff,
    })
}

/// What kind of material an adaptive ranker is choosing between.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RankKind {
    /// Groups of adjacent search matches.
    Search,
    /// One section per changed file in a unified diff.
    Diff,
    /// Chunks of long output that deterministic selection would defer.
    Output,
}

impl RankKind {
    pub fn as_str(self) -> &'static str {
        match self {
            RankKind::Search => "search_results",
            RankKind::Diff => "diff_files",
            RankKind::Output => "command_output",
        }
    }
}

/// A byte-addressed candidate an adaptive ranker may keep in view.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RankSection {
    pub id: String,
    pub stream: Stream,
    pub byte_start: u64,
    pub byte_end: u64,
    pub label: String,
    pub text: String,
}

fn task_relevance(task: Option<&TaskFrame>) -> Vec<String> {
    task.map(|task| {
        std::iter::once(task.goal.as_str())
            .chain(task.trusted_constraints.iter().map(String::as_str))
            .map(str::to_ascii_lowercase)
            .collect::<Vec<_>>()
    })
    .unwrap_or_default()
}

/// Candidate sections for adaptive ranking. Each section is an exact byte
/// range; the ranker only decides which ranges stay visible. Protected facts
/// are enforced later by `make_pack_with_selection` regardless of the ranking.
pub fn rank_sections(
    capture: &EvidenceCapture,
    stdout: &[u8],
    stderr: &[u8],
    task: Option<&TaskFrame>,
    default_search_path: Option<&str>,
) -> Result<(RankKind, Vec<RankSection>), &'static str> {
    if std::str::from_utf8(stdout).is_err() || std::str::from_utf8(stderr).is_err() {
        return Err("binary_or_invalid_utf8");
    }
    if is_rg_capture(capture) {
        let sections = search_sections_with_default_path(capture, stdout, default_search_path)?;
        return Ok((
            RankKind::Search,
            sections
                .into_iter()
                .map(|section| RankSection {
                    label: format!(
                        "{} lines {}-{}",
                        section.path, section.first_line, section.last_line
                    ),
                    id: section.id,
                    stream: Stream::Stdout,
                    byte_start: section.byte_start,
                    byte_end: section.byte_end,
                    text: section.text,
                })
                .collect(),
        ));
    }
    let stdout_lines = lines(stdout);
    let stdout_texts = stdout_lines
        .iter()
        .map(|line| std::str::from_utf8(line.bytes).unwrap())
        .collect::<Vec<_>>();
    if stdout_texts
        .iter()
        .any(|text| text.starts_with("diff --git a/"))
    {
        let sections = diff_file_runs(&stdout_texts)
            .into_iter()
            .enumerate()
            .map(|(index, (start, end))| {
                let byte_start = stdout_lines[start].start;
                let byte_end = stdout_lines[end - 1].end;
                let path = stdout_texts[start]
                    .trim_end()
                    .strip_prefix("diff --git a/")
                    .and_then(|rest| rest.split_once(" b/"))
                    .map_or("unknown", |(path, _)| path);
                RankSection {
                    id: format!("s{index:02}"),
                    stream: Stream::Stdout,
                    byte_start: byte_start as u64,
                    byte_end: byte_end as u64,
                    label: format!("diff of {path} ({} lines)", end - start),
                    text: std::str::from_utf8(&stdout[byte_start..byte_end])
                        .unwrap()
                        .into(),
                }
            })
            .collect::<Vec<_>>();
        return if sections.is_empty() {
            Err("no_rankable_sections")
        } else {
            Ok((RankKind::Diff, sections))
        };
    }
    let relevance_text = task_relevance(task);
    let tokens = reduce::relevance_tokens(&relevance_text);
    let mut sections = Vec::new();
    for (stream, bytes) in [(Stream::Stdout, stdout), (Stream::Stderr, stderr)] {
        let plan = stream_plan(
            capture,
            &stream,
            bytes,
            &relevance_text,
            &tokens,
            &Mode::Deterministic,
            None,
            None,
        )?;
        for (start, end) in reduce::rankable_runs(&plan.reasons, &plan.texts) {
            let byte_start = plan.segments[start].start;
            let byte_end = plan.segments[end - 1].end;
            sections.push(RankSection {
                id: format!("s{:02}", sections.len()),
                stream: stream.clone(),
                byte_start: byte_start as u64,
                byte_end: byte_end as u64,
                label: format!(
                    "{} lines {}-{}",
                    match stream {
                        Stream::Stdout => "stdout",
                        Stream::Stderr => "stderr",
                    },
                    start + 1,
                    end
                ),
                text: std::str::from_utf8(&bytes[byte_start..byte_end])
                    .unwrap()
                    .into(),
            });
        }
    }
    if sections.is_empty() {
        Err("no_rankable_sections")
    } else {
        Ok((RankKind::Output, sections))
    }
}

pub fn make_pack(
    capture: &EvidenceCapture,
    stdout: &[u8],
    stderr: &[u8],
    task: Option<&TaskFrame>,
    mode: Mode,
    budget: usize,
) -> PackDecision {
    make_pack_with_selection(capture, stdout, stderr, task, mode, budget, None, None)
}

pub fn make_pack_with_hint(
    capture: &EvidenceCapture,
    stdout: &[u8],
    stderr: &[u8],
    task: Option<&TaskFrame>,
    mode: Mode,
    budget: usize,
    preferred_success_test: Option<&str>,
) -> PackDecision {
    make_pack_with_selection(
        capture,
        stdout,
        stderr,
        task,
        mode,
        budget,
        preferred_success_test,
        None,
    )
}

#[allow(clippy::too_many_arguments)]
pub fn make_pack_with_selection(
    capture: &EvidenceCapture,
    stdout: &[u8],
    stderr: &[u8],
    task: Option<&TaskFrame>,
    mode: Mode,
    budget: usize,
    preferred_success_test: Option<&str>,
    adaptive_selection: Option<&AdaptiveSelection>,
) -> PackDecision {
    if std::str::from_utf8(stdout).is_err()
        || std::str::from_utf8(stderr).is_err()
        || stdout.contains(&0)
        || stderr.contains(&0)
    {
        return PackDecision {
            pack: None,
            bypass_reason: Some("binary_or_invalid_utf8".into()),
        };
    }
    if capture.completeness == Completeness::Partial {
        return PackDecision {
            pack: None,
            bypass_reason: Some("partial_host_output".into()),
        };
    }
    let is_search = is_rg_capture(capture);
    if is_search && capture.exit_code != Some(0) {
        return PackDecision {
            pack: None,
            bypass_reason: Some("search_nonzero_status".into()),
        };
    }
    let revision = task.map_or(0, |task| task.revision);
    let mut id_hash = Sha256::new();
    id_hash.update(capture.raw_sha256.as_bytes());
    id_hash.update(revision.to_be_bytes());
    id_hash.update(format!("{mode:?}").as_bytes());
    hash_text(&mut id_hash, POLICY_VERSION);
    hash_text(&mut id_hash, preferred_success_test.unwrap_or(""));
    if let Some(selection) = adaptive_selection {
        for ranges in [&selection.stdout_ranges, &selection.stderr_ranges] {
            id_hash.update((ranges.len() as u64).to_be_bytes());
            for (start, end) in ranges {
                id_hash.update(start.to_be_bytes());
                id_hash.update(end.to_be_bytes());
            }
        }
        hash_text(
            &mut id_hash,
            selection.default_search_path.as_deref().unwrap_or(""),
        );
    } else {
        id_hash.update(0u64.to_be_bytes());
        hash_text(&mut id_hash, "");
    }
    id_hash.update((budget as u64).to_be_bytes());
    if let Some(task) = task {
        id_hash.update([1]);
        hash_text(&mut id_hash, &task.session_id);
        hash_text(&mut id_hash, &task.workspace_id);
        hash_text(&mut id_hash, &task.goal);
        id_hash.update((task.trusted_constraints.len() as u64).to_be_bytes());
        for constraint in &task.trusted_constraints {
            hash_text(&mut id_hash, constraint);
        }
        hash_text(&mut id_hash, task.repo_revision.as_deref().unwrap_or(""));
    } else {
        id_hash.update([0]);
    }
    let pack_id = hex::encode(id_hash.finalize());
    let mut protected = vec![ProtectedFact {
        kind: "process_status".into(),
        value: match capture.status {
            ProcessStatus::Completed => "completed",
            ProcessStatus::Interrupted => "interrupted",
            ProcessStatus::ToolError => "tool_error",
            ProcessStatus::Unknown => "unknown",
        }
        .into(),
        source: "process".into(),
        stream: None,
        byte_start: None,
        byte_end: None,
    }];
    if let Some(code) = capture.exit_code {
        protected.push(ProtectedFact {
            kind: "exit_code".into(),
            value: code.to_string(),
            source: "process".into(),
            stream: None,
            byte_start: None,
            byte_end: None,
        });
    }
    if let Some(task) = task {
        for constraint in &task.trusted_constraints {
            protected.push(ProtectedFact {
                kind: "user_constraint".into(),
                value: constraint.clone(),
                source: "trusted_task_frame".into(),
                stream: None,
                byte_start: None,
                byte_end: None,
            });
        }
    }
    let mut blocks = Vec::new();
    let mut omissions = Vec::new();
    let mut recall = Vec::new();
    let expires = capture.created_at + Duration::hours(24);
    let relevance_text = task_relevance(task);
    let tokens = reduce::relevance_tokens(&relevance_text);
    for (stream, bytes) in [(Stream::Stdout, stdout), (Stream::Stderr, stderr)] {
        let StreamPlan {
            segments,
            texts,
            mut reasons,
            is_diff,
        } = match stream_plan(
            capture,
            &stream,
            bytes,
            &relevance_text,
            &tokens,
            &mode,
            preferred_success_test,
            adaptive_selection,
        ) {
            Ok(plan) => plan,
            Err(reason) => {
                return PackDecision {
                    pack: None,
                    bypass_reason: Some(reason.into()),
                }
            }
        };
        if let (Some(selection), false, Mode::Adaptive) = (adaptive_selection, is_search, &mode) {
            let ranges = match stream {
                Stream::Stdout => &selection.stdout_ranges,
                Stream::Stderr => &selection.stderr_ranges,
            };
            let selected = |line: &Line<'_>| {
                ranges
                    .iter()
                    .any(|(start, end)| line.start as u64 >= *start && line.end as u64 <= *end)
            };
            if is_diff {
                for (start, end) in diff_file_runs(&texts) {
                    let chosen = segments[start..end].iter().any(&selected);
                    if chosen {
                        for reason in &mut reasons[start..end] {
                            if *reason == Some(reduce::GENERATED_DIFF) {
                                *reason = None;
                            }
                        }
                    } else if !ranges.is_empty() {
                        for reason in &mut reasons[start + 1..end] {
                            reason.get_or_insert(reduce::ADAPTIVE_NONSELECTED);
                        }
                    }
                }
            } else {
                for (index, line) in segments.iter().enumerate() {
                    if reasons[index] == Some(reduce::LONG_MIDDLE) && selected(line) {
                        reasons[index] = None;
                    }
                }
            }
        }
        let stream_name = match stream {
            Stream::Stdout => "stdout",
            Stream::Stderr => "stderr",
        };
        let mut close = |start: usize, end: usize, reason: &'static str, count: u64| {
            let section_id = format!("{stream_name}-{start}-{end}");
            omissions.push(Omission {
                section_id: section_id.clone(),
                stream: stream.clone(),
                byte_start: start as u64,
                byte_end: end as u64,
                factual_count: count,
                reason: reason.into(),
            });
            recall.push(RecallRef {
                schema_version: SCHEMA_VERSION,
                capture_id: capture.capture_id.clone(),
                raw_sha256: capture.raw_sha256.clone(),
                expires_at: expires,
                section_id: Some(section_id),
                stream: Some(stream.clone()),
                byte_start: Some(start as u64),
                byte_end: Some(end as u64),
            });
        };
        // Runner progress interleaves with passing tests (Go's `=== RUN`);
        // one gap covers both. Gaps cheaper than their marker stay visible.
        let group_key = |reason: &'static str| {
            if reason == reduce::TEST_PROGRESS {
                reduce::TESTS
            } else {
                reason
            }
        };
        let mut index = 0;
        while index < reasons.len() {
            let Some(reason) = reasons[index] else {
                index += 1;
                continue;
            };
            let key = group_key(reason);
            let mut end = index + 1;
            while end < reasons.len() && reasons[end].map(group_key) == Some(key) {
                end += 1;
            }
            let size = segments[end - 1].end - segments[index].start;
            if size < MIN_OMISSION_BYTES && !is_silent(key) {
                for reason in &mut reasons[index..end] {
                    *reason = None;
                }
            }
            index = end;
        }
        let mut deferred: Option<(usize, &'static str)> = None;
        let mut deferred_tests = 0u64;
        let mut deferred_lines = 0u64;
        let mut visible = Vec::new();
        let finish = |key: &'static str, tests: u64, lines: u64| {
            if key == reduce::TESTS && tests == 0 {
                (reduce::TEST_PROGRESS, lines)
            } else if key == reduce::TESTS {
                (reduce::TESTS, tests)
            } else {
                (key, lines)
            }
        };
        for (index, line) in segments.iter().enumerate() {
            if let Some(reason) = reasons[index] {
                let key = group_key(reason);
                match deferred {
                    Some((_, current)) if current == key => {}
                    Some((start, current)) => {
                        let (reason, count) = finish(current, deferred_tests, deferred_lines);
                        close(start, line.start, reason, count);
                        deferred = Some((line.start, key));
                        deferred_tests = 0;
                        deferred_lines = 0;
                    }
                    None => deferred = Some((line.start, key)),
                }
                deferred_lines += 1;
                deferred_tests += u64::from(reason == reduce::TESTS);
                continue;
            }
            if let Some((start, current)) = deferred.take() {
                let (reason, count) = finish(current, deferred_tests, deferred_lines);
                close(start, line.start, reason, count);
                deferred_tests = 0;
                deferred_lines = 0;
            }
            visible.push(index);
        }
        if let Some((start, current)) = deferred {
            let (reason, count) = finish(current, deferred_tests, deferred_lines);
            close(start, bytes.len(), reason, count);
        }
        for index in visible {
            let line = &segments[index];
            let text = texts[index];
            if !is_diff {
                if let Some(kind) = fact_kind(text) {
                    protected.push(ProtectedFact {
                        kind: kind.into(),
                        value: text.trim_end_matches(['\r', '\n']).into(),
                        source: "capture".into(),
                        stream: Some(stream.clone()),
                        byte_start: Some(line.start as u64),
                        byte_end: Some(line.end as u64),
                    });
                }
            }
            push_contiguous(&mut blocks, stream.clone(), bytes, line.start, line.end);
        }
    }
    if mode == Mode::Passthrough || omissions.is_empty() {
        return PackDecision {
            pack: None,
            bypass_reason: Some(
                if mode == Mode::Passthrough {
                    "passthrough_mode"
                } else {
                    "already_lean"
                }
                .into(),
            ),
        };
    }
    recall.push(RecallRef {
        schema_version: SCHEMA_VERSION,
        capture_id: capture.capture_id.clone(),
        raw_sha256: capture.raw_sha256.clone(),
        expires_at: expires,
        section_id: None,
        stream: None,
        byte_start: None,
        byte_end: None,
    });
    let mut pack = EvidencePack {
        schema_version: SCHEMA_VERSION,
        pack_id,
        capture_id: capture.capture_id.clone(),
        raw_sha256: capture.raw_sha256.clone(),
        task_revision: revision,
        mode,
        coverage: PackCoverage::Complete,
        protected_facts: protected,
        blocks,
        omissions,
        recall,
        extra: Default::default(),
    };
    let selected_len = render_pack_formatted(capture, &pack, "jevto", PackFormat::Compact).len();
    if selected_len > budget {
        pack.coverage = PackCoverage::Overflow;
        pack.blocks = whole_blocks(stdout, stderr);
        pack.omissions.clear();
        pack.recall
            .retain(|reference| reference.section_id.is_none());
        return PackDecision {
            pack: Some(pack),
            bypass_reason: Some("protected_or_retained_material_exceeds_budget".into()),
        };
    }
    if selected_len >= stdout.len() + stderr.len() {
        return PackDecision {
            pack: None,
            bypass_reason: Some("no_net_reduction".into()),
        };
    }
    PackDecision {
        pack: Some(pack),
        bypass_reason: None,
    }
}

pub fn render_pack(capture: &EvidenceCapture, pack: &EvidencePack) -> String {
    render_pack_with_recall_prefix(capture, pack, "jevto")
}

pub fn render_pack_formatted(
    capture: &EvidenceCapture,
    pack: &EvidencePack,
    recall_prefix: &str,
    format: PackFormat,
) -> String {
    match format {
        PackFormat::Compact => render_pack_compact(capture, pack, recall_prefix),
        PackFormat::Verbose => render_pack_with_recall_prefix(capture, pack, recall_prefix),
    }
}

fn stream_rank(stream: &Stream) -> u8 {
    match stream {
        Stream::Stdout => 0,
        Stream::Stderr => 1,
    }
}

fn short_reason(reason: &str) -> &'static str {
    match reason {
        reduce::TESTS => "passing tests",
        reduce::TEST_PROGRESS => "test-runner progress lines",
        reduce::IDENTICAL => "identical repeated lines",
        reduce::SIMILAR => "similar lines",
        reduce::PROGRESS => "progress redraw lines",
        reduce::GENERATED_DIFF => "generated/lockfile diff lines",
        reduce::LONG_MIDDLE => "lower-relevance lines",
        reduce::ADAPTIVE_NONSELECTED => "lines ranked less relevant by Jev",
        reduce::BOILERPLATE => "runner boilerplate lines",
        reduce::DUPLICATE => "duplicate lines",
        reduce::RUNTIME_FRAMES => "runtime-internal stack frames",
        reduce::SAME_EDIT => "lines repeating the same edit in more files",
        reduce::SEARCH_MORE => "more matches",
        "repeated_search_match_content" => "repeated search matches",
        "adaptive_search_nonselected" => "search lines ranked less relevant by Jev",
        "successful_cargo_progress" => "Cargo progress lines",
        _ => "lines",
    }
}

fn render_pack_compact(
    capture: &EvidenceCapture,
    pack: &EvidencePack,
    recall_prefix: &str,
) -> String {
    let exit = capture
        .exit_code
        .map_or_else(|| "unknown".into(), |value| value.to_string());
    let omitted: u64 = pack.omissions.iter().map(|item| item.factual_count).sum();
    let mut text = format!("jevto exit={exit} omitted={omitted}\n");
    for constraint in pack
        .protected_facts
        .iter()
        .filter(|fact| fact.kind == "user_constraint")
    {
        text.push_str(&format!("constraint: {}\n", constraint.value));
    }
    // Interleave kept blocks and gap markers in original order per stream.
    let mut items = pack
        .blocks
        .iter()
        .map(|block| {
            (
                stream_rank(&block.stream),
                block.byte_start,
                Some(block),
                None,
            )
        })
        .chain(pack.omissions.iter().map(|omission| {
            (
                stream_rank(&omission.stream),
                omission.byte_start,
                None,
                Some(omission),
            )
        }))
        .collect::<Vec<_>>();
    items.sort_by_key(|(rank, start, _, _)| (*rank, *start));
    for (_, _, block, omission) in items {
        if let Some(block) = block {
            text.push_str(&block.rendered_text);
            if !block.rendered_text.ends_with('\n') {
                text.push('\n');
            }
        } else if let Some(omission) = omission.filter(|omission| !is_silent(&omission.reason)) {
            text.push_str(&format!(
                "... {} {} hidden [{}]\n",
                omission.factual_count,
                short_reason(&omission.reason),
                omission.section_id
            ));
        }
    }
    text.push_str(&format!(
        "recall: `{} recall {}` [--section ID]\n",
        recall_prefix,
        short_capture_id(&capture.capture_id)
    ));
    text
}

/// Omissions deferred without an inline marker: runner chrome and exact
/// echoes of lines that stay visible. Both still count and stay recallable.
fn is_silent(reason: &str) -> bool {
    reason == reduce::BOILERPLATE || reason == reduce::DUPLICATE
}

/// The 8-character prefix `jevto recall` resolves back to the capture.
pub fn short_capture_id(capture_id: &str) -> &str {
    capture_id.get(..8).unwrap_or(capture_id)
}

pub fn render_pack_with_recall_prefix(
    capture: &EvidenceCapture,
    pack: &EvidencePack,
    recall_prefix: &str,
) -> String {
    let header = if capture.tool == "codex_post_tool_use" {
        format!(
            "host result: observed; child exit: unavailable; captured: exact hook-visible text; child output completeness: unverified; selection: {:?}",
            pack.coverage
        )
    } else {
        format!(
            "process: {}{}; capture: {:?}; selection: {:?}",
            match capture.exit_code {
                Some(0) => "SUCCEEDED",
                Some(_) => "FAILED",
                None => "UNKNOWN",
            },
            capture
                .exit_code
                .map_or(String::new(), |code| format!(" (exit {code})")),
            capture.completeness,
            pack.coverage
        )
    };
    let mut text = format!(
        "jevto: {}\n{}\ncapture_id: {}\n",
        capture.command_display.as_deref().unwrap_or(&capture.tool),
        header,
        capture.capture_id
    );
    let constraints = pack
        .protected_facts
        .iter()
        .filter(|fact| fact.kind == "user_constraint");
    for constraint in constraints {
        text.push_str(&format!("trusted task constraint: {}\n", constraint.value));
    }
    let deferred_ok = pack
        .omissions
        .iter()
        .filter(|omission| omission.reason == "successful_test_inventory")
        .map(|omission| omission.factual_count)
        .sum::<u64>();
    if deferred_ok > 0 {
        let shown_ok = pack
            .blocks
            .iter()
            .flat_map(|block| block.rendered_text.lines())
            .filter(|line| passing_test_name(line).is_some())
            .count() as u64;
        text.push_str(&format!(
            "test inventory: {} explicit ok results ({shown_ok} shown, {deferred_ok} deferred); see process status above\n",
            shown_ok.saturating_add(deferred_ok)
        ));
    }
    for block in &pack.blocks {
        text.push_str(&format!(
            "\n[{:?} bytes {}..{}]\n",
            block.stream, block.byte_start, block.byte_end
        ));
        text.push_str(&block.rendered_text);
        if !block.rendered_text.ends_with('\n') {
            text.push('\n');
        }
    }
    if !pack.omissions.is_empty() {
        text.push_str("\nDeferred exact historical sections:\n");
        for omission in &pack.omissions {
            let description = match omission.reason.as_str() {
                "repeated_search_match_content" => "repeated search match lines",
                "adaptive_search_nonselected" => "search lines ranked below retained evidence",
                "successful_cargo_progress" => "successful Cargo progress lines",
                reduce::TESTS => "lines of successful test inventory",
                other => short_reason(other),
            };
            text.push_str(&format!(
                "- {}: {} {}; recall with `{} recall {} --section {}`\n",
                omission.section_id,
                omission.factual_count,
                description,
                recall_prefix,
                capture.capture_id,
                omission.section_id
            ));
        }
    }
    text.push_str(&format!(
        "Full historical recall: `{} recall {} --full`\n",
        recall_prefix, capture.capture_id
    ));
    text
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;
    use serde_json::json;

    fn fixture(stdout: &[u8], stderr: &[u8], code: i32) -> EvidenceCapture {
        EvidenceCapture {
            schema_version: 1,
            capture_id: uuid::Uuid::new_v4().to_string(),
            session_id: "s".into(),
            workspace_id: "w".into(),
            created_at: Utc::now(),
            tool: "run".into(),
            command_display: Some("cargo [arguments redacted]".into()),
            command_hash: None,
            raw_sha256: crate::canonical_hash(stdout, stderr),
            stdout_bytes: stdout.len() as u64,
            stderr_bytes: stderr.len() as u64,
            completeness: Completeness::Complete,
            status: ProcessStatus::Completed,
            exit_code: Some(code),
            repo_revision: None,
            extra: Default::default(),
        }
    }

    #[test]
    fn failed_test_and_quiet_warning_survive_success_inventory_reduction() {
        let mut out = b"warning: cache invalidated early\n".to_vec();
        for n in 0..24 {
            out.extend_from_slice(format!("test suite::case_{n} ... ok\n").as_bytes());
        }
        out.extend_from_slice(b"test suite::critical ... FAILED\nassertion failed: expected 4 actual: 3\ntest result: FAILED\n");
        let capture = fixture(&out, b"error: verifier failed\n", 101);
        let pack = make_pack(
            &capture,
            &out,
            b"error: verifier failed\n",
            None,
            Mode::Deterministic,
            8192,
        )
        .pack
        .unwrap();
        let display = render_pack(&capture, &pack);
        assert!(display.contains("FAILED (exit 101)"));
        assert!(display.contains("warning: cache invalidated early"));
        assert!(display.contains("suite::critical ... FAILED"));
        assert!(display.contains("expected 4 actual: 3"));
        assert!(display.contains(
            "test inventory: 24 explicit ok results (0 shown, 24 deferred); see process status above"
        ));
        assert_eq!(
            pack.omissions.iter().map(|o| o.factual_count).sum::<u64>(),
            24
        );
        assert!(pack
            .protected_facts
            .iter()
            .any(|f| f.kind == "distinct_warning"));
    }

    #[test]
    fn successful_cargo_progress_defers_only_known_lines_with_exact_ranges() {
        let mut err = Vec::new();
        for n in 0..24 {
            let action = if n % 2 == 0 {
                "    Checking"
            } else {
                "   Compiling"
            };
            err.extend_from_slice(
                format!("{action} probe_crate_{n:02} v0.1.0 (crates/probe_crate_{n:02})\n")
                    .as_bytes(),
            );
        }
        err.extend_from_slice(b"warning: keep the quiet diagnostic\n");
        err.extend_from_slice(
            b"    Finished `dev` profile [unoptimized + debuginfo] target(s) in 2.44s\n",
        );
        let capture = fixture(b"", &err, 0);
        let pack = make_pack(&capture, b"", &err, None, Mode::Deterministic, 8192)
            .pack
            .unwrap();
        let display = render_pack(&capture, &pack);
        assert!(display.contains("probe_crate_00"));
        assert!(display.contains("probe_crate_01"));
        assert!(display.contains("probe_crate_22"));
        assert!(display.contains("probe_crate_23"));
        assert!(display.contains("warning: keep the quiet diagnostic"));
        // Cargo's `Finished` line is silent runner chrome.
        assert!(!display.contains("Finished `dev` profile"));
        assert!(!display.contains("probe_crate_12"));
        assert_eq!(pack.omissions.len(), 2);
        assert_eq!(pack.omissions[1].reason, reduce::BOILERPLATE);
        let omission = &pack.omissions[0];
        assert_eq!(omission.reason, "successful_cargo_progress");
        assert_eq!(omission.factual_count, 20);
        assert!(std::str::from_utf8(
            &err[omission.byte_start as usize..omission.byte_end as usize]
        )
        .unwrap()
        .contains("probe_crate_12"));

        let relevant_task: TaskFrame = serde_json::from_value(json!({
            "schema_version": 1, "session_id": "s", "workspace_id": "w", "revision": 0,
            "goal": "Fix probe_crate_17", "trusted_constraints": []
        }))
        .unwrap();
        let relevant = make_pack(
            &capture,
            b"",
            &err,
            Some(&relevant_task),
            Mode::Deterministic,
            8192,
        )
        .pack
        .unwrap();
        assert!(render_pack(&capture, &relevant).contains("probe_crate_17"));
    }

    #[test]
    fn failed_or_unidentified_cargo_progress_uses_only_generic_similar_rule() {
        let err = (0..24)
            .map(|n| format!("    Checking probe_crate_{n:02} v0.1.0\n"))
            .collect::<String>()
            .into_bytes();
        for (code, program) in [(101, "cargo"), (0, "python")] {
            let mut capture = fixture(b"", &err, code);
            capture.command_display = Some(format!("{program} [0 arguments redacted]"));
            let pack = make_pack(&capture, b"", &err, None, Mode::Deterministic, 8192)
                .pack
                .unwrap();
            assert!(pack
                .omissions
                .iter()
                .all(|omission| omission.reason == reduce::SIMILAR));
            let view = render_pack_formatted(&capture, &pack, "jevto", PackFormat::Compact);
            assert!(view.contains(&format!("jevto exit={code}")));
            assert!(view.contains("probe_crate_00"));
            assert!(view.contains("probe_crate_23"));
            assert!(view.contains("21 similar lines hidden"));
        }
    }

    #[test]
    fn warning_like_test_names_defer_but_malformed_inventory_lines_stay_visible() {
        let mut out = Vec::new();
        for n in 0..80 {
            if n == 40 {
                out.extend_from_slice(b"test suite::warning_case ... ok\n");
            } else if n == 41 {
                out.extend_from_slice(b"test warning: migration must be reviewed ... ok\n");
            } else {
                out.extend_from_slice(format!("test suite::case_{n} ... ok\n").as_bytes());
            }
        }
        let capture = fixture(&out, b"", 0);
        let pack = make_pack(&capture, &out, b"", None, Mode::Deterministic, 8192)
            .pack
            .unwrap();
        let display = render_pack(&capture, &pack);
        assert!(!display.contains("suite::warning_case ... ok"));
        assert!(display.contains("warning: migration must be reviewed"));
        assert!(!display.contains("suite::case_39 ... ok"));
        assert!(pack.protected_facts.iter().any(|fact| {
            fact.kind == "distinct_warning" && fact.value.contains("migration must be reviewed")
        }));
    }

    #[test]
    fn adaptive_hint_promotes_only_a_passing_test_and_keeps_protected_facts() {
        let mut out = b"warning: early signal\n".to_vec();
        for n in 0..80 {
            out.extend_from_slice(format!("test suite::case_{n} ... ok\n").as_bytes());
        }
        out.extend_from_slice(
            b"test suite::critical ... FAILED\nassertion failed: expected 4 actual: 3\n",
        );
        let capture = fixture(&out, b"", 101);
        let pack = make_pack_with_hint(
            &capture,
            &out,
            b"",
            None,
            Mode::Adaptive,
            8192,
            Some("suite::case_17"),
        )
        .pack
        .unwrap();
        let display = render_pack(&capture, &pack);
        assert!(display.contains("suite::case_17 ... ok"));
        assert!(display.contains("warning: early signal"));
        assert!(display.contains("suite::critical ... FAILED"));
        assert!(display.contains("expected 4 actual: 3"));
        assert!(!display.contains("suite::case_16 ... ok"));
    }

    #[test]
    fn lean_result_stays_complete_and_binary_bypasses() {
        let data = b"test result: ok\nwarning: output matters\n";
        let capture = fixture(data, b"", 0);
        let decision = make_pack(&capture, data, b"", None, Mode::Deterministic, 1024);
        assert_eq!(decision.bypass_reason.as_deref(), Some("already_lean"));
        assert_eq!(
            make_pack(&capture, b"\xff", b"", None, Mode::Deterministic, 1024)
                .bypass_reason
                .as_deref(),
            Some("binary_or_invalid_utf8")
        );
    }

    #[test]
    fn protected_overflow_delivers_original() {
        let mut data = b"error: important\n".to_vec();
        for n in 0..24 {
            data.extend_from_slice(format!("test suite::{n} ... ok\n").as_bytes());
        }
        let capture = fixture(&data, b"", 1);
        let pack = make_pack(&capture, &data, b"", None, Mode::Deterministic, 1)
            .pack
            .unwrap();
        assert_eq!(pack.coverage, PackCoverage::Overflow);
        assert!(pack.omissions.is_empty());
    }

    #[test]
    fn trusted_task_content_is_visible_and_binds_pack_identity() {
        let out = (0..80)
            .map(|n| format!("test suite::case_{n} ... ok\n"))
            .collect::<String>()
            .into_bytes();
        let capture = fixture(&out, b"", 0);
        let task = |constraint: &str| -> TaskFrame {
            serde_json::from_value(json!({
                "schema_version": 1,
                "session_id": "s",
                "workspace_id": "w",
                "revision": 0,
                "goal": "Fix parser behavior",
                "trusted_constraints": [constraint]
            }))
            .unwrap()
        };
        let first = make_pack(
            &capture,
            &out,
            b"",
            Some(&task("Keep suite::case_17 visible")),
            Mode::Deterministic,
            8192,
        )
        .pack
        .unwrap();
        let second = make_pack(
            &capture,
            &out,
            b"",
            Some(&task("Keep suite::case_18 visible")),
            Mode::Deterministic,
            8192,
        )
        .pack
        .unwrap();
        assert_ne!(first.pack_id, second.pack_id);
        assert_eq!(first.task_revision, second.task_revision);
        let smaller_budget = make_pack(
            &capture,
            &out,
            b"",
            Some(&task("Keep suite::case_17 visible")),
            Mode::Deterministic,
            1,
        )
        .pack
        .unwrap();
        assert_ne!(first.pack_id, smaller_budget.pack_id);
        assert_eq!(smaller_budget.coverage, PackCoverage::Overflow);
        let first_view = render_pack(&capture, &first);
        assert!(first_view.contains("trusted task constraint: Keep suite::case_17 visible"));
        assert!(first_view.contains("test suite::case_17 ... ok"));
        assert!(!first_view.contains("test suite::case_18 ... ok"));
    }

    #[test]
    fn trusted_constraint_over_budget_forces_overflow() {
        let out = (0..80)
            .map(|n| format!("test suite::case_{n} ... ok\n"))
            .collect::<String>()
            .into_bytes();
        let capture = fixture(&out, b"", 0);
        let task: TaskFrame = serde_json::from_value(json!({
            "schema_version": 1,
            "session_id": "s",
            "workspace_id": "w",
            "revision": 0,
            "goal": "Fix parser behavior",
            "trusted_constraints": ["X".repeat(2000)]
        }))
        .unwrap();
        let pack = make_pack(&capture, &out, b"", Some(&task), Mode::Deterministic, 1024)
            .pack
            .unwrap();
        assert_eq!(pack.coverage, PackCoverage::Overflow);
        assert!(pack.omissions.is_empty());
    }

    #[test]
    fn repeated_exact_search_matches_defer_only_middle_locations() {
        let content = "let needle = cache.lookup(user_id).unwrap_or_default();";
        let out = (1..=80)
            .map(|line| format!("src/cache.rs:{line}:{content}\r\n"))
            .collect::<String>()
            .into_bytes();
        let mut capture = fixture(&out, b"warning: external note\n", 0);
        capture.command_display = Some("rg.EXE [4 arguments redacted]".into());
        let pack = make_pack(
            &capture,
            &out,
            b"warning: external note\n",
            None,
            Mode::Deterministic,
            8192,
        )
        .pack
        .unwrap();
        let view = render_pack(&capture, &pack);
        assert!(view.contains("src/cache.rs:1:"));
        assert!(view.contains("src/cache.rs:80:"));
        assert!(view.contains("warning: external note"));
        assert!(!view.contains("src/cache.rs:40:"));
        assert_eq!(pack.omissions.len(), 1);
        assert_eq!(pack.omissions[0].reason, "repeated_search_match_content");
        assert_eq!(pack.omissions[0].factual_count, 76);
        let omitted =
            &out[pack.omissions[0].byte_start as usize..pack.omissions[0].byte_end as usize];
        assert!(std::str::from_utf8(omitted)
            .unwrap()
            .contains("src/cache.rs:40:"));
        assert!(pack.blocks.iter().all(|block| {
            let source = if block.stream == Stream::Stdout {
                &out[..]
            } else {
                &b"warning: external note\n"[..]
            };
            block.sha256 == slice_hash(&source[block.byte_start as usize..block.byte_end as usize])
        }));
    }

    #[test]
    fn search_parser_bypasses_ambiguous_or_failed_results() {
        assert_eq!(
            search_match(b"C:\\repo\\src\\file.rs:12:needle here\n"),
            Some((r"C:\repo\src\file.rs", 12, "needle here"))
        );
        assert!(search_match(b"file:12:value:34:rest\n").is_none());
        let content = "warning: preserve this repeated matching content";
        let out = (1..=80)
            .map(|line| format!("src/cache.rs:{line}:{content}\n"))
            .collect::<String>()
            .into_bytes();
        let mut capture = fixture(&out, b"", 0);
        capture.command_display = Some("rg [4 arguments redacted]".into());
        // Matched source text is not a runtime fact: many matches keep a head.
        let pack = make_pack(&capture, &out, b"", None, Mode::Deterministic, 8192)
            .pack
            .unwrap();
        let view = render_pack(&capture, &pack);
        assert!(view.contains("src/cache.rs:12:"));
        assert!(!view.contains("src/cache.rs:13:"));
        assert!(pack
            .omissions
            .iter()
            .all(|omission| omission.reason == reduce::SEARCH_MORE));
        capture.exit_code = Some(2);
        assert_eq!(
            make_pack(&capture, &out, b"", None, Mode::Deterministic, 8192)
                .bypass_reason
                .as_deref(),
            Some("search_nonzero_status")
        );
        capture.exit_code = Some(0);
        let malformed = b"not a line-numbered search result\n";
        assert_eq!(
            make_pack(&capture, malformed, b"", None, Mode::Deterministic, 8192)
                .bypass_reason
                .as_deref(),
            Some("unsupported_search_format")
        );
    }

    #[test]
    fn search_sections_group_only_adjacent_lines_from_the_same_path() {
        let out = b"src/a.rs:10:first\nsrc/a.rs:11:second\nsrc/a.rs:20:third\nsrc/b.rs:1:fourth\n";
        let mut capture = fixture(out, b"", 0);
        capture.command_display = Some("rg [4 arguments redacted]".into());
        let sections = search_sections(&capture, out).unwrap();
        assert_eq!(sections.len(), 3);
        assert_eq!((sections[0].first_line, sections[0].last_line), (10, 11));
        assert_eq!(sections[0].text, "src/a.rs:10:first\nsrc/a.rs:11:second\n");
        assert_eq!((sections[1].first_line, sections[1].last_line), (20, 20));
        assert_eq!(sections[2].path, "src/b.rs");
        for section in sections {
            assert_eq!(
                section.text.as_bytes(),
                &out[section.byte_start as usize..section.byte_end as usize]
            );
        }
    }

    #[test]
    fn search_sections_accept_single_file_grep_lines_with_a_trusted_default_path() {
        let out = b"7:FIXME_JEVTO retry floor\n8:FIXME_JEVTO preserve warning\n";
        let mut capture = fixture(out, b"", 0);
        capture.command_display = Some("rg [arguments redacted]".into());
        let sections = search_sections_with_default_path(&capture, out, Some("src/retry.py"))
            .expect("single-file Grep output should parse");
        assert_eq!(sections.len(), 1);
        assert_eq!(sections[0].path, "src/retry.py");
        assert_eq!((sections[0].first_line, sections[0].last_line), (7, 8));
        assert_eq!(
            &out[sections[0].byte_start as usize..sections[0].byte_end as usize],
            out
        );
    }

    #[test]
    fn adaptive_search_retains_ranked_ranges_and_protected_warning() {
        let payload =
            "implementation detail with enough context to make selection materially smaller";
        let out = (0..12)
            .map(|index| format!("src/area_{index}.rs:{}:{payload} {index}\n", index + 1))
            .collect::<String>()
            .into_bytes();
        let warning = b"warning: local verifier note\n";
        let mut capture = fixture(&out, warning, 0);
        capture.command_display = Some("rg [4 arguments redacted]".into());
        let sections = search_sections(&capture, &out).unwrap();
        let selection = AdaptiveSelection {
            stdout_ranges: sections[4..12]
                .iter()
                .map(|section| (section.byte_start, section.byte_end))
                .collect(),
            stderr_ranges: Vec::new(),
            default_search_path: None,
        };
        let pack = make_pack_with_selection(
            &capture,
            &out,
            warning,
            None,
            Mode::Adaptive,
            8192,
            None,
            Some(&selection),
        )
        .pack
        .unwrap();
        let compact = render_pack_formatted(&capture, &pack, "jevto", PackFormat::Compact);
        assert!(!compact.contains("src/area_0.rs"));
        assert!(compact.contains("src/area_4.rs"));
        assert!(compact.contains("src/area_11.rs"));
        assert!(compact.contains("warning: local verifier note"));
        assert_eq!(
            pack.omissions
                .iter()
                .map(|item| item.factual_count)
                .sum::<u64>(),
            4
        );
        assert!(pack
            .omissions
            .iter()
            .all(|item| item.reason == "adaptive_search_nonselected"));
        assert!(compact.len() < out.len() + warning.len());
    }

    #[test]
    fn compact_format_keeps_fixed_overhead_below_256_bytes() {
        let out = (0..80)
            .map(|index| format!("test suite::case_{index} ... ok\n"))
            .collect::<String>()
            .into_bytes();
        let capture = fixture(&out, b"", 0);
        let pack = make_pack(&capture, &out, b"", None, Mode::Deterministic, 8192)
            .pack
            .unwrap();
        let compact = render_pack_formatted(&capture, &pack, "jevto", PackFormat::Compact);
        let retained = pack
            .blocks
            .iter()
            .map(|block| block.rendered_text.len())
            .sum::<usize>();
        assert!(
            compact.len() - retained < 256,
            "{} bytes",
            compact.len() - retained
        );
        assert!(compact.contains("80 passing tests hidden"));
        assert!(compact.contains("recall: `jevto recall"));
    }

    #[test]
    fn adaptive_selection_promotes_ranked_long_output_chunks_only() {
        let words = ["alpha", "beta", "gamma", "delta", "omega", "sigma", "kappa"];
        let mut out = String::new();
        for n in 0..500 {
            if n == 260 {
                out.push_str("importer skipped row 260 because column tz was blank\n");
                continue;
            }
            out.push_str(&format!(
                "{} {} {}: imported row {n}\n",
                words[n % 7],
                words[(n / 7) % 7],
                words[(n / 49) % 7]
            ));
        }
        let bytes = out.into_bytes();
        let mut capture = fixture(&bytes, b"", 0);
        capture.command_display = Some("python [1 arguments redacted]".into());
        let (kind, sections) = rank_sections(&capture, &bytes, b"", None, None).unwrap();
        assert_eq!(kind, RankKind::Output);
        let chosen = sections
            .iter()
            .find(|section| section.text.contains("column tz was blank"))
            .unwrap();
        let selection = AdaptiveSelection {
            stdout_ranges: vec![(chosen.byte_start, chosen.byte_end)],
            stderr_ranges: Vec::new(),
            default_search_path: None,
        };
        let deterministic = make_pack(&capture, &bytes, b"", None, Mode::Deterministic, 65536)
            .pack
            .unwrap();
        let adaptive = make_pack_with_selection(
            &capture,
            &bytes,
            b"",
            None,
            Mode::Adaptive,
            65536,
            None,
            Some(&selection),
        )
        .pack
        .unwrap();
        let deterministic_view = render_pack(&capture, &deterministic);
        let adaptive_view =
            render_pack_formatted(&capture, &adaptive, "jevto", PackFormat::Compact);
        assert!(!deterministic_view.contains("column tz was blank"));
        assert!(adaptive_view.contains("column tz was blank"));
        assert!(adaptive_view.contains("imported row 0\n"));
        assert!(adaptive_view.contains("imported row 499\n"));
        assert!(adaptive_view.len() * 3 < bytes.len());
    }

    #[test]
    fn adaptive_diff_selection_keeps_headers_and_chosen_file() {
        let mut diff = String::new();
        for name in ["src/a.rs", "src/b.rs", "src/c.rs"] {
            diff.push_str(&format!(
                "diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n@@ -1,3 +1,3 @@\n"
            ));
            for n in 0..20 {
                diff.push_str(&format!("+let value_{n} = compute_{n}(\"{name}\");\n"));
            }
        }
        let bytes = diff.into_bytes();
        let mut capture = fixture(&bytes, b"", 0);
        capture.command_display = Some("git [1 arguments redacted]".into());
        let (kind, sections) = rank_sections(&capture, &bytes, b"", None, None).unwrap();
        assert_eq!(kind, RankKind::Diff);
        assert_eq!(sections.len(), 3);
        let selection = AdaptiveSelection {
            stdout_ranges: vec![(sections[1].byte_start, sections[1].byte_end)],
            stderr_ranges: Vec::new(),
            default_search_path: None,
        };
        let pack = make_pack_with_selection(
            &capture,
            &bytes,
            b"",
            None,
            Mode::Adaptive,
            65536,
            None,
            Some(&selection),
        )
        .pack
        .unwrap();
        let view = render_pack_formatted(&capture, &pack, "jevto", PackFormat::Compact);
        assert!(view.contains("diff --git a/src/a.rs b/src/a.rs"));
        assert!(view.contains("diff --git a/src/c.rs b/src/c.rs"));
        assert!(view.contains("compute_5(\"src/b.rs\")"));
        assert!(!view.contains("compute_5(\"src/a.rs\")"));
        assert!(!view.contains("compute_5(\"src/c.rs\")"));
        assert!(pack
            .omissions
            .iter()
            .all(|omission| omission.reason == reduce::ADAPTIVE_NONSELECTED));
    }
}
