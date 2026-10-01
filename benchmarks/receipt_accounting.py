"""Observed Jev request accounting; do not infer free calls from missing usage."""
import math


def summarize(receipts):
    result = {key: 0 for key in ("calls", "attempted_calls", "successful_calls", "failed_calls", "cache_hits", "unknown_outcomes", "input_tokens", "unpriced_attempts")}
    result.update(accounting_version=1, calls_semantics="legacy_usage_bearing_responses; use attempted_calls for requests", cost_usd=0.0, fallbacks={})
    missing_tokens = 0
    for receipt in receipts:
        outcome = receipt.get("jev_call_outcome")
        reason = receipt.get("adaptive_fallback")
        if reason:
            result["fallbacks"][reason] = result["fallbacks"].get(reason, 0) + 1
        if outcome == "cache_hit" or receipt.get("jev_cache_hit") is True:
            result["cache_hits"] += 1
            continue
        usage = receipt.get("jev_usage")
        attempt = outcome in ("succeeded", "failed") or receipt.get("jev_call_attempted") is True or usage is not None
        success = outcome == "succeeded" or (outcome is None and usage is not None)
        failure = outcome == "failed"
        result["attempted_calls"] += int(attempt)
        result["successful_calls"] += int(success)
        result["failed_calls"] += int(failure)
        legacy_adaptive = outcome is None and (receipt.get("mode") == "adaptive" or "requested_mode" in receipt or "adaptive_fallback" in receipt)
        result["unknown_outcomes"] += int(not success and not failure and (attempt or legacy_adaptive))
        if usage is not None:
            result["calls"] += 1
            result["input_tokens"] += usage.get("total_input_tokens") or 0
        if attempt:
            usage = usage or {}
            missing_tokens += int(usage.get("total_input_tokens") is None)
            cost = usage.get("billed_cost")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0 and usage.get("currency") == "USD":
                result["cost_usd"] += cost
            else:
                result["unpriced_attempts"] += 1
    result["reported_cost_usd"] = result["cost_usd"]
    result["cost_complete"] = result["unpriced_attempts"] == 0 and result["unknown_outcomes"] == 0
    result["input_tokens_complete"] = missing_tokens == 0 and result["unknown_outcomes"] == 0
    return result
