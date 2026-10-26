"""Offline session review export with synthetic archives only."""

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import io
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from jsc_live.session_review import export_session_review, SessionReviewError

FIXTURE = ROOT / "tests/fixtures/session_review"


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = self.root / "session"
        shutil.copytree(FIXTURE, self.session)

    def test_complete_archive_links_sentences_sources_and_deduplicates_cards(self):
        before = {p.relative_to(self.session): p.read_bytes() for p in self.session.rglob("*") if p.is_file()}
        result = export_session_review(self.session, self.root / "export")
        self.assertEqual((result.sentence_count, result.turn_count, result.card_count), (2, 2, 2))
        page = result.html_path.read_text(encoding="utf-8")
        for expected in ('href="#sentence-1"', 'href="#turn-2"', 'course.md:6', '回答引用', '不代表使用者当时实际说过', '⚠'):
            self.assertIn(expected, page)
        notes = [s for s in result.anki_path.read_text(encoding="utf-8").splitlines() if not s.startswith("#")]
        self.assertEqual(len(notes), 2)
        self.assertTrue(all(len(row.split("\t")) == 3 for row in notes))
        self.assertEqual(before, {p.relative_to(self.session): p.read_bytes() for p in self.session.rglob("*") if p.is_file()})

    def test_old_archive_without_lookup_exports_empty_valid_anki(self):
        (self.session / "vocabulary.jsonl").unlink()
        result = export_session_review(self.session, anki_layout="fields")
        self.assertEqual(result.card_count, 0)
        tsv = result.anki_path.read_text(encoding="utf-8")
        self.assertIn("#tags column:5", tsv)
        self.assertTrue(all(line.startswith("#") for line in tsv.splitlines()))
        self.assertIn("没有保存查词记录", result.html_path.read_text(encoding="utf-8"))

    def test_incomplete_json_line_is_reported_without_discarding_valid_turns(self):
        with (self.session / "suggestions.jsonl").open("a", encoding="utf-8") as output:
            output.write('{"incomplete":\n[]\n')
        result = export_session_review(self.session)
        self.assertEqual(result.turn_count, 2)
        self.assertEqual(result.diagnostics, {"损坏的JSON行": 1, "格式不符的记录": 1})

    def test_user_data_is_escaped_and_cannot_become_script_or_tsv_fields(self):
        attack = '<script>alert("x")</script>\t\n'
        item = {"kind": "lookup", "word": attack, "meaning": "A&B", "sentence": '<img src="https://bad.invalid">'}
        (self.session / "vocabulary.jsonl").write_text(json.dumps(item), encoding="utf-8")
        result = export_session_review(self.session)
        page = result.html_path.read_text(encoding="utf-8")
        self.assertNotIn("<script>", page)
        self.assertNotIn('<img src="https:', page)
        self.assertIn("&lt;script&gt;", page)
        note = result.anki_path.read_text(encoding="utf-8").splitlines()[-1]
        self.assertEqual(len(note.split("\t")), 3)
        self.assertIn("A&amp;B", note)

    def test_traversal_clip_is_not_read_or_linked(self):
        (self.session / "clips/transcripts.tsv").write_text("../secret.wav\tSECRET\tja\t\n0003.wav\t安全。\tja\t\n", encoding="utf-8")
        result = export_session_review(self.session)
        self.assertEqual(result.sentence_count, 1)
        page = result.html_path.read_text(encoding="utf-8")
        self.assertNotIn("secret.wav", page)
        self.assertNotIn("SECRET", page)

    def test_existing_export_and_unknown_folder_are_rejected_without_changes(self):
        output = self.root / "output"
        output.mkdir()
        (output / "keep.txt").write_text("keep")
        with self.assertRaises(SessionReviewError):
            export_session_review(self.session, output)
        self.assertEqual((output / "keep.txt").read_text(), "keep")
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(SessionReviewError):
            export_session_review(empty)
        self.assertEqual(list(empty.iterdir()), [])

    def test_text_only_archive_keeps_unmatched_suggestions_without_guessing_ids(self):
        (self.session / "clips/transcripts.tsv").unlink()
        result = export_session_review(self.session)
        page = result.html_path.read_text(encoding="utf-8")
        self.assertEqual(result.sentence_count, 2)
        self.assertIn("[00:01]", page)
        self.assertNotIn('href="#sentence-1"', page)
        self.assertIn('id="turn-1"', page)

    def test_candidates_only_export_when_explicitly_requested(self):
        result = export_session_review(self.session, include_candidates=True, anki_layout="fields")
        self.assertEqual(result.card_count, 3)
        notes = [s for s in result.anki_path.read_text(encoding="utf-8").splitlines() if not s.startswith("#")]
        self.assertTrue(all(len(row.split("\t")) == 5 for row in notes))

    def test_existing_audio_is_linked_without_copying_and_same_word_contexts_remain_distinct(self):
        clip = self.session / "clips/0001.wav"
        with wave.open(str(clip), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\0\0" * 80)
        items = [{"kind": "lookup", "word": "上がる", "meaning": meaning, "sentence": sentence}
                 for meaning, sentence in [("升高", "温度が上がる。"), ("下班", "今日はもう上がる。")]]
        (self.session / "vocabulary.jsonl").write_text("\n".join(json.dumps(x) for x in items), encoding="utf-8")
        result = export_session_review(self.session)
        self.assertIn(clip.as_uri(), result.html_path.read_text(encoding="utf-8"))
        self.assertFalse(list(result.html_path.parent.glob("*.wav")))
        fronts = [s.split("\t")[0] for s in result.anki_path.read_text(encoding="utf-8").splitlines() if not s.startswith("#")]
        self.assertEqual(len(set(fronts)), 2)

    def test_cli_runs_real_export_without_api_or_live_worker(self):
        from jsc_live.main import main
        output = self.root / "cli-export"
        with patch("jsc_live.main.threading.Thread", side_effect=AssertionError("No worker")), \
                patch("jsc_live.suggest.Suggester", side_effect=AssertionError("No API client")), \
                patch("tkinter.Tk", side_effect=AssertionError("No window")), patch("sys.stdout", io.StringIO()):
            main(["--review-session", str(self.session), "--review-output", str(output)])
        self.assertTrue((output / "review.html").is_file())
        self.assertTrue((output / "anki.tsv").is_file())


if __name__ == "__main__":
    unittest.main()
