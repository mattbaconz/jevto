//! `jevto gain`: what JevTO did, from local receipts only.
//!
//! Token figures are estimates (bytes / 4), the same convention other output
//! filters use; they are not provider-billed tokens. Jev cost is the sum of
//! response-reported costs.

use jevto_core::{Receipt, Store};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::error::Error;
use std::path::Path;

/// A privacy-preserving label: program basename plus a short subcommand word.
pub fn command_label(program: &[String]) -> String {
    let Some(executable) = program.first() else {
        return "unknown".into();
    };
    // Split on both separators so a Windows path labels the same everywhere.
    let base = executable.rsplit(['/', '\\']).next().unwrap_or(executable);
    let name = Path::new(base)
        .file_stem()
        .map(|name| name.to_string_lossy().to_ascii_lowercase())
        .unwrap_or_else(|| "unknown".into());
    match program.get(1) {
        Some(word)
            if word.len() <= 12
                && word
                    .bytes()
                    .all(|byte| byte.is_ascii_alphabetic() || byte == b'-')
                && !word.starts_with('-') =>
        {
            format!("{name} {word}")
        }
        Some(word) if word == "-m" => match program.get(2) {
            Some(module) if module.bytes().all(|byte| byte.is_ascii_alphanumeric()) => {
                format!("{name} -m {module}")
            }
            _ => name,
        },
        _ => name,
    }
}

fn estimate_tokens(bytes: u64) -> u64 {
    bytes.div_ceil(4)
}

#[derive(Default)]
struct Row {
    runs: u64,
    reduced: u64,
    raw: u64,
    delivered: u64,
}

pub fn summarize(receipts: &[Receipt]) -> Value {
    let mut total = Row::default();
    let mut by_command: BTreeMap<String, Row> = BTreeMap::new();
    let mut bypass: BTreeMap<String, u64> = BTreeMap::new();
    let mut recalls = 0u64;
    for receipt in receipts {
        let command = receipt
            .extra
            .get("command")
            .and_then(Value::as_str)
            .unwrap_or("other")
            .to_string();
        let replaced = receipt.coverage.iter().any(|coverage| coverage.replaced);
        for row in [&mut total, by_command.entry(command).or_default()] {
            row.runs += 1;
            row.reduced += u64::from(replaced);
            row.raw += receipt.raw_bytes;
            row.delivered += receipt.delivered_bytes;
        }
        for coverage in &receipt.coverage {
            if let Some(reason) = &coverage.bypass_reason {
                *bypass.entry(reason.clone()).or_default() += 1;
            }
        }
        recalls += receipt.recalls;
    }
    let row_json = |row: &Row| {
        json!({
            "runs": row.runs,
            "reduced_runs": row.reduced,
            "raw_bytes": row.raw,
            "delivered_bytes": row.delivered,
            "saved_bytes": row.raw.saturating_sub(row.delivered),
            "estimated_tokens_saved": estimate_tokens(row.raw.saturating_sub(row.delivered)),
            "saved_percent": if row.raw == 0 { 0.0 } else { 100.0 * row.raw.saturating_sub(row.delivered) as f64 / row.raw as f64 },
        })
    };
    json!({
        "token_estimate": "bytes/4; not provider-billed tokens",
        "total": row_json(&total),
        "by_command": by_command.iter().map(|(command, row)| (command.clone(), row_json(row))).collect::<serde_json::Map<_, _>>(),
        "recalls": recalls,
        "bypass_reasons": bypass,
        "jev": crate::accounting::summarize(receipts)
    })
}

