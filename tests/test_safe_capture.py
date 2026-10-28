"""Capture entrypoints must not fall back to desktop or private inputs."""
import ctypes
from ctypes import wintypes
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "app")]
import capture_showcase
import test_window
import window_capture


class SafeCaptureTest(unittest.TestCase):
    def test_default_showcase_has_no_import_side_effects_or_data_reads(self):
        with patch.object(capture_showcase, "load_public_inputs", side_effect=AssertionError("No files")), \
                patch.object(capture_showcase, "render", side_effect=AssertionError("No windows")), patch("sys.stdout", io.StringIO()):
            self.assertEqual(capture_showcase.main([]), 0)

    def test_explicit_mode_requires_public_materials_before_rendering(self):
        with patch.object(capture_showcase, "render", side_effect=AssertionError("No windows")), patch("sys.stderr", io.StringIO()):
            for args in (["--render-saved"], ["--render-saved", "--materials", str(ROOT / "logs")]):
                with self.assertRaises(SystemExit) as exc:
                    capture_showcase.main(args)
                self.assertEqual(exc.exception.code, 2)

    def test_frozen_public_inputs_have_four_complete_tts_turns(self):
        rows = capture_showcase.load_public_inputs(capture_showcase.MATERIALS)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row.get("sources") for row in rows))

    def test_changed_public_log_is_rejected(self):
        with patch.object(capture_showcase, "SAVED_TEXT_SHA256", "0" * 64), self.assertRaisesRegex(ValueError, "changed"):
            capture_showcase.load_public_inputs(capture_showcase.MATERIALS)

    def test_foreign_window_is_rejected_before_obtaining_any_dc(self):
        user, gdi, root = Mock(), Mock(), Mock()
        user.GetAncestor.return_value = 123
        def owner(hwnd, pointer):
            ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))[0] = os.getpid() + 1
            return 1
        user.GetWindowThreadProcessId.side_effect = owner
        with patch.object(window_capture, "_apis", return_value=(user, gdi)), self.assertRaisesRegex(RuntimeError, "not owned"):
            window_capture.capture_owned_window(root, Path("unused.png"))
        user.GetDC.assert_not_called()
        user.PrintWindow.assert_not_called()
        gdi.CreateCompatibleDC.assert_not_called()

    def test_offscreen_boundary_failure_withdraws_before_any_capture(self):
        root, api = Mock(), Mock()
        api.GetAncestor.return_value = 123
        api.GetSystemMetrics.side_effect = lambda field: {76: 0, 78: 1920, 77: 0, 79: 1080}[field]
        def rect(hwnd, pointer):
            value = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
            value.left, value.top, value.right, value.bottom = 100, 100, 1000, 500
            return True
        api.GetWindowRect.side_effect = rect
        with patch.object(test_window, "_user32", return_value=api), self.assertRaisesRegex(RuntimeError, "beyond"):
            test_window.verify_offscreen(root)
        root.withdraw.assert_called_once()

    def test_offscreen_and_visible_modes_are_mutually_exclusive(self):
        factory = Mock()
        with self.assertRaises(ValueError):
            test_window.create_test_window(factory, show=True, offscreen=True)
        factory.assert_not_called()

    def test_e2e_screenshot_routes_to_own_window_without_powershell_capture(self):
        import e2e_suggest
        root = Mock()
        root._jsc_test_window_mode = "passive"
        root.winfo_viewable.return_value = True
        path = Path("unused.png")
        with patch.object(window_capture, "capture_owned_window") as capture, \
                patch.object(e2e_suggest.subprocess, "run", side_effect=AssertionError("No screen copy")):
            e2e_suggest.screenshot(root, path)
        capture.assert_called_once_with(root, path)


if __name__ == "__main__":
    unittest.main()
