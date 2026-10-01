mod accounting;
mod bounded_output;
mod claude_auto;
mod claude_hook;
mod claude_hook_setup;
mod codex_hook;
mod codex_pre_hook;
mod cursor_rule_setup;
mod cursor_runner_setup;
mod gain;
mod integration;
mod jev;
mod mcp;
mod mcp_run;

use clap::{Parser, Subcommand, ValueEnum};
use jevto_core::{
    make_pack_with_selection, render_pack_formatted, review_git_detailed, stale_advice,
    AdaptiveSelection, Completeness, Coverage, DiffAdvice, Mode, PackCoverage, PackFormat,
    ProcessStatus, Receipt, Store, Stream, TaskFrame, SCHEMA_VERSION,
};
use serde_json::json;
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::Instant;
use uuid::Uuid;

#[derive(Parser)]
#[command(
    name = "jevto",
    version,
    about = "Local evidence capture, exact recall, and advisory diff review"
)]
struct Cli {
    #[arg(long, global = true)]
    store_dir: Option<PathBuf>,
    #[command(subcommand)]
    command: Commands,
}

#[derive(Clone, Copy, ValueEnum)]
enum ModeArg {
    Passthrough,
    /// Rules only: no network.
    #[value(alias = "rules")]
    Deterministic,
    /// Rules plus Jev ranking (needs a policy and --allow-remote-jev).
    #[value(alias = "jev")]
    Adaptive,
    /// Full Jev when OPENROUTER_API_KEY is set, scoped to the current
    /// workspace; rules only otherwise.
    Auto,
}

impl From<ModeArg> for Mode {
    fn from(value: ModeArg) -> Self {
        value.resolve(jev_key_present())
    }
}

impl ModeArg {
    fn resolve(self, key_present: bool) -> Mode {
        match self {
            ModeArg::Passthrough => Mode::Passthrough,
            ModeArg::Deterministic => Mode::Deterministic,
            ModeArg::Adaptive => Mode::Adaptive,
            ModeArg::Auto => {
                if key_present {
                    Mode::Adaptive
                } else {
                    Mode::Deterministic
                }
            }
        }
    }
}

fn mode_diagnostics() -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    let override_mode = std::env::var("JEVTO_MODE").ok();
    let configured = match override_mode.as_deref() {
        Some(value) => ModeArg::from_str(value, false).map_err(|_| {
            "invalid JEVTO_MODE: expected passthrough, deterministic/rules, adaptive/jev, or auto"
        })?,
        None => ModeArg::Auto,
    };
    let key_present = jev_key_present();
    let effective = configured.resolve(key_present);
    Ok(json!({
        "default_mode": "auto",
        "configured_mode": configured.to_possible_value().expect("mode value").get_name(),
        "mode_source": if override_mode.is_some() { "JEVTO_MODE" } else { "built_in_default" },
        "effective_mode": effective,
        "network_default": if effective == Mode::Adaptive && key_present { "conditional" } else { "off" },
        "network_requirements": "A run needs a goal, rankable material, and an allowed outbound policy. Auto with a key uses the workspace policy; explicit adaptive also needs remote authorization and a policy. Per-run --mode overrides this diagnosis. Doctor sends no request."
    }))
}

/// Auto mode turns full Jev on only when a key is present at runtime. The
/// key itself is read later by the request code and never stored.
fn jev_key_present() -> bool {
    std::env::var("OPENROUTER_API_KEY").is_ok_and(|key| !key.trim().is_empty())
}

#[derive(Clone, Copy, ValueEnum)]
enum FormatArg {
    Compact,
    Verbose,
}

impl From<FormatArg> for PackFormat {
    fn from(value: FormatArg) -> Self {
        match value {
            FormatArg::Compact => PackFormat::Compact,
            FormatArg::Verbose => PackFormat::Verbose,
        }
    }
}

#[derive(Clone, Copy, ValueEnum)]
enum StreamArg {
    Stdout,
    Stderr,
}

#[derive(Clone, Copy, PartialEq, Eq, ValueEnum)]
enum RecallStyle {
    /// Plain `jevto` on PATH, or an explicit store path for this shell.
    Cli,
    /// Bash-quoted absolute executable, as Claude Code's Bash tool needs.
    Claude,
}

#[derive(Clone, Copy, ValueEnum)]
enum HookEvent {
    ClaudePre,
    ClaudePrompt,
}

#[derive(Clone, Copy, ValueEnum)]
enum HostArg {
    Codex,
    Claude,
    Cursor,
}

impl From<StreamArg> for Stream {
    fn from(value: StreamArg) -> Self {
        match value {
            StreamArg::Stdout => Stream::Stdout,
            StreamArg::Stderr => Stream::Stderr,
        }
    }
}

