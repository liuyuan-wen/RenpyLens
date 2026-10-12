"""Platform defaults shared by storage and desktop presentation."""

import os
import sys


def application_data_dir() -> str:
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RenpyLens")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/RenpyLens")
    return os.path.expanduser("~/.renpylens")


DEFAULT_FONT_FAMILY = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei"
