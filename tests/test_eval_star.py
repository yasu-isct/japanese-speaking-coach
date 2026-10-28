"""Fixed-input, failure-stop and privacy checks for the text-only evaluation."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.suggest import Reply, Suggestion  # noqa: E402
from tools import eval_star  # noqa: E402


def good_result(raw="public output"):
    return Suggestion(replies=[Reply("丁寧", "はい。", "好的。", star=True),
                               Reply("カジュアル", "うん。", "嗯。"),
                               Reply("かんたん", "はい。", "好的。")],
                      raw=raw, api_ms=1000.0)


class EvalStarTest(unittest.TestCase):
    def test_public_table_preserves_answers_and_explicit_partners(self):
        cases = eval_star.load_public_cases(ROOT / eval_star.PUBLIC_SOURCE)
        self.assertEqual(len(cases), 20)
        self.assertEqual([case["id"] for case in cases], list(range(1, 21)))
        self.assertEqual({case["id"]: case["partner"] for case in cases if case["partner"]},
                         {6: "上司", 10: "老师", 16: "同一个研究室的同学"})
        self.assertEqual(sum(case["expected"] == "カジュアル" for case in cases), 6)
        self.assertTrue(all(not case["context"] for case in cases))
        self.assertTrue(all("（对方是" not in case["sentences"][0] for case in cases))

    def test_private_tail_is_not_filtered_or_reordered(self):
        rows = [{"sentences": [f"sentence {index}"], "context": [f"context {index}"],
                 "partner": None, "error": "old error" if index == 99 else None} for index in range(105)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "private.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            cases = eval_star.load_private_cases(path)
            self.assertEqual(len(cases), 100)
            self.assertEqual(cases[0]["sentences"], ["sentence 5"])
            self.assertEqual(cases[-1]["context"], ["context 104"])
            self.assertEqual(cases[94]["sentences"], ["sentence 99"])
            path.write_text(json.dumps(rows[0]), encoding="utf-8")
            with self.assertRaises(eval_star.EvaluationError):
                eval_star.load_private_cases(path)

    def test_missing_and_duplicate_stars_do_not_count_as_model_choices(self):
        empty = Suggestion(error="HTTP 500: private server echo", api_ms=20)
        double = good_result()
        double.replies[1].star = True
        inferred = good_result()
        inferred.star_inferred = True
        interrupted = good_result()
        interrupted.error = "TimeoutError: response interrupted after a reply"
        records = [eval_star.measure(row, "丁寧") for row in (empty, double, inferred, interrupted, good_result())]
        summary = eval_star.summarize(records)
        self.assertEqual(summary["star_not_inferred"]["count"], 4)
        self.assertEqual(summary["exactly_one_model_star"]["count"], 1)
        self.assertEqual(summary["matches_expected"]["count"], 3)
        self.assertEqual(summary["model_matches_expected"]["count"], 1)
        self.assertEqual(summary["errors"], 2)
        self.assertEqual(records[0]["error_kind"], "HTTP 500")
        self.assertNotIn("private server echo", json.dumps(records))

    def test_latency_uses_median_and_nearest_rank_without_dropping_errors(self):
        records = []
        for value in range(1, 21):
            result = good_result()
            result.api_ms = value
            if value == 20:
                result.error = "TimeoutError: request failed"
            records.append(eval_star.measure(result))
        summary = eval_star.summarize(records)
        self.assertEqual(summary["api_ms"], {"n": 20, "p50": 10.5, "p95": 19})

    def _run_fake(self, result_factory, *, private=False):
        fake = type("FakeSuggester", (), {})()
        fake.model = "deepseek-flash"
        fake.extra_body = {"thinking": {"type": "disabled"}}
        fake.system_prompt = "test system"
        calls = []

        def suggest(sentences, context):
            calls.append((sentences, context, getattr(fake, "partner", None)))
            return result_factory(len(calls), sentences)

        fake.suggest = suggest
        public_case = {"id": 1, "sentences": ["public sentence"], "context": [], "partner": "上司", "expected": "丁寧"}
        private_case = {"id": 1, "sentences": ["PRIVATE INPUT SENTINEL"], "context": ["PRIVATE CONTEXT SENTINEL"], "partner": None}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = root / "app/jsc_live/suggest.py"
            module.parent.mkdir(parents=True)
            module.write_text("test source", encoding="utf-8")
            output = io.StringIO()
            with patch.object(eval_star, "load_public_cases", return_value=[public_case]), \
                    patch.object(eval_star, "load_private_cases", return_value=[private_case] if private else []), \
                    patch.object(eval_star, "load_env", return_value={}), \
                    patch.object(eval_star, "Suggester", return_value=fake) as constructor, \
                    contextlib.redirect_stdout(output):
                code = eval_star.run_evaluation("baseline", "all" if private else "A", root=root)
                constructor.assert_called_once_with(env={}, profile="")
            artifacts = list((root / "tests/live_suggest").glob("star-eval-*.json"))
            self.assertEqual(len(artifacts), 1)
            saved = artifacts[0].read_text(encoding="utf-8")
        return code, calls, saved, output.getvalue()

    def test_private_inputs_outputs_and_summaries_never_enter_public_artifact(self):
        def result_factory(index, sentences):
            return good_result("PRIVATE OUTPUT SENTINEL" if index == 5 else "public output")

        code, calls, saved, stdout = self._run_fake(result_factory, private=True)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 5)  # one warm-up, three public, one private
        self.assertEqual(calls[1][2], "上司")
        self.assertEqual(calls[-1][2], None)
        self.assertNotIn("PRIVATE", saved)
        self.assertNotIn("PRIVATE", stdout)
        self.assertNotIn("B_summary", saved)
        self.assertIn('"summary": "B"', stdout)
        payload = json.loads(saved)
        self.assertEqual(len(payload["A_results"]), 3)
        self.assertEqual(payload["B_input_count"], 1)
        self.assertEqual(len(payload["B_input_sha256"]), 64)

    def test_three_consecutive_request_errors_stop_without_retry_or_body_echo(self):
        def result_factory(index, sentences):
            return Suggestion(error="HTTP 503: PRIVATE SERVER ECHO", api_ms=10)

        code, calls, saved, stdout = self._run_fake(result_factory)
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 3)  # warm-up counts toward the consecutive-error limit
        self.assertFalse(json.loads(saved)["completed"])
        self.assertNotIn("PRIVATE SERVER ECHO", saved + stdout)


if __name__ == "__main__":
    unittest.main()
