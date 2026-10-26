"""Offline integration tests for external references, citations and setup.

All documents are fictional and created below logs/star-eval/tmp. No personal
profile, environment file, default user folder, audio device or API is accessed.
These tests verify wiring and provenance, not generated answers' factual truth.
"""

import copy
import io
import json
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.live_suggest import SuggestionManager  # noqa: E402
from jsc_live.material_setup import prepare_materials  # noqa: E402
from jsc_live.materials import MaterialsStore  # noqa: E402
from jsc_live.suggest import Suggester  # noqa: E402


ENV = {"LLM_API_KEY": "offline-test", "LLM_BASE_URL": "http://localhost:9", "LLM_MODEL": "fake"}
COURSE = "# 架空大学の授業\n\n- レポート提出期限：金曜日17時、COURSE_SENTINEL。\n"
LIBRARY = "# 架空図書館\n\n- 図書館の貸出期間：十四日、LIBRARY_SENTINEL。\n"


def response(reason="对方礼貌提问，按同等礼貌回答。"):
    return (
        "推荐|丁寧\n"
        "丁寧|資料を確認します。|我确认一下资料。|\n"
        "カジュアル|資料を見るね。|我看一下资料。|\n"
        "かんたん|確認します。|确认一下。|\n"
        f"理由|{reason}"
    )


