use jevto_core::{
    make_pack, render_pack, Completeness, Coverage, Mode, PackCoverage, ProcessStatus, Receipt,
    Store, SCHEMA_VERSION,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::error::Error;
use std::io::{self, Read, Write};
use uuid::Uuid;

const MAX_HOOK_INPUT_BYTES: u64 = 1024 * 1024;
const MAX_RESPONSE_BYTES: usize = 128 * 1024;
const PACK_BUDGET_BYTES: usize = 64 * 1024;

pub fn serve(store: &Store) -> Result<(), Box<dyn Error>> {
    let mut input = Vec::new();
    io::stdin()
        .take(MAX_HOOK_INPUT_BYTES + 1)
        .read_to_end(&mut input)?;
    if input.len() as u64 > MAX_HOOK_INPUT_BYTES {
        return Ok(());
    }
    let Ok(event) = serde_json::from_slice::<Value>(&input) else {
        return Ok(());
    };
    match process_event(store, &event) {
        Ok(Some(replacement)) => {
            io::stdout().write_all(serde_json::to_string(&replacement)?.as_bytes())?;
            io::stdout().write_all(b"\n")?;
        }
        Ok(None) => {}
        Err(error) => eprintln!("jevto: Codex hook bypassed ({error})"),
    }
    Ok(())
}

fn eligible(event: &Value) -> Option<(&str, &str, &str)> {
    if event.get("hook_event_name")?.as_str()? != "PostToolUse"
        || event.get("tool_name")?.as_str()? != "Bash"
    {
        return None;
    }
    let session = event.get("session_id")?.as_str()?;
    let cwd = event.get("cwd")?.as_str()?;
    let command = event.pointer("/tool_input/command")?.as_str()?;
    let response = event.get("tool_response")?.as_str()?;
    if session.is_empty()
        || cwd.is_empty()
        || command.to_ascii_lowercase().contains("jevto")
        || response.len() > MAX_RESPONSE_BYTES
    {
        return None;
    }
    let lower = response.to_ascii_lowercase();
    if lower.contains("truncated output")
        || lower.contains("output truncated")
        || lower.contains("... truncated")
        || !response
            .lines()
            .any(|line| line.starts_with("test result: ok."))
        || response.lines().any(|line| {
            let line = line.to_ascii_lowercase();
            line.contains(" ... failed")
                || line.contains("assertion failed")
                || line.starts_with("error:")
        })
    {
        return None;
    }
    Some((session, cwd, response))
}

fn process_event(store: &Store, event: &Value) -> Result<Option<Value>, Box<dyn Error>> {
    let Some((session, cwd, response)) = eligible(event) else {
        return Ok(None);
    };
    let response_bytes = response.as_bytes();
    let workspace_id = hex::encode(Sha256::digest(cwd.as_bytes()));
    let capture = store.capture(
        session,
        &workspace_id,
        "codex_post_tool_use",
        Some("Codex Bash output [command redacted]".into()),
        response_bytes,
        b"",
        Completeness::Complete,
        ProcessStatus::Unknown,
        None,
    )?;
    let decision = make_pack(
        &capture,
        response_bytes,
        b"",
        None,
        Mode::Deterministic,
        PACK_BUDGET_BYTES,
    );
    let Some(pack) = decision.pack else {
        return Ok(None);
    };
    if pack.coverage != PackCoverage::Complete {
        return Ok(None);
    }
    store.save_pack(&pack)?;
    let rendered = render_pack(&capture, &pack);
    let receipt = Receipt {
        schema_version: SCHEMA_VERSION,
        session_id: session.into(),
        run_id: Uuid::new_v4().to_string(),
        mode: Mode::Deterministic,
        coverage: vec![Coverage {
            tool_path: "codex_post_tool_use".into(),
            captured: true,
            replaced: false,
            bypass_reason: None,
        }],
        raw_bytes: response_bytes.len() as u64,
        delivered_bytes: rendered.len() as u64,
        recalls: 0,
        provider_usage: None,
        jev_usage: None,
        verification: None,
        extra: [
            ("capture_id".into(), json!(capture.capture_id)),
            ("capture_scope".into(), json!("codex_hook_visible_text")),
            ("replacement_requested".into(), json!(true)),
            ("host_delivery_confirmed".into(), json!(false)),
        ]
        .into_iter()
        .collect(),
    };
    store.save_receipt(&receipt)?;
    Ok(Some(json!({"continue":false,"stopReason":rendered})))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> (tempfile::TempDir, Store) {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::with_limits(temp.path().join("store"), 1024 * 1024, 24);
        (temp, store)
    }

    fn event(output: &str) -> Value {
        json!({
            "hook_event_name":"PostToolUse",
            "tool_name":"Bash",
            "session_id":"codex-session",
            "cwd":"C:\\fixture",
            "tool_input":{"command":"python noisy.py"},
            "tool_response":output
        })
    }

    #[test]
    fn successful_host_result_is_shorter_and_exactly_recallable() {
        let (_temp, store) = fixture();
        let raw = (0..80)
            .map(|case| format!("test suite::case_{case} ... ok\r\n"))
            .collect::<String>()
            + "test result: ok. 80 passed; 0 failed\r\n";
        let replacement = process_event(&store, &event(&raw)).unwrap().unwrap();
        let text = replacement["stopReason"].as_str().unwrap();
        assert_eq!(replacement["continue"], false);
        assert!(text.contains("80 lines of successful test inventory"));
        assert!(text.contains("test result: ok. 80 passed; 0 failed"));
        assert!(text.len() < raw.len());
        let receipts = store.receipts_for_session("codex-session").unwrap();
        assert_eq!(receipts.len(), 1);
        assert_eq!(receipts[0].coverage[0].tool_path, "codex_post_tool_use");
        assert!(!receipts[0].coverage[0].replaced);
        assert_eq!(receipts[0].extra["replacement_requested"], json!(true));
        assert_eq!(receipts[0].extra["host_delivery_confirmed"], json!(false));
        let capture_id = receipts[0].extra["capture_id"].as_str().unwrap();
        assert_eq!(
            store
                .recall(capture_id, jevto_core::Stream::Stdout, None, true)
                .unwrap(),
            raw.as_bytes()
        );
    }

    #[test]
    fn unknown_failure_truncation_and_existing_wrapper_leave_host_result_untouched() {
        let (_temp, store) = fixture();
        for (command, output) in [
            ("python noisy.py", "test suite::critical ... FAILED\n"),
            (
                "python noisy.py",
                "Warning: truncated output\ntest result: ok. 80 passed; 0 failed",
            ),
            (
                "jevto run -- python noisy.py",
                "test result: ok. 80 passed; 0 failed",
            ),
        ] {
            let mut input = event(output);
            input["tool_input"]["command"] = json!(command);
            assert!(process_event(&store, &input).unwrap().is_none());
        }
        assert!(store
            .receipts_for_session("codex-session")
            .unwrap()
            .is_empty());
    }
}
