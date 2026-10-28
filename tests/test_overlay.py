"""Suggestion display checks without a Tk window, GPU, or network."""

import copy
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from jsc_live.furigana import Segment  # noqa: E402
from jsc_live.overlay import SubtitleWindow  # noqa: E402


class FakeFont:
    def __init__(self, width=7, height=16):
        self.width, self.height = width, height

    def measure(self, text):
        return len(text) * self.width

    def metrics(self, name):
        return self.height


class RecordingCanvas:
    def __init__(self):
        self.items = []

    def create_text(self, x, y, **kwargs):
        font = kwargs["font"]
        lines = kwargs["text"].split("\n")
        limit = kwargs.get("width", max(font.measure(line) for line in lines))
        line_count = sum(max(1, math.ceil(font.measure(line) / max(1, limit))) for line in lines)
        width = min(limit, max(font.measure(line) for line in lines))
        left = x - width / 2 if kwargs.get("anchor", "center") in ("n", "center") else x
        self.items.append({**kwargs, "x": x, "y": y,
                           "bbox": (left, y, left + width, y + line_count * font.height)})
        return len(self.items)

    def create_line(self, *args, **kwargs):
        pass

    def create_rectangle(self, *args, **kwargs):
        pass

    def bbox(self, item):
        return self.items[item - 1]["bbox"]


def display(star=0, show_meanings=True, details=True):
    win = SubtitleWindow.__new__(SubtitleWindow)
    win.show_details = details  # existing checks cover the full (详情) view; glance view has its own tests
    win.width = 380
    win.mode = "full"
    win.show_meanings = show_meanings
    win.recording = False
    win.history_window = None
    win.canvas = RecordingCanvas()
    win.sugg_font = FakeFont(8, 20)
    win.sugg_star_font = FakeFont(9, 20)
    win.sugg_ruby_font = FakeFont(5, 12)
    win.meaning_font = FakeFont(6, 16)
    win.toolbar_font = FakeFont(6, 16)
    win.root = Mock()
    win.suggestion_timeout = 3.0
    win._drag_origin = None
    win.redraw = Mock()
    win.suggestion = {"uid": 1, "timed_out": False, "payload": {
        "state": "done", "reason": "REASON " * 20,
        "replies": [{"style": style, "star": index == star, "warn": index == 1,
                     "segments": [Segment(f"JP{index}")], "meaning": f"MEANING{index} " * 15}
                    for index, style in enumerate(("丁寧", "カジュアル", "かんたん"))]}}
    return win


