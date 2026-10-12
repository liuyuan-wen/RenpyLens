"""Shared desktop spacing and typography, expressed in Qt logical pixels.

The original desktop styles use large Windows controls.  macOS uses a denser
profile; Retina scaling remains Qt's responsibility.  Keep platform sizing here
so individual views do not need their own operating-system branches.
"""

import re
import sys

UI_SCALE = 0.65 if sys.platform == "darwin" else 1.0
PRIMARY_ACTION_FONT_PX = 13 if sys.platform == "darwin" else 26
BRAND_ICON_PX = 40 if sys.platform == "darwin" else 80
_PIXELS = re.compile(r"(font-size\s*:\s*)?(?<![\w.])(\d+)px\b")
_FONT_FAMILY = re.compile(r"font-family\s*:\s*([^;]+);")
_BUTTON_WEIGHT = re.compile(r"(QPushButton\s*\{[^{}]*?)font-weight\s*:\s*bold;", re.DOTALL)


def ui_px(value: int) -> int:
    """Convert legacy desktop sizes while keeping hairline borders crisp."""
    return value if value <= 1 else max(1, round(value * UI_SCALE))


def ui_font_px(value: int) -> int:
    """Match native macOS control text without scaling it like spacing."""
    if UI_SCALE == 1.0:
        return value
    if value <= 17:
        return 12
    if value < 24:
        return 13
    if value < 32:
        return round(value * 0.6)
    return round(value * 0.55)


def settings_icon(size: int):
    """Render a font-independent gear at standard and high display densities."""
    from PyQt5.QtCore import QRectF, Qt
    from PyQt5.QtGui import QIcon, QPainter, QPixmap
    from PyQt5.QtSvg import QSvgRenderer

    teeth = ''.join(
        f'<rect x="10" y="1" width="4" height="6" rx="0.6" '
        f'transform="rotate({angle} 12 12)"/>'
        for angle in range(0, 360, 45)
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        f'<g fill="#888">{teeth}'
        '<path fill-rule="evenodd" d="M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16'
        'M12 8.5a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7"/>'
        '</g></svg>'
    )
    renderer = QSvgRenderer(svg.encode('utf-8'))
    icon = QIcon()
    for density in (1, 2, 3):
        pixmap = QPixmap(size * density, size * density)
        pixmap.setDevicePixelRatio(density)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def ui_font_family() -> str:
    """Choose Chinese glyphs from the UI locale rather than macOS's locale."""
    from PyQt5.QtGui import QFontDatabase
    from i18n import manager

    chinese_family = {"zh_CN": "PingFang SC", "zh_TW": "PingFang TC"}
    return chinese_family.get(
        manager().locale, QFontDatabase.systemFont(QFontDatabase.GeneralFont).family()
    )


def refresh_ui_font(window):
    """Refresh the shared font after a language switch, keeping sizes intact."""
    if UI_SCALE != 1.0:
        window.setStyleSheet(_FONT_FAMILY.sub(
            lambda match: f'font-family: "{ui_font_family()}";',
            window.styleSheet(),
        ))


def ui_style(style: str) -> str:
    """Apply desktop sizing to a stylesheet or a CSS declaration list."""
    if UI_SCALE == 1.0:
        return style

    def font_family(match):
        family = match.group(1)
        if "Microsoft YaHei" in family:
            return f'font-family: "{ui_font_family()}";'
        if "Consolas" in family:
            return 'font-family: "Menlo", monospace;'
        return match.group(0)

    style = _FONT_FAMILY.sub(font_family, style)
    style = _BUTTON_WEIGHT.sub(r"\1font-weight: normal;", style)
    def pixels(match):
        font_prefix, number = match.groups()
        size = ui_font_px(int(number)) if font_prefix else ui_px(int(number))
        return f"{font_prefix or ''}{size}px"

    return _PIXELS.sub(pixels, style)


def ui_rich_text(text: str) -> str:
    """Scale inline styles without changing visible text such as game names."""
    return re.sub(r"(style\s*=\s*)([\"'])(.*?)\2",
                  lambda m: m[1] + m[2] + ui_style(m[3]) + m[2], text,
                  flags=re.DOTALL)


def fit_window_to_screen(window):
    """Keep restored desktop windows inside the current usable screen."""
    screen = window.screen()
    if screen is None:
        return
    bounds = screen.availableGeometry().adjusted(12, 12, -12, -12)
    # Leave room for the native title bar, which is outside QWidget's size.
    title_height = max(28, window.frameGeometry().height() - window.height())
    window.resize(min(window.width(), bounds.width()),
                  min(window.height(), bounds.height() - title_height))
    frame = window.frameGeometry()
    x = max(bounds.left(), min(frame.left(), bounds.right() - frame.width() + 1))
    y = max(bounds.top(), min(frame.top(), bounds.bottom() - frame.height() + 1))
    window.move(x, y)
