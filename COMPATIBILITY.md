# Enhanced Mock compatibility

The enhanced implementation is `python -m enhanced serve` (127.0.0.1:2027).
The upstream `mock_simulator.py`, CLI and MCP entry points are retained. The shared
HTTP client now bypasses system proxies for loopback addresses only. The legacy
mock still uses its original iid error behavior; use the enhanced server for this
project's fixed-error experiments. Official client default remains port 2026.

## Implemented

- `/enter`, `/measure`, `/clear`, `/exit` accepted responses and action cost fields.
- Initial receiver channel 1; straight-line motion 5 m/s; changed channel 1 s;
  measure 5 s; near <=5 m; optical clear <=20 m; failure 3 s, success 5 s.
- Clear moves the robot but does not change receiver channel, including on failure.
- Coordinates may leave the arena; finite numeric |x|,|y| <=2e6; integer channels 1–20.
- One robot session per world; serial HTTP server; virtual 360000 s and real 1200 s
  limits checked before each action; reject an action atomically if it would exceed limits.
- Successful and rejected request IDs cached per world; identical retry returns an
  independent copy without advancing time, emitting events, or adding observations.
  Reusing an ID with a different payload is rejected. Create a new world/server for
  a new mission; repeating `/enter` with a new ID does not reset existing progress.
- Sources: 10–16 unique channels, positions within R=1800 m, radii 1000–1500 m.
  `source_type`/`orientation` fields and 180° directional sensing are supported;
  generated scenarios currently use only omnidirectional sources.

## Deliberate differences / unverified details

- This is an independently implemented test double, not certified official behavior.
  No official executable comparison was performed. The original reference's claim
  of iid uniform error is not assumed to describe the official simulator.
- `deterministic_hash_fixed`: SHA-256(seed, channel, exact normalized float coordinates)
  selects a fixed error in [-1,1]. `worst_edge` selects a fixed ±1° endpoint by hash;
  this is an edge stress test, not a globally optimized adversarial policy.
- `svd_deg` is rounded to two decimals then normalized to [0,360). Quantization can
  put true bearing up to 1.005° from the reported angle. Q1 is called unmodified
  with its default ±1° wedges. Consequently the truth need not remain in the Q1
  region at rounding boundaries, especially in worst_edge mode. No silent widening
  or hidden ground-truth correction is applied. Q1 empty/unbounded/non-covering
  results are retained, and are not treated as certified clear locations.
- Responses omit real_timestamp_ms and live remaining_real_duration_s updates to
  keep replay/seed output deterministic. Timing limits use monotonic time.
- Robot IDs have basic session matching, not credential authentication. HTTP error
  classification is basic 400/403, not the complete official status/error taxonomy.
- No real-time wall delay, optical sensor simulation, terrain, collision physics,
  multi-arena scheduling, durable cross-process idempotency, or concurrent sessions.
- Logs are written on completed missions; crash-safe incremental logging is not
  implemented. The external HTTP run is saved on `/exit`; stop without exit loses it.
- Strategy separation is an API boundary, not a hostile-code sandbox: strategies
  receive only enter/measure/clear/exit methods and their responses. In-process Python
  reflection is not prevented. Run untrusted strategies in a separate process/service.
- GUI Simulation computes a bounded demo in a worker thread, saves the run, then
  continuously animates its virtual timeline. It is not live streaming while strategy
  execution is in progress. Replay never loads Q1 or executes the strategy.
- Single run display supports candidate markers via `CandidatePoints`; no Q2/Q3
  candidate selector, multi-source exploration policy, or Q4 experiment suite is supplied.
- Desktop source supports Python 3.10+ and Windows/macOS Qt. This change was tested
  on macOS; Windows execution and standalone .exe/.app packaging remain unverified.