class MaterialsIntegrationTest(unittest.TestCase):
    def setUp(self):
        temporary_root = ROOT / "logs" / "star-eval" / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="materials-integration-", dir=temporary_root)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.default_root = self.root / "default-materials"
        self.material_root = self.root / "injected-materials"
        for target, kwargs in (
            ("jsc_live.materials.default_materials_root", {"return_value": self.default_root}),
            ("jsc_live.material_setup.default_materials_root", {"return_value": self.default_root}),
            ("jsc_live.suggest.load_env", {"side_effect": AssertionError("Unexpected environment read")}),
            ("jsc_live.suggest.load_profile", {"side_effect": AssertionError("Unexpected private profile read")}),
            ("jsc_live.suggest.urllib.request.urlopen", {"side_effect": AssertionError("Network forbidden")}),
        ):
            guard = patch(target, **kwargs)
            guard.start()
            self.addCleanup(guard.stop)

    def write(self, name, text, *, root=None):
        path = (root or self.material_root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def course_store(self):
        self.write("course.md", COURSE)
        return MaterialsStore(self.material_root)

    def fake_stream(self, client, text=None):
        requests = []
        output = text if text is not None else response()

        def stream(messages, max_tokens=400):
            requests.append(copy.deepcopy(messages))
            # Split even citation tokens and omit the final newline.
            yield from (output[offset:offset + 7] for offset in range(0, len(output), 7))

        client._stream = Mock(side_effect=stream)
        return requests

    def reference_payload(self, messages):
        payloads = []
        for message in messages:
            if message["role"] != "user":
                continue
            for line in message["content"].splitlines():
                if line.startswith("{"):
                    parsed = json.loads(line)
                    if parsed.get("kind") == "untrusted_reference_data":
                        payloads.append(parsed)
        self.assertEqual(len(payloads), 1)
        return payloads[0]

    def test_explicit_empty_profile_never_creates_or_reads_default_store(self):
        self.write("course.md", COURSE, root=self.default_root)
        with patch("jsc_live.materials.MaterialsStore", side_effect=AssertionError("Default store forbidden")):
            client = Suggester(env=ENV, profile="")
            sent = self.fake_stream(client)
            client.prefetch("レポート")
            result = client.suggest("レポート", [])
        self.assertIsNone(client.materials)
        self.assertEqual(client.profile, "")
        self.assertEqual(result.sources, [])
        self.assertIsNone(result.retrieval_ms)
        self.assertFalse(result.retrieval_cached)
        self.assertTrue(result.complete)
        self.assertIsNone(result.error)
        self.assertNotIn("COURSE_SENTINEL", json.dumps(sent, ensure_ascii=False))
        self.assertEqual(client._stream.call_count, 1)

    def test_explicit_profile_and_store_are_rejected_as_ambiguous(self):
        with self.assertRaises(ValueError):
            Suggester(env=ENV, profile="", materials=self.course_store())

    def test_default_store_reads_external_data_only_into_user_message(self):
        self.write("course.md", COURSE, root=self.default_root)
        client = Suggester(env=ENV)
        sent = self.fake_stream(client)
        result = client.suggest("レポート", [])
        self.assertEqual(client.materials.root, self.default_root)
        self.assertEqual(client.profile, "")
        self.assertNotIn("COURSE_SENTINEL", client.system_prompt)
        self.assertNotIn("COURSE_SENTINEL", sent[0][0]["content"])
        payload = self.reference_payload(sent[0])
        self.assertIn("COURSE_SENTINEL", payload["sources"][0]["text"])
        self.assertEqual(result.sources[0]["relative_path"], "course.md")
        self.assertTrue(result.complete)
        self.assertIsNone(result.error)

    def test_injected_course_store_propagates_bounded_source_metadata(self):
        self.write("default.md", "DEFAULT_ONLY_SENTINEL", root=self.default_root)
        store = self.course_store()
        with patch("jsc_live.materials.MaterialsStore", side_effect=AssertionError("Injected store must be used")):
            client = Suggester(env=ENV, materials=store)
        sent = self.fake_stream(client)
        result = client.suggest("レポート提出期限", [])
        self.assertIs(client.materials, store)
        self.assertNotIn("COURSE_SENTINEL", client.system_prompt)
        payload = self.reference_payload(sent[0])
        self.assertEqual(len(payload["sources"]), 1)
        source = result.sources[0]
        self.assertEqual(source["id"], "S1")
        self.assertEqual(source["relative_path"], "course.md")
        self.assertEqual((source["start_line"], source["end_line"]), (3, 3))
        self.assertIn("COURSE_SENTINEL", source["text"])
        self.assertTrue(source["source_id"].startswith("material-"))
        self.assertEqual(payload["sources"][0], {key: value for key, value in source.items() if key != "source_id"})
        self.assertNotIn(str(self.material_root), json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("DEFAULT_ONLY_SENTINEL", json.dumps(sent, ensure_ascii=False))
        self.assertIsInstance(result.retrieval_ms, float)
        self.assertGreaterEqual(result.retrieval_ms, 0)
        self.assertFalse(result.retrieval_cached)

    def test_citations_resolve_only_current_ids_and_deduplicate(self):
        client = Suggester(env=ENV, materials=self.course_store())
        self.fake_stream(client, response("参考课程说明[S1][S1]；其他编号[S99]。"))
        snapshots = []
        result = client.suggest("レポート", [], on_update=lambda value: snapshots.append(copy.deepcopy(value)))
        self.assertEqual(result.reason.count("[course.md:3]"), 2)
        self.assertIn("[出处未匹配]", result.reason)
        self.assertNotIn("[S99]", result.reason)
        self.assertEqual(result.cited_source_ids, [result.sources[0]["source_id"]])
        self.assertEqual(snapshots[-1].reason, result.reason)
        self.assertEqual(snapshots[-1].cited_source_ids, result.cited_source_ids)
        self.assertTrue(result.complete)
        self.assertIsNone(result.error)

    def test_citation_alias_is_rebound_per_turn_and_missing_hit_cannot_reuse_it(self):
        self.write("course.md", COURSE)
        self.write("library.md", LIBRARY)
        client = Suggester(env=ENV, materials=MaterialsStore(self.material_root))
        self.fake_stream(client, response("资料说明[S1]。"))
        course = client.suggest("レポート", [])
        library = client.suggest("図書館", ["レポート"])
        missing = client.suggest("quasar_unmatched", ["図書館"])
        self.assertEqual([source["relative_path"] for source in course.sources], ["course.md"])
        self.assertEqual([source["relative_path"] for source in library.sources], ["library.md"])
        self.assertIn("[library.md:3]", library.reason)
        self.assertNotIn("course.md", library.reason)
        self.assertNotEqual(course.cited_source_ids, library.cited_source_ids)
        self.assertEqual(missing.sources, [])
        self.assertEqual(missing.cited_source_ids, [])
        self.assertIn("[出处未匹配]", missing.reason)
        self.assertNotIn("library.md", missing.reason)
        self.assertIn("[course.md:3]", course.reason)

    def test_prefetch_avoids_generation_and_reuses_only_matching_query(self):
        self.write("course.md", COURSE)
        self.write("library.md", LIBRARY)
        store = MaterialsStore(self.material_root)
        client = Suggester(env=ENV, materials=store)
        sent = self.fake_stream(client)
        with patch.object(store, "search", wraps=store.search) as search:
            client.prefetch(["レポート"])
            self.assertEqual(client._stream.call_count, 0)
            self.assertEqual(search.call_count, 1)
            repeated = client.suggest(["レポート"], [])
            self.assertTrue(repeated.retrieval_cached)
            self.assertEqual(search.call_count, 1)
            changed = client.suggest("図書館", ["レポート"])
            self.assertFalse(changed.retrieval_cached)
            self.assertEqual(search.call_count, 2)
        self.assertEqual([source["relative_path"] for source in changed.sources], ["library.md"])
        self.assertNotIn("COURSE_SENTINEL", sent[-1][1]["content"])
        self.assertIn("LIBRARY_SENTINEL", sent[-1][1]["content"])

    def test_returned_metadata_cannot_mutate_cached_citation_paths(self):
        client = Suggester(env=ENV, materials=self.course_store())
        self.fake_stream(client, response("课程[S1]。"))
        first = client.suggest("レポート", [])
        first.sources[0]["relative_path"] = "forged.md"
        first.sources[0]["text"] = "FORGED"
        second = client.suggest("レポート", [])
        self.assertTrue(second.retrieval_cached)
        self.assertEqual(second.sources[0]["relative_path"], "course.md")
        self.assertIn("COURSE_SENTINEL", second.sources[0]["text"])
        self.assertIn("[course.md:3]", second.reason)
        self.assertNotIn("forged", second.reason)

    def test_manager_prefetch_and_payload_keep_provenance_and_timing(self):
        store = self.course_store()
        client = Suggester(env=ENV, materials=store)
        self.fake_stream(client, response("课程[S1]。"))
        events = queue.Queue()
        log = Mock()
        log.records = {}
        furigana = Mock()
        furigana.annotate.side_effect = lambda text: [{"text": text}]
        with patch("jsc_live.live_suggest.threading.Thread"):
            manager = SuggestionManager(events, client, furigana, log=log)
        with patch.object(store, "search", wraps=store.search) as search:
            manager.submit(1, "レポート", 1.0)
            manager.prefetch("提出期限")
            manager.submit(2, "提出期限", 1.0)
            self.assertEqual(client._stream.call_count, 0)
            self.assertEqual(search.call_count, 2)
            manager.latest = 2
            manager._run(2, ["レポート", "提出期限"], [])
            self.assertEqual(search.call_count, 2)
        payloads = []
        while not events.empty():
            kind, uid, payload = events.get_nowait()
            self.assertEqual((kind, uid), ("suggest", 2))
            payloads.append(payload)
        self.assertEqual(payloads[-1]["state"], "done")
        self.assertEqual(payloads[-1]["sources"][0]["relative_path"], "course.md")
        self.assertEqual(payloads[-1]["cited_source_ids"], [payloads[-1]["sources"][0]["source_id"]])
        recorded = log.update.call_args.kwargs
        self.assertTrue(recorded["retrieval_cached"])
        self.assertGreaterEqual(recorded["retrieval_ms"], 0)
        self.assertEqual(recorded["sources"], payloads[-1]["sources"])

    def test_missing_and_empty_folders_still_allow_normal_suggestions(self):
        empty = self.root / "empty"
        empty.mkdir()
        missing = self.root / "does-not-exist"
        for folder in (empty, missing):
            with self.subTest(folder=folder.name):
                client = Suggester(env=ENV, materials=MaterialsStore(folder))
                sent = self.fake_stream(client)
                result = client.suggest("少々お待ちいただけますか？", [])
                self.assertTrue(result.complete)
                self.assertIsNone(result.error)
                self.assertEqual(result.sources, [])
                self.assertEqual(self.reference_payload(sent[0])["sources"], [])
        self.assertFalse(missing.exists())

    def test_migration_copies_bytes_preserves_source_and_refuses_overwrite(self):
        old = self.root / "fictional-old-profile.md"
        raw = b"\xef\xbb\xbf# Fictional learner\r\n- Hobby: painting\r\n"
        old.write_bytes(raw)
        destination = self.root / "migrated"
        prepared = prepare_materials(destination, old)
        self.assertEqual(prepared, destination.resolve())
        self.assertEqual(old.read_bytes(), raw)
        self.assertEqual((destination / "profile.md").read_bytes(), raw)
        old.write_bytes(b"# Different fictional input\n")
        with self.assertRaisesRegex(ValueError, "未覆盖"):
            prepare_materials(destination, old)
        self.assertEqual(old.read_bytes(), b"# Different fictional input\n")
        self.assertEqual((destination / "profile.md").read_bytes(), raw)

    def test_default_setup_creates_empty_folder_without_loading_other_inputs(self):
        self.assertFalse(self.default_root.exists())
        destination = prepare_materials()
        self.assertEqual(destination, self.default_root.resolve())
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual(MaterialsStore(destination).search("レポート"), [])
        self.assertEqual(prepare_materials(), destination)

    def test_cli_setup_exits_before_starting_suggestions_or_audio(self):
        from jsc_live.main import main

        asr_stub = ModuleType("jsc_live.asr")
        asr_stub.DEFAULT_MODEL = "unused-offline-model"
        destination = self.root / "cli-materials"
        output = io.StringIO()
        with patch.dict(sys.modules, {"jsc_live.asr": asr_stub}), patch("sys.stdout", output), \
                patch("jsc_live.main.threading.Thread", side_effect=AssertionError("No workers during setup")), \
                patch("jsc_live.suggest.Suggester", side_effect=AssertionError("No client during setup")):
            main(["--materials", str(destination), "--init-materials", "--suggest"])
        self.assertTrue(destination.is_dir())
        self.assertEqual(list(destination.iterdir()), [])
        self.assertIn("片段会发给 API", output.getvalue())


if __name__ == "__main__":
    unittest.main()
