//! Line-level reduction plan.
//!
//! A plan marks exact lines that may be deferred behind recall and gives each
//! deferral a reason. Every rule here is conservative: a deferred line is always
//! recallable byte-for-byte, protected facts stay visible unless an identical
//! line is visible next to them, and lines that the trusted task mentions are
//! never deferred. Recognizers are content-driven so they work for any command
//! that prints the same shape.

pub(crate) const TESTS: &str = "successful_test_inventory";
pub(crate) const TEST_PROGRESS: &str = "test_progress";
pub(crate) const IDENTICAL: &str = "repeated_identical_lines";
pub(crate) const SIMILAR: &str = "similar_lines";
pub(crate) const PROGRESS: &str = "terminal_progress";
pub(crate) const GENERATED_DIFF: &str = "generated_file_diff";
pub(crate) const LONG_MIDDLE: &str = "long_output_middle";
pub(crate) const ADAPTIVE_NONSELECTED: &str = "adaptive_nonselected";
/// Runner chrome with no task facts. Deferred silently (no inline marker) but
/// still recallable and counted.
pub(crate) const BOILERPLATE: &str = "runner_boilerplate";
/// Exact echoes of a line that stays visible (a failure recap repeating the
/// failing test's name). Deferred silently, like boilerplate.
pub(crate) const DUPLICATE: &str = "duplicate_of_visible_line";
/// Stack frames inside the language runtime or third-party packages.
pub(crate) const RUNTIME_FRAMES: &str = "runtime_internal_frames";
/// Diff bodies of files that repeat another visible file's exact edit.
pub(crate) const SAME_EDIT: &str = "same_edit_in_more_files";
/// Search matches outside the kept head, definitions, and goal matches.
pub(crate) const SEARCH_MORE: &str = "search_matches_beyond_head";

/// Passing-inventory lines are deferred once there are more than this many.
const MIN_INVENTORY: usize = 2;
const SEARCH_MIN_LINES: usize = 40;
const SEARCH_MIN_BYTES: usize = 4 * 1024;
const SEARCH_HEAD: usize = 12;
const MIN_SAME_EDIT_FILES: usize = 3;
const MIN_IDENTICAL_RUN: usize = 3;
const MIN_SIMILAR_RUN: usize = 8;
const LONG_MIN_BYTES: usize = 12 * 1024;
const LONG_MIN_LINES: usize = 150;
const LONG_HEAD: usize = 30;
const LONG_TAIL: usize = 50;
const FAILURE_CONTEXT_AFTER: usize = 8;
const FAILURE_CONTEXT_BEFORE: usize = 2;
/// Upper bound on lines in one adaptive candidate section.
pub(crate) const CHUNK_LINES: usize = 30;

const GENERATED_FILES: &[&str] = &[
    "cargo.lock",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lock",
    "bun.lockb",
    "poetry.lock",
    "pipfile.lock",
    "uv.lock",
    "go.sum",
    "composer.lock",
    "gemfile.lock",
    "flake.lock",
    "packages.lock.json",
];

const STOPWORDS: &[&str] = &[
    "about", "after", "again", "also", "because", "before", "being", "between", "build", "check",
    "code", "could", "does", "each", "file", "files", "find", "first", "from", "have", "into",
    "make", "more", "need", "only", "other", "please", "should", "some", "sure", "than", "that",
    "their", "them", "then", "there", "these", "they", "this", "those", "through", "under",
    "until", "using", "what", "when", "where", "which", "while", "will", "with", "without",
    "would", "your", "fails", "failing", "failure", "error", "errors", "test", "tests", "warning",
    "change", "changes", "update", "work", "works", "working", "look", "issue", "problem", "run",
    "running", "output", "result", "results", "just", "like", "want", "fail", "failed", "around",
];

/// Classify a line as a protected fact. Protected lines are never deferred
/// unless an identical copy stays visible.
pub(crate) fn fact_kind(text: &str) -> Option<&'static str> {
    let lower = text.to_ascii_lowercase();
    if lower.contains("warning") || lower.starts_with("warn:") {
        Some("distinct_warning")
    } else if lower.contains("assert") || lower.contains("expected") || lower.contains("actual:") {
        Some("assertion")
    } else if lower.contains("failed")
        || lower.contains("panic")
        || lower.contains("error")
        || lower.contains("exception")
        || lower.contains("traceback")
        || lower.contains("fatal")
    {
        Some("failure_identity")
    } else if lower.contains("-->") {
        Some("diagnostic_location")
    } else {
        None
    }
}

fn trimmed(text: &str) -> &str {
    text.trim_end_matches(['\r', '\n'])
}

fn clean_name(name: &str) -> bool {
    !name.is_empty() && !name.chars().any(char::is_control)
}

