use crate::mcp_run::{self, RunPolicy};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use jevto_core::{review_git, Mode, Store, Stream};
use serde_json::{json, Value};
use std::io::{self, BufRead, Write};

pub fn serve(store: &Store, run_policy: Option<&RunPolicy>) -> io::Result<()> {
    let input = io::stdin();
    let mut output = io::stdout().lock();
    for line in input.lock().lines() {
        let line = line?;
        let request: Value = match serde_json::from_str(&line) {
            Ok(value) => value,
            Err(_) => {
                writeln!(
                    output,
                    "{}",
                    json!({"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error"}})
                )?;
                output.flush()?;
                continue;
            }
        };
        if let Some(response) = handle_request(store, run_policy, &request) {
            writeln!(output, "{response}")?;
            output.flush()?;
        }
    }
    Ok(())
}

fn result(id: &Value, value: Value) -> Value {
    json!({"jsonrpc":"2.0","id":id,"result":value})
}
fn error(id: &Value, code: i32, message: &str) -> Value {
    json!({"jsonrpc":"2.0","id":id,"error":{"code":code,"message":message}})
}
fn tool_error(message: String) -> Value {
    json!({"content":[{"type":"text","text":message}],"isError":true})
}

fn handle_request(store: &Store, run_policy: Option<&RunPolicy>, request: &Value) -> Option<Value> {
    let id = request.get("id")?;
    let method = request.get("method")?.as_str()?;
    let params = request.get("params").unwrap_or(&Value::Null);
    Some(match method {
        "initialize" => result(
            id,
            json!({"protocolVersion":"2025-11-25","capabilities":{"tools":{}},"serverInfo":{"name":"jevto","version":env!("CARGO_PKG_VERSION")}}),
        ),
        "ping" => result(id, json!({})),
        "tools/list" => {
            let mut tools = json!([
                {"name":"jevto_recall","description":"Return exact historical captured bytes and record a local recall receipt. The structured result includes base64 for byte fidelity.","annotations":{"readOnlyHint":false,"destructiveHint":false,"openWorldHint":false},"inputSchema":{"type":"object","properties":{"capture_id":{"type":"string"},"section_id":{"type":"string"},"stream":{"type":"string","enum":["stdout","stderr"]},"byte_start":{"type":"integer","minimum":0},"byte_end":{"type":"integer","minimum":0},"full":{"type":"boolean"}},"required":["capture_id"]}},
                {"name":"jevto_review","description":"Read-only advisory review of the current Git workspace. Findings are not verification results.","annotations":{"readOnlyHint":true,"openWorldHint":false},"inputSchema":{"type":"object","properties":{"base_ref":{"type":"string"},"task_revision":{"type":"integer","minimum":0},"workspace_id":{"type":"string"}}}},
                {"name":"jevto_status","description":"Report local mode, coverage counts, and per-run receipt details for sessions with up to eight runs.","annotations":{"readOnlyHint":true,"openWorldHint":false},"inputSchema":{"type":"object","properties":{"session_id":{"type":"string"}},"required":["session_id"]}}
            ]).as_array().cloned().expect("static tool list");
            if let Some(policy) = run_policy {
                tools.push(mcp_run::tool_definition(policy));
            }
            result(id, json!({"tools":tools}))
        }
        "tools/call" => {
            let name = params.get("name").and_then(Value::as_str).unwrap_or("");
            let args = params.get("arguments").unwrap_or(&Value::Null);
            let response = match name {
                "jevto_recall" => recall(store, args),
                "jevto_review" => review(args),
                "jevto_status" => status(store, args),
                "jevto_run" => run_policy.map_or_else(
                    || tool_error("jevto_run is disabled; command was not run".into()),
                    |policy| mcp_run::call(store, policy, args),
                ),
                _ => tool_error(format!("unknown_tool: {name}")),
            };
            result(id, response)
        }
        _ => error(id, -32601, "Method not found"),
    })
}

