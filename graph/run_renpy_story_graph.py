"""Extract a semantic ICFG and narrative basic-block graph from a Ren'Py game."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph.renpy_runner import TemporaryLoader, resolve_executable


RUNTIME_SOURCE = Path(__file__).with_name("renpy_story_graph_runtime.py")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("game", help="Ren'Py game directory or executable")
    parser.add_argument("--output", default="tests/renpy_story_graph.json")
    parser.add_argument("--narrative-output")
    parser.add_argument("--entry-point", action="append", dest="entry_points")
    parser.add_argument("--schema-version", type=int, choices=(2, 3), default=3)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--project-dir", help="temporary Ren'Py project root; defaults to the executable directory")
    args = parser.parse_args()

    executable = resolve_executable(args.game)
    project_dir = Path(args.project_dir).expanduser().resolve() if args.project_dir else executable.parent
    game_dir = project_dir / "game"
    if not game_dir.is_dir():
        raise RuntimeError(f"Ren'Py game directory not found: {game_dir}")
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="renpylens-story-graph-") as temp:
        temp_path = Path(temp)
        settings_path = temp_path / "settings.json"
        settings_path.write_text(json.dumps({
            "output": str(output),
            "entry_points": args.entry_points or ["start"],
            "schema_version": args.schema_version,
        }), encoding="utf-8")
        environment = os.environ.copy()
        environment["RENPYLENS_STORY_GRAPH_SETTINGS"] = str(settings_path)
        environment["RENPYLENS_SHADOW_RUNTIME_DIR"] = str(RUNTIME_SOURCE.parent)
        environment["RENPYLENS_SEMANTICS_DIR"] = str(ROOT / "src")
        command = [
            str(executable), str(project_dir), "renpylens_story_graph",
            "--savedir", str(temp_path / "saves"),
        ]
        started = time.monotonic()
        with TemporaryLoader(game_dir, "renpy_story_graph_runtime"):
            process = subprocess.Popen(
                command,
                cwd=str(project_dir),
                env=environment,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            try:
                exit_code = process.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
                exit_code = 124

    if output.is_file():
        result = json.loads(output.read_text(encoding="utf-8"))
    else:
        result = {
            "status": "timeout" if exit_code == 124 else "crashed",
            "error": "Ren'Py exited before writing the graph.",
        }
    result["process_exit_code"] = exit_code
    result["runner_elapsed_seconds"] = round(time.monotonic() - started, 3)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    narrative_output = (
        Path(args.narrative_output).expanduser().resolve()
        if args.narrative_output
        else output.with_name(output.stem + "_narrative.json")
    )
    if result.get("status") == "completed":
        narrative_output.write_text(json.dumps({
            "status": "completed",
            "engine": result.get("engine"),
            "schema_version": result.get("schema_version"),
            "provenance": result.get("provenance"),
            "coverage": result.get("coverage"),
            "audit": result.get("audit"),
            "model": result.get("model"),
            "entry_points": result.get("entry_points"),
            "summary": result.get("summary"),
            "narrative_graph": result.get("narrative_graph"),
        }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps({
        "status": result.get("status"),
        "output": str(output),
        "narrative_output": str(narrative_output),
        "summary": result.get("summary"),
        "audit_valid": (result.get("audit") or {}).get("valid"),
        "coverage": result.get("coverage"),
        "elapsed_seconds": result.get("runner_elapsed_seconds"),
        "error": result.get("error"),
    }, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "completed" else (exit_code or 1)


if __name__ == "__main__":
    raise SystemExit(main())
