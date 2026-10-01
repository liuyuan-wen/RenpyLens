# Semantic regression assets

`baselines/phase0.json` records the phase-0 engine, provenance, artifact hashes,
and StoryGraph/Shadow Route counts. The four files under the ignored
`artifacts/phase0-large-game/` directory are retained local large-game evidence.

Compare a rebuilt StoryGraph or route with the frozen evidence using:

```powershell
python tests/semantics/baseline_tools.py exact-v2 `
  tests/semantics/artifacts/phase0-large-game/renpy_story_graph.json `
  tests/renpy_story_graph.json
python tests/semantics/baseline_tools.py semantic-route `
  tests/semantics/artifacts/phase0-large-game/shadow_route_result.json `
  tests/shadow_route_result.json
```

The fixture game under `fixtures/semantic_cases/` is intentionally small and
isolates the phase-0 Ren'Py constructs listed in its README.

Run the repository sequence (fixture lint, unit tests, and the local exact-v2
and semantic-route artifacts when present; pass `--game` to execute a fixture
through Ren'Py):

```powershell
python tests/semantics/run_phase_regression.py
```

StoryGraph commands write schema v3 by default. Existing tools can load it
through the Kernel codec, and `--schema-version 2` remains available for the
exact-v2 baseline comparison.
