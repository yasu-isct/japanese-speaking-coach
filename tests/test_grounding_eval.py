"""Offline checks for fixed public grounding inputs and review-only metrics."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.suggest import Reply, Suggestion  # noqa: E402
from tools import eval_grounding  # noqa: E402

TMP_ROOT = ROOT / "logs/star-eval/tmp"


def good_result():
    return Suggestion(
        replies=[Reply("丁寧", "【出身地】出身です。", "我来自【出身地】。", star=True),
                 Reply("カジュアル", "【出身地】出身だよ。", "我来自【出身地】。"),
                 Reply("かんたん", "【出身地】です。", "【出身地】。")],
        raw="PUBLIC RESPONSE", reason="公开测试理由", api_ms=1000.0, first_token_ms=500.0,
    )


class GroundingEvalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TMP_ROOT.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def fixture_root(self):
        # Copy only allowlisted public assets into this test's own temporary root.
        with tempfile.TemporaryDirectory(prefix="grounding-eval-", dir=TMP_ROOT) as tmp:
            root = Path(tmp)
            for name in (eval_grounding.FIXTURE, *eval_grounding.PUBLIC_PROFILES):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / name).read_bytes())
            for name in eval_grounding.SOURCE_FILES:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("public source hash fixture\n", encoding="utf-8")
            yield root

    def test_fixture_keeps_sixteen_cases_with_explicit_public_profiles_and_rubrics(self):
        fixture, cases, provenance = eval_grounding.load_fixture()
        self.assertEqual([case["id"] for case in cases], list(range(1, 17)))
        self.assertEqual(len(fixture["cases"]), 16)
        self.assertTrue(all(case["rubric"] for case in cases))
        self.assertTrue(all(isinstance(case["profile_text"], str) for case in cases))
        self.assertEqual([case["id"] for case in cases if case["expected_facts"]], [10, 11])
        self.assertTrue(all(case["profile_text"] == "" for case in cases[:9]))
        self.assertEqual(cases[13]["profile"], {"path": "profile.example.md"})
        self.assertEqual(set(provenance["profile_sources"]), set(eval_grounding.PUBLIC_PROFILES))
        self.assertEqual(len(provenance["fixed_input_sha256"]), 64)

    def test_private_and_traversal_profile_paths_are_rejected_before_loading(self):
        with self.fixture_root() as root:
            path = root / eval_grounding.FIXTURE
            fixture = json.loads(path.read_text(encoding="utf-8"))
            for name in ("profile.md", "../profile.md", "tests/live_suggest/../../profile.md"):
                with self.subTest(path=name):
                    fixture["cases"][0]["profile"] = {"path": name}
                    path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
                    with patch.object(eval_grounding, "load_profile") as loader:
                        with self.assertRaises(eval_grounding.EvaluationError):
                            eval_grounding.load_fixture(root)
                        loader.assert_not_called()

    def test_fixed_input_hash_uses_raw_sources_not_profile_preprocessing(self):
        with self.fixture_root() as root:
            original, cases, before = eval_grounding.load_fixture(root)
            with patch.object(eval_grounding, "load_profile", return_value="PROCESSED TEST PROFILE"):
                unchanged, transformed, after = eval_grounding.load_fixture(root)
            self.assertEqual(original, unchanged)
            self.assertEqual(before, after)
            self.assertNotEqual(cases[9]["profile_text"], transformed[9]["profile_text"])
            self.assertEqual(transformed[9]["profile_text"], "PROCESSED TEST PROFILE")
            self.assertIn("（例）", before["profile_sources"]["profile.example.md"]["raw_text"])

    def test_changing_raw_fixture_or_public_profile_changes_fixed_input_hash(self):
        with self.fixture_root() as root:
            _, _, before = eval_grounding.load_fixture(root)
            profile = root / "profile.example.md"
            profile.write_text(profile.read_text(encoding="utf-8") + "\n> extra public hint\n", encoding="utf-8")
            _, _, changed_source = eval_grounding.load_fixture(root)
            self.assertEqual(before["fixture_sha256"], changed_source["fixture_sha256"])
            self.assertNotEqual(before["fixed_input_sha256"], changed_source["fixed_input_sha256"])
            fixture_path = root / eval_grounding.FIXTURE
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            fixture["cases"][0]["sentences"] = ["どちらのご出身ですか？"]
            fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
            _, _, changed_fixture = eval_grounding.load_fixture(root)
            self.assertNotEqual(changed_source["fixture_sha256"], changed_fixture["fixture_sha256"])
            self.assertNotEqual(changed_source["fixed_input_sha256"], changed_fixture["fixed_input_sha256"])

    def test_placeholder_presence_never_produces_a_grounding_verdict(self):
        result = good_result()
        result.replies[0].text = "【名字】です。東京に住んでいます。"
        row = eval_grounding.measure(result)
        self.assertEqual(row["replies_with_placeholders"], 3)
        self.assertEqual(row["review_status"], "pending")
        self.assertIsNone(row["grounding_verdict"])
        self.assertIsNone(row["review"]["unsupported_personal_claims"])
        self.assertIsNone(row["review"]["directly_useful"])
        summary = eval_grounding.summarize([row])
        self.assertEqual(summary["placeholder_diagnostic"]["replies_with_placeholders"], 3)
        self.assertEqual(summary["review_status"], "pending")
        self.assertIsNone(summary["grounding_verdict"])

    def test_latency_includes_failed_attempts_with_median_and_nearest_rank(self):
        rows = []
        for elapsed in range(1, 21):
            result = good_result()
            result.api_ms = elapsed
            if elapsed == 20:
                result.error = "TimeoutError: test error body"
            rows.append(eval_grounding.measure(result))
        summary = eval_grounding.summarize(rows)
        self.assertEqual(summary["api_ms"], {"n": 20, "p50": 10.5, "p95": 19})
        self.assertEqual(summary["errors"], 1)
        self.assertNotIn("test error body", json.dumps(rows))

    def run_fake(self, result_factory):
        calls, constructors = [], []

        def construct(*, env, profile):
            self.assertIsInstance(profile, str)
            constructors.append((env, profile))
            client = SimpleNamespace(model="deepseek-flash", extra_body={"thinking": {"type": "disabled"}},
                                     profile=profile, system_prompt="PUBLIC PROMPT\n" + profile, partner=None)

            def suggest(sentences, context):
                calls.append({"sentences": sentences, "context": context,
                              "partner": client.partner, "profile": client.profile})
                return result_factory(len(calls))

            client.suggest = suggest
            return client

        with self.fixture_root() as root:
            _, cases, _ = eval_grounding.load_fixture(root)
            stdout = io.StringIO()
            with patch.object(eval_grounding, "load_env", return_value={"stub": "public test"}), \
                    patch.object(eval_grounding, "Suggester", side_effect=construct), \
                    contextlib.redirect_stdout(stdout):
                code = eval_grounding.run_evaluation("baseline", root)
            artifacts = list((root / "tests/live_suggest").glob("grounding-eval-*.json"))
            self.assertEqual(len(artifacts), 1)
            saved = artifacts[0].read_text(encoding="utf-8")
        return code, calls, constructors, cases, json.loads(saved), saved, stdout.getvalue()

    def test_full_run_is_two_fixed_passes_plus_one_warmup_and_saves_review_evidence(self):
        code, calls, constructors, cases, payload, _, _ = self.run_fake(lambda _: good_result())
        self.assertEqual(code, 0)
        self.assertTrue(payload["completed"])
        self.assertEqual(payload["requests_sent"], 33)
        self.assertEqual(len(calls), 33)
        self.assertEqual(calls[0], {"sentences": "はい。", "context": [], "partner": None, "profile": ""})
        self.assertEqual([(row["rep"], row["id"]) for row in payload["results"]],
                         [(rep, case_id) for rep in (1, 2) for case_id in range(1, 17)])
        expected_calls = [{"sentences": case["sentences"], "context": case["context"],
                           "partner": case["partner"], "profile": case["profile_text"]} for case in cases] * 2
        self.assertEqual(calls[1:], expected_calls)
        self.assertTrue(all(env == {"stub": "public test"} and profile is not None
                            for env, profile in constructors))
        self.assertEqual(payload["summary"]["n"], 32)
        self.assertEqual(set(payload["prompt_sha256_by_case"]), {str(i) for i in range(1, 17)})
        self.assertEqual(set(payload["source_sha256"]), set(eval_grounding.SOURCE_FILES))
        for row, case in zip(payload["results"], cases * 2):
            self.assertEqual(row["rubric"], case["rubric"])
            self.assertEqual(row["expected_facts"], case["expected_facts"])
            self.assertEqual(row["expected_unknown_slots"], case["expected_unknown_slots"])
            self.assertEqual(row["effective_profile"], case["profile_text"])
            self.assertEqual(row["raw"], "PUBLIC RESPONSE")
            self.assertEqual(len(row["replies"]), 3)
            self.assertEqual(row["review_status"], "pending")
            self.assertIsNone(row["grounding_verdict"])

    def test_three_consecutive_request_errors_stop_without_retry_or_error_body_echo(self):
        def failed(_):
            return Suggestion(error="HTTP 503: PRIVATE SERVER ECHO", api_ms=20.0)

        code, calls, _, _, payload, saved, stdout = self.run_fake(failed)
        self.assertEqual(code, 2)
        self.assertFalse(payload["completed"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(payload["requests_sent"], 3)
        self.assertEqual(payload["summary"]["n"], 2)
        self.assertEqual(payload["warmup_error_kind"], "HTTP 503")
        self.assertNotIn("PRIVATE SERVER ECHO", saved + stdout)

    def test_success_resets_consecutive_request_error_count(self):
        def intermittent(index):
            if index in (1, 2, 4):
                return Suggestion(error="HTTP 503: transient test failure", api_ms=20.0)
            return good_result()

        code, calls, _, _, payload, _, _ = self.run_fake(intermittent)
        self.assertEqual(code, 0)
        self.assertTrue(payload["completed"])
        self.assertEqual(len(calls), 33)
        self.assertEqual(payload["summary"]["errors"], 2)

    def test_model_thinking_and_fixed_request_settings_are_enforced(self):
        bad_settings = [
            ("other-model", {"thinking": {"type": "disabled"}}),
            ("deepseek-flash", {"thinking": {"type": "enabled"}}),
            ("deepseek-flash", {"thinking": {"type": "disabled"}, "max_tokens": 10000}),
        ]
        for model, extra_body in bad_settings:
            with self.subTest(model=model, extra_body=extra_body):
                with self.assertRaises(eval_grounding.EvaluationError):
                    eval_grounding.validate_client(SimpleNamespace(model=model, extra_body=extra_body))


if __name__ == "__main__":
    unittest.main()
