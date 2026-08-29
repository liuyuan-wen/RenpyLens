# -*- coding: utf-8 -*-

import os
import sys
import unittest

from PyQt5.QtCore import QCoreApplication


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from hook_server import HookServer


class HookServerProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_structured_rpgmaker_event_keeps_source_display_tokens_and_choices(self):
        server = HookServer()
        events = []
        prefetch = []
        server.text_event_received.connect(events.append)
        server.prefetch_received.connect(prefetch.extend)

        server._emit_current_text(
            {
                "engine": "rpgmaker_mz",
                "session_id": "session-1",
                "current_segment": {
                    "source": "Gold: ⟦RL_V_1⟧ ⟦RL_G⟧",
                    "display_text": "Gold: 250 G",
                    "token_values": {"⟦RL_V_1⟧": "250", "⟦RL_G⟧": "G"},
                    "who": "Merchant",
                },
                "choice_segments": [
                    {
                        "source": "Buy ⟦RL_V_2⟧",
                        "display_text": "Buy 3",
                        "token_values": {"⟦RL_V_2⟧": "3"},
                    }
                ],
                "prefetch": [
                    {
                        "source": "Next ⟦RL_N_1⟧",
                        "display_text": "Next Harold",
                        "token_values": {"⟦RL_N_1⟧": "Harold"},
                    }
                ],
                "menu_active": True,
            }
        )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["current"]["source"], "Gold: ⟦RL_V_1⟧ ⟦RL_G⟧")
        self.assertEqual(event["current"]["display_text"], "Gold: 250 G")
        self.assertEqual(event["choices"][0]["source"], "Buy ⟦RL_V_2⟧")
        self.assertTrue(event["menu_active"])
        self.assertEqual(prefetch[0]["source"], "Next ⟦RL_N_1⟧")

    def test_legacy_renpy_payload_still_uses_existing_signal(self):
        server = HookServer()
        received = []
        server.text_received.connect(lambda *args: received.append(args))

        server._emit_current_text(
            {
                "who": "Eileen",
                "what": "Hello",
                "italic": True,
                "choices": ["Yes", "No"],
                "menu_active": True,
            }
        )

        self.assertEqual(received, [("Eileen", "Hello", True, ["Yes", "No"], True)])


if __name__ == "__main__":
    unittest.main()
