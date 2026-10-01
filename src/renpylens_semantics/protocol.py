"""Versioned runtime semantic event envelope helpers."""

PROTOCOL_VERSION = 3


class EventSequencer(object):
    def __init__(self, start=0):
        self.value = int(start or 0)

    def next(self):
        self.value += 1
        return self.value


class EventOrderTracker(object):
    """Track v3 state and event order while retaining legacy messages.

    Current text and branch predictions use separate socket connections, so
    they may arrive in either order within one state revision. Each stream
    still rejects duplicates; selection and route changes remain barriers.

    The tracker intentionally has no Ren'Py dependency so the desktop host,
    fixtures, and protocol unit tests share exactly the same ordering rule.
    """

    def __init__(self):
        self.reset()

    def reset(self, graph_id="", session_id=""):
        self.graph_id = str(graph_id or "")
        self.session_id = str(session_id or "")
        self.state_version = -1
        self.event_seq = -1
        self._text_event_seqs = {}
        self._ordered_event_seq = -1

    def establish(self, graph_id="", session_id=""):
        self.reset(graph_id, session_id)

    def accept(self, message):
        message = message or {}
        if protocol_version(message) < PROTOCOL_VERSION:
            return True
        session_id = str(message.get("session_id") or "")
        graph_id = str(message.get("graph_id") or "")
        if not session_id or not graph_id:
            return False
        try:
            event_seq = int(message.get("event_seq", -1))
        except (TypeError, ValueError):
            return False
        if event_seq < 0:
            return False
        try:
            state_version = int(message.get("state_version", -1))
        except (TypeError, ValueError):
            return False
        message_type = str(message.get("type") or "")
        if message_type in (
                "current", "branch_prefetch", "menu_selected", "route_invalidated"):
            if state_version < 0:
                return False
        if self.session_id and session_id != self.session_id:
            return False
        if self.graph_id and graph_id and graph_id != self.graph_id:
            return False
        if event_seq >= 0 and self.event_seq >= 0 and event_seq <= self.event_seq:
            # A later prediction must not hide current text from the same
            # state. Do not reorder either stream across a route control event
            # or an already accepted event of its own type.
            if not (
                message_type in ("current", "branch_prefetch")
                and state_version == self.state_version
                and event_seq > self._ordered_event_seq
                and event_seq > self._text_event_seqs.get(message_type, -1)
            ):
                return False
        if state_version >= 0 and self.state_version >= 0 and state_version < self.state_version:
            return False
        if session_id:
            self.session_id = session_id
        if graph_id:
            self.graph_id = graph_id
        self.event_seq = max(self.event_seq, event_seq)
        if message_type in ("current", "branch_prefetch"):
            self._text_event_seqs[message_type] = event_seq
        else:
            self._ordered_event_seq = event_seq
        if state_version >= 0:
            self.state_version = max(self.state_version, state_version)
        return True


def envelope(message_type, graph_id="", session_id="", state_version=None,
             node_id="", event_seq=None, edge_id=None, block_id=None, **payload):
    result = {
        "type": str(message_type or ""),
        "protocol_version": PROTOCOL_VERSION,
        "graph_id": str(graph_id or ""),
        "session_id": str(session_id or ""),
        "event_seq": int(event_seq) if event_seq is not None else 0,
        "node_id": str(node_id or ""),
    }
    if state_version is not None:
        result["state_version"] = int(state_version)
    if edge_id:
        result["edge_id"] = str(edge_id)
    if block_id:
        result["block_id"] = str(block_id)
    result.update(payload)
    return result


def protocol_version(message):
    try:
        return int((message or {}).get("protocol_version", 1))
    except (TypeError, ValueError):
        return 1
