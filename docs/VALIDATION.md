# Validation — 2026-09-12

Environment: macOS, Python 3.12.8, PySide6 6.11.2, pytest 9.1.1.

- `python -m pytest -q`: **19 passed** (no skipped tests).
- Covers five scenario generators and reproducibility, both fixed error models,
  channel uniqueness, reception/near/clear boundaries, action costs, unchanged
  receiver channel after clear, idempotency and rejection without side effects,
  continuous midpoint motion, delayed outcomes, rewind, replay order, persisted Q1
  geometry, and original SimulatorClient against an actual loopback HTTP server.
- Qt test covers play/pause, stepping, seek in both directions, observer toggles,
  speed choices and screenshot generation. Native macOS window/playback also
  smoke-tested; screenshots visually checked and channel panel clipping fixed.
- `demo --seed 42`: 109.5218536 virtual seconds, 447.6092681 m, 3 measures,
  0 switches, 0 failed clears, 1/15 sources cleared; 2 LocalizationUpdate events.
  This is one-target integration, not a full Q3 mission result.
- `batch --seeds 42,43,44`: all five scenarios, 15 runs per error model,
  30 total across deterministic_hash_fixed and worst_edge; JSON and CSV exported.
- `git diff --check` and Python byte compilation passed.
- No Windows runtime or standalone packaging validation has been performed.

The bundled `examples/enhanced-demo` preserves the five replay files from seed 42.
Q1 source SHA-256 is recorded in that run's metadata; only its `localize` callable
was used. Paper files and synced `sources/` references were not edited.
