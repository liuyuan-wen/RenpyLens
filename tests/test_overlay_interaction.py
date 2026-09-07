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

from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, QRect, QSize, Qt  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtGui import QFontDatabase, QMouseEvent, QPixmap  # noqa: E402
from PyQt5.QtWidgets import QApplication, QStyle, QStyleOptionSlider  # noqa: E402

from config import DEFAULT_CONFIG  # noqa: E402
from main import MainWindow  # noqa: E402
from overlay import TranslationOverlay  # noqa: E402


class OverlayPointerInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        existing = QCoreApplication.instance()
        cls.app = existing if isinstance(existing, QApplication) else QApplication([])
        # The offscreen platform does not discover Windows fonts automatically.
        font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("msyh.ttc", "msyhbd.ttc"):
            QFontDatabase.addApplicationFont(str(font_dir / name))

    def setUp(self):
        self.overlay = TranslationOverlay(copy.deepcopy(DEFAULT_CONFIG))
        self.overlay._save_config = lambda: None
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
        persist.assert_called_once_with(include_size=False)

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

    def test_read_body_cursor_is_independent_from_resize_border(self):
        self.overlay.setCursor(Qt.SizeHorCursor)

        self.assertEqual(self.overlay.cursor().shape(), Qt.SizeHorCursor)
        self.assertEqual(self.overlay.read_container.cursor().shape(), Qt.OpenHandCursor)
        self.assertEqual(self.overlay.label.cursor().shape(), Qt.OpenHandCursor)
        self.assertEqual(self.overlay.text_scrollbar.cursor().shape(), Qt.ArrowCursor)

        self.overlay._set_read_drag_cursor(Qt.ClosedHandCursor)
        self.assertEqual(self.overlay.read_container.cursor().shape(), Qt.ClosedHandCursor)
        self.overlay._clear_pointer_interaction()
        self.assertEqual(self.overlay.read_container.cursor().shape(), Qt.OpenHandCursor)

    def test_visibility_toggle_keeps_position_until_reset_is_requested(self):
        class OverlayStub:
            def __init__(self):
                self.visible = True
                self.reset_count = 0

            def isVisible(self):
                return self.visible

            def hide(self):
                self.visible = False

            def show(self):
                self.visible = True

            def reset_to_default_position(self):
                self.reset_count += 1

        overlay = OverlayStub()
        host = SimpleNamespace(overlay=overlay)

        MainWindow._toggle_overlay_visibility(host)
        MainWindow._toggle_overlay_visibility(host)

        self.assertTrue(overlay.visible)
        self.assertEqual(overlay.reset_count, 0)

        MainWindow._reset_overlay_position(host)
        self.assertEqual(overlay.reset_count, 1)

    def test_overlay_menu_expands_to_the_left_of_its_arrow(self):
        class MenuStub:
            def __init__(self):
                self.popup_position = None

            def sizeHint(self):
                return QSize(120, 40)

            def popup(self, position):
                self.popup_position = position

        class ButtonStub:
            @staticmethod
            def width():
                return 34

            @staticmethod
            def height():
                return 38

            @staticmethod
            def mapToGlobal(position):
                return QPoint(200, 100) + position

        menu = MenuStub()
        host = SimpleNamespace(overlay_menu=menu, btn_overlay_menu=ButtonStub())

        MainWindow._show_overlay_menu(host)

        self.assertEqual(menu.popup_position, QPoint(114, 138))

    def test_screen_text_flag_does_not_force_a_background_for_short_text(self):
        self.assertFalse(self.overlay.label._screen_text_mode)

        self.overlay.set_screen_text_mode(True)
        self.assertTrue(self.overlay.label._screen_text_mode)
        self.overlay.label.setText("Short screen label")
        with patch.object(self.overlay, "_screen_limits", return_value=(1600, 1000)):
            self.overlay._adjust_height()
        self.assertFalse(self.overlay._long_text_mode)
        self.assertIn("background: transparent", self.overlay.read_container.styleSheet())

        self.overlay.set_screen_text_mode(False)
        self.assertFalse(self.overlay.label._screen_text_mode)

    def test_long_text_layout_is_reused_until_text_or_width_changes(self):
        label = self.overlay.label
        label.resize(800, 100)
        label.setText("Long screen text " * 200)

        first_layout = label._get_text_layout()
        self.assertIs(first_layout, label._get_text_layout())

        label.setText("Different screen text " * 200)
        self.assertIsNot(first_layout, label._get_text_layout())

        second_layout = label._get_text_layout()
        label.resize(700, label.height())
        self.assertIsNot(second_layout, label._get_text_layout())

    def test_painting_does_not_expand_label_to_full_long_text_height(self):
        label = self.overlay.label
        label.resize(800, label.height())
        label.setFixedHeight(300)
        label.setText("Long translated line\n" * 80)
        target = QPixmap(label.size())

        label.render(target)

        self.assertEqual(label.height(), 300)

    def test_set_text_selects_long_text_geometry_before_returning(self):
        self.overlay.config["overlay_width"] = 800
        self.overlay.config["overlay_long_height"] = 0
        with patch.object(self.overlay, "_screen_limits", return_value=(1600, 1000)):
            self.overlay.set_text("Long translated line\n" * 80)

        self.assertTrue(self.overlay._long_text_mode)
        self.assertEqual(self.overlay.height(), 308)
        self.assertFalse(self.overlay._height_adjust_timer.isActive())

    def test_smaller_saved_long_height_has_no_first_frame_top_gap(self):
        self.overlay.config["overlay_width"] = 800
        self.overlay.config["overlay_long_height"] = 300
        self.overlay.label.setText("Long translated line\n" * 80)
        with patch.object(self.overlay, "_screen_limits", return_value=(1600, 1000)):
            self.overlay._adjust_height()
            self.overlay.show()
            self.app.processEvents()
            self.assertEqual(self.overlay.height(), 308)

            self.overlay.config["overlay_long_height"] = 180
            self.overlay._adjust_height()

        self.assertEqual(self.overlay.height(), 188)
        self.assertEqual(self.overlay.read_container.height(), 180)
        self.assertEqual(self.overlay.label.y(), self.overlay.LONG_TEXT_PANEL_PADDING)
        self.assertEqual(self.overlay.label.height(), 164)

    def test_painted_long_text_is_reused_while_dragging(self):
        label = self.overlay.label
        label.resize(800, 400)
        label.setText("Long screen text " * 200)
        path, _ = label._get_text_layout()

        first_paint = label._get_painted_text(path)
        self.assertIs(first_paint, label._get_painted_text(path))

        label.set_screen_text_mode(True)
        self.assertIsNot(first_paint, label._get_painted_text(path))

    def test_long_text_raster_cache_only_covers_visible_viewport(self):
        label = self.overlay.label
        label.resize(800, 300)
        label.setText("Long screen text\n" * 200)
        label.set_long_text_mode(True)
        path, content_height = label._get_text_layout()

        first_paint = label._get_painted_text(path)
        label.set_scroll_offset(100)
        scrolled_paint = label._get_painted_text(path)

        self.assertGreater(content_height, label.height())
        self.assertEqual(first_paint.size(), label.size())
        self.assertEqual(scrolled_paint.size(), label.size())
        self.assertIsNot(first_paint, scrolled_paint)

    def test_drag_press_cancels_pending_height_adjustment(self):
        self.overlay._height_adjust_timer.start(1000)
        event = SimpleNamespace(
            button=lambda: Qt.LeftButton,
            pos=lambda: QPoint(20, 20),
            globalPos=lambda: QPoint(500, 500),
            accept=lambda: None,
        )

        self.overlay.mousePressEvent(event)

        self.assertFalse(self.overlay._height_adjust_timer.isActive())
        self.assertIsNotNone(self.overlay._drag_pos)

    def test_drag_release_does_not_relayout_or_persist_overlay_size(self):
        self.overlay._drag_pos = QPoint(10, 10)
        event = SimpleNamespace(accept=lambda: None)

        with (
            patch.object(self.overlay, "_adjust_height") as adjust,
            patch.object(self.overlay, "_persist_window_geometry") as persist,
        ):
            self.overlay.mouseReleaseEvent(event)

        adjust.assert_not_called()
        persist.assert_called_once_with(include_size=False)

    def test_moving_long_text_overlay_keeps_its_exact_size(self):
        self.overlay.config["overlay_width"] = 920
        self.overlay.config["overlay_long_height"] = 180
        self.overlay.label.setText("Long translated line\n" * 80)
        with patch.object(self.overlay, "_screen_limits", return_value=(1600, 1000)):
            self.overlay._adjust_height()
        original_size = self.overlay.size()
        self.overlay._drag_pos = QPoint(10, 10)

        with patch.object(self.overlay, "_persist_window_geometry"):
            self.overlay.mouseReleaseEvent(SimpleNamespace(accept=lambda: None))

        self.assertEqual(self.overlay.size(), original_size)

    def test_long_text_uses_thirty_percent_viewport_and_scrollbar(self):
        self.overlay.config["overlay_width"] = 800
        self.overlay.config["overlay_long_height"] = 0
        self.overlay.label.setText("Long translated line\n" * 40)

        with patch.object(self.overlay, "_screen_limits", return_value=(1600, 1000)):
            self.overlay._adjust_height()

        self.assertTrue(self.overlay._long_text_mode)
        self.assertTrue(self.overlay.label._long_text_mode)
        self.assertFalse(self.overlay.text_scrollbar.isHidden())
        self.assertEqual(self.overlay.height(), 308)
        self.assertEqual(self.overlay.label.height(), 284)
        self.assertGreater(self.overlay.text_scrollbar.maximum(), 0)
        self.assertIn("rgba(8, 10, 16, 190)", self.overlay.read_container.styleSheet())

    def test_short_text_does_not_enable_long_text_container(self):
        self.overlay.label.setText("Short translation")

        with patch.object(self.overlay, "_screen_limits", return_value=(1600, 1000)):
            self.overlay._adjust_height()

        self.assertFalse(self.overlay._long_text_mode)
        self.assertTrue(self.overlay.text_scrollbar.isHidden())
        self.assertIn("background: transparent", self.overlay.read_container.styleSheet())
        self.assertTrue(all(handle.isHidden() for handle in self.overlay._read_resize_handles.values()))

    def test_long_text_window_can_resize_from_every_border(self):
        start = QRect(100, 100, 800, 308)
        cases = (
            ({"left"}, QPoint(-50, 0), QRect(50, 100, 850, 308)),
            ({"right"}, QPoint(50, 0), QRect(100, 100, 850, 308)),
            ({"top"}, QPoint(0, -50), QRect(100, 50, 800, 358)),
            ({"bottom"}, QPoint(0, 50), QRect(100, 100, 800, 358)),
        )
        self.overlay._long_text_mode = True
        self.overlay.label.set_long_text_mode(True)
        with patch.object(self.overlay, "_screen_limits", return_value=(1600, 1000)):
            for edges, delta, expected in cases:
                self.overlay.setGeometry(start)
                self.overlay._resize_edges = edges
                self.overlay._resize_start_pos = QPoint(500, 500)
                self.overlay._resize_start_geometry = QRect(start)
                self.overlay._resize_read_window(QPoint(500, 500) + delta)
                self.assertEqual(self.overlay.geometry(), expected)

    def test_live_resize_reflows_text_before_release(self):
        self.overlay.show()
        self.app.processEvents()
        self.overlay.config["overlay_width"] = 800
        self.overlay.label.setText("Long translated line " * 400)
        with patch.object(self.overlay, "_screen_limits", return_value=(1600, 1000)):
            self.overlay._adjust_height()
        before_height = self.overlay.label._get_text_layout()[1]
        self.overlay._resize_edges = {"right"}
        self.overlay._resize_start_pos = QPoint(500, 500)
        self.overlay._resize_start_geometry = self.overlay.geometry()
        self.overlay._resize_read_window(QPoint(200, 500))

        content_height = self.overlay.label._get_text_layout()[1]
        self.assertGreater(content_height, before_height)
        self.assertEqual(
            self.overlay.text_scrollbar.maximum(),
            content_height - self.overlay.label.height(),
        )
        self.assertFalse(self.overlay.label._paint_frozen)

    def test_long_text_reuses_rasterized_lines_when_scrolling(self):
        label = self.overlay.label
        label.resize(800, 300)
        label.setText("".join(f"Line {i} with <i>italics</i>\n" for i in range(2000)))
        lines, height = label._get_text_layout()
        self.assertEqual(len(label._line_images), 0)
        label._get_painted_text(lines)
        first_images = dict(label._line_images)
        self.assertLess(len(first_images), 20)
        label.set_scroll_offset(label.fontMetrics().height())
        label._get_painted_text(lines)
        for key, pixmap in first_images.items():
            self.assertIs(label._line_images[key], pixmap)
        label.set_scroll_offset(height - label.height())
        label._get_painted_text(lines)
        self.assertLess(len(label._line_images), 40)

    def test_line_pixels_reused_after_width_change_and_invalidated_by_style(self):
        label = self.overlay.label
        label.resize(800, 300)
        label.setText("Short <i>italic</i> line")
        lines, _ = label._get_text_layout()
        first = label._get_line_image(lines[0])
        label.resize(700, 300)
        lines, _ = label._get_text_layout()
        self.assertIs(first, label._get_line_image(lines[0]))
        label.set_text_color("#ff0000")
        self.assertIsNot(first, label._get_line_image(lines[0]))
        for i in range(300):
            label._get_line_image([(str(i), False)])
        self.assertLessEqual(len(label._line_images), 256)

    def test_long_text_resize_handles_are_real_mouse_targets(self):
        self.overlay.config["overlay_width"] = 800
        self.overlay.label.setText("Long translated line\n" * 40)
        with patch.object(self.overlay, "_screen_limits", return_value=(1600, 1000)):
            self.overlay._adjust_height()
            self.overlay.show()
            self.app.processEvents()

            self.assertTrue(all(not handle.isHidden() for handle in self.overlay._read_resize_handles.values()))
            right = self.overlay._read_resize_handles["right"]
            self.assertEqual(right.width(), self.overlay.READ_RESIZE_HANDLE_SIZE)
            self.assertEqual(right.x(), self.overlay.width() - right.width())

            start = right.rect().center()
            start_global = right.mapToGlobal(start)
            QTest.mousePress(right, Qt.LeftButton, pos=start)
            target = start + QPoint(50, 0)
            move = QMouseEvent(
                QEvent.MouseMove,
                target,
                start_global + QPoint(50, 0),
                Qt.NoButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
            QApplication.sendEvent(right, move)
            QTest.mouseRelease(right, Qt.LeftButton, pos=target)

            self.assertEqual(self.overlay.width(), 850)

    def test_long_text_scrollbar_can_be_dragged(self):
        self.overlay.config["overlay_width"] = 800
        self.overlay.label.setText("Long translated line\n" * 80)
        with patch.object(self.overlay, "_screen_limits", return_value=(1600, 1000)):
            self.overlay._adjust_height()
        self.overlay.show()
        self.app.processEvents()

        scrollbar = self.overlay.text_scrollbar
        option = QStyleOptionSlider()
        scrollbar.initStyleOption(option)
        handle = scrollbar.style().subControlRect(
            QStyle.CC_ScrollBar,
            option,
            QStyle.SC_ScrollBarSlider,
            scrollbar,
        )
        QTest.mousePress(scrollbar, Qt.LeftButton, pos=handle.center())
        target = QPoint(handle.center().x(), scrollbar.height() - 10)
        move = QMouseEvent(
            QEvent.MouseMove,
            target,
            scrollbar.mapToGlobal(target),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        QApplication.sendEvent(scrollbar, move)
        QTest.mouseRelease(
            scrollbar,
            Qt.LeftButton,
            pos=target,
        )

        self.assertGreater(scrollbar.value(), 0)
        self.assertEqual(self.overlay.label._scroll_offset, scrollbar.value())

    def test_mouse_wheel_over_long_text_scrolls_the_panel(self):
        self.overlay.config["overlay_width"] = 800
        self.overlay.label.setText("Long translated line\n" * 80)
        with patch.object(self.overlay, "_screen_limits", return_value=(1600, 1000)):
            self.overlay._adjust_height()
        self.overlay.show()
        self.app.processEvents()

        accepted = []
        wheel = SimpleNamespace(
            type=lambda: QEvent.Wheel,
            pixelDelta=lambda: QPoint(),
            angleDelta=lambda: QPoint(0, -120),
            accept=lambda: accepted.append(True),
        )
        handled = self.overlay.eventFilter(self.overlay.read_container, wheel)

        self.assertTrue(handled)
        self.assertEqual(accepted, [True])
        self.assertGreater(self.overlay.text_scrollbar.value(), 0)
        self.assertEqual(
            self.overlay.label._scroll_offset,
            self.overlay.text_scrollbar.value(),
        )

    def test_language_selector_preserves_codes_versions_and_credits(self):
        host = SimpleNamespace(
            _screen_text_active=True,
            _last_displayed_data={},
            config={"show_character_name": True},
            _normalize_speaker=lambda value: value,
            _resolve_runtime_tokens=lambda text, _source: text,
        )

        display = MainWindow._format_display(
            host,
            "",
            "",
            "",
            choices=[
                "RU 1.07 Язык по умолчанию",
                "EN 1.07 PGN",
                "ES 1.06 cur3n79",
            ],
            choice_translations=["默认语言", "语言设置", "语言设置"],
        )

        self.assertEqual(
            display,
            "[1] RU 1.07 默认语言\n[2] EN 1.07 PGN\n[3] ES 1.06 cur3n79",
        )


if __name__ == "__main__":
    unittest.main()
