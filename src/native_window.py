"""The small native part of a desktop overlay; called on the Qt UI thread."""

import os
import sys


if sys.platform == "darwin":
    from Foundation import NSObject

    class RenpyLensPointerTracker(NSObject):
        """Forward Qt's tracking events and refresh an inactive panel's cursor."""

        def mouseEntered_(self, event):
            self.delegate.mouseEntered_(event)
            self.view.cursorUpdate_(event)

        def mouseMoved_(self, event):
            self.delegate.mouseMoved_(event)
            self.view.cursorUpdate_(event)

        def mouseExited_(self, event):
            self.delegate.mouseExited_(event)


def configure_pointer_tracking(widget):
    """Let a macOS overlay track hovering while the game has focus."""
    if sys.platform != "darwin" or os.environ.get("QT_QPA_PLATFORM", "").casefold() == "offscreen":
        return
    import objc
    from AppKit import (
        NSTrackingArea, NSTrackingActiveInActiveApp, NSTrackingActiveAlways,
        NSTrackingCursorUpdate,
    )

    view = objc.objc_object(c_void_p=int(widget.winId()))
    # NSTrackingArea does not retain its owner. Keep the forwarding objects
    # alive with the Qt widget; the original owner still processes Qt events.
    trackers = []
    for area in view.trackingAreas():
        options = area.options()
        if options & NSTrackingActiveInActiveApp:
            tracker = RenpyLensPointerTracker.alloc().init()
            tracker.view = view
            tracker.delegate = area.owner()
            # AppKit omits cursorUpdate for ActiveAlways, so the tracker
            # refreshes it after forwarding enter/move to Qt.
            options = (options & ~(NSTrackingActiveInActiveApp | NSTrackingCursorUpdate)) | NSTrackingActiveAlways
            replacement = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                area.rect(), options, tracker, area.userInfo(),
            )
            view.removeTrackingArea_(area)
            view.addTrackingArea_(replacement)
            trackers.append(tracker)
    if trackers:
        widget._native_pointer_trackers = trackers


def enforce_topmost(widget):
    if os.environ.get("QT_QPA_PLATFORM", "").casefold() == "offscreen":
        return
    if sys.platform == "win32":
        import win32con
        import win32gui

        win32gui.SetWindowPos(
            int(widget.winId()), win32con.HWND_TOPMOST, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
        )
    elif sys.platform == "darwin":
        import objc
        from AppKit import (
            NSFloatingWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorCanJoinAllApplications,
            NSWindowStyleMaskNonactivatingPanel,
        )

        view = objc.objc_object(c_void_p=int(widget.winId()))
        window = view.window()
        window.setHidesOnDeactivate_(False)
        window.setStyleMask_(window.styleMask() | NSWindowStyleMaskNonactivatingPanel)
        window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorCanJoinAllApplications
        )
        window.setLevel_(NSFloatingWindowLevel)
