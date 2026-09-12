# Jammers Lab — enhanced testing and continuous desktop animation

This platform adds an observer-only PySide6 desktop application, reproducible mock,
Q1 integration demo and saved event replay without changing the original CLI/MCP.
It calls the adjacent `CUMCM-2026/code/第一问.py::localize(measurements)` without editing
or copying that model or any paper content. See [COMPATIBILITY.md](COMPATIBILITY.md)
for assumptions, rounding boundaries, and incomplete official behavior.

![Desktop mission observer](docs/enhanced-desktop.png)

A saved demonstration is included: `python -m enhanced gui --replay examples/enhanced-demo`.
This replay works without the adjacent Q1 repository.

## Setup and launch

Use Python 3.10+ with the two repositories adjacent:

```text
workspace/
  CUMCM-2026/code/第一问.py
  JammersSimulator-Tool/
```

From `JammersSimulator-Tool`:

```sh
python -m venv .venv
# macOS:
source .venv/bin/activate
# Windows PowerShell instead:
# .venv\Scripts\Activate.ps1
python -m pip install -r requirements-enhanced.txt
python -m enhanced gui
```

Select scenario, seed and error model, press **Simulation · 新建**, then **播放**.
The dog starts at (0,0) and moves continuously at 5 m/s virtual speed. Playback
speeds: 0.5/1/2/5/10/20/50×. Pause, step to the next event completion, or drag the
timeline in either direction. Wheel zoom and drag pan; 地图复位 restores the arena.
The map uses east +x, north +y. Truth and reception radii are observer overlays;
only observations and localization results reach the strategy.

```sh
python -m enhanced demo --seed 42 --scenario uniform --output runs/demo42
python -m enhanced gui --replay runs/demo42
python -m enhanced demo --error-model worst_edge --output runs/edge42
python -m enhanced batch --seeds 42,43,44 --output runs/batch
python -m enhanced serve --port 2027 --output runs/http-mission
python -m pytest -q
```

Output directories must be new to prevent overwriting existing evidence. Omit
`--output` for a timestamped directory. `--q1 /path/to/第一问.py` overrides the adjacent
model location. Replay needs only the saved logs; Q1 and the paper repo can be absent.
For CLI/MCP install the original `requirements.txt` and set
`SIMULATOR_URL=http://127.0.0.1:2027` to use the enhanced server; leave it unset to
use the official simulator at 2026. See the original README for those commands.

## Modules

```text
enhanced/
  world.py       scenario generation, private truth, rules and action events
  strategy.py    Client protocol, external Q1 loader, run_mission demo
  runner.py      response adapter, composition, run persistence
  replay.py      event validation, time projection and metrics
  ui.py          QGraphicsScene desktop observer, worker and playback
  server.py      sequential REST mock on 2027
  __main__.py    gui / demo / serve / batch commands
tests/
  test_enhanced.py  rules, replay, seed, integration and original HTTP client
  test_ui.py        desktop controls and render smoke test
```

`Move`, `ChannelSwitch`, `Measure`, `Clear` carry start/end virtual seconds; results
become visible only at end. `LocalizationUpdate` carries float display geometry,
status, measurement count, farthest pair, diameter, circle and coverage result.
`MissionStart`, `MissionEnd` and `CandidatePoints` are instantaneous. Sequence numbers
break ties; replay rejects nonmonotone or reordered logs. Full precision Q1 values
remain in the model during decisions; only observer geometry is converted to floats.

Each completed run saves:

- `scenario.json`: schema, seed, scenario, error model, strategy name/version, Q1 hash.
- `ground_truth.json`: initial sources, never injected into strategy configuration.
- `events.jsonl`: ordered actions and derived notifications.
- `observations.jsonl`: unique request/response history (identical retries deduplicated).
- `metrics.json`: counts, distance, virtual time, action time breakdown and provenance.

Batch creates `runs.csv` and `summary.json`, averaging by scenario. Average localization
clear time means elapsed from the first direction/near observation to successful clear,
conditional on success; no successful clears is null. Clear rate is cleared / total
sources, so a one-target Q1 demo naturally has low whole-mission clear rate. Worst seed
sorts by lowest clear rate, then longest total virtual time; `worst_replay` identifies
the retained run. These are integration statistics, not a Q3 strategy evaluation.

## Attach a genuine Q3 strategy

Implement `run_mission(client, config)` as a generator. Use `client.enter()`,
`client.measure(x,y,channel)`, `client.clear(x,y,channel)`, `client.exit()` and reject
unaccepted responses. Maintain your own observations and call Q1 using those only.
Yield `('LocalizationUpdate', data)` using `localization_data(...)`, or
`('CandidatePoints', {'points': [[x,y], ...]})`. The runner timestamps notifications
at current virtual time; the strategy must not emit simulated action or truth events.

```python
from enhanced.runner import simulate
from enhanced.world import ScenarioConfig
from my_q3_strategy import run_mission

simulate(ScenarioConfig(seed=42), output='runs/q3-42',
         strategy=run_mission, strategy_name='My Q3', strategy_version='0.1')
```

For a live official simulator, the original `SimulatorClient` satisfies the same
public method interface. Drive the strategy generator with that client; separate
logging/observer wiring is needed for official responses. The enhanced `World` and
Qt view do not need modification to evaluate a new policy through the runner.
