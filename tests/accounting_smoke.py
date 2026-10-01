"""Offline regressions for truthful diagnostics and receipt accounting."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))
from token_bench import receipt_jev

EXE = ROOT / "target" / "debug" / ("jevto.exe" if os.name == "nt" else "jevto")


class AccountingTests(unittest.TestCase):
    def summarize(self, *receipts):
        with tempfile.TemporaryDirectory(prefix="jevto-accounting-") as directory:
            store = Path(directory)
            (store / "receipts").mkdir()
            for index, receipt in enumerate(receipts):
                (store / "receipts" / f"{index}.json").write_text(json.dumps(receipt))
            return receipt_jev(store)

    def test_failed_attempt_is_counted_and_unpriced(self):
        result = self.summarize({"jev_call_attempted": True, "jev_call_outcome": "failed", "adaptive_fallback": "jev_http_failed"})
        self.assertEqual(result.get("attempted_calls"), 1)
        self.assertEqual(result.get("failed_calls"), 1)
        self.assertEqual(result.get("unpriced_attempts"), 1)
        self.assertFalse(result.get("cost_complete", True))

    def test_successful_no_evidence_response_keeps_cost_and_fallback(self):
        result = self.summarize({"jev_call_attempted": True, "jev_call_outcome": "succeeded", "adaptive_fallback": "jev_no_relevant_evidence", "jev_usage": {"total_input_tokens": 400, "billed_cost": 0.001, "currency": "USD"}})
        self.assertEqual(result.get("successful_calls"), 1)
        self.assertEqual(result.get("fallbacks"), {"jev_no_relevant_evidence": 1})
        self.assertEqual(result["cost_usd"], 0.001)
        self.assertTrue(result.get("cost_complete"))

    def test_cache_never_rebills_historical_usage(self):
        result = self.summarize({"jev_cache_hit": True, "jev_call_outcome": "cache_hit", "jev_usage": {"total_input_tokens": 400, "billed_cost": 0.001}})
        self.assertEqual(result["cache_hits"], 1)
        self.assertEqual(result["cost_usd"], 0)
        self.assertEqual(result["input_tokens"], 0)
        self.assertEqual(result.get("attempted_calls"), 0)

    def test_legacy_attempt_without_result_stays_unknown(self):
        result = self.summarize({"jev_call_attempted": True, "adaptive_fallback": "jev_no_relevant_evidence"})
        self.assertEqual(result.get("attempted_calls"), 1)
        self.assertEqual(result.get("unknown_outcomes"), 1)
        self.assertEqual(result.get("failed_calls"), 0)

    def test_preflight_fallback_has_no_attempt(self):
        result = self.summarize({"jev_call_outcome": "not_attempted", "adaptive_fallback": "jev_cache_invalid"})
        self.assertEqual(result.get("attempted_calls"), 0)
        self.assertEqual(result.get("unknown_outcomes"), 0)
        self.assertEqual(result.get("fallbacks"), {"jev_cache_invalid": 1})

    def test_multiple_fallbacks_are_not_overwritten(self):
        result = self.summarize(*[{"jev_call_outcome": "not_attempted", "adaptive_fallback": reason} for reason in ["missing_jev_key", "jev_cache_invalid", "missing_jev_key"]])
        self.assertEqual(result.get("fallbacks"), {"missing_jev_key": 2, "jev_cache_invalid": 1})

    def test_python_and_cli_aggregation_agree_for_mixed_receipts(self):
        records = [
            {"jev_call_outcome": "succeeded", "jev_usage": {"total_input_tokens": 400, "billed_cost": 0.001, "currency": "USD"}, "adaptive_fallback": "jev_no_relevant_evidence"},
            {"jev_call_outcome": "failed", "jev_call_attempted": True, "adaptive_fallback": "jev_http_failed"},
            {"jev_call_outcome": "cache_hit", "jev_cache_hit": True, "jev_usage": {"total_input_tokens": 400, "billed_cost": 0.001, "currency": "USD"}},
            {"jev_call_attempted": True, "adaptive_fallback": "legacy_unknown"},
            {"jev_call_outcome": "not_attempted", "adaptive_fallback": "jev_cache_invalid"},
        ]
        with tempfile.TemporaryDirectory(prefix="jevto-mixed-accounting-") as directory:
            root = Path(directory)
            (root / "receipts").mkdir()
            for index, item in enumerate(records):
                receipt = {"schema_version": 1, "session_id": "mixed", "run_id": f"00000000-0000-4000-8000-{index:012d}", "mode": "deterministic", "coverage": [], "raw_bytes": 0, "delivered_bytes": 0, "recalls": 0, **item}
                if "jev_usage" in receipt:
                    receipt["jev_usage"] = {"provider": "fixture", "model": "fixture", "source": "fixture", **receipt["jev_usage"]}
                (root / "receipts" / f"{index}.json").write_text(json.dumps(receipt))
            expected = receipt_jev(root)
            run = subprocess.run([str(EXE), "--store-dir", str(root), "gain", "--json"], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            actual = json.loads(run.stdout)["jev"]
            for key in actual:
                self.assertEqual(expected[key], actual[key], key)


class DoctorTests(unittest.TestCase):
    def doctor(self, mode=None, key=""):
        with tempfile.TemporaryDirectory(prefix="jevto-doctor-") as directory:
            env = os.environ.copy()
            env.pop("JEVTO_MODE", None)
            env["OPENROUTER_API_KEY"] = key
            env["JEVTO_STORE_DIR"] = directory
            if mode is not None:
                env["JEVTO_MODE"] = mode
            return subprocess.run([str(EXE), "doctor", "--json"], env=env, capture_output=True, text=True)

    def test_default_auto_without_key_is_local(self):
        result = self.doctor()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["default_mode"], "auto")
        self.assertEqual(data.get("effective_mode"), "deterministic")
        self.assertEqual(data["network_default"], "off")

    def test_auto_with_key_reports_conditional_network_without_key_value(self):
        sentinel = "fabricated-credential-must-not-appear"
        result = self.doctor(key=sentinel)
        data = json.loads(result.stdout)
        self.assertEqual(data.get("effective_mode"), "adaptive")
        self.assertEqual(data["network_default"], "conditional")
        self.assertNotIn(sentinel, result.stdout + result.stderr)

    def test_rules_override_and_alias_are_reported(self):
        result = self.doctor(mode="rules", key="fabricated-unused")
        data = json.loads(result.stdout)
        self.assertEqual(data.get("configured_mode"), "deterministic")
        self.assertEqual(data.get("mode_source"), "JEVTO_MODE")
        self.assertEqual(data["network_default"], "off")

    def test_aliases_passthrough_and_invalid_mode(self):
        for mode, effective in [("jev", "adaptive"), ("adaptive", "adaptive"), ("passthrough", "passthrough"), ("deterministic", "deterministic")]:
            with self.subTest(mode=mode):
                run = self.doctor(mode=mode)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(json.loads(run.stdout)["effective_mode"], effective)
                self.assertEqual(json.loads(run.stdout)["network_default"], "off")
        self.assertEqual(self.doctor(mode="not-a-mode").returncode, 2)


class ResponseTests(unittest.TestCase):
    def exercise(self, outcome="selected", repeat=False):
        with tempfile.TemporaryDirectory(prefix="jevto-response-") as directory:
            root = Path(directory)
            store = root / "store"
            script = root / "emit.py"
            script.write_text("words = ['alpha','beta','gamma','delta','omega','sigma','kappa']\nfor n in range(600):\n print(f'{words[n%7]} {words[(n//7)%7]} {words[(n//49)%7]}: processed record {n}')\n")
            task = root / "task.json"
            task.write_text(json.dumps({"schema_version": 1, "session_id": "response", "workspace_id": "offline", "revision": 0, "goal": "Why did the importer skip the malformed record?", "trusted_constraints": []}))
            policy = root / "policy.json"
            policy.write_text(json.dumps({"schema_version": 1, "data_classification": "synthetic", "allow_output_snippets": True, "allowed_roots": [str(root)]}))
            calls = []

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    calls.append(request)
                    ids = [section["id"] for section in request["state"]["sections"]]
                    response = {"model": "typesafe/jev-1.13-20260917", "answers": {
                        "where": {"type": "choice", "choice": ids[0], "probabilities": {key: int(key == ids[0]) for key in ids}},
                        "exists": {"type": "noul", "noul": 0.1 if outcome in ("no_evidence", "invalid_no_evidence") else 0.99},
                        "need": {"type": "score", "score": 1, "probabilities": {str(i): int(i == 1) for i in range(5)}}},
                        "usage": {"input_tokens": 400, "output_tokens": 30, "cost": 0.001}}
                    if outcome == "bad_answer":
                        response["answers"]["where"]["choice"] = "unknown-section"
                    if outcome == "partial_usage":
                        del response["usage"]["output_tokens"]
                    if outcome == "wrong_model":
                        response["model"] = "unrecognized-model"
                    if outcome == "invalid_no_evidence":
                        del response["answers"]["need"]
                    if outcome == "cache_write_failure":
                        (store / "jev-cache").write_text("block cache write after the request")
                    body = b"not-json" if outcome == "malformed" else json.dumps(response).encode()
                    self.send_response(503 if outcome == "http_failure" else 200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = {key: value for key, value in os.environ.items() if not key.startswith("JEVTO_") and key != "OPENROUTER_API_KEY"}
                env.update(OPENROUTER_API_KEY="fabricated-local-only", JEVTO_JEV_ENDPOINT=f"http://127.0.0.1:{server.server_port}/", PYTHONDONTWRITEBYTECODE="1")
                argv = [str(EXE), "--store-dir", str(store), "run", "--mode", "adaptive", "--task-file", str(task), "--remote-policy", str(policy), "--allow-remote-jev", "--", sys.executable, str(script)]
                for index in range(2 if repeat or outcome == "invalid_cache" else 1):
                    result = subprocess.run(argv, cwd=root, env=env, capture_output=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if outcome == "invalid_cache" and index == 0:
                        next((store / "jev-cache").glob("*.json")).write_text("invalid JSON")
                receipts = [json.loads(path.read_text()) for path in (store / "receipts").glob("*.json")]
                self.assertEqual(len(calls), 1, "fixture must reach the local fake response exactly once")
                return receipts
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_no_evidence_response_is_successful_and_priced(self):
        receipt = self.exercise("no_evidence")[0]
        self.assertEqual(receipt.get("jev_usage", {}).get("billed_cost"), 0.001)
        self.assertEqual(receipt.get("jev_call_outcome"), "succeeded")
        self.assertEqual(receipt["adaptive_fallback"], "jev_no_relevant_evidence")

    def test_cache_write_failure_preserves_successful_call_and_cost(self):
        receipt = self.exercise("cache_write_failure")[0]
        self.assertEqual(receipt.get("jev_call_outcome"), "succeeded")
        self.assertEqual(receipt.get("jev_usage", {}).get("billed_cost"), 0.001)
        self.assertEqual(receipt["adaptive_fallback"], "jev_cache_write_failed")

    def test_bad_answer_preserves_observed_usage(self):
        receipt = self.exercise("bad_answer")[0]
        self.assertEqual(receipt.get("jev_call_outcome"), "failed")
        self.assertEqual(receipt.get("jev_usage", {}).get("billed_cost"), 0.001)

    def test_malformed_and_http_failures_are_unpriced_attempts(self):
        for outcome in ["malformed", "http_failure"]:
            with self.subTest(outcome=outcome):
                receipt = self.exercise(outcome)[0]
                self.assertTrue(receipt.get("jev_call_attempted"))
                self.assertEqual(receipt.get("jev_call_outcome"), "failed")
                self.assertIsNone(receipt.get("jev_usage"))

    def test_repeated_decision_uses_cache_without_a_second_charge(self):
        receipts = self.exercise(repeat=True)
        self.assertEqual(sorted(r.get("jev_call_outcome", "missing") for r in receipts), ["cache_hit", "succeeded"])
        self.assertEqual(sum(r.get("jev_usage", {}).get("billed_cost", 0) for r in receipts), 0.001)

    def test_partial_usage_and_model_mismatch_keep_known_accounting(self):
        for outcome in ["partial_usage", "wrong_model"]:
            with self.subTest(outcome=outcome):
                receipt = self.exercise(outcome)[0]
                self.assertEqual(receipt.get("jev_call_outcome"), "failed")
                self.assertEqual(receipt.get("jev_usage", {}).get("billed_cost"), 0.001)
                self.assertEqual(receipt["jev_usage"]["total_input_tokens"], 400)

    def test_invalid_no_evidence_answer_is_failed_not_successful(self):
        receipt = self.exercise("invalid_no_evidence")[0]
        self.assertEqual(receipt.get("jev_call_outcome"), "failed")
        self.assertEqual(receipt.get("jev_usage", {}).get("billed_cost"), 0.001)

    def test_capture_limit_is_known_zero_attempts(self):
        with tempfile.TemporaryDirectory(prefix="jevto-limit-accounting-") as directory:
            env = os.environ.copy()
            env.update(OPENROUTER_API_KEY="", JEVTO_MAX_CAPTURE_BYTES="128", JEVTO_STORE_DIR=directory)
            result = subprocess.run([str(EXE), "run", "--mode", "adaptive", "--", sys.executable, "-c", "print('x'*256)"], env=env, capture_output=True)
            self.assertEqual(result.returncode, 0)
            receipt = json.loads(next((Path(directory) / "receipts").glob("*.json")).read_text())
            self.assertEqual(receipt.get("jev_call_outcome"), "not_attempted")

    def test_invalid_cache_falls_back_without_an_extra_request(self):
        receipts = self.exercise("invalid_cache")
        self.assertEqual(sorted(r.get("jev_call_outcome", "missing") for r in receipts), ["not_attempted", "succeeded"])
        invalid = next(r for r in receipts if r.get("adaptive_fallback") == "jev_cache_invalid")
        self.assertIsNone(invalid.get("jev_call_attempted"))
        self.assertIsNone(invalid.get("jev_usage"))


if __name__ == "__main__":
    unittest.main()