fn recall(store: &Store, args: &Value) -> Value {
    let Some(id) = args.get("capture_id").and_then(Value::as_str) else {
        return tool_error("missing capture_id".into());
    };
    let resolved = match store.resolve_capture_id(id) {
        Ok(resolved) => resolved,
        Err(error) => return tool_error(format!("recall unavailable: {error}")),
    };
    let id = resolved.as_str();
    let full = args.get("full").and_then(Value::as_bool).unwrap_or(false);
    let selection = if let Some(section) = args.get("section_id").and_then(Value::as_str) {
        store
            .find_section(id, section)
            .and_then(|(stream, start, end)| {
                store
                    .recall(id, stream.clone(), Some((start, end)), false)
                    .map(|bytes| (stream, bytes))
            })
    } else {
        let stream = match args
            .get("stream")
            .and_then(Value::as_str)
            .unwrap_or("stdout")
        {
            "stdout" => Stream::Stdout,
            "stderr" => Stream::Stderr,
            _ => return tool_error("invalid stream".into()),
        };
        let range = match (
            args.get("byte_start").and_then(Value::as_u64),
            args.get("byte_end").and_then(Value::as_u64),
        ) {
            (Some(start), Some(end)) => Some((start, end)),
            (None, None) => None,
            _ => return tool_error("byte_start and byte_end must be supplied together".into()),
        };
        store
            .recall(id, stream.clone(), range, full)
            .map(|bytes| (stream, bytes))
    };
    match selection {
        Ok((stream, bytes)) => {
            if let Err(error) = store.record_recall(id, bytes.len()) {
                eprintln!("jevto: recall receipt unavailable ({error})");
            }
            let base64 = STANDARD.encode(&bytes);
            let text = match std::str::from_utf8(&bytes) {
                Ok(text) => text.to_owned(),
                Err(_) => format!(
                    "{} bytes of binary evidence; use structuredContent.base64",
                    bytes.len()
                ),
            };
            json!({"content":[{"type":"text","text":text}],"structuredContent":{"capture_id":id,"stream":stream,"byte_length":bytes.len(),"base64":base64},"isError":false})
        }
        Err(error) => tool_error(error.to_string()),
    }
}

fn review(args: &Value) -> Value {
    let base = args
        .get("base_ref")
        .and_then(Value::as_str)
        .unwrap_or("HEAD");
    let revision = args
        .get("task_revision")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let workspace = args
        .get("workspace_id")
        .and_then(Value::as_str)
        .unwrap_or("mcp-workspace");
    let root = match std::env::current_dir() {
        Ok(path) => path,
        Err(error) => return tool_error(error.to_string()),
    };
    match review_git(&root, workspace, revision, base) {
        Ok(advice) => {
            json!({"content":[{"type":"text","text":format!("Advisory diff review: {} findings; {} paths unreviewed. Candidate {}", advice.findings.len(), advice.unreviewed_paths.len(), advice.candidate_sha256)}],"structuredContent":advice,"isError":false})
        }
        Err(error) => tool_error(error.to_string()),
    }
}