#[derive(Subcommand)]
enum Commands {
    Init {
        #[arg(value_enum)]
        host: HostArg,
        #[arg(long)]
        apply: bool,
        #[arg(long)]
        adopt_existing: bool,
    },
    Disable {
        #[arg(value_enum)]
        host: HostArg,
        #[arg(long)]
        apply: bool,
    },
    InitCodexHook {
        #[arg(long, default_value = ".")]
        workspace: PathBuf,
        #[arg(long)]
        apply: bool,
    },
    DisableCodexHook {
        #[arg(long, default_value = ".")]
        workspace: PathBuf,
        #[arg(long)]
        apply: bool,
    },
    InitCodexPreHook {
        #[arg(long, default_value = ".")]
        workspace: PathBuf,
        #[arg(long)]
        apply: bool,
    },
    DisableCodexPreHook {
        #[arg(long, default_value = ".")]
        workspace: PathBuf,
        #[arg(long)]
        apply: bool,
    },
    InitClaudeHook {
        #[arg(long, default_value = ".")]
        workspace: PathBuf,
        #[arg(long)]
        apply: bool,
    },
    DisableClaudeHook {
        #[arg(long, default_value = ".")]
        workspace: PathBuf,
        #[arg(long)]
        apply: bool,
    },
    /// Route Claude Code test, build, lint, search, and diff commands through
    /// JevTO automatically, and record each prompt as the local session goal.
    InitClaudeAuto {
        #[arg(long, default_value = ".")]
        workspace: PathBuf,
        #[arg(long)]
        apply: bool,
    },
    DisableClaudeAuto {
        #[arg(long, default_value = ".")]
        workspace: PathBuf,
        #[arg(long)]
        apply: bool,
    },
    /// Hook entrypoints invoked by host configuration.
    #[command(hide = true)]
    Hook {
        #[arg(value_enum)]
        event: HookEvent,
    },
    /// Summarize bytes and estimated tokens saved, recalls, and Jev cost from
    /// local receipts.
    Gain {
        #[arg(long)]
        session: Option<String>,
        #[arg(long)]
        json: bool,
    },
    InitCursorRunner {
        #[arg(long)]
        workspace: PathBuf,
        #[arg(long)]
        policy: PathBuf,
        #[arg(long)]
        apply: bool,
    },
    DisableCursorRunner {
        #[arg(long)]
        workspace: PathBuf,
        #[arg(long)]
        apply: bool,
    },
    InitCursorRule {
        #[arg(long)]
        workspace: PathBuf,
        #[arg(long)]
        apply: bool,
        #[arg(required = true, last = true)]
        program: Vec<String>,
    },
    DisableCursorRule {
        #[arg(long)]
        workspace: PathBuf,
        #[arg(long)]
        apply: bool,
    },
    Run {
        #[arg(long)]
        session: Option<String>,
        #[arg(long)]
        workspace_id: Option<String>,
        #[arg(long)]
        task_file: Option<PathBuf>,
        #[arg(long, value_enum, default_value = "auto", env = "JEVTO_MODE")]
        mode: ModeArg,
        #[arg(long, default_value_t = 65536)]
        budget: usize,
        #[arg(
            long,
            env = "JEVTO_ALLOW_REMOTE_JEV",
            value_parser = clap::builder::BoolishValueParser::new()
        )]
        allow_remote_jev: bool,
        #[arg(long, env = "JEVTO_REMOTE_POLICY")]
        remote_policy: Option<PathBuf>,
        /// How recall commands are written in the compact view.
        #[arg(long = "host", value_enum, default_value = "cli")]
        recall_style: RecallStyle,
        #[arg(long)]
        jev_preview: bool,
        #[arg(long, value_enum, default_value = "compact")]
        format: FormatArg,
        #[arg(required = true, last = true)]
        program: Vec<String>,
    },
    Recall {
        /// Capture ID as printed (8+ characters), or `last` for the newest capture
        capture_id: String,
        #[arg(long, conflicts_with_all = ["lines", "full"])]
        section: Option<String>,
        #[arg(long, conflicts_with_all = ["section", "full"])]
        lines: Option<String>,
        #[arg(long, conflicts_with_all = ["section", "lines"])]
        full: bool,
        #[arg(long, value_enum, default_value = "stdout")]
        stream: StreamArg,
    },
    Review {
        #[arg(long, default_value = ".")]
        workspace: PathBuf,
        #[arg(long, default_value = "HEAD")]
        base: String,
        #[arg(long, default_value_t = 0)]
        task_revision: u64,
        #[arg(long)]
        workspace_id: Option<String>,
        #[arg(long)]
        json: bool,
        /// Task frame whose goal drives the optional Jev scope check.
        #[arg(long)]
        task_file: Option<PathBuf>,
        /// Outbound policy that must set allow_diff_snippets.
        #[arg(long)]
        remote_policy: Option<PathBuf>,
        #[arg(long)]
        allow_remote_jev: bool,
        #[arg(long)]
        jev_preview: bool,
    },
    AdviceStatus {
        advice_file: PathBuf,
        #[arg(long, default_value = ".")]
        workspace: PathBuf,
        #[arg(long)]
        task_revision: u64,
    },
    Report {
        #[arg(long)]
        session: String,
        #[arg(long)]
        json: bool,
    },
    Doctor {
        #[arg(long)]
        json: bool,
    },
    Purge,
    Mcp {
        #[arg(long)]
        run_policy: Option<PathBuf>,
        #[arg(long, requires = "run_policy")]
        run_policy_sha256: Option<String>,
    },
    CodexHook,
    CodexPreHook {
        #[arg(long)]
        workspace: PathBuf,
    },
    ClaudeHook {
        #[arg(long)]
        workspace: PathBuf,
    },
}

fn main() {
    let cli = Cli::parse();
    let code = match dispatch(cli.command, cli.store_dir) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("jevto: {error}");
            2
        }
    };
    // `process::exit` skips stdout's buffered tail; flush it first.
    let _ = io::stdout().flush();
    std::process::exit(code);
}

