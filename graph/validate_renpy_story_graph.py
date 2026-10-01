"""Compatibility CLI for the StoryGraph Kernel audit service."""

import os
import sys

semantics_dir = os.environ.get("RENPYLENS_SEMANTICS_DIR")
if not semantics_dir:
    semantics_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if semantics_dir not in sys.path:
    sys.path.insert(0, semantics_dir)

from renpylens_semantics.audit import main, validate

__all__ = ["main", "validate"]


if __name__ == "__main__":
    raise SystemExit(main())
