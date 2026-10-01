//! Receipt accounting: call outcomes and optimization fallback are independent.
use jevto_core::{Mode, Receipt};
use serde_json::{json, Value};
use std::collections::BTreeMap;

pub fn summarize(receipts: &[Receipt]) -> Value {
    let (mut attempted, mut succeeded, mut failed, mut cached, mut unknown) =
        (0u64, 0u64, 0u64, 0u64, 0u64);
    let (mut calls, mut input, mut unpriced, mut missing_tokens) = (0u64, 0u64, 0u64, 0u64);
    let mut cost = 0.0;
    let mut fallbacks = BTreeMap::<String, u64>::new();
    for receipt in receipts {
        let outcome = receipt
            .extra
            .get("jev_call_outcome")
            .and_then(Value::as_str);
        let cache = outcome == Some("cache_hit")
            || receipt.extra.get("jev_cache_hit").and_then(Value::as_bool) == Some(true);
        if let Some(reason) = receipt
            .extra
            .get("adaptive_fallback")
            .and_then(Value::as_str)
        {
            *fallbacks.entry(reason.to_owned()).or_default() += 1;
        }
        if cache {
            cached += 1;
            continue;
        }
        let usage = receipt.jev_usage.as_ref();
        let attempt = matches!(outcome, Some("succeeded" | "failed"))
            || receipt
                .extra
                .get("jev_call_attempted")
                .and_then(Value::as_bool)
                == Some(true)
            || usage.is_some();
        let success = outcome == Some("succeeded") || (outcome.is_none() && usage.is_some());
        let failure = outcome == Some("failed");
        attempted += u64::from(attempt);
        succeeded += u64::from(success);
        failed += u64::from(failure);
        let legacy_adaptive = outcome.is_none()
            && (receipt.mode == Mode::Adaptive
                || receipt.extra.contains_key("requested_mode")
                || receipt.extra.contains_key("adaptive_fallback"));
        unknown += u64::from(!success && !failure && (attempt || legacy_adaptive));
        if let Some(usage) = usage {
            calls += 1; // Compatibility: legacy `calls` counted usage-bearing responses.
            input += usage.total_input_tokens.unwrap_or(0);
        }
        if attempt {
            missing_tokens += u64::from(usage.and_then(|u| u.total_input_tokens).is_none());
            match usage.and_then(|u| {
                u.billed_cost
                    .filter(|c| c.is_finite() && *c >= 0.0 && u.currency.as_deref() == Some("USD"))
            }) {
                Some(value) => cost += value,
                None => unpriced += 1,
            }
        }
    }
    json!({
        "accounting_version": 1,
        "calls": calls,
        "calls_semantics": "legacy_usage_bearing_responses; use attempted_calls for requests",
        "attempted_calls": attempted, "successful_calls": succeeded, "failed_calls": failed,
        "cache_hits": cached, "unknown_outcomes": unknown,
        "input_tokens": input, "input_tokens_complete": missing_tokens == 0 && unknown == 0,
        "reported_cost_usd": cost, "cost_usd": cost,
        "unpriced_attempts": unpriced, "cost_complete": unpriced == 0 && unknown == 0,
        "fallbacks": fallbacks,
    })
}