fn dispatch(
    command: Commands,
    store_dir: Option<PathBuf>,
) -> Result<i32, Box<dyn std::error::Error>> {
    let mut store = Store::default_local();
    if let Some(path) = &store_dir {
        store.root = path.clone();
    }
    match command {
        Commands::Init {
            host,
            apply,
            adopt_existing,
        } => {
            integration::init(host, apply, adopt_existing, &store)?;
            Ok(0)
        }
        Commands::Disable { host, apply } => {
            integration::disable(host, apply, &store)?;
            Ok(0)
        }
        Commands::InitCodexHook { workspace, apply } => {
            integration::init_codex_hook(&workspace, apply, &store)?;
            Ok(0)
        }
        Commands::DisableCodexHook { workspace, apply } => {
            integration::disable_codex_hook(&workspace, apply, &store)?;
            Ok(0)
        }
        Commands::InitCodexPreHook { workspace, apply } => {
            integration::init_codex_pre_hook(&workspace, apply, &store)?;
            Ok(0)
        }
        Commands::DisableCodexPreHook { workspace, apply } => {
            integration::disable_codex_pre_hook(&workspace, apply, &store)?;
            Ok(0)
        }
        Commands::InitClaudeHook { workspace, apply } => {
            claude_hook_setup::init(&workspace, apply, &store)?;
            Ok(0)
        }
        Commands::DisableClaudeHook { workspace, apply } => {
            claude_hook_setup::disable(&workspace, apply, &store)?;
            Ok(0)
        }
        Commands::InitCursorRunner {
            workspace,
            policy,
            apply,
        } => {
            cursor_runner_setup::init(&workspace, &policy, apply, &store)?;
            Ok(0)
        }
        Commands::DisableCursorRunner { workspace, apply } => {
            cursor_runner_setup::disable(&workspace, apply, &store)?;
            Ok(0)
        }
        Commands::InitCursorRule {
            workspace,
            apply,
            program,
        } => {
            cursor_rule_setup::init(&workspace, &program, apply, &store)?;
            Ok(0)
        }
        Commands::DisableCursorRule { workspace, apply } => {
            cursor_rule_setup::disable(&workspace, apply, &store)?;
            Ok(0)
        }
        Commands::Run {
            session,
            workspace_id,
            task_file,
            mode,
            budget,
            allow_remote_jev,
            remote_policy,
            jev_preview,
            format,
            recall_style,
            program,
        } => {
            // Auto mode with a key is full Jev: the user opted in by setting
            // the key, and the implicit policy covers only this workspace.
            let jev_auto = matches!(mode, ModeArg::Auto) && jev_key_present();
            let allow_remote_jev = allow_remote_jev || jev_auto;
            let requested_mode: Mode = mode.into();
            let mut task: Option<TaskFrame> = match task_file {
                Some(path) => Some(serde_json::from_slice(&fs::read(path)?)?),
                None => None,
            };
            let session = session
                .or_else(|| task.as_ref().map(|task| task.session_id.clone()))
                .unwrap_or_else(|| Uuid::new_v4().to_string());
            let workspace_id = workspace_id
                .or_else(|| task.as_ref().map(|task| task.workspace_id.clone()))
                .unwrap_or_else(|| "explicit-cli".into());
            if task.is_none() {
                // A host hook may have recorded this session's goal.
                if let Ok(Some(goal)) = store.session_goal(&session) {
                    task = Some(TaskFrame {
                        schema_version: SCHEMA_VERSION,
                        session_id: session.clone(),
                        workspace_id: workspace_id.clone(),
                        revision: goal.revision,
                        goal: goal.goal,
                        trusted_constraints: Vec::new(),
                        repo_revision: None,
                        extra: Default::default(),
                    });
                }
            }
            if let Some(task) = &task {
                if task.session_id != session || task.workspace_id != workspace_id {
                    return Err("task frame session/workspace does not match this run".into());
                }
            }
            let mut task_frame_fallback = None;
            if let Some(frame) = &task {
                if let Err(error) = store.observe_task_frame(frame) {
                    task_frame_fallback = Some(error.to_string());
                    task = None;
                }
            }
            let executable = &program[0];
            let arguments = &program[1..];
            let display = format!(
                "{} [{} arguments redacted]",
                Path::new(executable)
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy(),
                arguments.len()
            );
            let output = match bounded_output::run(
                executable,
                arguments,
                bounded_output::capture_limit(store.max_bytes),
            )? {
                bounded_output::ChildOutput::Captured(output) => output,
                bounded_output::ChildOutput::Bypassed { status, raw_bytes } => {
                    if let Some(frame) = &task {
                        if let Err(error) = store.observe_task_frame(frame) {
                            task_frame_fallback = Some(error.to_string());
                            task = None;
                        }
                    }
                    let mut extra = serde_json::Map::new();
                    extra.insert("jev_call_outcome".into(), json!("not_attempted"));
                    extra.insert("workspace_id".into(), json!(workspace_id));
                    extra.insert("child_exit".into(), json!(status.code()));
                    if let Some(frame) = &task {
                        extra.insert("task_revision".into(), json!(frame.revision));
                    }
                    if let Some(reason) = task_frame_fallback {
                        extra.insert("task_frame_fallback".into(), json!(reason));
                    }
                    if requested_mode == Mode::Adaptive {
                        extra.insert("requested_mode".into(), json!("adaptive"));
                        extra.insert("adaptive_fallback".into(), json!("capture_limit_exceeded"));
                    }
                    let receipt = Receipt {
                        schema_version: SCHEMA_VERSION,
                        session_id: session,
                        run_id: Uuid::new_v4().to_string(),
                        mode: Mode::Passthrough,
                        coverage: vec![Coverage {
                            tool_path: "explicit_cli_run".into(),
                            captured: false,
                            replaced: false,
                            bypass_reason: Some("capture_limit_exceeded".into()),
                        }],
                        raw_bytes,
                        delivered_bytes: raw_bytes,
                        recalls: 0,
                        provider_usage: None,
                        jev_usage: None,
                        verification: None,
                        extra: extra.into_iter().collect(),
                    };
                    if let Err(error) = store.save_receipt(&receipt) {
                        eprintln!("jevto: receipt unavailable ({error})");
                    }
                    return Ok(status.code().unwrap_or(130));
                }
            };
            if let Some(frame) = &task {
                if let Err(error) = store.observe_task_frame(frame) {
                    task_frame_fallback = Some(error.to_string());
                    task = None;
                }
            }
            let exit = output.status.code();
            let status = if exit.is_some() {
                ProcessStatus::Completed
            } else {
                ProcessStatus::Interrupted
            };
            let exit_code = exit.unwrap_or(130);
            let capture = match store.capture(
                &session,
                &workspace_id,
                "jevto run",
                Some(display),
                &output.stdout,
                &output.stderr,
                Completeness::Complete,
                status,
                exit,
            ) {
                Ok(record) => record,
                Err(error) => {
                    eprintln!("jevto: capture bypass ({error}); child result follows unchanged");
                    deliver_original(&output.stdout, &output.stderr)?;
                    return Ok(exit_code);
                }
            };
            let mut effective_mode = requested_mode.clone();
            let mut adaptive_selection: Option<AdaptiveSelection> = None;
            let mut jev_usage = None;
            let mut jev_elapsed_ms = None;
            let mut jev_observation = jev::CallObservation::default();
            let mut jev_cache_hit = false;
            let mut jev_model = None;
            let mut jev_exists = None;
            let mut jev_detail = None;
            let mut adaptive_fallback = None;
            let mut jev_request = None;
            if requested_mode == Mode::Adaptive {
                let prepared = if let Some(reason) = task_frame_fallback.as_deref() {
                    Err(reason)
                } else if task.is_none() {
                    Err(if jev_auto {
                        "no_session_goal"
                    } else {
                        "adaptive mode requires --task-file"
                    })
                } else if !allow_remote_jev && !jev_preview {
                    Err("remote_jev_not_authorized")
                } else {
                    let cwd = std::env::current_dir()?;
                    match (remote_policy.as_deref(), jev_auto) {
                        (Some(path), _) => jev::OutboundPolicy::load(path, &cwd),
                        (None, true) => jev::OutboundPolicy::workspace(&cwd),
                        (None, false) => Err("adaptive mode requires --remote-policy"),
                    }
                    .and_then(|policy| {
                        jev::prepare(
                            task.as_ref().expect("checked above"),
                            &program,
                            &capture,
                            &output.stdout,
                            &output.stderr,
                            None,
                            &policy,
                        )
                    })
                };
                if let Ok(prepared) = &prepared {
                    jev_request = Some(json!({
                        "kind": prepared.kind().as_str(),
                        "candidates": prepared.candidate_count(),
                        "bytes": prepared.request_bytes(),
                    }));
                }
                match prepared {
                    Ok(prepared) if jev_preview => {
                        eprintln!(
                            "jevto: Jev outbound preview (no request sent): {}",
                            prepared.preview()
                        );
                        adaptive_fallback = Some("jev_preview_only".to_owned());
                    }
                    Ok(prepared) if !allow_remote_jev => {
                        let _ = prepared;
                        adaptive_fallback = Some("remote_jev_not_authorized".to_owned());
                    }
                    Ok(prepared) => {
                        let key = std::env::var("OPENROUTER_API_KEY").ok();
                        let started = Instant::now();
                        match jev::decide_cached_observed(
                            &prepared,
                            key.as_deref(),
                            &store.root,
                            &mut jev_observation,
                        ) {
                            Ok(decision) => {
                                jev_detail = Some(json!({
                                    "kind": decision.kind.as_str(),
                                    "need": decision.need,
                                    "candidates": decision.candidates,
                                    "kept": decision.kept,
                                }));
                                adaptive_selection = Some(decision.selection);
                                jev_usage = decision.usage;
                                jev_cache_hit = decision.cache_hit;
                                jev_model = Some(decision.model);
                                jev_exists = Some(decision.exists);
                            }
                            Err(reason) => adaptive_fallback = Some(reason.to_owned()),
                        }
                        // Usage remains observed even when selection or a cache write fails.
                        if jev_observation.usage.is_some() {
                            jev_usage = jev_observation.usage.clone();
                        }
                        jev_elapsed_ms =
                            Some(started.elapsed().as_millis().min(u64::MAX as u128) as u64);
                    }
                    Err(reason) => adaptive_fallback = Some(reason.to_owned()),
                }
                if adaptive_fallback.is_some() {
                    effective_mode = Mode::Deterministic;
                }
            }
            let decision = make_pack_with_selection(
                &capture,
                &output.stdout,
                &output.stderr,
                task.as_ref(),
                effective_mode.clone(),
                budget,
                None,
                adaptive_selection.as_ref(),
            );
            let mut replaced = false;
            let mut bypass = decision.bypass_reason.or(adaptive_fallback.clone());
            let delivered_bytes;
            if let Some(pack) = decision.pack {
                if pack.coverage == PackCoverage::Overflow {
                    deliver_original(&output.stdout, &output.stderr)?;
                    delivered_bytes = output.stdout.len() + output.stderr.len();
                } else {
                    match render_run_pack(
                        &capture,
                        &pack,
                        store_dir.as_deref(),
                        format.into(),
                        recall_style,
                    ) {
                        Ok(rendered) if rendered.len() > budget => {
                            bypass = Some("rendered_pack_exceeds_budget".into());
                            deliver_original(&output.stdout, &output.stderr)?;
                            delivered_bytes = output.stdout.len() + output.stderr.len();
                        }
                        Ok(rendered)
                            if rendered.len() >= output.stdout.len() + output.stderr.len() =>
                        {
                            bypass = Some("rendered_pack_no_net_reduction".into());
                            deliver_original(&output.stdout, &output.stderr)?;
                            delivered_bytes = output.stdout.len() + output.stderr.len();
                        }
                        Ok(rendered) => {
                            if let Err(error) = store.save_pack(&pack) {
                                bypass = Some(format!("pack_store_failed: {error}"));
                                deliver_original(&output.stdout, &output.stderr)?;
                                delivered_bytes = output.stdout.len() + output.stderr.len();
                            } else {
                                io::stdout().write_all(rendered.as_bytes())?;
                                delivered_bytes = rendered.len();
                                replaced = true;
                            }
                        }
                        Err(error) => {
                            bypass = Some(format!("recall_command_unavailable: {error}"));
                            deliver_original(&output.stdout, &output.stderr)?;
                            delivered_bytes = output.stdout.len() + output.stderr.len();
                        }
                    }
                }
            } else {
                deliver_original(&output.stdout, &output.stderr)?;
                delivered_bytes = output.stdout.len() + output.stderr.len();
            }
            let mut extra = serde_json::Map::new();
            extra.insert("capture_id".into(), json!(capture.capture_id));
            extra.insert("command".into(), json!(gain::command_label(&program)));
            extra.insert("workspace_id".into(), json!(workspace_id));
            extra.insert("child_exit".into(), json!(exit));
            if let Some(frame) = &task {
                extra.insert("task_revision".into(), json!(frame.revision));
            }
            if let Some(reason) = task_frame_fallback {
                extra.insert("task_frame_fallback".into(), json!(reason));
            }
            if requested_mode == Mode::Adaptive {
                extra.insert("requested_mode".into(), json!("adaptive"));
            }
            if jev_auto {
                extra.insert("jev_auto".into(), json!(true));
            }
            if let Some(reason) = adaptive_fallback {
                extra.insert("adaptive_fallback".into(), json!(reason));
            }
            if let Some(elapsed) = jev_elapsed_ms {
                extra.insert("jev_elapsed_ms".into(), json!(elapsed));
            }
            extra.insert("jev_call_outcome".into(), json!(jev_observation.outcome));
            if jev_observation.attempted {
                extra.insert("jev_call_attempted".into(), json!(true));
            }
            if jev_cache_hit {
                extra.insert("jev_cache_hit".into(), json!(true));
            }
            if let Some(model) = jev_model {
                extra.insert("jev_model".into(), json!(model));
            }
            if let Some(exists) = jev_exists {
                extra.insert("jev_exists".into(), json!(exists));
            }
            if let Some(detail) = jev_detail {
                extra.insert("jev_decision".into(), detail);
            }
            if let Some(request) = jev_request {
                extra.insert("jev_request".into(), request);
            }
            let receipt = Receipt {
                schema_version: SCHEMA_VERSION,
                session_id: session,
                run_id: Uuid::new_v4().to_string(),
                mode: effective_mode,
                coverage: vec![Coverage {
                    tool_path: "explicit_cli_run".into(),
                    captured: true,
                    replaced,
                    bypass_reason: bypass,
                }],
                raw_bytes: (output.stdout.len() + output.stderr.len()) as u64,
                delivered_bytes: delivered_bytes as u64,
                recalls: 0,
                provider_usage: None,
                jev_usage,
                verification: None,
                extra: extra.into_iter().collect(),
            };
            if let Err(error) = store.save_receipt(&receipt) {
                eprintln!("jevto: receipt unavailable ({error})");
            }
            Ok(exit_code)
        }
        Commands::Recall {
            capture_id,
            section,
            lines,
            full,
            stream,
        } => {
            let capture_id = store.resolve_capture_id(&capture_id)?;
            let data = if let Some(section) = section {
                let (section_stream, start, end) = store.find_section(&capture_id, &section)?;
                store.recall(&capture_id, section_stream, Some((start, end)), false)?
            } else if let Some(lines) = lines {
                let (start, end) = lines.split_once(':').ok_or("--lines must be START:END")?;
                store.recall_lines(&capture_id, stream.into(), start.parse()?, end.parse()?)?
            } else {
                // `--full` is the default when no narrower selection is given.
                let _ = full;
                let capture = store.read_capture(&capture_id)?;
                if capture.record.completeness == Completeness::Partial {
                    return Err("partial: full original unavailable".into());
                }
                io::stdout().write_all(&capture.stdout)?;
                io::stderr().write_all(&capture.stderr)?;
                if let Err(error) =
                    store.record_recall(&capture_id, capture.stdout.len() + capture.stderr.len())
                {
                    eprintln!("jevto: recall receipt unavailable ({error})");
                }
                return Ok(0);
            };
            io::stdout().write_all(&data)?;
            if let Err(error) = store.record_recall(&capture_id, data.len()) {
                eprintln!("jevto: recall receipt unavailable ({error})");
            }
            Ok(0)
        }
        Commands::Review {
            workspace,
            base,
            task_revision,
            workspace_id,
            json,
            task_file,
            remote_policy,
            allow_remote_jev,
            jev_preview,
        } => {
            let id = workspace_id.unwrap_or_else(|| "explicit-cli".into());
            let (mut advice, summaries) =
                review_git_detailed(&workspace, &id, task_revision, &base)?;
            if allow_remote_jev || jev_preview {
                match scope_review(
                    &workspace,
                    task_file.as_deref(),
                    remote_policy.as_deref(),
                    allow_remote_jev,
                    &summaries,
                ) {
                    Ok(Some(review)) => {
                        advice.findings.extend(review.findings);
                        advice.extra.insert(
                            "jev_scope".into(),
                            json!({
                                "scope_level": review.scope,
                                "scope_scale": "0 tightly within goal .. 3 mostly unrelated",
                                "files": review
                                    .needed
                                    .iter()
                                    .map(|(path, probability)| json!({"path": path, "needed_probability": probability}))
                                    .collect::<Vec<_>>(),
                                "model": review.model,
                                "usage": review.usage,
                            }),
                        );
                    }
                    Ok(None) => {}
                    Err(reason) => {
                        advice
                            .extra
                            .insert("jev_scope_fallback".into(), json!(reason));
                    }
                }
            }
            if json {
                println!("{}", serde_json::to_string_pretty(&advice)?);
            } else {
                println!("candidate: {}\nreviewed: {} paths; unreviewed: {} paths\nfindings: {} (advisory; no verification verdict)", advice.candidate_sha256, advice.reviewed_paths.len(), advice.unreviewed_paths.len(), advice.findings.len());
                if let Some(scope) = advice.extra.get("jev_scope") {
                    println!(
                        "jev scope level: {:.2} (0 = tightly within goal, 3 = mostly unrelated)",
                        scope["scope_level"].as_f64().unwrap_or_default()
                    );
                }
                if let Some(reason) = advice.extra.get("jev_scope_fallback") {
                    println!("jev scope check skipped: {reason}");
                }
                for finding in advice.findings {
                    println!(
                        "- {}: {}\n  {}",
                        finding.path, finding.evidence, finding.question
                    );
                }
            }
            Ok(0)
        }
        Commands::AdviceStatus {
            advice_file,
            workspace,
            task_revision,
        } => {
            let advice: DiffAdvice = serde_json::from_slice(&fs::read(advice_file)?)?;
            println!(
                "{}",
                if stale_advice(&workspace, &advice, task_revision)? {
                    "stale_candidate"
                } else {
                    "current"
                }
            );
            Ok(0)
        }
        Commands::Report { session, json } => {
            let receipts = store.receipts_for_session(&session)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&receipts)?);
            } else {
                let raw: u64 = receipts.iter().map(|r| r.raw_bytes).sum();
                let delivered: u64 = receipts.iter().map(|r| r.delivered_bytes).sum();
                let recalls: u64 = receipts.iter().map(|r| r.recalls).sum();
                let recalled_bytes: u64 = receipts
                    .iter()
                    .filter_map(|r| {
                        r.extra
                            .get("recalled_bytes")
                            .and_then(|value| value.as_u64())
                    })
                    .sum();
                let observed = accounting::summarize(&receipts);
                let jev_attempts = observed["attempted_calls"].as_u64().unwrap_or(0);
                let unknown = observed["unknown_outcomes"].as_u64().unwrap_or(0);
                let unpriced = observed["unpriced_attempts"].as_u64().unwrap_or(0);
                let jev_reported_cost = observed["reported_cost_usd"].as_f64().unwrap_or(0.0);
                let cost_display = if jev_attempts == 0 && unknown == 0 {
                    "none (no Jev calls)".to_owned()
                } else if unpriced == jev_attempts && unknown == 0 {
                    "unknown (no priced response)".to_owned()
                } else {
                    format!(
                        "${jev_reported_cost:.8} known; {unpriced} attempt(s) unpriced; {unknown} outcome(s) unknown"
                    )
                };
                println!(
                    "Jev outcomes: {} successful, {} failed, {} cached, {} unknown; fallbacks: {}",
                    observed["successful_calls"],
                    observed["failed_calls"],
                    observed["cache_hits"],
                    unknown,
                    observed["fallbacks"]
                );
                println!("session: {session}\nobserved routed results: {}\nlocal raw bytes: {raw}\nlocal selected bytes: {delivered}\nrecalls: {recalls}\nrecalled bytes: {recalled_bytes}\nJev calls attempted: {jev_attempts}\nJev response-reported cost: {cost_display}\ncoding-provider usage: unknown\ncoding-provider bill: unknown\nverified task outcome: unknown", receipts.len());
                for receipt in receipts {
                    for coverage in receipt.coverage {
                        println!(
                            "- {} captured={} replaced={} bypass={}",
                            coverage.tool_path,
                            coverage.captured,
                            coverage.replaced,
                            coverage.bypass_reason.unwrap_or_else(|| "none".into())
                        );
                    }
                }
            }
            Ok(0)
        }
        Commands::Doctor { json } => {
            let mode_diagnostics = mode_diagnostics()?;
            let health = store
                .initialize()
                .map(|_| "writable")
                .unwrap_or("unavailable");
            let registrations = integration::host_registrations(&store);
            let registration_summary = registrations
                .iter()
                .map(|item| format!("{}={}", item.host, item.state))
                .collect::<Vec<_>>()
                .join(", ");
            let mut value = json!({
                "version": env!("CARGO_PKG_VERSION"),
                "store": {"path": store.root, "health": health, "retention_hours": store.retention_hours, "max_bytes": store.max_bytes, "run_capture_limit_bytes": bounded_output::capture_limit(store.max_bytes)},
                "host_registrations": registrations,
                "paths": [
                    {"tool_path":"jevto run (explicit argv)","capture":"complete within the run capture limit; larger output streams through without recall","replacement":"implemented within the run capture limit","failed_command":"exit preserved","permission":"caller controls command"},
                    {"tool_path":"jevto mcp recall/review/status (default)","capture":"no new command capture; recall reads historical bytes and records a local receipt","replacement":"unsupported","failed_command":"not_applicable; default MCP server exposes no command runner","permission":"host MCP grant required"},
                    {"tool_path":"jevto mcp --run-policy exact-command","capture":"opt-in exact command ID invokes the explicit CLI wrapper; MCP response is bounded to 1 MiB","replacement":"selected child output returned through the tool; no ordinary shell interception","failed_command":"child exit is separate from MCP transport status","permission":"external policy file and host MCP tool grant required; Codex 0.158 auto denied and approve grant succeeded, but its read-only shell sandbox did not constrain the MCP child","installed_by_init":false},
                    {"tool_path":"Claude Code 2.1.283 project PostToolUse hook: successful Bash, PowerShell, and Grep search results","capture":"exact hook-visible recall; native Bash capture observed; actual Grep event shape replayed locally","replacement":"shape-preserving Bash updatedToolOutput observed by the host; Grep replacement passed exact-event replay and awaits a post-fix native turn","failed_command":"outside PostToolUse success scope","permission":"Claude project settings and host trust apply"},
                    {"tool_path":"Codex 0.144.4 project PostToolUse Bash hook: passing Rust test inventory","capture":"hook-visible text","replacement":"observed on successful command","failed_command":"not invoked in observed nonzero command","permission":"Codex project hook trust required","child_exit":"unavailable","child_output_completeness":"unverified"},
                    {"tool_path":"Codex 0.158.0-alpha.2.1 project PostToolUse Bash hook in code mode","capture":"hook-visible text observed","replacement":"not delivered in observed nested tool call","failed_command":"unverified","permission":"Codex project hook trust required","child_exit":"unavailable","child_output_completeness":"unverified"},
                    {"tool_path":"Codex 0.158.0-alpha.2.1 JevTO MCP status/recall/review","capture":"historical read and advisory review; no ordinary tool interception","replacement":"unsupported","failed_command":"not_applicable; invalid capture ID tool error observed","permission":"scoped grant for three tools observed; without grant approval denied under policy never","tool_calls":"model-driven success on named version"},
                    {"tool_path":"Cursor CLI 2026.09.23-86fc751 JevTO MCP status on grok-4.7-xhigh","capture":"historical receipt read; no ordinary tool interception","replacement":"unsupported","failed_command":"not_applicable; MCP failure path unverified","permission":"observed with force mode and disabled Windows sandbox; approval path unverified","tool_calls":"model-driven status success on named version; Cursor exposed text content but not structured fields; recall/review unverified"},
                    {"tool_path":"Cursor CLI 2026.09.26-dd393fe exact verifier project rule on grok-4.7-xhigh","capture":"one instructed jevto run wrapper per passing or failing task in tracked disposable Git projects","replacement":"agent followed the project rule; no native interception","failed_command":"one verifier failed with child exit 101 and assertion retained","permission":"observed with force/trust and disabled Windows sandbox; unforced approval unverified","tool_calls":"completed passing and failing verifier turns with exact local recall and owned rule removal"},
                    {"tool_path":"Codex 0.158.0-alpha.2.1 project PreToolUse Bash hook: plain Windows PowerShell cargo test","capture":"complete child stdout and stderr via wrapper","replacement":"observed in agent-visible successful and failed calls","failed_command":"child exit 101 and assertion retained; outer PowerShell reported exit 1","permission":"project hook trust required; workspace sandbox denial observed; approval prompts unverified","child_exit":"recorded","child_output_completeness":"complete for wrapped child"},
                    {"tool_path":"Codex 0.158.0-alpha.2.1 project PreToolUse Bash hook: plain Windows PowerShell cargo check","capture":"complete child stdout and stderr via wrapper","replacement":"observed on a successful 24-crate check: 2424 raw bytes, 1142 selected bytes","failed_command":"compiler diagnostic retained; child exit 101 propagated as outer exit 101","permission":"workspace-write denied Cargo target outside the disposable project; hook trust required; approval prompts unverified","child_exit":"recorded","child_output_completeness":"complete for wrapped child"},
                    {"tool_path":"Codex 0.158.0-alpha.2.1 project PreToolUse Bash hook: plain Windows PowerShell cargo build","capture":"complete child stdout and stderr via wrapper","replacement":"observed on a successful 24-crate build: 2424 raw bytes, 1142 selected bytes","failed_command":"compiler diagnostic retained; child exit 101 propagated as outer exit 101","permission":"workspace-write denied Cargo target outside the disposable project; hook trust required; approval prompts unverified","child_exit":"recorded","child_output_completeness":"complete for wrapped child"},
                    {"tool_path":"Codex 0.158.0-alpha.2.1 project PreToolUse Bash hook: simple Windows PowerShell rg -n -H","capture":"complete child stdout and stderr via wrapper","replacement":"observed for 80 repeated exact search matches","failed_command":"missing-file child exit 2 and error retained; outer PowerShell reported exit 1","permission":"project hook trust required; approval prompts unverified; separate rg permission-denial test not run","child_exit":"recorded","child_output_completeness":"complete for wrapped child"},
                    {"tool_path":"Claude Code project PreToolUse Bash/PowerShell rewrite + UserPromptSubmit goal (init-claude-auto)","capture":"complete child stdout and stderr via the explicit wrapper","replacement":"command rewritten before execution through updatedInput; no permission decision is set","failed_command":"child exit preserved","permission":"Claude's normal permission flow applies; Claude Code 2.1.283 on Haiku 4.5 routed passing unittest runs through the rewrite in six live sessions, each with a receipt; failing commands verified by local replay only","child_exit":"recorded","child_output_completeness":"complete for wrapped child"},
                    {"tool_path":"Codex native tool","capture":"unsupported","replacement":"unsupported","failed_command":"unsupported","permission":"host controlled"},
                    {"tool_path":"Cursor native tool","capture":"unsupported","replacement":"unsupported","failed_command":"unsupported","permission":"host controlled"}
                ],
                "adaptive_jev":"auto_when_openrouter_key_present_workspace_scoped_choice_noul_score_for_search_diff_and_long_output_sections; opt_in_noul_score_diff_scope_review",
                "deterministic_formats":["test_inventories: rust libtest, go test -v, python unittest -v, pytest -v, node:test, jest/vitest, TAP","runner_boilerplate","successful_cargo_progress","repeated_and_similar_lines","terminal_progress_redraws","lockfile_and_minified_diffs","long_output_head_tail_facts_goal_terms_and_rare_lines","consecutive_repeated_rg_matches"],
                "output_formats":["compact","verbose"]
            });
            value
                .as_object_mut()
                .expect("doctor object")
                .extend(mode_diagnostics.as_object().expect("mode object").clone());
            if json {
                println!("{}", serde_json::to_string_pretty(&value)?);
            } else {
                let configured_mode = mode_diagnostics["configured_mode"]
                    .as_str()
                    .unwrap_or("unknown");
                let effective_mode = mode_diagnostics["effective_mode"]
                    .as_str()
                    .unwrap_or("unknown");
                let network_default = mode_diagnostics["network_default"]
                    .as_str()
                    .unwrap_or("unknown");
                println!("JevTO {}\nmode: {configured_mode} -> {effective_mode}\nnetwork: {network_default}; run prerequisites apply (doctor sends no requests)\nstore: {} ({health})\nMCP registrations (config only): {registration_summary}\ncoverage: explicit jevto run; three non-executing MCP tools model-driven on Codex 0.158 with scoped grants; Cursor 2026.09.23 model-driven MCP status on exact Grok 4.7 xhigh under force mode; Cursor 2026.09.26 passing and failing exact verifier project-rule turns on xhigh under force mode; Codex 0.144.4 narrow success post-hook; Codex 0.158 Windows PowerShell pre-hook for plain cargo test/check/build and simple rg searches after project trust; Claude 2.1.283 project PostToolUse replacement observed for one Bash search, with post-fix Grep replacement proven by exact-event replay. Native Grep delivery after the parser fix, Cursor recall/review, native Cursor shell interception, and broader host coverage remain unverified; Claude 2.1.283 project PreToolUse auto-route (init-claude-auto) observed in six live sessions\nadaptive Jev: auto with a key; explicit adaptive requires authorization and policy; OpenRouter Choice + Noul + Score over search, diff, and long-output sections; opt-in Noul + Score diff scope review", env!("CARGO_PKG_VERSION"), store.root.display());
            }
            Ok(0)
        }
        Commands::Purge => {
            let removed = store.purge_expired()?;
            println!("expired captures removed: {removed}");
            Ok(0)
        }
        Commands::Mcp {
            run_policy,
            run_policy_sha256,
        } => {
            let policy = run_policy
                .as_deref()
                .map(|path| mcp_run::load_pinned(path, run_policy_sha256.as_deref()))
                .transpose()?;
            mcp::serve(&store, policy.as_ref())?;
            Ok(0)
        }
        Commands::CodexHook => {
            codex_hook::serve(&store)?;
            Ok(0)
        }
        Commands::CodexPreHook { workspace } => {
            codex_pre_hook::serve(&workspace)?;
            Ok(0)
        }
        Commands::ClaudeHook { workspace } => {
            claude_hook::serve(&store, &workspace)?;
            Ok(0)
        }
        Commands::InitClaudeAuto { workspace, apply } => {
            claude_auto::init(&workspace, apply, &store)?;
            Ok(0)
        }
        Commands::DisableClaudeAuto { workspace, apply } => {
            claude_auto::disable(&workspace, apply, &store)?;
            Ok(0)
        }
        Commands::Hook { event } => {
            // A store chosen by JEVTO_STORE_DIR reaches the rewritten command
            // through the inherited environment; only a flag must be repeated.
            let explicit = store_dir.is_some();
            match event {
                HookEvent::ClaudePre => claude_auto::pre_tool_use(&store, explicit)?,
                HookEvent::ClaudePrompt => claude_auto::user_prompt_submit(&store)?,
            }
            Ok(0)
        }
        Commands::Gain { session, json } => {
            gain::report(&store, session.as_deref(), json)?;
            Ok(0)
        }
    }
}

