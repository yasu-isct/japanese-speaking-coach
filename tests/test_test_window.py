"""Offline checks: no real Tk root, desktop interaction, audio, or API requests."""

import ctypes
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import test_window as subject  # noqa: E402


class FakeRoot:
    def __init__(self, calls, alpha=1.0):
        self.calls, self.alpha = calls, alpha
        self.current_state = "normal"
        self.destroyed = False

    def withdraw(self):
        self.calls.append(("withdraw",))
        self.current_state = "withdrawn"

    def destroy(self):
        self.calls.append(("destroy",))
        self.destroyed = True

    def update_idletasks(self):
        self.calls.append(("idle",))

    def state(self, value=None):
        if value is not None:
            self.calls.append(("state", value))
            self.current_state = value
        return self.current_state

    def winfo_id(self):
        return 0x123456789

    def attributes(self, name):
        if name != "-alpha":
            raise AssertionError(name)
        return self.alpha


class FakeUser32:
    """Only exposes the allowed APIs; focus/mouse APIs are deliberately absent."""

    hwnd = 0x23456789A
    foreground = 0x3456789AB

    def __init__(self, root, calls, *, drop_flags=None, replace_hwnd=False,
                 fail_layered=False, fail_position=False, steal_focus=False):
        self.root, self.calls = root, calls
        self.style = 0x80  # An existing unrelated flag must survive.
        self.style_reads = 0
        self.drop_flags, self.replace_hwnd = drop_flags, replace_hwnd
        self.fail_layered, self.fail_position = fail_layered, fail_position
        self.steal_focus = steal_focus

    def GetForegroundWindow(self):
        return self.hwnd if self.steal_focus and self.root.current_state == "normal" else self.foreground

    def GetAncestor(self, hwnd, flag):
        self.calls.append(("ancestor", hwnd, flag))
        if hwnd != self.root.winfo_id() or flag != subject.GA_ROOT:
            raise AssertionError("Must resolve the root's real top-level HWND")
        return self.hwnd + int(self.replace_hwnd and self.root.current_state == "normal")

    def IsWindowVisible(self, hwnd):
        self.check_hwnd(hwnd)
        return self.root.current_state == "normal"

    def check_hwnd(self, hwnd):
        if hwnd != self.hwnd:
            raise AssertionError("Win32 operation used a child/truncated HWND")

    def GetWindowLongW(self, hwnd, field):
        self.check_hwnd(hwnd)
        if field != subject.GWL_EXSTYLE:
            raise AssertionError("Only EXSTYLE may be accessed")
        self.style_reads += 1
        value = self.style
        if ((self.drop_flags == "before" and self.style_reads >= 2)
                or (self.drop_flags == "after" and self.root.current_state == "normal")):
            value &= ~subject.WS_EX_TRANSPARENT
        self.calls.append(("read_style", value))
        return value

    def SetWindowLongW(self, hwnd, field, value):
        self.check_hwnd(hwnd)
        if field != subject.GWL_EXSTYLE:
            raise AssertionError("Only EXSTYLE may be changed")
        self.calls.append(("write_style", value))
        previous, self.style = self.style, value
        return previous

    def SetLayeredWindowAttributes(self, hwnd, color, alpha, flags):
        self.check_hwnd(hwnd)
        self.calls.append(("layered", color, alpha, flags))
        return not self.fail_layered

    def SetWindowPos(self, hwnd, insert_after, x, y, width, height, flags):
        self.check_hwnd(hwnd)
        self.calls.append(("position", insert_after.value, x, y, width, height, flags))
        return not self.fail_position


class TestWindowTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.root = FakeRoot(self.calls)
        self.original = Mock(return_value=self.root)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(subject.tk, "Tk", self.original))
        self.stack.enter_context(patch.object(subject.ctypes, "set_last_error", create=True))
        self.stack.enter_context(patch.object(subject.ctypes, "get_last_error", return_value=0, create=True))
        self.stack.enter_context(patch.object(subject.ctypes, "WinError", side_effect=OSError("Win32 failure"), create=True))

    def factory(self, marker=None):
        root = subject.tk.Tk()
        self.assertEqual(root.state(), "withdrawn", "factory must never receive an initially shown root")
        self.calls.append(("factory", marker))
        return SimpleNamespace(root=root)

    def passive(self, **options):
        api = FakeUser32(self.root, self.calls, **options)
        self.stack.enter_context(patch.object(subject.sys, "platform", "win32"))
        self.stack.enter_context(patch.object(subject, "_user32", return_value=api))
        return api

    def assert_closed(self):
        self.assertTrue(self.root.destroyed)
        self.assertEqual(self.root.state(), "withdrawn")
        self.assertIs(subject.tk.Tk, self.original)

    def test_default_stays_hidden_and_restores_tk_before_return(self):
        with patch.object(subject, "_show_passive") as show:
            window = subject.create_test_window(self.factory, "forwarded")
        show.assert_not_called()
        self.assertEqual(self.calls[:2], [("withdraw",), ("factory", "forwarded")])
        self.assertIs(subject.tk.Tk, self.original)
        self.assertEqual(window.root.state(), "withdrawn")
        self.assertEqual(window.test_window, {"mode": "hidden", "visible_display_measured": False})
        self.assertFalse(window.root.destroyed)
        self.assertNotIn(("state", "normal"), self.calls)

    def test_constructor_failure_restores_tk_and_destroys_partial_root(self):
        def broken():
            subject.tk.Tk()
            raise ValueError("constructor failed")

        with self.assertRaisesRegex(ValueError, "constructor failed"):
            subject.create_test_window(broken)
        self.assert_closed()

    def test_non_windows_preview_rejects_before_constructing_any_root(self):
        factory = Mock()
        with patch.object(subject.sys, "platform", "linux"), self.assertRaisesRegex(RuntimeError, "only on Windows"):
            subject.create_test_window(factory, show=True)
        factory.assert_not_called()
        self.original.assert_not_called()

    def test_passive_flags_and_opaque_layer_are_verified_before_show(self):
        api = self.passive()
        window = subject.create_test_window(self.factory, show=True)
        shown_at = self.calls.index(("state", "normal"))
        expected_style = 0x80 | subject.PASSIVE_FLAGS
        self.assertIn(("write_style", expected_style), self.calls[:shown_at])
        self.assertIn(("read_style", expected_style), self.calls[:shown_at])
        self.assertIn(("layered", 0, 255, 2), self.calls[:shown_at])
        positions = [call for call in self.calls if call[0] == "position"]
        self.assertEqual(len(positions), 2)
        self.assertTrue(all(call[-1] & subject.SWP_NOACTIVATE for call in positions))
        self.assertTrue(all(call[-1] & subject.SWP_NOMOVE and call[-1] & subject.SWP_NOSIZE for call in positions))
        self.assertEqual(window.test_window["hwnd"], api.hwnd)
        self.assertEqual(window.test_window["foreground_before"], api.foreground)
        self.assertEqual(window.test_window["foreground_after"], api.foreground)
        self.assertEqual(window.root._jsc_test_window_mode, "passive")
        self.assertIs(subject.tk.Tk, self.original)

    def test_missing_flag_prevents_any_show_and_destroys_window(self):
        self.passive(drop_flags="before")
        with self.assertRaisesRegex(RuntimeError, "did not retain"):
            subject.create_test_window(self.factory, show=True)
        self.assertNotIn(("state", "normal"), self.calls)
        self.assert_closed()

    def test_layered_failure_prevents_show_even_at_full_opacity(self):
        self.passive(fail_layered=True)
        with self.assertRaises(OSError):
            subject.create_test_window(self.factory, show=True)
        self.assertNotIn(("state", "normal"), self.calls)
        self.assert_closed()

    def test_position_failure_prevents_show(self):
        self.passive(fail_position=True)
        with self.assertRaises(OSError):
            subject.create_test_window(self.factory, show=True)
        self.assertNotIn(("state", "normal"), self.calls)
        self.assert_closed()

    def test_replaced_hwnd_after_show_is_destroyed(self):
        self.passive(replace_hwnd=True)
        with self.assertRaisesRegex(RuntimeError, "changed while showing"):
            subject.create_test_window(self.factory, show=True)
        self.assert_closed()

    def test_lost_flag_after_show_is_destroyed(self):
        self.passive(drop_flags="after")
        with self.assertRaisesRegex(RuntimeError, "changed while showing"):
            subject.create_test_window(self.factory, show=True)
        self.assert_closed()

    def test_unexpected_foreground_activation_is_destroyed_without_refocusing(self):
        self.passive(steal_focus=True)
        with self.assertRaisesRegex(RuntimeError, "foreground"):
            subject.create_test_window(self.factory, show=True)
        self.assert_closed()

    def test_win32_signatures_keep_hwnd_pointer_sized_and_style_long(self):
        api = SimpleNamespace(**{name: Mock() for name in (
            "GetAncestor", "GetWindowLongW", "SetWindowLongW", "SetLayeredWindowAttributes",
            "SetWindowPos", "GetForegroundWindow", "IsWindowVisible", "GetSystemMetrics", "GetWindowRect")})
        with patch.object(subject.ctypes, "WinDLL", return_value=api, create=True) as loader:
            result = subject._user32()
        loader.assert_called_once_with("user32", use_last_error=True)
        self.assertIs(result, api)
        self.assertIs(api.GetAncestor.restype, subject.wintypes.HWND)
        self.assertIs(api.GetForegroundWindow.restype, subject.wintypes.HWND)
        self.assertEqual(api.SetWindowLongW.argtypes, [subject.wintypes.HWND, ctypes.c_int, subject.wintypes.LONG])
        self.assertEqual(api.SetWindowPos.argtypes[:2], [subject.wintypes.HWND, subject.wintypes.HWND])


if __name__ == "__main__":
    unittest.main()
