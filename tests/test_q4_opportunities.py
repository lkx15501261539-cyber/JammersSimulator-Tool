"""Independent counterexamples and continuous Q4 opportunity certificates."""
from fractions import Fraction as Q
import math
from pathlib import Path
import sys
from types import SimpleNamespace

CODE = Path(__file__).resolve().parents[1]/'model_sources'/'q4'
sys.path.insert(0,str(CODE))
from cu import Feasible, certified_outer, discovery_certificate, search_points, snap
from opportunities import (certified_clear_point, distance_safe, find_pairs,
                           safe_visible, _region, _d2, _near_plane)
sys.path.remove(str(CODE))

from enhanced.runner import ResponseClient
from enhanced.world import ScenarioConfig, World


def constrained():
    F = Feasible([(0,0,0),(1000,-1000,90)])
    return F, certified_outer(F)


def polygon_fixture(boxes, directions=((-100,0,0),)):
    # A synthetic known region E, with no extra P halfplanes. This isolates
    # continuous polygon/rounding behavior from the Q1 bearing construction.
    return SimpleNamespace(P=(), directions=directions), tuple(tuple(map(Q,b)) for b in boxes)


def test_legal_pair_and_certified_quality_on_full_physical_region():
    F,E = constrained()
    Z = ((500,500),(500,-500),(1500,500),(1500,-500))
    pairs = find_pairs(F,E,Z,range(4))
    assert [(p.left,p.right) for p in pairs] == [(0,1)]
    p = pairs[0]
    assert .6 < p.quality < .71 and p.preferred
    assert all(distance_safe(F,E,z) for z in Z[:2])
    # A finite independent stress check supplements, not replaces, the
    # exact affine/convex certificate used by the production predicates.
    for x in range(970,1031,2):
        for y in range(-30,31,2):
            g = (x,y)
            if not F.contains(g):
                continue
            a = (math.degrees(math.atan2(-y,-x))+360)%360
            angles = [math.degrees(math.atan2(z[1]-y,z[0]-x)) for z in Z[:2]]
            deltas = [(b-a+180)%360-180 for b in angles]
            assert deltas[0] < 0 < deltas[1]
            assert deltas[1]-deltas[0] <= 180
            assert all(abs(math.sin(math.radians(d))) >= p.quality for d in deltas)
            for phi in range(0,360,5):
                visible = lambda b:abs((b-phi+180)%360-180) <= 90
                if visible(a):
                    assert any(visible(b) for b in angles)


def test_remaining_indices_and_coordinates_are_actual_snapped_points():
    F,E = constrained()
    Z = ((500.0004,500.0004),(500.0004,-500.0004))
    assert find_pairs(F,E,Z,[0]) == []
    pairs = find_pairs(F,E,Z,[1,0,1])
    assert [(p.left,p.right) for p in pairs] == [(0,1)]
    assert pairs == find_pairs(F,E,tuple(map(snap,Z)),[0,1])


def test_non_safe_far_point_and_same_side_cannot_form_pair():
    F,E = constrained()
    assert not distance_safe(F,E,(10000,500))
    assert find_pairs(F,E,((10000,500),(500,-500)),[0,1]) == []
    assert find_pairs(F,E,((500,500),(600,500)),[0,1]) == []


def test_estimated_source_would_accept_but_continuous_F_rejects():
    F = Feasible([(0,0,0)])
    E = certified_outer(F)
    Z = ((500,500),(500,-500))
    assert F.contains((10,0)) and F.contains((1000,0))
    assert all(distance_safe(F,E,z) for z in Z)
    # At the estimated center (1000,0), the angles are +/-45 degrees.
    # At another valid g=(10,0), their wrap spread exceeds 180 degrees.
    assert find_pairs(F,E,Z,[0,1]) == []


def test_first_direction_near_cut_removes_origin_without_removing_5_001m():
    F = Feasible([(0,0,0)])
    E = certified_outer(F)
    polygons,hull = _region(F,E)
    assert min(g[0] for g in hull) == 4 and (Q(0),Q(0)) not in hull
    assert F.contains((5.001,0))
    # These small safe opportunities have a valid pair over the WHOLE first
    # F. The uncut E spuriously retained S0 and rejected all sides at cross=0.
    assert [(p.left,p.right) for p in find_pairs(F,E,((1,1),(1,-1)),[0,1])] == [(0,1)]
    g = (Q('5.001'),Q(0))
    assert any(all((b[0]-a[0])*(g[1]-a[1])-(b[1]-a[1])*(g[0]-a[0]) >= 0
                   for a,b in zip(poly,poly[1:]+poly[:1])) for poly in polygons)


def test_near_cut_preserves_actual_points_at_5_001m_across_angle_wrap():
    for theta in (0.,.001,37.,89.999,90.,179.999,180.,270.,359.999):
        record = (321.123,-456.789,theta)
        F = Feasible([record])
        a,b,c = _near_plane(record)
        for offset in (-1.,0.,1.):
            angle = math.radians(theta+offset)
            g = (record[0]+5.001*math.cos(angle), record[1]+5.001*math.sin(angle))
            assert F.contains(g)
            assert a*Q(str(g[0]))+b*Q(str(g[1])) <= c


def test_width_180_closed_boundary_and_antipodal_wrap():
    F,E = polygon_fixture([(0,0,0,0)])
    pairs = find_pairs(F,E,((0,100),(0,-100)),[0,1])
    assert len(pairs) == 1 and pairs[0].quality == 1 and not pairs[0].preferred
    # A point opposite the original successful position has delta=+180;
    # a zero cross must never turn that into a false "left" certificate.
    assert find_pairs(F,E,((100,0),(0,-100)),[0,1]) == []


