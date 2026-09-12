"""Shared Q1 geometry, Q2 distance candidates, and executable C/U bounds.

No distribution or hidden source state is an input. F is implicit; E is a
conservative union of closed boxes. P always contains only bearing halfplanes.
Q1's rational trigonometric coefficient convention is retained unchanged.
"""
from dataclasses import dataclass, field
from fractions import Fraction as Q
import importlib.util
from pathlib import Path
import math
import sys

_spec = importlib.util.spec_from_file_location('q4_shared_q1', Path(__file__).with_name('第一问.py'))
q1 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = q1
_spec.loader.exec_module(q1)

ALPHA = 1.005  # +/-1 degree physical error plus two-decimal quantization.
SPEED = 5.0
Point = tuple[float, float]
Box = tuple[Q, Q, Q, Q]  # xmin, ymin, xmax, ymax


def snap(p):
    return tuple(round(float(v), 3) for v in p)


def min_d2(p, box):
    x, y = map(lambda v: Q(str(v)), p)
    a, b, c, d = box
    return max(a-x, 0, x-c)**2 + max(b-y, 0, y-d)**2


def max_d2(p, box):
    x, y = map(lambda v: Q(str(v)), p)
    a, b, c, d = box
    return max((a-x)**2, (c-x)**2) + max((b-y)**2, (d-y)**2)


def outside_planes(box, planes):
    a, b, c, d = box
    return any(hx*(a if hx >= 0 else c) + hy*(b if hy >= 0 else d) > z
               for hx, hy, z in planes)


@dataclass
class Feasible:
    """F: positive physical constraints and optional joint negative history.

    E deliberately relaxes orientation/negative constraints. After local
    no_signal the baseline retains this F and immediately executes saved U.
    """
    directions: list = field(default_factory=list)
    negative_history: list = field(default_factory=list)

    @property
    def P(self):
        return tuple(q1.build_halfplanes(self.directions, ALPHA)) if self.directions else ()

    def contains(self, X):
        x, y = map(lambda v: Q(str(v)), X)
        if x*x+y*y > 1800**2 or any(a*x+b*y > c for a,b,c in self.P):
            return False
        distances = [math.dist(X, (a,b)) for a,b,_ in self.directions]
        if any(not 5 < d <= 1500 for d in distances):
            return False
        R_min = max([1000, *distances])
        negatives = [z for z in self.negative_history if math.dist(X,z) <= R_min]
        if not negatives:  # An omnidirectional state is feasible.
            return True
        # Feasibility of one fixed orientation: test all angular boundary
        # points and open arcs. No stochastic sampling is used.
        positive = [math.degrees(math.atan2(b-X[1], a-X[0])) % 360
                    for a,b,_ in self.directions]
        negative = [math.degrees(math.atan2(z[1]-X[1],z[0]-X[0])) % 360 for z in negatives]
        boundaries = sorted({(v+sign*90)%360 for v in positive+negative for sign in (-1,1)})
        candidates = boundaries + [(a + ((boundaries[(i+1)%len(boundaries)]-a)%360)/2)%360
                                   for i,a in enumerate(boundaries)]
        delta = lambda a,b: abs((a-b+180)%360-180)
        return any(all(delta(a,phi) <= 90 for a in positive) and
                   all(delta(a,phi) > 90 for a in negative) for phi in candidates)


def certified_outer(F, max_cells=100000):
    """Cover F by dyadic boxes; reject only exact rational necessary tests.

    The leaf diagonal/2 is 19.8874m. Each rounded center covers its entire
    box at 19.9m, verified with rational squared distances (including interior).
    This certifies the Q1 coefficient model, not transcendental roundoff.
    """
    pending = [(Q(-1800), Q(-1800), Q(1800), Q(1800))]
    leaves, checked = [], 0
    planes = F.P
    while pending:
        box = pending.pop()
        checked += 1
        if checked > max_cells:
            raise RuntimeError('outer certification budget exhausted')
        if min_d2((0,0),box) > 1800**2 or outside_planes(box,planes):
            continue
        if any(min_d2((x,y),box) > 1500**2 or max_d2((x,y),box) <= 5**2
               for x,y,_ in F.directions):
            continue
        a,b,c,d = box
        if (c-a)**2+(d-b)**2 <= 4*Q('19.9')**2:
            leaves.append(box)
        else:
            mx,my = (a+c)/2,(b+d)/2
            pending.extend([(a,b,mx,my),(mx,b,c,my),(a,my,mx,d),(mx,my,c,d)])
    if not leaves:
        raise RuntimeError('empty outer region: inconsistent observations')
    return tuple(leaves)


def path_cost(route, p, b=None):
    if not route:
        return 0.0 if b is None else math.dist(p,b)/SPEED
    length = math.dist(p,route[0]) + sum(math.dist(a,z) for a,z in zip(route,route[1:]))
    if b is not None:
        length += math.dist(route[-1],b)
    return length/SPEED + 3*len(route)+2


def two_opt(route, score, rounds=2):
    """Evaluate the whole supplied score; no adaptive four-edge shortcut."""
    route = list(route)
    best = score(route)
    for _ in range(rounds):
        improved = False
        for i in range(len(route)-1):
            for j in range(i+2,len(route)+1):
                candidate = route[:i]+route[i:j][::-1]+route[j:]
                cost = score(candidate)
                if cost < best-1e-9:
                    route,best,improved = candidate,cost,True
        if not improved:
            break
    return tuple(route)


@dataclass(frozen=True)
class OpticalPlan:
    route: tuple
    cells: tuple
    cost: float

    def verifies(self):
        # Every closed box, including its interior, lies in its own 19.9m disk.
        points = set(self.route)
        return bool(self.cells) and all(
            (center := snap(((a+c)/2,(b+d)/2))) in points and
            max_d2(center,(a,b,c,d)) <= Q('19.9')**2 for a,b,c,d in self.cells)