fn status(store: &Store, args: &Value) -> Value {
    let Some(session) = args.get("session_id").and_then(Value::as_str) else {
        return tool_error("missing session_id".into());
    };
    match store.receipts_for_session(session) {
        Ok(receipts) => {
            let raw: u64 = receipts.iter().map(|r| r.raw_bytes).sum();
            let delivered: u64 = receipts.iter().map(|r| r.delivered_bytes).sum();
            let adaptive_runs = receipts.iter().filter(|r| r.mode == Mode::Adaptive).count();
            let captured_runs = receipts
                .iter()
                .filter(|r| r.coverage.iter().any(|coverage| coverage.captured))
                .count();
            let replaced_runs = receipts
                .iter()
                .filter(|r| r.coverage.iter().any(|coverage| coverage.replaced))
                .count();
            let bypassed_runs = receipts
                .iter()
                .filter(|r| {
                    r.coverage
                        .iter()
                        .any(|coverage| coverage.bypass_reason.is_some())
                })
                .count();
            let details_omitted = receipts.len() > 8;
            let run_details = if details_omitted {
                Vec::new()
            } else {
                receipts
                    .iter()
                    .map(|receipt| {
                        json!({
                            "run_id": receipt.run_id,
                            "mode": receipt.mode,
                            "coverage": receipt.coverage,
                            "raw_bytes": receipt.raw_bytes,
                            "delivered_bytes": receipt.delivered_bytes,
                            "child_exit": receipt.extra.get("child_exit"),
                        })
                    })
                    .collect::<Vec<_>>()
            };
            let mode = match receipts.first() {
                None => "none",
                Some(first) if receipts.iter().all(|receipt| receipt.mode == first.mode) => {
                    match first.mode {
                        Mode::Passthrough => "passthrough",
                        Mode::Deterministic => "deterministic",
                        Mode::Adaptive => "adaptive",
                    }
                }
                Some(_) => "mixed",
            };
            // Some MCP hosts expose only text content to the model. Keep the
            // same bounded facts visible there as in structuredContent.
            let mut text = format!(
                "{mode} observed mode; {} explicit runs ({} captured, {} replaced, {} bypassed); {raw} raw bytes, {delivered} delivered bytes; coding-provider usage unknown; no native host interception",
                receipts.len(), captured_runs, replaced_runs, bypassed_runs
            );
            if details_omitted {
                text.push_str(
                    "\nPer-run details omitted because this session has more than 8 runs.",
                );
            } else {
                for (index, receipt) in receipts.iter().enumerate() {
                    let run_mode = match receipt.mode {
                        Mode::Passthrough => "passthrough",
                        Mode::Deterministic => "deterministic",
                        Mode::Adaptive => "adaptive",
                    };
                    let child_exit = receipt
                        .extra
                        .get("child_exit")
                        .and_then(Value::as_i64)
                        .map_or_else(|| "unknown".to_string(), |code| code.to_string());
                    let bypass_reasons = receipt
                        .coverage
                        .iter()
                        .filter_map(|coverage| coverage.bypass_reason.as_deref())
                        .collect::<Vec<_>>();
                    let bypass = if bypass_reasons.is_empty() {
                        "none".to_string()
                    } else {
                        bypass_reasons.join(", ")
                    };
                    text.push_str(&format!(
                        "\nrun {}: mode={run_mode}, raw_bytes={}, delivered_bytes={}, child_exit={child_exit}, bypass_reason={bypass}",
                        index + 1,
                        receipt.raw_bytes,
                        receipt.delivered_bytes
                    ));
                }
            }
            json!({"content":[{"type":"text","text":text}],"structuredContent":{"session_id":session,"mode":mode,"runs":receipts.len(),"adaptive_runs":adaptive_runs,"captured_runs":captured_runs,"replaced_runs":replaced_runs,"bypassed_runs":bypassed_runs,"run_details":run_details,"details_omitted":details_omitted,"raw_bytes":raw,"delivered_bytes":delivered,"provider_usage":null,"native_host_interception":false},"isError":false})
        }
        Err(error) => tool_error(error.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mcp_exposes_only_read_only_tools() {
        let store = Store::with_limits(
            tempfile::tempdir().unwrap().path().to_path_buf(),
            1024 * 1024,
            24,
        );
        let listed = handle_request(
            &store,
            None,
            &json!({"jsonrpc":"2.0","id":1,"method":"tools/list"}),
        )
        .unwrap();
        let tools = listed["result"]["tools"].as_array().unwrap();
        assert_eq!(tools.len(), 3);
        assert!(!tools.iter().any(|tool| tool["name"] == "jevto_run"));
        assert_eq!(tools[0]["annotations"]["readOnlyHint"], false);
        assert_eq!(tools[0]["annotations"]["destructiveHint"], false);
        assert_eq!(tools[1]["annotations"]["readOnlyHint"], true);
        assert_eq!(tools[2]["annotations"]["readOnlyHint"], true);
    }

    #[test]
    fn mcp_recall_returns_exact_bytes_in_base64() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::with_limits(dir.path().to_path_buf(), 1024 * 1024, 24);
        let record = store
            .capture(
                "s",
                "w",
                "run",
                None,
                b"a\r\nb",
                b"",
                jevto_core::Completeness::Complete,
                jevto_core::ProcessStatus::Completed,
                Some(0),
            )
            .unwrap();
        let response = handle_request(&store, None, &json!({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"jevto_recall","arguments":{"capture_id":record.capture_id,"full":true}}})).unwrap();
        let bytes = STANDARD
            .decode(
                response["result"]["structuredContent"]["base64"]
                    .as_str()
                    .unwrap(),
            )
            .unwrap();
        assert_eq!(bytes, b"a\r\nb");
    }

    #[test]
    fn mcp_status_reports_none_passthrough_and_mixed_modes() {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::with_limits(dir.path().to_path_buf(), 1024 * 1024, 24);
        let request = json!({"session_id":"status-test"});
        assert_eq!(
            status(&store, &request)["structuredContent"]["mode"],
            "none"
        );
        for (mode, expected) in [
            (Mode::Passthrough, "passthrough"),
            (Mode::Deterministic, "mixed"),
        ] {
            let captured = mode == Mode::Deterministic;
            let receipt = jevto_core::Receipt {
                schema_version: jevto_core::SCHEMA_VERSION,
                session_id: "status-test".into(),
                run_id: uuid::Uuid::new_v4().to_string(),
                mode,
                coverage: vec![jevto_core::Coverage {
                    tool_path: "explicit_cli_run".into(),
                    captured,
                    replaced: false,
                    bypass_reason: (!captured).then(|| "capture_limit_exceeded".into()),
                }],
                raw_bytes: if captured { 41 } else { 84 },
                delivered_bytes: if captured { 12 } else { 84 },
                recalls: 0,
                provider_usage: None,
                jev_usage: None,
                verification: None,
                extra: [("child_exit".into(), json!(if captured { 0 } else { 7 }))]
                    .into_iter()
                    .collect(),
            };
            store.save_receipt(&receipt).unwrap();
            let result = status(&store, &request);
            let detail = result["structuredContent"]["run_details"]
                .as_array()
                .unwrap()
                .iter()
                .find(|detail| detail["run_id"] == receipt.run_id)
                .unwrap();
            assert_eq!(result["structuredContent"]["mode"], expected);
            assert_eq!(detail["coverage"][0]["captured"], captured);
            assert_eq!(detail["child_exit"], if captured { 0 } else { 7 });
            let text = result["content"][0]["text"].as_str().unwrap();
            if captured {
                assert!(text.contains("raw_bytes=41, delivered_bytes=12, child_exit=0"));
            } else {
                assert!(text.contains("raw_bytes=84, delivered_bytes=84, child_exit=7"));
                assert!(text.contains("bypass_reason=capture_limit_exceeded"));
            }
        }
        let result = status(&store, &request);
        assert_eq!(result["structuredContent"]["captured_runs"], 1);
        assert_eq!(result["structuredContent"]["bypassed_runs"], 1);
        assert_eq!(result["structuredContent"]["details_omitted"], false);
        let mut crowded_receipt = store
            .receipts_for_session("status-test")
            .unwrap()
            .pop()
            .unwrap();
        for _ in 0..7 {
            crowded_receipt.run_id = uuid::Uuid::new_v4().to_string();
            store.save_receipt(&crowded_receipt).unwrap();
        }
        let crowded = status(&store, &request);
        assert_eq!(crowded["structuredContent"]["runs"], 9);
        assert_eq!(crowded["structuredContent"]["details_omitted"], true);
        assert_eq!(crowded["structuredContent"]["run_details"], json!([]));
        assert!(crowded["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("Per-run details omitted"));
    }
}