def test_zero_distance_has_no_atan2_certificate_and_near_is_directional():
    F,E = polygon_fixture([(0,0,0,0)])
    assert find_pairs(F,E,((0,0),(0,-100)),[0,1]) == []
    assert distance_safe(F,E,(0,0)) and safe_visible(F,E,(0,0))
    assert not safe_visible(F,E,(1,0))  # within 5m can still be behind a source
    assert certified_clear_point(F,E) == (0.,0.)


def test_pointwise_distance_lower_bound_is_not_a_global_radius():
    F,E = polygon_fixture([(1100,-1,1200,1)], directions=((0,0,0),))
    assert distance_safe(F,E,(10,0))  # >1000m, but always nearer than success
    assert not distance_safe(F,E,(-10,0))
    assert safe_visible(F,E,(0,0))


def test_all_positive_measurements_can_strengthen_radius_lower_bound():
    F,E = polygon_fixture([(800,-1,900,1)], directions=((0,0,0),(2000,0,180)))
    assert distance_safe(F,E,(1900,100))
    first_only = SimpleNamespace(P=(),directions=F.directions[:1])
    assert not distance_safe(first_only,E,(1900,100))


def test_no_observations_or_empty_certificate_never_certifies_pair():
    F = Feasible()
    assert find_pairs(F,(),((1,1),(1,-1)),[0,1]) == []
    assert not distance_safe(F,(),(0,0))
    assert not safe_visible(F,(),(0,0))
    assert certified_clear_point(F,()) is None


def test_geometry_budget_exhaustion_rejects_instead_of_sampling():
    F,E = polygon_fixture([(0,0,0,0)]*10001)
    assert find_pairs(F,E,((0,100),(0,-100)),[0,1]) == []
    assert not distance_safe(F,E,(0,100))
    assert certified_clear_point(F,E) is None


def test_direct_clear_uses_continuous_F_clipped_by_P():
    F = Feasible([(-200,0,0),(0,-200,90)])
    # This coarse E covers F; clipping the same P constraints gives a tight
    # polygon. Its full vertices, not E centers, are the coverage witnesses.
    E = ((Q(-100),Q(-100),Q(100),Q(100)),)
    q = certified_clear_point(F,E)
    assert q is not None
    polygons,hull = _region(F,E)
    assert polygons and all(_d2(tuple(map(Q,q)),g) <= 400 for g in hull)
    assert math.dist(q,(100,100)) > 20  # covering P∩E, not claiming all E
    for x in range(-100,101,5):
        for y in range(-100,101,5):
            if F.contains((x,y)):
                assert math.dist(q,(x,y)) <= 20


def test_clear_rounded_center_can_destroy_exact_20m_coverage():
    # Continuous MEC radius is exactly 20, center x=.00049. No millimeter
    # center covers both endpoints. A pre-snap radius test would be unsound.
    F,E = polygon_fixture([(Q('-19.99951'),0,Q('-19.99951'),0),
                            (Q('20.00049'),0,Q('20.00049'),0)])
    assert certified_clear_point(F,E) is None
    F,E = polygon_fixture([(Q('-19.99851'),0,Q('-19.99851'),0),
                            (Q('19.99949'),0,Q('19.99949'),0)])
    assert certified_clear_point(F,E) == (0.,0.)


def test_clear_cell_interior_and_edge_are_covered_and_large_region_rejected():
    F,E = polygon_fixture([(-12,-16,12,16)])
    q = certified_clear_point(F,E)
    assert q == (0.,0.)  # all four exact boundary corners at 20m
    assert all(math.dist(q,(x,y)) <= 20 for x in range(-12,13) for y in range(-16,17))
    F,E = polygon_fixture([(-20,-1,20,1)])
    assert certified_clear_point(F,E) is None


def test_actual_safe_no_signal_has_visible_opposite_side():
    F,E = constrained()
    Z = ((500,500),(500,-500))
    pair, = find_pairs(F,E,Z,[0,1])
    world = World(ScenarioConfig(problem=4,count=10,error_model='worst_edge'))
    world.sources[0] = dict(channel=1,x=1000.,y=0.,source_type='directional',
                            orientation=250.,recv_radius=1000.)
    for index,s in enumerate(world.sources[1:],2):
        s['channel'] = index
    client = ResponseClient(world.request)
    client.enter()
    assert client.measure(0,0,1)['measure_result'] == 'direction'
    assert client.measure(1000,-1000,1)['measure_result'] == 'direction'
    assert client.measure(*Z[pair.left],1)['measure_result'] == 'no_signal'
    assert client.measure(*Z[pair.right],1)['measure_result'] == 'direction'


def test_original_25_coordinates_rounding_counterexample_in_simulator():
    old = search_points()
    proposed = ((0.,0.),)+tuple(snap((r*math.cos(math.radians(30*i+offset)),
                                      r*math.sin(math.radians(30*i+offset))))
                               for r,offset in ((1000.,0),(1800/math.cos(math.radians(15)),15))
                               for i in range(12))
    assert discovery_certificate(old)['passed']
    assert not discovery_certificate(proposed)['passed']
    assert not discovery_certificate(proposed)['outer_contains_arena']
    counts = []
    for Z in (proposed,old):
        world = World(ScenarioConfig(problem=4,count=10,error_model='worst_edge'))
        world.sources[0] = dict(channel=1,x=1558.845,y=900.001,source_type='directional',
                                orientation=30.00005,recv_radius=1000.)
        for index,s in enumerate(world.sources[1:],2):
            s['channel'] = index
        client = ResponseClient(world.request)
        client.enter()
        results = [client.measure(*z,1)['measure_result'] for z in Z]
        counts.append(results.count('direction'))
    assert counts == [0,2]