/// Name of a passing test if the line is an exact passing-test inventory line
/// in a recognized framework format.
pub fn passing_test_name(text: &str) -> Option<&str> {
    let line = trimmed(text);
    // Rust libtest: `test NAME ... ok`
    if let Some((name, status)) = line
        .strip_prefix("test ")
        .and_then(|rest| rest.split_once(" ... "))
    {
        if status == "ok" && clean_name(name) && !name.chars().any(char::is_whitespace) {
            return Some(name);
        }
        return None;
    }
    // Python unittest -v: `NAME (dotted.path) ... ok`
    if let Some(head) = line.strip_suffix(" ... ok") {
        if let Some((name, rest)) = head.split_once(" (") {
            if rest.ends_with(')')
                && clean_name(name)
                && !name.chars().any(char::is_whitespace)
                && !rest.chars().any(char::is_whitespace)
            {
                return Some(name);
            }
        }
        return None;
    }
    let start = line.trim_start();
    // Go test -v: `--- PASS: NAME (0.00s)`
    if let Some(rest) = start.strip_prefix("--- PASS: ") {
        let (name, timing) = rest.rsplit_once(" (")?;
        if timing.ends_with("s)") && clean_name(name) {
            return Some(name);
        }
        return None;
    }
    // pytest -v: `path::name PASSED [ 10%]`
    if let Some((node, status)) = start.split_once(" PASSED") {
        let status = status.trim();
        if node.contains("::")
            && !node.contains(' ')
            && (status.is_empty() || (status.starts_with('[') && status.ends_with("%]")))
        {
            return node.rsplit_once("::").map(|(_, name)| name);
        }
        return None;
    }
    // node:test spec reporter `✔ name (1.2ms)`, jest/vitest `✓ name (3 ms)` or `√ name`.
    for marker in ["✔ ", "✓ ", "√ "] {
        if let Some(rest) = start.strip_prefix(marker) {
            let name = match rest.rsplit_once(" (") {
                Some((name, timing)) if timing.ends_with("ms)") || timing.ends_with("s)") => name,
                _ => rest,
            };
            return clean_name(name).then_some(name);
        }
    }
    // TAP: `ok 12 - name`
    if let Some(rest) = start.strip_prefix("ok ") {
        let (number, name) = rest.split_once(" - ")?;
        if !number.is_empty() && number.bytes().all(|byte| byte.is_ascii_digit()) {
            let upper = name.to_ascii_uppercase();
            if upper.contains("# SKIP") || upper.contains("# TODO") {
                return None;
            }
            if clean_name(name) {
                return Some(name);
            }
        }
    }
    None
}

/// Test-runner and build chrome that carries no pass/fail or diagnostic fact.
pub(crate) fn is_boilerplate(text: &str) -> bool {
    let line = trimmed(text);
    let start = line.trim_start();
    if start.starts_with("Running unittests ")
        || start.starts_with("Running tests/")
        || start.starts_with("Running tests\\")
        || start.starts_with("Running benches/")
        || start.starts_with("Doc-tests ")
        || start.starts_with("note: run with `RUST_BACKTRACE=1`")
        || (start.starts_with("Finished `") && start.contains("target(s) in "))
        || start.starts_with("Blocking waiting for file lock")
        || start.starts_with("rootdir: ")
        || start.starts_with("cachedir: ")
        || start.starts_with("configfile: ")
        || start.starts_with("plugins: ")
        || (start.starts_with("platform ") && start.contains(" -- Python "))
        || start == "Ran all test suites."
    {
        return true;
    }
    // libtest `running 12 tests`; the `test result:` line repeats the count.
    if let Some(count) = line.strip_prefix("running ").and_then(|rest| {
        rest.strip_suffix(" tests")
            .or_else(|| rest.strip_suffix(" test"))
    }) {
        return !count.is_empty() && count.bytes().all(|byte| byte.is_ascii_digit());
    }
    // pytest `collected 12 items` and headed rulers such as
    // `==== test session starts ====`.
    if start.starts_with("collected ") && start.ends_with(" items")
        || (start.starts_with("=====") && start.contains(" test session starts "))
    {
        return true;
    }
    // node:test zero counters and timing (`ℹ skipped 0`, `ℹ duration_ms 41.2`).
    if let Some(rest) = start.strip_prefix("ℹ ") {
        return rest.starts_with("duration_ms ")
            || ["suites 0", "cancelled 0", "skipped 0", "todo 0"].contains(&rest);
    }
    if let Some(rest) =
        line.strip_prefix("test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured;")
    {
        return rest.contains("finished in");
    }
    // unittest rulers such as `======` and `------`.
    line.len() >= 20
        && (line.bytes().all(|byte| byte == b'=') || line.bytes().all(|byte| byte == b'-'))
}

/// Structural test-runner noise that carries no pass/fail fact by itself.
pub(crate) fn is_test_progress(text: &str) -> bool {
    let line = trimmed(text);
    let start = line.trim_start();
    if ["=== RUN ", "=== PAUSE ", "=== CONT ", "=== NAME "]
        .iter()
        .any(|prefix| start.starts_with(prefix))
    {
        return true;
    }
    // pytest quiet progress: `tests/test_a.py .....s..   [ 40%]` (no F or E).
    if let Some((head, tail)) = line.rsplit_once(" [") {
        if tail.ends_with("%]") {
            if let Some((path, marks)) = head.split_once(' ') {
                let marks = marks.trim();
                return path.ends_with(".py")
                    && !marks.is_empty()
                    && marks
                        .chars()
                        .all(|character| matches!(character, '.' | 's' | 'x' | 'X'));
            }
        }
    }
    false
}

/// A stack frame inside the runtime or a dependency, not the project's code.
fn is_runtime_frame(text: &str) -> bool {
    let start = trimmed(text).trim_start();
    if let Some(frame) = start.strip_prefix("at ") {
        return frame.starts_with("node:")
            || frame.contains("(node:")
            || frame.contains("node_modules")
            || frame.contains("(internal/")
            || ["java.", "jdk.internal.", "sun.", "org.junit.", "junit."]
                .iter()
                .any(|prefix| frame.starts_with(prefix));
    }
    if let Some(frame) = start.strip_prefix("File \"") {
        let lower = frame.to_ascii_lowercase();
        return frame.starts_with("<frozen ")
            || lower.contains("site-packages")
            || lower.contains("/lib/python")
            || lower.contains("\\lib\\python")
            || lower.contains("\\lib\\unittest\\")
            || lower.contains("/lib/unittest/");
    }
    false
}

/// Matched text of an rg line (`path:12:text`), or the whole line.
fn search_content(text: &str) -> &str {
    let line = trimmed(text);
    let bytes = line.as_bytes();
    for (index, byte) in bytes.iter().enumerate() {
        if *byte != b':' {
            continue;
        }
        let digits = bytes[index + 1..]
            .iter()
            .take_while(|byte| byte.is_ascii_digit())
            .count();
        if digits > 0 && bytes.get(index + 1 + digits) == Some(&b':') {
            return &line[index + digits + 2..];
        }
    }
    line
}

