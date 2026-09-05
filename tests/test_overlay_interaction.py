# -*- coding: utf-8 -*-

import copy
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PyQt5.QtCore import QCoreApplication, QPoint, Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from config import DEFAULT_CONFIG  # noqa: E402
from overlay import TranslationOverlay  # noqa: E402


class OverlayPointerInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        existing = QCoreApplication.instance()
        cls.app = existing if isinstance(existing, QApplication) else QApplication([])

    def setUp(self):
        self.overlay = TranslationOverlay(copy.deepcopy(DEFAULT_CONFIG))
        self.overlay.move(100, 100)

    def tearDown(self):
        self.overlay.close()

    @staticmethod
    def _move_event(buttons, global_pos=QPoint(500, 500), local_pos=QPoint(20, 20)):
        return SimpleNamespace(
            buttons=lambda: buttons,
            globalPos=lambda: global_pos,
            pos=lambda: local_pos,
            accept=lambda: None,
        )

    def test_stale_drag_does_not_move_overlay_without_left_button(self):
        self.overlay._drag_pos = QPoint(10, 10)

        with patch.object(self.overlay, "_persist_window_geometry") as persist:
            self.overlay.mouseMoveEvent(self._move_event(Qt.NoButton))

        self.assertEqual(self.overlay.pos(), QPoint(100, 100))
        self.assertIsNone(self.overlay._drag_pos)
        persist.assert_called_once_with(include_size=True)

    def test_drag_moves_overlay_while_left_button_is_held(self):
        self.overlay._drag_pos = QPoint(10, 10)

        self.overlay.mouseMoveEvent(self._move_event(Qt.LeftButton))

        self.assertEqual(self.overlay.pos(), QPoint(490, 490))
        self.assertIsNotNone(self.overlay._drag_pos)

    def test_reset_position_clears_stale_resize_state(self):
        self.overlay._is_resizing = True
        self.overlay._resize_start_pos = QPoint(100, 100)
        self.overlay._resize_start_size = self.overlay.size()

        with patch.object(self.overlay, "_persist_window_geometry"):
            self.overlay.reset_to_default_position()

        self.assertFalse(self.overlay._is_resizing)
        self.assertIsNone(self.overlay._resize_start_pos)
        self.assertIsNone(self.overlay._resize_start_size)


if __name__ == "__main__":
    unittest.main()
