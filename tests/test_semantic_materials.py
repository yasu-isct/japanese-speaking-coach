"""Optional retrieval plumbing with synthetic vectors; no model/network needed."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from jsc_live.materials import MaterialsStore, MaterialsLimits
from jsc_live.semantic_materials import SemanticEncoder, SemanticMaterialsStore, SemanticUnavailable, open_materials, verify_assets
from jsc_live.material_control import MaterialsController
from jsc_live.suggest import Suggester


class CharacterTokenizer:
    def encode(self, text, add_special_tokens=True):
        return SimpleNamespace(ids=[ord(char) for char in text], offsets=[(i, i+1) for i in range(len(text))],
                               attention_mask=[1]*len(text), type_ids=[0]*len(text))

    def encode_batch(self, texts):
        return [self.encode(text) for text in texts]


class FakeEncoder:
    def __init__(self):
        self.calls = []

    def windows(self, heading, text):
        return [heading + "\n" + text, heading + "\n" + text]

    def encode(self, texts):
        return np.array([[1., 0.] if "PRIMARY" in text else [.64, .77] for text in texts]).reshape(-1, 2)

    def query(self, text):
        self.calls.append(text)
        return np.array([1., 0.])


class SemanticWindowsTest(unittest.TestCase):
    def encoder(self):
        encoder = SemanticEncoder.__new__(SemanticEncoder)
        encoder.lock = threading.Lock()
        encoder.tokenizer = CharacterTokenizer()
        encoder.np = np
        return encoder

    def test_windows_cover_long_body_including_tail_with_bounded_heading(self):
        encoder = self.encoder()
        body = "".join(chr(0x4e00 + i) for i in range(700))
        heading = "H" * 160
        windows = encoder.windows(heading, body)
        covered = set()
        for window in windows:
            title, piece = window.split("\n", 1)
            self.assertEqual(len(title), 24)
            self.assertLessEqual(len(window), 128)
            start = body.index(piece)
            covered.update(range(start, start + len(piece)))
        self.assertEqual(covered, set(range(len(body))))
        self.assertTrue(windows[-1].endswith(body[-30:]))

    def test_long_query_uses_final_question_window(self):
        encoder = self.encoder()
        encoder.encode = Mock(return_value=np.array([[1., 0.]]))
        encoder.query("Earlier topic. " * 60 + "FINAL QUESTION")
        sent = encoder.encode.call_args.args[0][0]
        self.assertIn("FINAL QUESTION", sent)
        self.assertLessEqual(len(sent), 128)

    def test_encoding_is_padding_invariant_and_rejects_silent_truncation(self):
        encoder = self.encoder()
        encoder.pad_id = 0
        encoder.inputs = {"input_ids", "attention_mask", "token_type_ids"}
        def run(_, feed):
            ids = feed["input_ids"]
            values = np.zeros((*ids.shape, 384), dtype=np.float32)
            values[..., 0] = ids
            values[..., 1] = 10
            return [values]
        encoder.session = SimpleNamespace(run=run)
        alone = encoder.encode(["abc"])[0]
        padded = encoder.encode(["abc", "LONGER SENTENCE"])[0]
        np.testing.assert_allclose(alone, padded)
        self.assertAlmostEqual(float(np.linalg.norm(alone)), 1.0, places=6)
        with self.assertRaises(SemanticUnavailable):
            encoder.encode(["x" * 129])


class SemanticStoreTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.folder = self.root / "materials"
        self.folder.mkdir()
        (self.folder / "notes.md").write_text("# Synthetic\n\nPRIMARY loan period\n\nSECONDARY renewal conditions\n", encoding="utf-8")
        self.encoder = FakeEncoder()
        self.store = SemanticMaterialsStore(self.folder, encoder=self.encoder)

    def test_complementary_chunk_survives_and_windows_do_not_duplicate_sources(self):
        hits = self.store.search("paraphrase with no lexical matches")
        self.assertEqual(len(hits), 2)
        self.assertEqual(len({hit.source_id for hit in hits}), 2)
        self.assertIn("SECONDARY", hits[1].text)
        self.assertEqual([chunk.source_id for chunk in self.store.chunks],
                         [chunk.source_id for chunk in MaterialsStore(self.folder).chunks])
        payload = json.loads(self.store.prompt_data(hits))
        self.assertEqual(payload["sources"][1]["text"], "SECONDARY renewal conditions")

    def test_semantic_candidates_keep_result_limit_and_character_budget(self):
        store = SemanticMaterialsStore(self.folder, encoder=self.encoder,
                                       limits=MaterialsLimits(max_chunk_chars=40, max_result_chars=40))
        hits = store.search("unmatched query")
        self.assertLessEqual(sum(len(hit.text) for hit in hits), 40)
        self.assertEqual(len(self.store.search("unmatched query", limit=1)), 1)
        with self.assertRaises(ValueError):
            self.store.search("query", limit=-1)

    def test_empty_query_empty_library_and_zero_limit_do_not_run_query_encoder(self):
        self.assertEqual(self.store.search("  "), [])
        self.assertEqual(self.store.search("PRIMARY", limit=0), [])
        empty = SemanticMaterialsStore(self.root / "missing", encoder=self.encoder)
        self.assertEqual(empty.search("query"), [])
        self.assertEqual(self.encoder.calls, [])

    def test_inference_failure_keeps_lexical_retrieval_without_repeated_model_calls(self):
        self.encoder.query = Mock(side_effect=SemanticUnavailable("test failure"))
        old = MaterialsStore(self.folder).search("loan period")
        for _ in range(2):
            got = self.store.search("loan period")
            self.assertEqual([h.source_id for h in got], [h.source_id for h in old])
        self.encoder.query.assert_called_once()
        self.assertTrue(self.store.semantic_failed.is_set())

    def test_reload_preserves_encoder_and_cache_reads_new_materials(self):
        client = Suggester(env={"LLM_API_KEY": "fake", "LLM_BASE_URL": "https://example.invalid", "LLM_MODEL": "unused"},
                           materials=self.store)
        controller = MaterialsController(client, settings_path=self.root / "settings.json")
        client.prefetch("unmatched question")
        self.assertTrue(client._retrieve("unmatched question")[3])
        (self.folder / "notes.md").write_text("PRIMARY changed content", encoding="utf-8")
        info = controller.reload()
        self.assertIsInstance(client.materials, SemanticMaterialsStore)
        self.assertIs(client.materials.encoder, self.encoder)
        data, _, _, cached = client._retrieve("unmatched question")
        self.assertFalse(cached)
        self.assertIn("changed content", data)
        self.assertIn("本地多语言", info["message"])

    def test_bad_model_falls_back_without_importing_runtime_or_networking(self):
        with self.assertRaises(SemanticUnavailable):
            verify_assets(self.root / "missing-model")
        with patch("jsc_live.semantic_materials.SemanticEncoder", side_effect=SemanticUnavailable("missing")):
            store, warning = open_materials(self.folder, self.root / "missing-model")
        self.assertIs(type(store), MaterialsStore)
        self.assertEqual(warning, "missing")
        self.assertTrue(store.search("loan period"))

    def test_failed_index_reload_keeps_previous_snapshot_and_returns_a_user_error(self):
        client = SimpleNamespace(materials=self.store)
        controller = MaterialsController(client, settings_path=self.root / "settings.json")
        self.store.rebuild = Mock(side_effect=SemanticUnavailable("synthetic failure"))
        with self.assertRaisesRegex(ValueError, "已保留原资料"):
            controller.reload()
        self.assertIs(client.materials, self.store)

    def test_default_store_never_constructs_a_semantic_encoder(self):
        with patch("jsc_live.semantic_materials.SemanticEncoder", side_effect=AssertionError("No model")):
            store, warning = open_materials(self.folder)
        self.assertIs(type(store), MaterialsStore)
        self.assertIsNone(warning)


if __name__ == "__main__":
    unittest.main()