/// Source text that declares a named item (function, type, constant, class).
pub(crate) fn is_definition(code: &str) -> bool {
    let mut rest = code.trim_start();
    loop {
        let stripped = [
            "pub(crate) ",
            "pub(super) ",
            "pub ",
            "export default ",
            "export ",
            "async ",
            "static ",
            "public ",
            "private ",
            "protected ",
            "internal ",
            "final ",
            "abstract ",
            "unsafe ",
            "extern ",
            "declare ",
            "override ",
            "inline ",
        ]
        .iter()
        .find_map(|prefix| rest.strip_prefix(prefix));
        match stripped {
            Some(next) => rest = next,
            None => break,
        }
    }
    [
        "fn ",
        "def ",
        "class ",
        "struct ",
        "enum ",
        "trait ",
        "type ",
        "interface ",
        "impl ",
        "function ",
        "func ",
        "const ",
        "mod ",
        "module ",
        "#define ",
        "macro_rules! ",
    ]
    .iter()
    .any(|keyword| rest.starts_with(keyword))
}

/// Identifier tokens removed and added by one file's diff body, ignoring
/// tokens on both sides. Files with equal non-empty signatures made the same
/// edit (a rename or mechanical refactor).
fn edit_signature(lines: &[&str]) -> Option<(Vec<String>, Vec<String>)> {
    let identifiers = |text: &str| {
        text.split(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
            .filter(|word| word.starts_with(|c: char| c.is_ascii_alphabetic() || c == '_'))
            .map(str::to_string)
            .collect::<std::collections::BTreeSet<_>>()
    };
    let mut removed = std::collections::BTreeSet::new();
    let mut added = std::collections::BTreeSet::new();
    let mut changed = 0;
    for line in lines {
        let line = trimmed(line);
        if line.starts_with("---") || line.starts_with("+++") {
            continue;
        }
        if let Some(body) = line.strip_prefix('-') {
            removed.extend(identifiers(body));
            changed += 1;
        } else if let Some(body) = line.strip_prefix('+') {
            added.extend(identifiers(body));
            changed += 1;
        }
    }
    let only_removed = removed.difference(&added).cloned().collect::<Vec<_>>();
    let only_added = added.difference(&removed).cloned().collect::<Vec<_>>();
    (changed >= 4 && !(only_removed.is_empty() && only_added.is_empty()))
        .then_some((only_removed, only_added))
}

fn has_redraw(text: &str) -> bool {
    trimmed(text).contains('\r')
}

/// Mask digits and long hex runs so lines that differ only in counters,
/// durations, addresses, or hashes share one template.
fn template(text: &str) -> String {
    let line = trimmed(text);
    let mut out = String::with_capacity(line.len());
    let mut token = String::new();
    let flush = |token: &mut String, out: &mut String| {
        if token.is_empty() {
            return;
        }
        let digits = token.bytes().any(|byte| byte.is_ascii_digit());
        let hex = token.len() >= 7 && token.bytes().all(|byte| byte.is_ascii_hexdigit());
        if hex && digits {
            out.push('#');
        } else {
            let mut previous_digit = false;
            for character in token.chars() {
                if character.is_ascii_digit() {
                    if !previous_digit {
                        out.push('#');
                    }
                    previous_digit = true;
                } else {
                    out.push(character);
                    previous_digit = false;
                }
            }
        }
        token.clear();
    };
    for character in line.chars() {
        if character.is_ascii_alphanumeric() {
            token.push(character);
        } else {
            flush(&mut token, &mut out);
            out.push(character);
        }
    }
    flush(&mut token, &mut out);
    out
}

/// Identifier-like goal tokens used to keep task-relevant lines visible.
pub(crate) fn relevance_tokens(relevance_text: &[String]) -> Vec<String> {
    let mut tokens = Vec::new();
    for text in relevance_text {
        for raw in text.split(|character: char| {
            !(character.is_ascii_alphanumeric() || matches!(character, '_' | ':' | '.' | '/' | '-'))
        }) {
            let token = raw
                .trim_matches(|character: char| matches!(character, ':' | '.' | '/' | '-'))
                .to_ascii_lowercase();
            if token.len() >= 4 && !STOPWORDS.contains(&token.as_str()) && !tokens.contains(&token)
            {
                tokens.push(token);
            }
        }
    }
    tokens
}

pub(crate) struct PlanInput<'a> {
    pub lines: &'a [&'a str],
    pub relevance_text: &'a [String],
    pub relevance_tokens: &'a [String],
    pub preferred_test: Option<&'a str>,
    pub is_search: bool,
    pub is_diff: bool,
}

/// Returns one optional deferral reason per line. `None` means visible.
/// Existing reasons are respected; this only fills lines still visible.
pub(crate) fn plan(input: &PlanInput<'_>, reasons: &mut [Option<&'static str>]) {
    plan_rules(input, reasons);
    if !input.is_diff {
        tidy(input.lines, reasons);
    }
}

