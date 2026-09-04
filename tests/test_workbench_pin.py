import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from main import MainWindow  # noqa: E402


class WorkbenchStub:
    def __init__(self, visible=False):
        self.visible = visible
        self.pinned = None

    def isVisible(self):
        return self.visible

    def update_config(self, config):
        self.pinned = config["workbench_pinned"]

    def isMinimized(self):
        return False

    def show(self):
        self.visible = True

    def raise_(self):
        pass

    def activateWindow(self):
        pass

    def focus_entry(self, _source):
        pass


def make_host(main_pinned, workbench_visible=False):
    host = SimpleNamespace(
        config={"workbench_pinned": not main_pinned},
        _is_pinned=main_pinned,
        workbench=WorkbenchStub(visible=workbench_visible),
        _refresh_workbench_entries=lambda selected_source="": None,
        _update_workbench_toggle_button=lambda: None,
    )
    return host


class WorkbenchPinTests(unittest.TestCase):
    def test_workbench_pin_follows_main_window_when_opened(self):
        for main_pinned in (False, True):
            with self.subTest(main_pinned=main_pinned):
                host = make_host(main_pinned)

                MainWindow._show_workbench(host)

                self.assertIs(host.config["workbench_pinned"], main_pinned)
                self.assertIs(host.workbench.pinned, main_pinned)

    def test_visible_workbench_keeps_its_independent_pin_state(self):
        host = make_host(main_pinned=True, workbench_visible=True)

        MainWindow._show_workbench(host)

        self.assertIs(host.config["workbench_pinned"], False)
        self.assertIsNone(host.workbench.pinned)
