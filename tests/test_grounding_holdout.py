"""Offline stream tests for the paired holdout runner and isolated profiles."""

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

from tools import eval_grounding_holdout as holdout  # noqa: E402

RealSuggester = holdout.Suggester
TMP_ROOT = ROOT / "logs/star-eval/tmp"
ENV = {"LLM_API_KEY": "public-test-key", "LLM_BASE_URL": "https://api.deepseek.com", "LLM_MODEL": "deepseek-flash"}
GOOD = ("推荐|丁寧\n丁寧|【回答】です。|【回答】。|\n"
        "カジュアル|【回答】だよ。|【回答】。|\nかんたん|【回答】です。|【回答】。|\n理由|公开测试。")


class GroundingHoldoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TMP_ROOT.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def fixture_root(self):
        with tempfile.TemporaryDirectory(prefix="grounding-holdout-", dir=TMP_ROOT) as tmp:
            root = Path(tmp).resolve()
            self.assertTrue(root.is_relative_to(TMP_ROOT.resolve()))
            path = root / holdout.FIXTURE
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / holdout.FIXTURE).read_bytes())
            for name in (holdout.SUGGEST_SOURCE, *holdout.SHARED_SOURCES):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("public source fixture\n", encoding="utf-8")
            yield root

    def test_eight_fixed_cases_use_only_explicit_inline_fictional_profiles(self):
        _, cases, provenance = holdout.load_fixture()
        self.assertEqual([case["id"] for case in cases], list(range(1, 9)))
        self.assertTrue(all(set(case["profile"]) == {"text"} for case in cases))
        self.assertEqual([case["id"] for case in cases if case["expected_facts"]], [6])
        self.assertEqual([case["id"] for case in cases if not case["expected_unknown_slots"]], [8])
        self.assertEqual(len(provenance["fixed_input_sha256"]), 64)

    def test_file_profile_and_incomplete_case_set_are_rejected(self):
        with self.fixture_root() as root:
            path = root / holdout.FIXTURE
            fixture = json.loads(path.read_text(encoding="utf-8"))
            fixture["cases"][0]["profile"] = {"path": "profile.md"}
            path.write_text(json.dumps(fixture), encoding="utf-8")
            with self.assertRaises(holdout.EvaluationError):
                holdout.load_fixture(root)
            fixture["cases"] = fixture["cases"][:-1]
            path.write_text(json.dumps(fixture), encoding="utf-8")
            with self.assertRaises(holdout.EvaluationError):
                holdout.load_fixture(root)

    def test_raw_fixture_changes_are_detected_by_input_hash(self):
        with self.fixture_root() as root:
            _, _, before = holdout.load_fixture(root)
            path = root / holdout.FIXTURE
            fixture = json.loads(path.read_text(encoding="utf-8"))
            fixture["cases"][0]["profile"]["text"] = "# Explicit fictional test change"
            path.write_text(json.dumps(fixture), encoding="utf-8")
            _, _, after = holdout.load_fixture(root)
            self.assertNotEqual(before["fixture_sha256"], after["fixture_sha256"])
            self.assertNotEqual(before["fixed_input_sha256"], after["fixed_input_sha256"])

    def test_git_baseline_is_registered_for_dataclasses_without_changing_worktree(self):
        source = ("from dataclasses import dataclass\nfrom pathlib import Path\n"
                  "ROOT = Path(__file__).resolve().parents[2]\n"
                  "@dataclass\nclass BaselineRecord:\n    value: str\n")
        response = SimpleNamespace(stdout=source.encode("utf-8"))
        with patch.object(holdout.subprocess, "run", return_value=response) as command:
            module, source_hash = holdout.load_baseline(ROOT)
        self.addCleanup(sys.modules.pop, module.__name__, None)
        self.assertEqual(module.BaselineRecord("test").value, "test")
        self.assertEqual(module.ROOT, ROOT.resolve())
        self.assertEqual(module.__file__, str(ROOT / holdout.SUGGEST_SOURCE))
        self.assertIs(sys.modules[module.__name__], module)
        self.assertEqual(source_hash, hashlib.sha256(response.stdout).hexdigest())
        args = command.call_args.args[0]
        self.assertEqual(args, ["git", "-c", "safe.directory=" + ROOT.resolve().as_posix(), "show",
                                "2fa3014:app/jsc_live/suggest.py"])
        self.assertTrue(command.call_args.kwargs["check"])

    def run_fake_streams(self, fails=lambda variant, index: False):
        calls, clients = [], []

        def tagged_class(variant):
            class FakeStreamSuggester(RealSuggester):
                def __init__(client, *, env, profile):
                    self.assertIsInstance(profile, str)
                    client.raw_profile = profile
                    client.variant = variant
                    client.env_identity = id(env)
                    super().__init__(env=env, profile=profile)
                    clients.append(client)

                def _stream(client, messages, max_tokens=400):
                    calls.append({"variant": variant, "profile": client.raw_profile,
                                  "client_id": id(client), "messages": messages,
                                  "max_tokens": max_tokens, "partner": client.partner})
                    if fails(variant, len(calls)):
                        raise urllib.error.URLError("PRIVATE ERROR BODY")
                    # The real Suggester.suggest consumes a stream crossing line boundaries.
                    for index in range(0, len(GOOD), 7):
                        yield GOOD[index:index + 7]

            return FakeStreamSuggester

        baseline_class, current_class = tagged_class("baseline"), tagged_class("current")
        with self.fixture_root() as root:
            _, cases, _ = holdout.load_fixture(root)
            stdout = io.StringIO()
            with patch.object(holdout, "load_baseline", return_value=(SimpleNamespace(Suggester=baseline_class), "a" * 64)), \
                    patch.object(holdout, "Suggester", current_class), \
                    patch.object(holdout, "load_env", return_value=ENV), \
                    patch("urllib.request.urlopen", side_effect=AssertionError("Network must not be used")) as network, \
                    contextlib.redirect_stdout(stdout):
                code = holdout.run_evaluation(root)
            network.assert_not_called()
            artifacts = list((root / "tests/live_suggest").glob("grounding-holdout-*.json"))
            self.assertEqual(len(artifacts), 1)
            saved = artifacts[0].read_text(encoding="utf-8")
        return code, calls, clients, cases, json.loads(saved), saved, stdout.getvalue()

    def test_paired_order_request_limit_and_profile_isolation_through_real_stream_parser(self):
        code, calls, clients, cases, payload, _, _ = self.run_fake_streams()
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 18)
        self.assertEqual(payload["requests_sent"], 18)
        self.assertTrue(payload["completed"])
        self.assertEqual([call["variant"] for call in calls], ["baseline", "current"] * 9)
        self.assertEqual([call["profile"] for call in calls[:2]], ["", ""])
        self.assertEqual([(row["id"], row["variant"]) for row in payload["results"]],
                         [(case_id, variant) for case_id in range(1, 9) for variant in holdout.VARIANTS])
        for index, case in enumerate(cases):
            pair = calls[2 + 2 * index:4 + 2 * index]
            self.assertEqual([call["profile"] for call in pair], [case["profile"]["text"]] * 2)
            self.assertNotEqual(pair[0]["client_id"], pair[1]["client_id"])
            self.assertEqual([call["partner"] for call in pair], [case["partner"]] * 2)
            self.assertEqual(pair[0]["messages"][1], pair[1]["messages"][1])
        self.assertEqual(len({id(client) for client in clients}), len(clients))
        for variant in holdout.VARIANTS:
            self.assertEqual(payload["summary_by_variant"][variant]["n"], 8)
            self.assertIsNone(payload["summary_by_variant"][variant]["grounding_verdict"])
        for row in payload["results"]:
            self.assertEqual(row["raw"], GOOD)
            self.assertEqual(len(row["replies"]), 3)
            self.assertTrue(row["complete"])
            self.assertEqual(row["review_status"], "pending")
            self.assertIn("rubric", row)
            self.assertIn("expected_facts", row)
        self.assertEqual(payload["source_sha256"]["baseline"], "a" * 64)

    def test_three_global_request_errors_stop_without_retry_or_error_body_leak(self):
        code, calls, _, _, payload, saved, stdout = self.run_fake_streams(lambda variant, index: True)
        self.assertEqual(code, 2)
        self.assertFalse(payload["completed"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(payload["requests_sent"], 3)
        self.assertEqual(len(payload["results"]), 1)
        self.assertNotIn("PRIVATE ERROR BODY", saved + stdout)

    def test_repeated_errors_on_one_side_stop_despite_success_on_the_other_side(self):
        code, calls, _, _, payload, _, _ = self.run_fake_streams(lambda variant, index: variant == "baseline")
        self.assertEqual(code, 2)
        self.assertFalse(payload["completed"])
        self.assertEqual(len(calls), 5)
        self.assertEqual([call["variant"] for call in calls], ["baseline", "current", "baseline", "current", "baseline"])
        self.assertEqual(payload["summary_by_variant"]["baseline"]["errors"], 2)
        self.assertEqual(payload["summary_by_variant"]["current"]["errors"], 0)


if __name__ == "__main__":
    unittest.main()
