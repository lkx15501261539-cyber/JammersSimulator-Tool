"""Route reception certificates are separate from sampled Q2 ranking."""
import hashlib
import json
import math
from pathlib import Path
import sys
import zipfile

import pytest

CODE = Path(__file__).resolve().parents[1]/'model_sources'/'q4'
sys.path.insert(0,str(CODE))
from cu import ALPHA,Feasible,certified_outer,q1,snap
from opportunities import PreparedRouteGeometry,prepare_score_runtime,distance_safe
sys.path.remove(str(CODE))


@pytest.fixture
def geometry():
    F = Feasible([(0.,0.,0.)])
    return PreparedRouteGeometry(F,certified_outer(F))


def exact_sample_score(geometry,point,alpha=ALPHA):
    value = 0.
    for g in geometry.source_samples:
        if math.dist(g,point) <= 5:
            continue
        beta = math.degrees(math.atan2(g[1]-point[1],g[0]-point[0]))
        for error in geometry.errors:
            result = q1.localize([*geometry.F.directions,(*point,(beta+error)%360)],error_deg=alpha)
            if result.status in ('空集','无界'):
                return math.inf
            value = max(value,result.diameter or 0.)
    return value


def test_path_intersection_yields_certified_zero_detour_J_ranked_points(geometry):
    segments = [((300.,-1200.),(300.,1200.))]
    intervals = geometry.certified_intervals(segments)
    assert len(intervals) == 1
    interval = intervals[0]
    assert 0 < interval['t_min'] < interval['t_max'] < 1
    assert geometry._certify_raw(interval['endpoint_a'])
    assert geometry._certify_raw(interval['endpoint_b'])
    candidates = geometry.path_candidates(segments)
    assert len(candidates) >= 3
    assert candidates[0].J_hat == min(c.J_hat for c in candidates)
    assert candidates[0].point != (300.,0.)  # the middle is poor pure-P geometry
    for item in candidates:
        assert item.level == 1 and item.segment_index == 0
        assert item.extra_move_s == 0
        assert item.certificate['raw_segment_certified']
        assert item.certificate['snapped_rechecked']
        assert geometry.certify(item.point)
        assert distance_safe(geometry.F,geometry.E,item.point)


def test_millimetre_snap_is_recertified_and_detour_is_quantization_only(geometry):
    A,B = (300.123,-1200.789),(713.321,1100.123)
    candidates = geometry.path_candidates([(A,B)])
    assert candidates
    for item in candidates:
        assert item.point == snap(item.point)
        actual = max(0.,(math.dist(A,item.point)+math.dist(item.point,B)-math.dist(A,B))/5)
        assert item.extra_move_s == actual
        assert actual <= math.sqrt(2)/5000+1e-9
        assert geometry.certify(item.point)


def test_no_route_intersection_uses_nearby_points_only_within_tau(geometry):
    segments = [((0.,1010.),(1500.,1010.))]
    assert geometry.path_candidates(segments) == []
    assert geometry.nearby_candidates(segments,0) == []
    nearby = geometry.nearby_candidates(segments,20)
    assert nearby
    assert all(c.level == 2 and 0 < c.extra_move_s <= 20 for c in nearby)
    assert all(geometry.certify(c.point) for c in nearby)
    assert nearby[0].J_hat == min(c.J_hat for c in nearby)
    with pytest.raises(ValueError):
        geometry.nearby_candidates(segments,-1)


def test_far_route_falls_back_only_when_global_is_explicitly_allowed(geometry):
    segments = [((5000.,5000.),(6000.,6000.))]
    assert geometry.choose(segments) is None
    candidate = geometry.choose(segments,allow_global=True)
    assert candidate is not None and candidate.level == 3
    assert candidate.segment_index == 0 and candidate.extra_move_s > 1000
    assert candidate.certificate['optimality'] == 'best scored finite candidate only'


def test_global_finite_Q2_candidates_have_real_insertion_cost_when_attached(geometry):
    raw = geometry.global_candidates()
    assert raw and all(math.isinf(c.extra_move_s) and c.segment_index is None for c in raw)
    segments = [((0.,1010.),(1500.,1010.)),((0.,-1010.),(1500.,-1010.))]
    attached = geometry.global_candidates(segments)
    assert attached
    for item in attached:
        expected = min(geometry._extra(item.point,A,B) for A,B in segments)
        assert item.extra_move_s == expected
        assert geometry.certify(item.point)


def test_all_previously_measured_positions_have_infinite_loss():
    F = Feasible([(0.,0.,0.)])
    geometry = PreparedRouteGeometry(F,certified_outer(F),[(300.,600.)])
    assert math.isinf(geometry.score((0.,0.)))
    assert math.isinf(geometry.score((300.0004,600.0004)))
    assert geometry.path_candidates([((300.,600.),(300.,600.))]) == []