def optical_routes(cells):
    """Endpoint-independent certified snake candidates shared with scheduling."""
    centers = {snap(((a+c)/2,(b+d)/2)) for a,b,c,d in cells}
    routes = []
    for axis in (0,1):
        rows = {}
        for z in centers:
            rows.setdefault(z[axis],[]).append(z)
        route = []
        for i,key in enumerate(sorted(rows)):
            route.extend(sorted(rows[key],key=lambda z:z[1-axis],reverse=bool(i%2)))
        routes.extend([tuple(route),tuple(reversed(route))])
    return tuple(routes)


def Uhat(F, p, b=None, *, E=None, saved=None):
    """Uhat_k(F,p;b): executable upper bound; never claimed to be U*_k."""
    cells = certified_outer(F) if E is None else E
    routes = optical_routes(cells)
    if saved is not None:
        # Callers retain a plan covering a superset of current F; keep its
        # certificate/cells intact instead of asserting identical box layouts.
        routes_saved = OpticalPlan(saved.route,saved.cells,path_cost(saved.route,p,b))
    best = min(routes,key=lambda r:path_cost(r,p,b))
    result = OpticalPlan(best,cells,path_cost(best,p,b))
    if saved is not None and routes_saved.cost < result.cost:
        result = routes_saved
    if not result.verifies():
        raise RuntimeError('optical coverage certificate failed')
    return result


def S_dist(F, E):
    """Q2 finite candidates filtered by a sufficient distance certificate.

    Uses max(1000, previous distances) as in Q2, not a probability model.
    Directional illumination is deliberately NOT inferred from this test.
    """
    x,y,theta = F.directions[-1]
    points = []
    for length in (300,600,900):
        for offset in (-60,60):
            a = math.radians(theta+offset)
            points.append(snap((x+length*math.cos(a),y+length*math.sin(a))))
    old = {(a,b) for a,b,_ in F.directions}
    result = []
    for S in points:
        if S in old:
            continue
        if all(max_d2(S,box) <= max(Q(1000**2),*(min_d2((a,b),box) for a,b,_ in F.directions))
               for box in E):
            result.append(S)
    return tuple(result)


@dataclass(frozen=True)
class CPlan:
    cost: float
    U: OpticalPlan
    S: Point | None = None
    fallback: OpticalPlan | None = None
    branch_costs: tuple = ()


def C4(F,p,c,k,r_k,b=None,*,E=None,saved=None):
    """One-step C4/U comparison, with a legal no_signal -> unchanged F branch.

    Every direction posterior is a subset of F. The same saved certified U
    is a valid terminal policy for every angle (one full-circle bin). Thus
    no_signal's full-F U dominates these conservative direction bounds.
    This baseline often selects U immediately; it does not fake information
    gain by omitting the worst branch. Refining bins cannot remove that branch.
    """
    U = Uhat(F,p,b,E=E,saved=saved)
    best = CPlan(U.cost,U)
    if r_k <= 0:
        return best
    for S in S_dist(F,E if E is not None else U.cells):
        fallback = Uhat(F,S,b,E=E,saved=U)
        near = 5+(0 if b is None else math.dist(S,b)/SPEED)
        branches = (('no_signal',fallback.cost),('direction_all_angles',fallback.cost),('near',near))
        cost = math.dist(p,S)/SPEED+(c != k)+5+max(z[1] for z in branches)
        if cost < best.cost-1e-9:
            best = CPlan(cost,U,S,fallback,branches)
    return best


Vhat = C4  # Same closed-loop approximate task interface; r=0 is U.


def search_points():
    return ((0.,0.),)+tuple(snap((r*math.cos(math.radians(30*i+offset)),
                                  r*math.sin(math.radians(30*i+offset))))
                            for r,offset in ((980,0),(1880,15)) for i in range(12))


def discovery_certificate(points=None):
    """Continuous discovery by a conforming triangulation, using integer mm.

    All triangle edges <=1000m: every X in a triangle is within 1000m of
    all its vertices. A convex combination cannot have all vertices strictly
    behind any halfplane through X. Outer polygon contains B(O,1800).
    """
    points = search_points() if points is None else tuple(points)
    if len(points) != 25:
        return {'passed':False,'reason':'requires 25 points'}
    Z = [tuple(round(v*1000) for v in p) for p in points]
    cross = lambda a,b,c:(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    triangles = []
    for i in range(12):
        a,an,b,bprev = 1+i,1+(i+1)%12,13+i,13+(i-1)%12
        triangles.extend([(0,a,an),(a,b,an),(a,bprev,b)])
    max_edge2 = max(sum((Z[a][j]-Z[b][j])**2 for j in (0,1))
                    for t in triangles for a,b in zip(t,t[1:]+t[:1]))
    areas = [cross(*(Z[i] for i in t)) for t in triangles]
    outer = Z[13:]
    containment = all((det := a[0]*b[1]-a[1]*b[0]) > 0 and
                      det*det >= 1800000**2*sum((a[j]-b[j])**2 for j in (0,1))
                      for a,b in zip(outer,outer[1:]+outer[:1]))
    polygon_area2 = sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(outer,outer[1:]+outer[:1]))
    # Topology is fixed above; positive oriented triangles and exact area
    # equality verify the annulus fan has neither inverted pieces nor gaps.
    passed = (Z[0] == (0,0) and all(a>0 for a in areas) and
              sum(areas)==polygon_area2 and containment and max_edge2 <= 1000000**2)
    return dict(passed=passed,triangles=len(triangles),max_edge_m=math.sqrt(max_edge2)/1000,
                outer_contains_arena=containment,integer_mm=True)