/// Drop layout left dangling by deferrals: blank separators next to hidden
/// lines, and a repeated section header (`failures:`) whose body is hidden.
fn tidy(lines: &[&str], reasons: &mut [Option<&'static str>]) {
    let blank = |index: usize| trimmed(lines[index]).trim().is_empty();
    for index in 0..lines.len() {
        if reasons[index].is_some() || !blank(index) {
            continue;
        }
        let hidden_before = index > 0 && reasons[index - 1].is_some();
        let hidden_after = index + 1 < lines.len() && reasons[index + 1].is_some();
        if hidden_before || hidden_after {
            reasons[index] = Some(BOILERPLATE);
        }
    }
    let mut seen = std::collections::HashSet::new();
    for index in 0..lines.len() {
        if reasons[index].is_some() {
            continue;
        }
        let text = trimmed(lines[index]).trim();
        if text.ends_with(':') && seen.contains(text) {
            let next = (index + 1..lines.len()).find(|next| !blank(*next));
            if next.is_none_or(|next| reasons[next].is_some()) {
                reasons[index] = Some(DUPLICATE);
                continue;
            }
        }
        seen.insert(text);
    }
}

fn plan_rules(input: &PlanInput<'_>, reasons: &mut [Option<&'static str>]) {
    let lines = input.lines;
    let mentioned = |text: &str| {
        let lower = trimmed(text).trim().to_ascii_lowercase();
        // Very short lines (blank, braces, bullets) match any goal text.
        lower.len() >= 4
            && input
                .relevance_text
                .iter()
                .any(|relevance| relevance.contains(lower.as_str()))
    };

    // 0. Runner chrome and repeated blank lines.
    let mut previous_blank = false;
    for (index, line) in lines.iter().enumerate() {
        let blank = trimmed(line).trim().is_empty();
        if reasons[index].is_none()
            && ((blank && previous_blank) || (!input.is_diff && is_boilerplate(line)))
        {
            reasons[index] = Some(BOILERPLATE);
        }
        previous_blank = blank;
    }
    // Leading and trailing blank lines.
    if !input.is_diff {
        for index in (0..lines.len()).take_while(|index| trimmed(lines[*index]).trim().is_empty()) {
            reasons[index].get_or_insert(BOILERPLATE);
        }
        for index in (0..lines.len())
            .rev()
            .take_while(|index| trimmed(lines[*index]).trim().is_empty())
        {
            reasons[index].get_or_insert(BOILERPLATE);
        }
    }

    // 0b. Stack frames inside the runtime or dependencies (with a Python
    //     frame's source excerpt on the next line).
    if !input.is_diff && !input.is_search {
        let mut index = 0;
        while index < lines.len() {
            if reasons[index].is_none()
                && is_runtime_frame(lines[index])
                && fact_kind(lines[index]).is_none()
            {
                reasons[index] = Some(RUNTIME_FRAMES);
                let python = trimmed(lines[index]).trim_start().starts_with("File \"");
                let indent = |text: &str| text.len() - text.trim_start().len();
                if python
                    && index + 1 < lines.len()
                    && reasons[index + 1].is_none()
                    && indent(lines[index + 1]) > indent(lines[index])
                    && !trimmed(lines[index + 1])
                        .trim_start()
                        .starts_with("File \"")
                    && fact_kind(lines[index + 1]).is_none()
                {
                    index += 1;
                    reasons[index] = Some(RUNTIME_FRAMES);
                }
            }
            index += 1;
        }
    }

    // 1. Passing-test inventory and runner progress.
    let passing = lines
        .iter()
        .filter(|line| passing_test_name(line).is_some())
        .count();
    let progress = lines.iter().filter(|line| is_test_progress(line)).count();
    if passing > MIN_INVENTORY || (passing + progress > MIN_INVENTORY && progress > 0) {
        // The summary line states the exact passing count, so no sample
        // passing lines are needed; a preferred (hinted) test stays visible.
        let keep = usize::from(input.preferred_test.is_some());
        let mut retained = 0;
        for (index, line) in lines.iter().enumerate() {
            if reasons[index].is_some() {
                continue;
            }
            if is_test_progress(line) && fact_kind(line).is_none() {
                reasons[index] = Some(TEST_PROGRESS);
                continue;
            }
            let Some(name) = passing_test_name(line) else {
                continue;
            };
            let lower = name.to_ascii_lowercase();
            let goal_mentions = input
                .relevance_text
                .iter()
                .any(|text| text.contains(&lower));
            let preferred = input
                .preferred_test
                .is_some_and(|test| test.eq_ignore_ascii_case(name));
            // A strict passing-inventory line is a pass even when the test's
            // name contains words like error or warning.
            if passing > MIN_INVENTORY && !goal_mentions && !preferred && retained >= keep {
                reasons[index] = Some(TESTS);
            } else {
                retained += 1;
            }
        }
    }

    // 2. Generated-file diffs (lockfiles and minified bundles).
    if input.is_diff {
        let mut index = 0;
        while index < lines.len() {
            let Some(path) = trimmed(lines[index])
                .strip_prefix("diff --git a/")
                .and_then(|rest| rest.split_once(" b/"))
                .map(|(path, _)| path)
            else {
                index += 1;
                continue;
            };
            let lower = path.to_ascii_lowercase();
            let base = lower.rsplit('/').next().unwrap_or(&lower);
            let generated = GENERATED_FILES.contains(&base)
                || base.ends_with(".min.js")
                || base.ends_with(".min.css")
                || base.ends_with(".js.map");
            let mut end = index + 1;
            while end < lines.len() && !trimmed(lines[end]).starts_with("diff --git ") {
                end += 1;
            }
            if generated && end - index > 6 && !mentioned(path) {
                for reason in &mut reasons[index + 1..end] {
                    reason.get_or_insert(GENERATED_DIFF);
                }
            }
            index = end;
        }

        // 2b. Mechanical edits repeated across files: the first file with a
        //     given edit signature stays whole; later ones keep their header.
        let mut runs = Vec::new();
        let mut start = None;
        for (index, line) in lines.iter().enumerate() {
            if line.starts_with("diff --git ") {
                if let Some(begin) = start.replace(index) {
                    runs.push((begin, index));
                }
            }
        }
        if let Some(begin) = start {
            runs.push((begin, lines.len()));
        }
        let signatures = runs
            .iter()
            .map(|(start, end)| {
                let untouched = reasons[*start..*end].iter().all(Option::is_none);
                let path = trimmed(lines[*start]).to_ascii_lowercase();
                (untouched
                    && !input.relevance_text.iter().any(|text| {
                        path.split([' ', '/'])
                            .filter(|part| part.len() >= 4 && part.contains('.'))
                            .any(|part| text.contains(part))
                    }))
                .then(|| edit_signature(&lines[*start..*end]))
                .flatten()
            })
            .collect::<Vec<_>>();
        for (index, signature) in signatures.iter().enumerate() {
            let Some(signature) = signature else { continue };
            let group = signatures
                .iter()
                .filter(|other| other.as_ref() == Some(signature))
                .count();
            let first = signatures
                .iter()
                .position(|other| other.as_ref() == Some(signature));
            if group >= MIN_SAME_EDIT_FILES && first != Some(index) {
                let (start, end) = runs[index];
                for reason in &mut reasons[start + 1..end] {
                    reason.get_or_insert(SAME_EDIT);
                }
            }
        }
    }

    // 3. Terminal redraw lines (progress bars written with carriage returns).
    let redraws = lines.iter().filter(|line| has_redraw(line)).count();
    if redraws >= 2 {
        for (index, line) in lines.iter().enumerate() {
            if reasons[index].is_none() && has_redraw(line) && fact_kind(line).is_none() {
                reasons[index] = Some(PROGRESS);
            }
        }
    }

    // 4. Runs of identical lines: keep the first, defer the copies.
    let mut start = 0;
    while start < lines.len() {
        let mut end = start + 1;
        while end < lines.len() && trimmed(lines[end]) == trimmed(lines[start]) {
            end += 1;
        }
        if end - start >= MIN_IDENTICAL_RUN
            && reasons[start..end].iter().all(Option::is_none)
            && !input.is_diff
        {
            for reason in &mut reasons[start + 1..end] {
                *reason = Some(IDENTICAL);
            }
        }
        start = end;
    }

    // 5. Runs of lines sharing one template (counters, timings, hashes).
    if !input.is_search && !input.is_diff {
        let templates = lines.iter().map(|line| template(line)).collect::<Vec<_>>();
        let mut start = 0;
        while start < lines.len() {
            let eligible = |index: usize| {
                reasons[index].is_none()
                    && templates[index].len() >= 6
                    && fact_kind(lines[index]).is_none()
                    && !mentioned(lines[index])
            };
            if !eligible(start) {
                start += 1;
                continue;
            }
            let mut end = start + 1;
            while end < lines.len() && eligible(end) && templates[end] == templates[start] {
                end += 1;
            }
            if end - start >= MIN_SIMILAR_RUN {
                for reason in &mut reasons[start + 2..end - 1] {
                    *reason = Some(SIMILAR);
                }
            }
            start = end;
        }
    }

    // 5b. Test-runner recaps: a line that exactly repeats a visible earlier
    //     line, or a bare name echoing an earlier visible failure line.
    if !input.is_search && !input.is_diff && passing + progress > 0 {
        let mut seen = std::collections::HashSet::new();
        let mut facts: Vec<&str> = Vec::new();
        for (index, line) in lines.iter().enumerate() {
            if reasons[index].is_some() {
                continue;
            }
            let text = trimmed(line).trim();
            let structural =
                text.ends_with(':') || text.starts_with("File \"") || text.starts_with("at ");
            let echo = !text.contains(char::is_whitespace)
                && text.len() >= 8
                && facts.iter().any(|fact| fact.contains(text));
            if text.len() >= 8 && !structural && (seen.contains(text) || echo) {
                reasons[index] = Some(DUPLICATE);
                continue;
            }
            seen.insert(text);
            if fact_kind(text).is_some() {
                facts.push(text);
            }
        }
    }

    // 6. Long outputs: keep head, tail, protected facts with context, and
    //    task-relevant lines; defer the rest of the middle for recall.
    if input.is_diff {
        return;
    }
    if input.is_search {
        plan_search(input, reasons);
        return;
    }
    let visible = (0..lines.len())
        .filter(|index| reasons[*index].is_none())
        .collect::<Vec<_>>();
    let visible_bytes = visible
        .iter()
        .map(|index| lines[*index].len())
        .sum::<usize>();
    if visible.len() < LONG_MIN_LINES || visible_bytes < LONG_MIN_BYTES {
        return;
    }
    let mut keep = vec![false; lines.len()];
    for index in visible.iter().take(LONG_HEAD) {
        keep[*index] = true;
    }
    for index in visible.iter().rev().take(LONG_TAIL) {
        keep[*index] = true;
    }
    let tokens = frequent_filtered(input.relevance_tokens, lines);
    for (index, line) in lines.iter().enumerate() {
        if reasons[index].is_some() {
            continue;
        }
        // Search results are matched source lines, not runtime diagnostics:
        // `Err(...)` or `assert!` in code is not a failure to protect.
        let runtime_fact = if input.is_search {
            None
        } else {
            fact_kind(line)
        };
        if let Some(kind) = runtime_fact {
            let (before, after) = match kind {
                "failure_identity" | "assertion" => (FAILURE_CONTEXT_BEFORE, FAILURE_CONTEXT_AFTER),
                _ => (0, 1),
            };
            let from = index.saturating_sub(before);
            let to = (index + after + 1).min(lines.len());
            for flag in &mut keep[from..to] {
                *flag = true;
            }
        } else if mentioned(line) || line_matches_tokens(line, &tokens) {
            keep[index] = true;
        }
    }
    // Anomalies: lines whose shape is rare in an otherwise repetitive output
    // (a single eviction notice among thousands of request logs).
    for index in rare_lines(lines, &visible) {
        keep[index] = true;
    }
    for index in visible {
        if !keep[index] {
            reasons[index] = Some(LONG_MIDDLE);
        }
    }
}