class OverlaySuggestionTest(unittest.TestCase):
    def test_drag_without_window_press_is_ignored_and_toolbar_clears_old_origin(self):
        win = display()
        event = SimpleNamespace(x=20, y=10, x_root=200, y_root=100)
        win._drag(event)
        win.root.geometry.assert_not_called()
        win._drag_origin = (5, 6)
        win.canvas.find_overlapping = Mock(return_value=[1])
        win.canvas.gettags = Mock(return_value=["btn:meanings"])
        win._start_drag(event)
        self.assertIsNone(win._drag_origin)
        win._drag(event)
        win.root.geometry.assert_not_called()

    def test_drag_after_window_press_moves_window_and_keeps_bottom_in_sync(self):
        win = display()
        win.canvas.find_overlapping = Mock(return_value=[])
        win.root.winfo_x.return_value = 100
        win.root.winfo_y.return_value = 100
        win.root.winfo_height.return_value = 40
        win._start_drag(SimpleNamespace(x=10, y=20, x_root=110, y_root=120))
        win._drag(SimpleNamespace(x_root=210, y_root=220))
        self.assertEqual((win.left, win.bottom), (200, 240))
        win.root.geometry.assert_called_once_with("+200+200")

    def test_long_meanings_wrap_and_later_content_starts_after_them(self):
        win = display()
        bottom = win._draw_suggestion(30, 16, win.width - 32)
        meanings = [item for item in win.canvas.items if item["text"].startswith("MEANING")]
        self.assertEqual(len(meanings), 3)
        self.assertTrue(all(item.get("width", 0) > 0 for item in meanings))
        self.assertTrue(all(item["bbox"][2] <= win.width - 16 for item in meanings))
        next_reply = next(item for item in win.canvas.items if item["text"] == "JP1")
        self.assertGreaterEqual(next_reply["bbox"][1], meanings[0]["bbox"][3])
        self.assertGreaterEqual(bottom, max(item["bbox"][3] for item in win.canvas.items))

    def test_hiding_chinese_keeps_japanese_warning_and_recommendation(self):
        win = display(show_meanings=False)
        win._draw_suggestion(30, 16, win.width - 32)
        texts = [item["text"] for item in win.canvas.items]
        self.assertEqual([text for text in texts if text.startswith("JP")], ["JP0", "JP1", "JP2"])
        self.assertEqual(sum("⭐ 推荐" in text for text in texts), 1)
        self.assertEqual(sum("⚠ 对此对象不要用" == text for text in texts), 1)
        self.assertFalse(any("MEANING" in text or "REASON" in text for text in texts))

    def test_recommendation_changes_font_without_reordering_styles(self):
        for star in range(3):
            with self.subTest(star=star):
                win = display(star=star, show_meanings=False)
                win._draw_suggestion(30, 16, win.width - 32)
                replies = [item for item in win.canvas.items if item["text"].startswith("JP")]
                self.assertEqual([item["text"] for item in replies], ["JP0", "JP1", "JP2"])
                self.assertEqual([item["font"] is win.sugg_star_font for item in replies],
                                 [index == star for index in range(3)])

    def test_translation_toggle_does_not_modify_reply_or_reset_for_a_new_turn(self):
        win = display()
        original = copy.deepcopy(win.suggestion)
        win._press("meanings")
        self.assertFalse(win.show_meanings)
        self.assertEqual(win.suggestion, original)
        win.redraw.assert_called_once()
        win._on_suggest(2, {"state": "pending"})
        win._on_suggest(2, original["payload"])
        self.assertFalse(win.show_meanings)
        win.set_mode("subtitles")
        win.set_mode("full")
        self.assertFalse(win.show_meanings)
        win._press("meanings")
        self.assertTrue(win.show_meanings)

    def test_toolbar_names_the_available_action_and_hides_it_outside_full_mode(self):
        for mode, visible, label in (("full", True, "收起中文"), ("full", False, "展开中文"),
                                     ("collapsed", False, None), ("subtitles", True, None)):
            with self.subTest(mode=mode, visible=visible):
                win = display(show_meanings=visible)
                win.mode = mode
                win._draw_toolbar()
                actions = [item["text"] for item in win.canvas.items if item.get("tags") == "btn:meanings"]
                self.assertEqual(actions, [label] if label else [])



class GlanceViewTest(unittest.TestCase):
    """D-039: in the default glance view only the ⭐ reply is shown in full."""

    def long_display(self):
        win = display(star=0, details=False)
        for reply in win.suggestion["payload"]["replies"]:
            reply["segments"] = [Segment("長い返事です。" * 12)]
        return win

    def texts(self, win):
        return [item["text"] for item in win.canvas.items]

    def test_other_replies_take_one_line_and_hide_their_meaning(self):
        win = self.long_display()
        win._draw_suggestion(0, 10, 360)
        texts = self.texts(win)
        self.assertIn("MEANING0 " * 15, texts)          # ⭐ reply keeps its meaning
        self.assertNotIn("MEANING1 " * 15, texts)
        self.assertNotIn("MEANING2 " * 15, texts)
        self.assertEqual(texts.count("…"), 2)          # each other reply is cut once
        self.assertIn("⚠ 对此对象不要用", texts)          # the warning is never hidden

    def test_reason_is_cut_to_one_line(self):
        win = self.long_display()
        win._draw_suggestion(0, 10, 360)
        reason = next(t for t in self.texts(win) if t.startswith("理由："))
        self.assertTrue(reason.endswith("…"))
        self.assertLessEqual(win.meaning_font.measure(reason), 360)

    def test_glance_view_is_shorter_than_details_view(self):
        glance, full = self.long_display(), display(star=0, details=True)
        for reply in full.suggestion["payload"]["replies"]:
            reply["segments"] = [Segment("長い返事です。" * 12)]
        self.assertLess(glance._draw_suggestion(0, 10, 360), full._draw_suggestion(0, 10, 360))

    def test_clip_text_keeps_short_text_unchanged(self):
        font = FakeFont(6, 16)
        self.assertEqual(SubtitleWindow._clip_text("短い", 100, font), "短い")
        clipped = SubtitleWindow._clip_text("あ" * 50, 60, font)
        self.assertTrue(clipped.endswith("…"))
        self.assertLessEqual(font.measure(clipped), 60)


if __name__ == "__main__":
    unittest.main()