def test_J_matches_Q1_pure_P_with_current_Q4_half_angle(geometry):
    point = (450.,779.423)
    actual = geometry.score(point)
    expected = exact_sample_score(geometry,point)
    assert math.isfinite(actual)
    assert actual == pytest.approx(expected,rel=2e-7,abs=1e-7)
    assert geometry.metadata['half_angle_deg'] == 1.005
    # The old 1-degree kernel configuration would be a different metric.
    assert abs(actual-exact_sample_score(geometry,point,alpha=1.)) > .01
    assert 'not a continuous' in geometry.metadata['score_scope']


def test_samples_are_from_observations_not_world_truth_and_not_certificates(geometry):
    assert len(geometry.source_samples) == 9
    assert all(geometry.F.contains(g) for g in geometry.source_samples)
    assert geometry.metadata['hidden_truth_used'] is False
    # A single estimate would accept this point, but the continuous F near
    # the first measurement makes its reception impossible to guarantee.
    geometry.source_samples = ((1000.,0.),)
    assert geometry._prefilter((1900.,0.))
    assert not geometry.certify((1900.,0.))


def test_empty_legal_sample_set_is_infinite_loss_not_zero(geometry):
    geometry.source_samples = ()
    assert math.isinf(geometry.score((300.,600.)))
    assert geometry.path_candidates([((300.,600.),(300.,600.))]) == []


def test_q1_exact_fallback_uses_same_loss_definition(tmp_path):
    F = Feasible([(0.,0.,0.)])
    geometry = PreparedRouteGeometry(F,certified_outer(F),options={
        'q2_solver_path':str(tmp_path/'unavailable-solver.py'),
        'score_source_samples':5,'score_error_samples':3})
    assert geometry.metadata['backend'] == 'q1_fraction'
    point = (450.,779.423)
    assert geometry.score(point) == exact_sample_score(geometry,point)


def test_loaded_q2_kernel_bytes_match_existing_baseline_archive():
    metadata = prepare_score_runtime()
    if metadata['backend'] == 'q1_fraction':
        pytest.skip('optional fast Q2 dependencies unavailable; exact Q1 path is tested')
    assert metadata['backend'] == 'q3_pure_diameter'
    with zipfile.ZipFile(metadata['archive']) as archive:
        content = archive.read(metadata['archive_entry'])
        prefix = metadata['archive_entry'].rsplit('/',1)[0]
        manifest = json.loads(archive.read(prefix+'/SHA256SUMS.json'))
    assert hashlib.sha256(content).hexdigest() == metadata['solver_sha256'] == manifest['solver.py']


def test_near_branch_is_zero_geometric_loss_without_claiming_Q4_visibility():
    F = Feasible([(-100.,0.,0.),(0.,-100.,90.)])
    geometry = PreparedRouteGeometry(F,certified_outer(F))
    assert geometry.source_samples and all(math.dist(g,(0,0)) < 5 for g in geometry.source_samples)
    assert geometry.score((0.,0.)) == 0.
    assert 'sampled pure-P diameter' in geometry.metadata['score_scope']


def test_new_geometry_does_not_mutate_F_or_measurement_history(geometry):
    before = list(geometry.F.directions),list(geometry.F.negative_history),geometry.E
    geometry.path_candidates([((300.,-1200.),(300.,1200.))])
    assert (geometry.F.directions,geometry.F.negative_history,geometry.E) == before


def test_prepared_geometry_is_a_snapshot_not_a_mixed_history_cache():
    F = Feasible([(0.,0.,0.)])
    prepared = PreparedRouteGeometry(F,certified_outer(F))
    old_key = prepared.history_key
    F.directions.append((1000.,-1000.,90.))
    assert prepared.F.directions == [(0.,0.,0.)]
    assert prepared.history_key == old_key
    updated = PreparedRouteGeometry(F,certified_outer(F))
    assert updated.history_key != old_key and len(updated.F.directions) == 2


def test_no_signal_exclusion_refresh_preserves_F_and_allows_next_level(geometry):
    point = (300.,600.)
    segments = [(point,point)]
    assert geometry.choose(segments).level == 1
    old_directions = list(geometry.F.directions)
    old_samples = geometry.source_samples
    geometry.exclude_measured([point])
    assert math.isinf(geometry.score(point))
    assert geometry.F.directions == old_directions and geometry.source_samples == old_samples
    assert geometry.path_candidates(segments) == []
    next_candidate = geometry.choose(segments)
    assert next_candidate is not None and next_candidate.level == 2
    assert next_candidate.point != point and next_candidate.extra_move_s <= 5