/// Many search matches: keep the first matches, every definition among the
/// matches, and goal-relevant matches; defer the rest behind recall. An agent
/// searching a symbol usually needs its definition, which can sit anywhere in
/// path-sorted output.
fn plan_search(input: &PlanInput<'_>, reasons: &mut [Option<&'static str>]) {
    let lines = input.lines;
    let visible = (0..lines.len())
        .filter(|index| reasons[*index].is_none())
        .collect::<Vec<_>>();
    let bytes = visible
        .iter()
        .map(|index| lines[*index].len())
        .sum::<usize>();
    if visible.len() < SEARCH_MIN_LINES && bytes < SEARCH_MIN_BYTES {
        return;
    }
    let definitions = visible
        .iter()
        .filter(|index| is_definition(search_content(lines[**index])))
        .count();
    // When most matches are definitions (searching for `fn `), none stand out.
    let definitions_stand_out = definitions * 3 <= visible.len();
    let tokens = frequent_filtered(input.relevance_tokens, lines);
    for (position, index) in visible.iter().enumerate() {
        let line = lines[*index];
        let lower = trimmed(line).to_ascii_lowercase();
        let content = search_content(&lower).trim();
        let keep = position < SEARCH_HEAD
            || (definitions_stand_out && is_definition(search_content(line)))
            || line_matches_tokens(line, &tokens)
            || (content.len() >= 4
                && input
                    .relevance_text
                    .iter()
                    .any(|text| text.contains(content)));
        if !keep {
            reasons[*index] = Some(SEARCH_MORE);
        }
    }
}