/// Optional write-side Jev scope check. Returns `Ok(None)` for preview only.
fn scope_review(
    workspace: &Path,
    task_file: Option<&Path>,
    remote_policy: Option<&Path>,
    allow_remote_jev: bool,
    summaries: &[jevto_core::ChangedFileSummary],
) -> Result<Option<jev::ScopeReview>, String> {
    let task: TaskFrame = serde_json::from_slice(
        &fs::read(task_file.ok_or("scope review requires --task-file")?)
            .map_err(|_| "task_frame_unreadable")?,
    )
    .map_err(|_| "task_frame_invalid")?;
    let policy = jev::OutboundPolicy::load(
        remote_policy.ok_or("scope review requires --remote-policy")?,
        workspace,
    )?;
    if !policy.allows_diffs() {
        return Err("remote_policy_denies_diffs".into());
    }
    let files = summaries
        .iter()
        .filter(|file| !file.deleted)
        .map(|file| jev::ChangedFile {
            path: file.path.clone(),
            added: file.added,
            removed: file.removed,
            excerpt: file.excerpt.clone(),
        })
        .collect::<Vec<_>>();
    let request = jev::prepare_scope(&task.goal, &files)?;
    if !allow_remote_jev {
        eprintln!("jevto: Jev scope preview (no request sent): {request}");
        return Ok(None);
    }
    let key = std::env::var("OPENROUTER_API_KEY").map_err(|_| "missing_jev_key")?;
    jev::review_scope(request, &key)
        .map(Some)
        .map_err(str::to_owned)
}

