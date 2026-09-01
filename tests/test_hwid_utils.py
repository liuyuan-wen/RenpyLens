# -*- coding: utf-8 -*-

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hwid_utils import NO_EXPIRY, _extract_expiry


class TrialExpiryTests(unittest.TestCase):
    def test_no_expiry_is_returned_as_a_language_neutral_status(self):
        self.assertEqual(_extract_expiry({"has_expiry": False}), NO_EXPIRY)
        self.assertEqual(_extract_expiry({"no_expiry": True}), NO_EXPIRY)

    def test_real_expiry_dates_keep_their_display_value(self):
        self.assertEqual(
            _extract_expiry({"expires_at": "2030-05-06T12:30:00Z"}),
            "2030-05-06",
        )


if __name__ == "__main__":
    unittest.main()