/// Compact stand-in for a chunk of output sent to a remote ranker: each
/// distinct line shape once, verbatim at its first occurrence, with a count
/// of similar lines. A rare line deep in a repetitive chunk survives, where
/// clipping the raw text would cut it off.
pub fn digest(text: &str) -> String {
    let mut order: Vec<(String, &str, usize)> = Vec::new();
    for line in text.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let mut key = shape(line);
        if key.is_empty() {
            key = template(line);
        }
        match order.iter_mut().find(|(shape, _, _)| *shape == key) {
            Some((_, _, count)) => *count += 1,
            None => order.push((key, line, 1)),
        }
    }
    let mut out = String::new();
    for (_, line, count) in order {
        out.push_str(line);
        if count > 1 {
            out.push_str(&format!("  [+{} similar]", count - 1));
        }
        out.push('\n');
    }
    out
}

/// Coarse line shape: the first few words with digits masked and trailing
/// punctuation removed, ignoring a leading timestamp-like token.
fn shape(line: &str) -> String {
    let masked = template(line);
    let mut words = masked
        .split_whitespace()
        .filter(|word| word.chars().any(char::is_alphabetic))
        .map(|word| word.trim_end_matches([':', ',', ';']));
    let mut shape = String::new();
    for word in words.by_ref().take(3) {
        shape.push_str(word);
        shape.push(' ');
    }
    shape
}

/// Visible lines whose shape occurs at most twice, when the output is
/// otherwise dominated by a few repeated shapes. Returns nothing when rare
/// lines are common (for example, every compile line is unique).
fn rare_lines(lines: &[&str], visible: &[usize]) -> Vec<usize> {
    let mut counts = std::collections::HashMap::<String, usize>::new();
    let shapes = visible
        .iter()
        .map(|index| {
            let shape = shape(lines[*index]);
            *counts.entry(shape.clone()).or_default() += 1;
            shape
        })
        .collect::<Vec<_>>();
    let rare = visible
        .iter()
        .zip(&shapes)
        .filter(|(index, shape)| {
            !shape.is_empty() && counts[*shape] <= 2 && trimmed(lines[**index]).trim().len() >= 12
        })
        .map(|(index, _)| *index)
        .collect::<Vec<_>>();
    if rare.len() > 40 || rare.len() * 20 > visible.len() {
        return Vec::new();
    }
    rare
}

fn line_matches_tokens(line: &str, tokens: &[String]) -> bool {
    if tokens.is_empty() {
        return false;
    }
    let lower = trimmed(line).to_ascii_lowercase();
    tokens.iter().any(|token| lower.contains(token.as_str()))
}

/// Drop tokens that match so many lines that they no longer discriminate. A
/// goal word such as a service name or `10:42` can match hundreds of lines in
/// a log; keeping them all would overflow the view budget.
fn frequent_filtered(tokens: &[String], lines: &[&str]) -> Vec<String> {
    let limit = (lines.len() / 50).clamp(8, 40);
    tokens
        .iter()
        .filter(|token| {
            lines
                .iter()
                .filter(|line| trimmed(line).to_ascii_lowercase().contains(token.as_str()))
                .count()
                <= limit
        })
        .cloned()
        .collect()
}