pub fn report(store: &Store, session: Option<&str>, as_json: bool) -> Result<(), Box<dyn Error>> {
    let receipts = match session {
        Some(session) => store.receipts_for_session(session)?,
        None => store.all_receipts()?,
    };
    let summary = summarize(&receipts);
    if as_json {
        println!("{}", serde_json::to_string_pretty(&summary)?);
        return Ok(());
    }
    let total = &summary["total"];
    println!(
        "JevTO gain ({} receipts in {})",
        receipts.len(),
        store.root.display()
    );
    println!(
        "  runs: {} ({} reduced)   raw: {} B   delivered: {} B   saved: {} B ({:.1}%, ~{} tokens)",
        total["runs"],
        total["reduced_runs"],
        total["raw_bytes"],
        total["delivered_bytes"],
        total["saved_bytes"],
        total["saved_percent"].as_f64().unwrap_or_default(),
        total["estimated_tokens_saved"],
    );
    println!("  recalls: {}", summary["recalls"]);
    let jev = &summary["jev"];
    if jev["attempted_calls"].as_u64().unwrap_or(0) > 0
        || jev["cache_hits"].as_u64().unwrap_or(0) > 0
        || jev["unknown_outcomes"].as_u64().unwrap_or(0) > 0
    {
        println!(
            "  jev: {} attempted, {} successful, {} failed, {} cache hits, {} unknown outcomes; {} observed input tokens (complete={}), ${:.6} known cost (complete={}, {} unpriced attempts)",
            jev["attempted_calls"],
            jev["successful_calls"],
            jev["failed_calls"],
            jev["cache_hits"],
            jev["unknown_outcomes"],
            jev["input_tokens"],
            jev["input_tokens_complete"],
            jev["reported_cost_usd"].as_f64().unwrap_or_default(),
            jev["cost_complete"],
            jev["unpriced_attempts"]
        );
    }
    if let Some(commands) = summary["by_command"].as_object() {
        let mut rows = commands.iter().collect::<Vec<_>>();
        rows.sort_by_key(|(_, row)| std::cmp::Reverse(row["saved_bytes"].as_u64().unwrap_or(0)));
        println!(
            "  {:<24} {:>5} {:>10} {:>10} {:>7}",
            "command", "runs", "raw B", "saved B", "saved"
        );
        for (command, row) in rows.into_iter().take(15) {
            let number = |key: &str| row[key].as_u64().unwrap_or(0);
            println!(
                "  {:<24} {:>5} {:>10} {:>10} {:>6.1}%",
                command,
                number("runs"),
                number("raw_bytes"),
                number("saved_bytes"),
                row["saved_percent"].as_f64().unwrap_or_default()
            );
        }
    }
    println!("  token figures are bytes/4 estimates, not provider-billed usage");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_labels_keep_only_safe_words() {
        let label = |args: &[&str]| {
            command_label(&args.iter().map(|arg| arg.to_string()).collect::<Vec<_>>())
        };
        assert_eq!(
            label(&["C:\\bin\\cargo.exe", "test", "-p", "x"]),
            "cargo test"
        );
        assert_eq!(
            label(&["python", "-m", "pytest", "tests/"]),
            "python -m pytest"
        );
        assert_eq!(label(&["python", "secret_script.py"]), "python");
        assert_eq!(label(&["rg", "-n", "-H", "token"]), "rg");
    }

    #[test]
    fn summary_totals_bytes_and_estimated_tokens() {
        let receipt: Receipt = serde_json::from_value(json!({
            "schema_version": 1,
            "session_id": "s",
            "run_id": "00000000-0000-4000-8000-000000000000",
            "mode": "deterministic",
            "coverage": [{"tool_path": "explicit_cli_run", "captured": true, "replaced": true}],
            "raw_bytes": 4000,
            "delivered_bytes": 1000,
            "recalls": 1,
            "command": "cargo test"
        }))
        .unwrap();
        let summary = summarize(&[receipt]);
        assert_eq!(summary["total"]["saved_bytes"], 3000);
        assert_eq!(summary["total"]["estimated_tokens_saved"], 750);
        assert_eq!(summary["by_command"]["cargo test"]["runs"], 1);
        assert_eq!(summary["recalls"], 1);
    }
}
