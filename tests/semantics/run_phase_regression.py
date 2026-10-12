"""Run current StoryGraph and runtime semantic regression checks.

The large-game artifacts are intentionally local and ignored by Git.  Full
acceptance requires both a Ren'Py launcher and the preserved large game.
``--static-only`` is deliberately separate so a skipped runtime is never
reported as a passed phase gate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
FIXTURE_GAME = Path(__file__).resolve().parent / "fixtures" / "semantic_cases" / "game"
BASELINE_TOOLS = Path(__file__).resolve().parent / "baseline_tools.py"
FIXTURE_STATE_NAMES = (
    "semantic_score", "semantic_counter", "semantic_random", "semantic_name",
    "phase0_checkpoint",
    "semantic_value", "semantic_items", "persistent.semantic_initialized",
    "persistent.semantic_flag", "persistent.semantic_screen_seen",
)
FIXTURE_GOLDEN_ROOT = FIXTURE_GAME.parent / "golden"


class _HookTraceCollector(object):
    """Collect one JSON payload per connection from a temporary Hook.

    The production Hook currently uses one connection for one JSON message.
    This collector deliberately mirrors that contract only in the test
    harness; transport framing itself remains a phase-7 concern.
    """

    def __init__(self):
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self.port = self._socket.getsockname()[1]
        self._socket.listen(32)
        self._socket.settimeout(0.2)
        self.messages = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="fixture-hook-trace")
        self._thread.daemon = True

    def start(self):
        self._thread.start()

    def close(self):
        self._stop.set()
        self._thread.join(2)
        self._socket.close()

    def _run(self):
        while not self._stop.is_set():
            try:
                client, _address = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                client.settimeout(1)
                chunks = []
                while True:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                value = json.loads(b"".join(chunks).decode("utf-8"))
                if isinstance(value, dict):
                    self.messages.append(value)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                pass
            finally:
                client.close()


def _install_fixture_hook(project, port):
    """Install the same rendered Hook/archive pair used by the injector."""
    from injector import _render_hook_script, _stage_semantics_archive
    from renpylens_semantics.renpy_adapter import graph_id_for

    game = project / "game"
    rendered = _render_hook_script(
        str(ROOT / "assets" / "_translator_hook.rpy"), port, graph_id_for(str(game))
    )
    (game / "_translator_hook.rpy").write_text(rendered, encoding="utf-8", newline="\n")
    staged = _stage_semantics_archive(str(SRC / "renpylens_semantics"), str(game))
    os.replace(staged, str(game / "_renpylens_semantics.zip"))


def _canonical_json(value):
    """Remove run-only fields before fixture golden comparison."""
    if isinstance(value, str):
        # Runner output may retain a debug filename.  Fixture project roots
        # are intentionally ephemeral and cannot be golden evidence.
        return re.sub(r"(?:[A-Za-z]:)?[^\n\r]*renpylens-phase0-[^/\\\n\r]+[/\\]fixture-project",
                      "<fixture-project>", value)
    if isinstance(value, list):
        return [_canonical_json(item) for item in value]
    if not isinstance(value, dict):
        return value
    ignored = set(("process_exit_code", "runner_elapsed_seconds", "elapsed_seconds"))
    return dict((key, _canonical_json(item)) for key, item in value.items()
                if key not in ignored)


def _normalize_hook_trace(messages):
    """Validate v3 ordering, then remove ephemeral transport values."""
    from renpylens_semantics.protocol import EventOrderTracker, PROTOCOL_VERSION

    trackers = {}
    session_numbers = {}
    for message in messages:
        key = (str(message.get("graph_id") or ""), str(message.get("session_id") or ""))
        if key not in trackers:
            trackers[key] = EventOrderTracker()
            trackers[key].establish(*key)
            session_numbers[key] = len(session_numbers) + 1
    normalized = []
    session_ordinals = {}
    for message in messages:
        key = (str(message.get("graph_id") or ""), str(message.get("session_id") or ""))
        tracker = trackers[key]
        if int(message.get("protocol_version", 1) or 1) >= PROTOCOL_VERSION:
            if "event_seq" not in message:
                raise ValueError("fixture Hook trace has a v3 event without event_seq")
            if not tracker.accept(message):
                raise ValueError("fixture Hook trace contains unordered v3 events in arrival order")
            event_type = str(message.get("type") or "")
            node_id = str(message.get("node_id") or "")
            if event_type in ("current", "branch_prefetch", "menu_selected") and node_id:
                if not re.match(r"^N_[0-9a-f]{32}$", node_id):
                    raise ValueError("fixture Hook trace contains a noncanonical node ID")
        ordinal = session_ordinals.get(key, 0) + 1
        session_ordinals[key] = ordinal
        item = _canonical_json(message)
        item.pop("event_seq", None)
        item["session_id"] = "session-%d" % session_numbers[key]
        item["event_order"] = ordinal
        item.pop("control_port", None)
        normalized.append(item)
    types = set(item.get("type") for item in normalized)
    # A trace containing only startup packets proves that the Hook imported,
    # not that it observed the fixture.  The runtime driver must exercise a
    # visible statement, a menu selection, and a genuine rollback invalidation
    # before a golden may be accepted.
    required = set(("current", "menu_selected", "route_invalidated"))
    missing = sorted(required - types)
    if missing:
        raise ValueError("fixture Hook trace did not exercise: %s" % ", ".join(missing))
    return {"fixture": "semantic_cases", "events": normalized}


def _run(name, command, required=True):
    print("[semantics] %s" % name)
    completed = subprocess.run(command, cwd=str(ROOT))
    ok = completed.returncode == 0
    if not ok:
        print("[semantics] %s failed with exit code %s" % (name, completed.returncode))
    return ok or not required


def fixture_lint():
    source = "\n".join(path.read_text(encoding="utf-8") for path in FIXTURE_GAME.glob("*.rpy"))
    required = (
        "label ", "default ", "persistent.", "menu ", "if ", "while ",
        "jump ", "call ", "return", "renpy.random", "renpy.input",
        "call screen", "textbutton", "translate test",
        "phase0_checkpoint",
    )
    missing = [marker for marker in required if marker not in source]
    labels = [
        line.split(":", 1)[0]
        for path in FIXTURE_GAME.glob("*.rpy")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("label ")
    ]
    golden = json.loads((FIXTURE_GAME.parent / "golden.json").read_text(encoding="utf-8"))
    missing_entries = [
        entry for entry in golden.get("entry_labels", [])
        if not any(label.startswith("label " + entry) for label in labels)
    ]
    if missing or missing_entries or len(labels) != len(set(labels)):
        print(json.dumps({
            "missing": missing,
            "missing_entries": missing_entries,
            "duplicate_labels": len(labels) - len(set(labels)),
        }))
        return False
    print("[semantics] fixture labels: %s" % ", ".join(labels))
    return True


def _fixture_project(root):
    root.mkdir(parents=True, exist_ok=True)
    project = root / "fixture-project"
    game = project / "game"
    shutil.copytree(FIXTURE_GAME, game)
    return project


def _fixture_renpy_lint(renpy_executable, output_root):
    project = _fixture_project(output_root / "lint")
    return _run("fixture Ren'Py lint", [str(renpy_executable), str(project), "lint"])


def _fixture_runtime(renpy_executable, output_root, args):
    """Build the fixture graph and one deterministic shadow route per entry.

    The project is copied before the command loader is installed, so neither
    the fixture source nor the supplied engine/game is modified.
    """
    project = _fixture_project(output_root)
    collector = _HookTraceCollector()
    _install_fixture_hook(project, collector.port)
    collector.start()
    golden = json.loads((FIXTURE_GAME.parent / "golden.json").read_text(encoding="utf-8"))
    entries = list(golden.get("entry_labels", []))
    graph_path = output_root / "fixture_story_graph.json"
    graph_command = [
        sys.executable, str(ROOT / "graph" / "run_renpy_story_graph.py"),
        str(renpy_executable), "--project-dir", str(project), "--schema-version", "3",
        "--output", str(graph_path),
    ]
    for entry in entries:
        graph_command.extend(("--entry-point", entry))
    try:
        if not _run("fixture runtime graph", graph_command):
            return None
        routes = []
        for entry in entries:
            route_path = output_root / ("fixture_shadow_%s.json" % entry)
            command = [
                sys.executable, str(ROOT / "tests" / "tools" / "run_renpy_random_playthrough.py"),
                str(renpy_executable), "--project-dir", str(project), "--output", str(route_path),
                "--seed", str(args.seed), "--input-value", args.input_value,
                "--entry-point", entry,
            ]
            for state_name in FIXTURE_STATE_NAMES:
                command.extend(("--state-name", state_name))
            if not _run("fixture shadow route " + entry, command):
                return None
            routes.append(json.loads(route_path.read_text(encoding="utf-8")))
        route_path = output_root / "fixture_shadow_route.json"
        route_path.write_text(json.dumps({"fixture": "semantic_cases", "routes": routes},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        # Sender threads can be scheduled immediately after process exit.
        # Give their final connection a short bounded window before closing.
        time.sleep(0.15)
        collector.close()
    hook_path = output_root / "fixture_hook_trace.json"
    try:
        trace = _normalize_hook_trace(collector.messages)
    except ValueError as error:
        print("[semantics] invalid fixture Hook trace: %s" % error)
        return None
    hook_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"graph": graph_path, "route": route_path, "hook": hook_path}


def _compare_fixture_artifacts(actual, update_goldens=False):
    names = ("fixture_story_graph.json", "fixture_shadow_route.json", "fixture_hook_trace.json")
    if update_goldens:
        FIXTURE_GOLDEN_ROOT.mkdir(parents=True, exist_ok=True)
        for name in names:
            source = actual["graph" if "story" in name else "route" if "shadow" in name else "hook"]
            if not source.is_file():
                print("[semantics] fixture golden source missing: %s" % source.name)
                return False
            shutil.copy2(source, FIXTURE_GOLDEN_ROOT / name)
        return True
    for name in names:
        source = actual["graph" if "story" in name else "route" if "shadow" in name else "hook"]
        expected = FIXTURE_GOLDEN_ROOT / name
        if not source.is_file() or not expected.is_file():
            print("[semantics] fixture golden unavailable: %s" % name)
            return False
        try:
            actual_value = _canonical_json(json.loads(source.read_text(encoding="utf-8")))
            expected_value = _canonical_json(json.loads(expected.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError):
            print("[semantics] fixture golden JSON is invalid: %s" % name)
            return False
        if actual_value != expected_value:
            print("[semantics] fixture golden mismatch: %s" % name)
            return False
    return True


def _verify_v3_graph(path):
    from renpylens_semantics.audit import validate
    from renpylens_semantics.serialization import load_graph

    value = load_graph(path).to_dict()
    audit = validate(value)
    print("[semantics] current graph: nodes=%d edges=%d blocks=%d valid=%s" % (
        len(value["icfg"]["nodes"]), len(value["icfg"]["edges"]),
        len(value["narrative_graph"]["blocks"]), audit["valid"]))
    return audit["valid"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", help="deprecated alias for --large-game")
    parser.add_argument("--renpy-executable", help="Ren'Py launcher used for a temporary fixture project")
    parser.add_argument("--large-game", help="preserved large Ren'Py game directory or executable")
    parser.add_argument("--static-only", action="store_true")
    parser.add_argument("--update-goldens", action="store_true")
    parser.add_argument("--baseline-graph")
    parser.add_argument("--baseline-route")
    parser.add_argument("--graph", help="current v3 graph for comparison")
    parser.add_argument("--route", help="actual route for semantic-route comparison")
    parser.add_argument("--skip-unittest", action="store_true")
    parser.add_argument("--seed", type=int, default=20260908)
    parser.add_argument("--input-value", default="Kagemusha")
    parser.add_argument("--entry-point", default="start")
    parser.add_argument("--output-dir", help="directory for artifacts from this run")
    parser.add_argument("--report", help="JSON acceptance report path")
    args = parser.parse_args()
    if args.game and not args.large_game:
        args.large_game = args.game
    output_root = (Path(args.output_dir).expanduser().resolve() if args.output_dir else
                   Path(tempfile.mkdtemp(prefix="renpylens-phase0-")))
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = (Path(args.report).expanduser().resolve() if args.report else
                   output_root / "phase_regression_report.json")
    statuses = {}

    def status(name, value, reason=""):
        statuses[name] = {"status": value}
        if reason:
            statuses[name]["reason"] = reason

    def finish(code):
        payload = {
            "mode": "static" if args.static_only else "full",
            "result": "passed" if code == 0 else "failed",
            "source": {"root": str(ROOT), "python": sys.version},
            "engine": str(args.renpy_executable or ""),
            "large_game": str(args.large_game or ""),
            "checks": statuses,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[semantics] report: %s" % report_path)
        return code

    if not args.static_only and (not args.renpy_executable or not args.large_game):
        status("fixture_runtime", "not_run", "missing --renpy-executable")
        status("large_rebuild", "not_run", "missing --large-game")
        print("[semantics] full acceptance requires --renpy-executable and --large-game; use --static-only for repository checks")
        return finish(2)

    if not fixture_lint():
        status("fixture_source_check", "failed")
        return finish(1)
    status("fixture_source_check", "passed")
    graph_path = Path(args.graph).expanduser().resolve() if args.graph else None
    route_path = Path(args.route).expanduser().resolve() if args.route else None
    baseline_graph = Path(args.baseline_graph).expanduser().resolve() if args.baseline_graph else None
    baseline_route = Path(args.baseline_route).expanduser().resolve() if args.baseline_route else None
    if not args.static_only:
        if not _fixture_renpy_lint(args.renpy_executable, output_root):
            status("fixture_renpy_lint", "failed")
            return finish(1)
        status("fixture_renpy_lint", "passed")
        fixture = _fixture_runtime(args.renpy_executable, output_root, args)
        if fixture is None or not _compare_fixture_artifacts(fixture, args.update_goldens):
            status("fixture_runtime", "failed")
            return finish(1)
        status("fixture_runtime", "passed")
        graph_path = graph_path or output_root / "large_story_graph.json"
        route_path = route_path or output_root / "large_shadow_route.json"
        if not _run("large v3 graph", [
            sys.executable, str(ROOT / "graph" / "run_renpy_story_graph.py"),
            args.large_game, "--output", str(graph_path),
        ]) or not _verify_v3_graph(graph_path):
            status("large_rebuild", "failed")
            return finish(1)
        status("large_rebuild", "passed")
        route_command = [
            sys.executable, str(ROOT / "tests" / "tools" / "run_renpy_random_playthrough.py"),
            args.large_game, "--output", str(route_path), "--seed", str(args.seed),
            "--input-value", args.input_value, "--entry-point", args.entry_point,
        ]
        for state_name in FIXTURE_STATE_NAMES:
            route_command.extend(("--state-name", state_name))
        if not _run("large deterministic shadow route", route_command):
            status("semantic_route_generation", "failed")
            return finish(1)
        status("semantic_route_generation", "passed")
    else:
        print("[semantics] static-only: fixture runtime and large-game rebuild were not executed")
        status("fixture_runtime", "not_run", "static-only mode")
        status("fixture_renpy_lint", "not_run", "static-only mode")
        status("large_rebuild", "not_run", "static-only mode")

    if not args.skip_unittest and not _run("unit tests", [
        sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v",
    ]):
        status("unit_tests", "failed")
        return finish(1)
    if not args.skip_unittest:
        status("unit_tests", "passed")
    else:
        status("unit_tests", "not_run", "--skip-unittest")

    if baseline_graph and graph_path:
        if not _run("current graph comparison", [
            sys.executable, str(BASELINE_TOOLS), "story-graph",
            str(baseline_graph), str(graph_path),
        ]):
            status("story_graph", "failed")
            return finish(1)
        status("story_graph", "passed")
    else:
        print("[semantics] graph comparison skipped (pass --baseline-graph and --graph/--large-game)")
        status("story_graph", "not_run", "no current rebuild artifact")

    if baseline_route and route_path:
        if not _run("semantic route", [
            sys.executable, str(BASELINE_TOOLS), "semantic-route",
            str(baseline_route), str(route_path),
        ]):
            status("semantic_route", "failed")
            return finish(1)
        status("semantic_route", "passed")
    else:
        print("[semantics] semantic-route skipped (pass --baseline-route and --route/--large-game)")
        status("semantic_route", "not_run", "no current route artifact")
    print("[semantics] regression sequence completed")
    return finish(0)


if __name__ == "__main__":
    raise SystemExit(main())