/// Group consecutive deferred-for-relevance lines into bounded candidate
/// sections an adaptive ranker may promote back into view.
pub(crate) fn rankable_runs(
    reasons: &[Option<&'static str>],
    lines: &[&str],
) -> Vec<(usize, usize)> {
    // Grow chunks for very long outputs so a ranker sees at most ~60 of them.
    let deferred = reasons
        .iter()
        .filter(|reason| **reason == Some(LONG_MIDDLE))
        .count();
    let chunk_lines = CHUNK_LINES.max(deferred.div_ceil(60));
    let mut runs = Vec::new();
    let mut index = 0;
    while index < reasons.len() {
        if reasons[index] != Some(LONG_MIDDLE) {
            index += 1;
            continue;
        }
        let mut end = index + 1;
        while end < reasons.len() && reasons[end] == Some(LONG_MIDDLE) {
            end += 1;
        }
        let mut chunk_start = index;
        while chunk_start < end {
            let limit = (chunk_start + chunk_lines).min(end);
            // Prefer breaking after a blank line near the limit.
            let mut chunk_end = limit;
            if limit < end {
                if let Some(blank) = (chunk_start + chunk_lines / 2..limit)
                    .rev()
                    .find(|line| trimmed(lines[*line]).trim().is_empty())
                {
                    chunk_end = blank + 1;
                }
            }
            runs.push((chunk_start, chunk_end));
            chunk_start = chunk_end;
        }
        index = end;
    }
    runs
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run_plan<'a>(text: &'a str, goal: &str) -> (Vec<&'a str>, Vec<Option<&'static str>>) {
        let lines = text.split_inclusive('\n').collect::<Vec<_>>();
        let relevance = if goal.is_empty() {
            Vec::new()
        } else {
            vec![goal.to_ascii_lowercase()]
        };
        let tokens = relevance_tokens(&relevance);
        let mut reasons = vec![None; lines.len()];
        plan(
            &PlanInput {
                lines: &lines,
                relevance_text: &relevance,
                relevance_tokens: &tokens,
                preferred_test: None,
                is_search: false,
                is_diff: text.starts_with("diff --git"),
            },
            &mut reasons,
        );
        (lines, reasons)
    }

    fn visible(lines: &[&str], reasons: &[Option<&'static str>]) -> String {
        lines
            .iter()
            .zip(reasons)
            .filter(|(_, reason)| reason.is_none())
            .map(|(line, _)| *line)
            .collect()
    }

    #[test]
    fn search_keeps_head_definitions_and_goal_matches() {
        let mut text = String::new();
        for n in 0..60 {
            text.push_str(&format!(
                "src/a_{n:02}.rs:3:    let x = cfg.retry_budget + {n};\n"
            ));
            if n == 40 {
                text.push_str("src/net.rs:2:pub fn default_retry_budget() -> u32 {\n");
            }
        }
        let lines = text.split_inclusive('\n').collect::<Vec<_>>();
        let mut reasons = vec![None; lines.len()];
        plan(
            &PlanInput {
                lines: &lines,
                relevance_text: &[],
                relevance_tokens: &[],
                preferred_test: None,
                is_search: true,
                is_diff: false,
            },
            &mut reasons,
        );
        let view = visible(&lines, &reasons);
        assert!(view.contains("src/a_00.rs"));
        assert!(view.contains("src/a_11.rs"));
        assert!(!view.contains("src/a_12.rs"));
        assert!(view.contains("pub fn default_retry_budget()"));
        assert!(reasons
            .iter()
            .flatten()
            .all(|reason| *reason == SEARCH_MORE));
        assert!(is_definition("export async function load(x) {"));
        assert!(is_definition("    pub(crate) struct Pool {"));
        assert!(!is_definition("    let pool = Pool::new();"));
    }

    #[test]
    fn digest_keeps_rare_lines_and_counts_repeats() {
        let mut text = String::new();
        for n in 0..29 {
            text.push_str(&format!("10:00:{n:02} INFO http: /api/cart 200 {n}ms\n"));
            if n == 25 {
                text.push_str("10:00:25 WARN token refresh rejected: clock skew 340s\n");
            }
        }
        let digest = digest(&text);
        assert!(digest.contains("clock skew 340s"));
        assert!(digest.contains("[+28 similar]"));
        assert!(digest.len() * 10 < text.len());
    }

    #[test]
    fn repeated_mechanical_edit_keeps_one_file_and_every_header() {
        let mut text = String::new();
        for name in ["a", "b", "c", "d"] {
            text.push_str(&format!(
                "diff --git a/src/{name}.rs b/src/{name}.rs\n--- a/src/{name}.rs\n+++ b/src/{name}.rs\n@@ -1,2 +1,2 @@\n-    log_info(ctx, \"{name} 1\");\n+    tracing::info!(\"{name} 1\");\n-    log_info(ctx, \"{name} 2\");\n+    tracing::info!(\"{name} 2\");\n"
            ));
        }
        text.push_str("diff --git a/src/bucket.rs b/src/bucket.rs\n--- a/src/bucket.rs\n+++ b/src/bucket.rs\n@@ -1 +1 @@\n-        self.tokens = self.tokens + add;\n+        self.tokens = (self.tokens + add).min(self.capacity);\n");
        let (lines, reasons) = run_plan(&text, "");
        let view = visible(&lines, &reasons);
        assert!(view.contains("-    log_info(ctx, \"a 1\");"));
        assert!(!view.contains("\"b 1\""));
        assert!(view.contains("diff --git a/src/d.rs b/src/d.rs"));
        assert!(view.contains(".min(self.capacity)"));
        assert!(reasons.iter().flatten().all(|reason| *reason == SAME_EDIT));
        // A goal naming the file keeps its body.
        let (lines, reasons) = run_plan(&text, "check c.rs");
        assert!(visible(&lines, &reasons).contains("\"c 1\""));
    }

    #[test]
    fn runtime_frames_and_failure_recaps_are_deferred() {
        let text = "test tests::a ... ok\ntest tests::b ... ok\ntest tests::c ... ok\ntest tests::parse_fails ... FAILED\n\nfailures:\n\n---- tests::parse_fails stdout ----\nboom\n    at Test.run (node:internal/test_runner/test:1081:25)\n    at userCode (src/app.js:3:9)\n\nfailures:\n    tests::parse_fails\n\ntest result: FAILED. 3 passed; 1 failed\n";
        let (lines, reasons) = run_plan(text, "");
        let view = visible(&lines, &reasons);
        assert!(view.contains("test tests::parse_fails ... FAILED"));
        assert!(view.contains("at userCode (src/app.js:3:9)"));
        assert!(!view.contains("node:internal"));
        assert!(!view.contains("    tests::parse_fails\n"));
        assert_eq!(view.matches("failures:").count(), 1);
        assert!(view.contains("test result: FAILED."));
    }

    #[test]
    fn recognizes_passing_lines_across_frameworks() {
        for (line, name) in [
            ("test a::b ... ok\n", "a::b"),
            (
                "test_parse (pkg.tests.ParseTest.test_parse) ... ok\n",
                "test_parse",
            ),
            (
                "    --- PASS: TestParse/sub_case (0.00s)\n",
                "TestParse/sub_case",
            ),
            (
                "tests/test_x.py::test_y PASSED                [ 50%]\n",
                "test_y",
            ),
            ("  ✔ parses input (1.234ms)\n", "parses input"),
            ("    ✓ renders button (3 ms)\n", "renders button"),
            ("    √ renders button\n", "renders button"),
            ("ok 12 - handles empty input\n", "handles empty input"),
        ] {
            assert_eq!(passing_test_name(line), Some(name), "{line}");
        }
        for line in [
            "test a::b ... FAILED\n",
            "--- FAIL: TestParse (0.00s)\n",
            "tests/test_x.py::test_y FAILED\n",
            "  ✖ parses input (1.2ms)\n",
            "not ok 3 - broken\n",
            "ok 4 - skipped # SKIP no network\n",
            "test result: ok. 3 passed\n",
        ] {
            assert_eq!(passing_test_name(line), None, "{line}");
        }
    }

    #[test]
    fn go_verbose_output_keeps_failure_and_summary() {
        let mut text = String::new();
        for n in 0..30 {
            text.push_str(&format!(
                "=== RUN   TestCase{n}\n--- PASS: TestCase{n} (0.00s)\n"
            ));
        }
        text.push_str("=== RUN   TestBroken\n    broken_test.go:12: expected 4, got 3\n--- FAIL: TestBroken (0.00s)\nFAIL\nFAIL\texample.com/pkg\t0.012s\n");
        let (lines, reasons) = run_plan(&text, "");
        let visible = lines
            .iter()
            .zip(&reasons)
            .filter(|(_, reason)| reason.is_none())
            .map(|(line, _)| *line)
            .collect::<String>();
        assert!(visible.contains("--- FAIL: TestBroken"));
        assert!(visible.contains("expected 4, got 3"));
        assert!(visible.contains("FAIL\texample.com/pkg"));
        assert!(!visible.contains("--- PASS: TestCase0 "));
        assert!(!visible.contains("TestCase20"));
        assert!(!visible.contains("=== RUN"));
    }

    #[test]
    fn identical_and_similar_runs_keep_edges_and_protected_lines() {
        let mut text = String::new();
        for n in 0..40 {
            text.push_str(&format!("Downloaded crate_{n} v1.{n}.0 in {n}ms\n"));
        }
        for _ in 0..5 {
            text.push_str("warning: unused import\n");
        }
        let (lines, reasons) = run_plan(&text, "");
        assert!(reasons[0].is_none() && reasons[1].is_none() && reasons[39].is_none());
        assert_eq!(reasons[2], Some(SIMILAR));
        assert!(reasons[40].is_none());
        assert!(reasons[41..45]
            .iter()
            .all(|reason| *reason == Some(IDENTICAL)));
        assert_eq!(lines.len(), 45);
    }

    #[test]
    fn lockfile_diff_body_is_deferred_but_header_and_source_stay() {
        let mut text = String::from("diff --git a/Cargo.lock b/Cargo.lock\n");
        for n in 0..40 {
            text.push_str(&format!("+name = \"dep{n}\"\n"));
        }
        text.push_str("diff --git a/src/lib.rs b/src/lib.rs\n+fn changed() {}\n");
        let (lines, reasons) = run_plan(&text, "");
        assert!(reasons[0].is_none());
        assert!(reasons[1..41]
            .iter()
            .all(|reason| *reason == Some(GENERATED_DIFF)));
        assert!(reasons[41..].iter().all(Option::is_none));
        assert_eq!(lines.len(), 43);
    }

    #[test]
    fn long_output_keeps_edges_failures_with_context_and_goal_lines() {
        let mut text = String::new();
        let words = ["alpha", "beta", "gamma", "delta", "omega", "sigma", "kappa"];
        for n in 0..400 {
            text.push_str(&format!(
                "{} {} {}: processed batch {n} of 400\n",
                words[n % 7],
                words[(n / 7) % 7],
                words[(n / 49) % 7]
            ));
            if n == 200 {
                text.push_str("ERROR storage: reconnect_budget exhausted after 3 attempts\n");
                text.push_str("  at storage::retry (src/storage.rs:88)\n");
            }
            if n == 300 {
                text.push_str("note: session_cache evicted 12 entries\n");
            }
        }
        let (lines, reasons) = run_plan(&text, "Investigate session_cache eviction");
        let visible = lines
            .iter()
            .zip(&reasons)
            .filter(|(_, reason)| reason.is_none())
            .map(|(line, _)| *line)
            .collect::<String>();
        assert!(visible.contains("reconnect_budget exhausted"));
        assert!(visible.contains("src/storage.rs:88"));
        assert!(visible.contains("session_cache evicted"));
        assert!(visible.contains("processed batch 0 of"));
        assert!(visible.contains("processed batch 399 of"));
        assert!(!visible.contains("processed batch 150 of"));
        assert!(visible.len() * 4 < text.len());
        let runs = rankable_runs(&reasons, &lines);
        assert!(!runs.is_empty());
        assert!(runs.len() <= 61);
    }

    #[test]
    fn boilerplate_and_blank_runs_are_silent_but_facts_stay() {
        let text = "     Running unittests src/lib.rs (target/debug/deps/x-1)\n\n\n\nnote: run with `RUST_BACKTRACE=1` environment variable to display a backtrace\nerror: test failed, to rerun pass `--lib`\n   Doc-tests x\nrunning 0 tests\ntest result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n";
        let (lines, reasons) = run_plan(text, "");
        let visible = lines
            .iter()
            .zip(&reasons)
            .filter(|(_, reason)| reason.is_none())
            .map(|(line, _)| *line)
            .collect::<String>();
        assert_eq!(visible, "error: test failed, to rerun pass `--lib`\n");
        assert!(reasons
            .iter()
            .flatten()
            .all(|reason| *reason == BOILERPLATE));
    }

    #[test]
    fn goal_tokens_skip_stopwords_and_keep_identifiers() {
        let tokens =
            relevance_tokens(&["fix the failing test for parse_header in src/http.rs".into()]);
        assert!(tokens.contains(&"parse_header".to_string()));
        assert!(tokens.contains(&"src/http.rs".to_string()));
        assert!(!tokens.contains(&"failing".to_string()));
        assert!(!tokens.contains(&"test".to_string()));
    }
}