fn deliver_original(stdout: &[u8], stderr: &[u8]) -> io::Result<()> {
    io::stdout().write_all(stdout)?;
    io::stderr().write_all(stderr)?;
    Ok(())
}

/// Whether plain `jevto` on PATH resolves to this executable.
fn on_path(exe: &Path) -> bool {
    let Ok(exe) = fs::canonicalize(exe) else {
        return false;
    };
    std::env::var_os("PATH").is_some_and(|path| {
        std::env::split_paths(&path).any(|directory| {
            ["jevto", "jevto.exe"]
                .iter()
                .any(|name| fs::canonicalize(directory.join(name)).is_ok_and(|found| found == exe))
        })
    })
}

fn render_run_pack(
    capture: &jevto_core::EvidenceCapture,
    pack: &jevto_core::EvidencePack,
    store_dir: Option<&Path>,
    format: PackFormat,
    recall_style: RecallStyle,
) -> io::Result<String> {
    let recall_prefix = if recall_style == RecallStyle::Claude {
        let exe = std::env::current_exe()?;
        let quote = |text: String| format!("'{}'", text.replace('\\', "/").replace('\'', "'\\''"));
        match store_dir {
            Some(dir) => format!(
                "{} --store-dir {}",
                quote(exe.to_string_lossy().into_owned()),
                quote(
                    std::path::absolute(dir)?
                        .to_string_lossy()
                        .trim_start_matches(r"\\?\")
                        .to_owned()
                )
            ),
            None if on_path(&exe) => "jevto".into(),
            None => quote(exe.to_string_lossy().into_owned()),
        }
    } else if let Some(dir) = store_dir {
        let path = fs::canonicalize(dir)?;
        let exe = std::env::current_exe()?;
        if cfg!(windows) {
            format!(
                "& '{}' --store-dir '{}'",
                exe.to_string_lossy().replace('\'', "''"),
                path.to_string_lossy().replace('\'', "''")
            )
        } else {
            format!(
                "'{}' --store-dir '{}'",
                exe.to_string_lossy().replace('\'', "'\\''"),
                path.to_string_lossy().replace('\'', "'\\''")
            )
        }
    } else {
        "jevto".into()
    };
    Ok(render_pack_formatted(capture, pack, &recall_prefix, format))
}
