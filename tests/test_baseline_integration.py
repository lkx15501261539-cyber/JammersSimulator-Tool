"""Run the uploaded frozen packages through the real process bridge."""
import json
from pathlib import Path
import zipfile
import pytest
from enhanced.baseline import default_archive, run_baseline
from enhanced.world import ScenarioConfig
from enhanced.replay import load_run, project

@pytest.mark.parametrize('model',['hexagon_v1','spiral_v1'])
def test_original_baseline_complete_mission(tmp_path,model):
    archive=default_archive()
    if not archive.exists(): pytest.skip('Original Baseline archive not supplied')
    output=tmp_path/model
    run=run_baseline(ScenarioConfig(seed=20260912,error_model='baseline_fixed_field'),archive,model,output)
    assert run['metadata']['completion']=='completed'
    assert run['metadata']['model_files_unchanged']
    assert run['metadata']['metrics_reconciled']
    end=project(run['events'],run['events'][-1]['end'])
    assert len(end['cleared'])==len(run['sources'])==10
    assert end['measure_count']>=100
    assert any(e['type']=='LocalizationUpdate' for e in run['events'])
    assert load_run(output)['events']==run['events']
    # Original config passed verbatim, not reduced to speed up integration tests.
    with zipfile.ZipFile(archive) as z:
        expected=json.loads(z.read(f'{model}/config.json'))
    session=json.loads((output/'baseline-original'/'session.json').read_text())
    assert session['settings']==expected
    # No world sources appear in requests or responses consumed by original code.
    log=(output/'baseline-original'/'actions.jsonl').read_text()
    assert 'recv_radius' not in log and 'source_type' not in log


def test_cancelled_baseline_is_saved_without_success(tmp_path):
    archive=default_archive()
    if not archive.exists(): pytest.skip('Original Baseline archive not supplied')
    run=run_baseline(ScenarioConfig(error_model='baseline_fixed_field'),archive,'hexagon_v1',tmp_path/'cancelled',cancelled=lambda:True)
    assert run['metadata']['completion']=='cancelled'
    assert project(run['events'],0)['cleared']==set()
    assert load_run(tmp_path/'cancelled')['events'][-1]['data']['reason']=='cancelled'
