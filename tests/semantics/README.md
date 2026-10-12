# Semantic regression assets

StoryGraph uses schema v3 exclusively. The Kernel codec, exporter, validator,
and offline viewer share the current typed representation.

The fixture game under `fixtures/semantic_cases/` covers control flow, calls,
state, screens, translation, input, and random expressions.

Run repository source and unit checks:

```powershell
python tests/semantics/run_phase_regression.py --static-only
```

For runtime acceptance, provide `--renpy-executable` and `--large-game`.
Current graphs can be compared using `baseline_tools.py story-graph`;
deterministic routes use `baseline_tools.py semantic-route`.
