"""The document stage must stay offline by default and reject changed inputs."""
import contextlib
import io
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import eval_document_answers as stage


class DocumentStageTest(unittest.TestCase):
    def test_default_does_not_load_credentials_or_model(self):
        with patch.object(stage, "load_env") as env, patch.object(stage, "SemanticEncoder") as model:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(stage.main([]), 0)
            env.assert_not_called()
            model.assert_not_called()

    def test_invalid_explicit_run_rejected_before_loading_credentials(self):
        with patch.object(stage, "load_env") as env, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                stage.main(["--run-api", "--model-dir", "unused", "--label", "../unsafe"])
            env.assert_not_called()

    def test_public_fixture_is_complete(self):
        cases, manifest = stage.load_cases()
        self.assertEqual(len(cases), 8)
        self.assertEqual(len({case["id"] for case in cases}), 8)
        self.assertEqual(sum(case["answer_available"] for case in cases), 7)
        self.assertEqual(len(manifest["sha256"]), 10)

    def test_modified_or_extra_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("document-stage", "long_pdf", "documents", "general-independent"):
                source = ROOT / "tests/fixtures/materials" / name
                shutil.copytree(source, root / "tests/fixtures/materials" / name)
            with patch.object(stage, "ROOT", root):
                stage.load_cases()
                extra = root / "tests/fixtures/materials/documents/data/extra.txt"
                extra.write_text("unexpected", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "file list changed"):
                    stage.load_cases()
                extra.unlink()
                source = root / "tests/fixtures/materials/documents/data/readme.txt"
                source.write_text("changed source", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Frozen input changed"):
                    stage.load_cases()


if __name__ == "__main__":
    unittest.main()
