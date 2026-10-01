# Semantic fixture contract

This project is intentionally small and contains one isolated example for
each phase-0 semantic boundary.  The fixture runner must execute the labels
`semantic_start`, `semantic_call_start`, `semantic_state_start`,
`semantic_input_start`, `semantic_screen_start`, and
`semantic_translation_start` with deterministic input and random providers.

Rollback is driven by the runtime harness after `semantic_start` reaches its
checkpoint; it is not represented by a script statement.  Expected results
belong in the local phase-0 baseline directory and must contain no absolute
game paths.
