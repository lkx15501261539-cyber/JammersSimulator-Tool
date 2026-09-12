"""Continuous, sufficient certificates for Q4 v2 opportunity measurements.

The caller supplies the existing physical feasible set F and certified E >= F.
Each E cell is clipped by P and certified physical cuts, using Q1's rational
coefficient convention. All
accept/reject predicates are exact on these closed polygons, not samples of F.
Ignoring the remaining physical constraints is a conservative relaxation.

The strict left/right signs deliberately reject antipodal and zero-vector
ambiguities. Rejection means "not certified", not "mathematically impossible".
No probability or new source-position estimator is introduced.
"""
from dataclasses import dataclass
from fractions import Fraction as Q
from functools import lru_cache
import math

from cu import ALPHA, q1, snap


def _point(p):
    return tuple(v if isinstance(v, Q) else Q(str(v)) for v in p)


def _d2(p, q):
    return (p[0]-q[0])**2 + (p[1]-q[1])**2


def _cross_at(a, b, g):
    # Affine in g: the g_x*g_y terms cancel exactly.
    return (a[0]-g[0])*(b[1]-g[1]) - (a[1]-g[1])*(b[0]-g[0])


def _clip(poly, plane):
    if not poly:
        return ()
    a, b, c = plane
    out = []
    p = poly[-1]
    vp = a*p[0] + b*p[1] - c
    for q in poly:
        vq = a*q[0] + b*q[1] - c
        if (vp > 0) != (vq > 0):
            t = vp/(vp-vq)
            out.append((p[0]+t*(q[0]-p[0]), p[1]+t*(q[1]-p[1])))
        if vq <= 0:
            out.append(q)
        p, vp = q, vq
    return tuple(dict.fromkeys(out))


@lru_cache(maxsize=96)
def _geometry(E, P):
    # A failed budget must reject, never fall back to checking cell centers.
    if not E or len(E) > 10000:
        return (), ()
    polygons = []
    for box in E:
        a, b, c, d = map(lambda v: v if isinstance(v, Q) else Q(str(v)), box)
        poly = ((a,b), (c,b), (c,d), (a,d))
        for plane in P:
            poly = _clip(poly, plane)
            if not poly:
                break
        if poly:
            polygons.append(poly)
    hull = q1._convex_hull(p for poly in polygons for p in poly)
    return tuple(polygons), hull


@lru_cache(maxsize=512)
def _near_plane(record):
    """Prove a forward halfplane implied by a direction's strict d>5 rule.

    In the rational Q1 wedge v=lambda*u_lower+mu*u_upper, lambda,mu>=0.
    The three exact quadratic coefficients checked below imply
    25*(w dot v)^2 >= 16*||v||^2. Positive endpoint projections select the
    positive root. Thus ||v||>5 implies w dot v>4; its closed relaxation is
    a safe clipping plane. Unlike deleting a vertex, this handles the whole
    near cell continuously while retaining every possible source position.
    """
    x,y,theta = map(lambda v: v if isinstance(v,Q) else Q(str(v)),record)
    alpha = Q(str(ALPHA))
    w = q1._unit_direction(theta)
    lower,upper = q1._unit_direction(theta-alpha),q1._unit_direction(theta+alpha)
    dot = lambda a,b:a[0]*b[0]+a[1]*b[1]
    a,b = dot(w,lower),dot(w,upper)
    if (a <= 0 or b <= 0 or 25*a*a < 16*dot(lower,lower)
            or 25*b*b < 16*dot(upper,upper) or 25*a*b < 16*dot(lower,upper)):
        return None  # Failure to prove the cut never removes a part of F.
    return -w[0],-w[1],-w[0]*x-w[1]*y-4


def _region(F, E):
    planes = tuple(F.P)+tuple(p for record in F.directions
                             if (p := _near_plane(tuple(record))) is not None)
    return _geometry(tuple(tuple(b) for b in E), planes)


def _min_d2(p, poly):
    if len(poly) == 1:
        return _d2(p, poly[0])
    edges = tuple(zip(poly, poly[1:]+poly[:1]))
    if len(poly) >= 3 and all(_cross_at(a, b, p) >= 0 for a,b in edges):
        return Q(0)
    answer = min(_d2(p, q) for q in poly)
    for a, b in edges:
        dx, dy = b[0]-a[0], b[1]-a[1]
        norm = dx*dx+dy*dy
        if not norm:
            continue
        t = max(Q(0), min(Q(1), ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/norm))
        answer = min(answer, _d2(p, (a[0]+t*dx, a[1]+t*dy)))
    return answer


def _distance_data(F, polygons):
    successes = tuple(_point((a,b)) for a,b,_ in F.directions)
    lower = tuple(max(Q(1000**2), *(_min_d2(p, poly) for p in successes))
                  for poly in polygons)
    return successes, lower


def _distance_safe(S, polygons, successes, lower):
    if not polygons or not successes:
        return False
    for poly, radius2 in zip(polygons, lower):
        if max(_d2(S,g) for g in poly) <= radius2:
            continue
        # ||S-g||²-||Si-g||² is affine. This preserves the pointwise radius
        # lower bound even when the separate interval bounds are too loose.
        if any(all(_d2(S,g)-_d2(previous,g) <= 0 for g in poly)
               for previous in successes):
            continue
        return False
    return True


def distance_safe(F, E, S):
    """Certify ||snap(S)-g|| <= max(1000, successful distances), all g in F."""
    polygons, _ = _region(F,E)
    if not F.directions or not polygons:
        return False
    return _distance_safe(_point(snap(S)), polygons, *_distance_data(F,polygons))


def _sqrt_down(ratio):
    """Float lower bound, checked against the exact squared rational bound."""
    value = math.sqrt(float(ratio))
    while Q.from_float(value)**2 > ratio:
        value = math.nextafter(value, 0.)
    return value


@dataclass(frozen=True)
class PairCertificate:
    left: int
    right: int
    quality: float
    preferred: bool = False
    method: str = 'rational E/P/physical-cut distance and cross-product bounds'


def _side(S0, S, hull):
    values = tuple(_cross_at(S0, S, g) for g in hull)
    if max(values) < 0:
        side, numerator = -1, -max(values)
    elif min(values) > 0:
        side, numerator = 1, min(values)
    else:
        return 0, Q(0)
    # Strict sign excludes S==g and the opposite-ray wrap boundary.
    denominator = max(_d2(S0,g) for g in hull)*max(_d2(S,g) for g in hull)
    return side, numerator*numerator/denominator


def _preferred(S0, S, hull):
    """Sufficient full-region certificate for 30 <= |delta| <= 60 degrees."""
    cross = tuple(abs(_cross_at(S0,S,g)) for g in hull)
    mid = ((S0[0]+S[0])/2,(S0[1]+S[1])/2)
    dot_min = _min_d2(mid,hull)-_d2(S0,S)/4
    dot_max = max((S0[0]-g[0])*(S[0]-g[0])+(S0[1]-g[1])*(S[1]-g[1]) for g in hull)
    return (dot_min > 0 and 3*min(cross)**2 >= dot_max**2
            and max(cross)**2 <= 3*dot_min**2)


def find_pairs(F, E, Z, remaining):
    """Return certified ordered (left, right) indices in the remaining route.

    Both points use the coordinates actually sent by the controller (snap).
    For each g, cross(S0-g,L-g)<0<cross(S0-g,R-g) puts the wrapped angles in
    (-180,0) and (0,180). cross(L-g,R-g)>=0 then proves their spread <=180.
    Since each cross product is affine in g, hull vertices prove the complete
    region, including its interior. The quality is a conservative J2 bound.
    """
    if not F.directions:
        return []
    polygons, hull = _region(F,E)
    if not hull:
        return []
    S0 = _point(F.directions[0][:2])
    successes, lower = _distance_data(F,polygons)
    candidates = []
    for j in dict.fromkeys(remaining):
        S = _point(snap(Z[j]))
        side, quality2 = _side(S0,S,hull)
        if side and _distance_safe(S,polygons,successes,lower):
            candidates.append((j,S,side,quality2))
    result = []
    for left,L,signL,ql in candidates:
        if signL != -1:
            continue
        for right,R,signR,qr in candidates:
            if signR == 1 and all(_cross_at(L,R,g) >= 0 for g in hull):
                result.append(PairCertificate(left,right,_sqrt_down(min(ql,qr)),
                    _preferred(S0,L,hull) and _preferred(S0,R,hull)))
    return result


def safe_visible(F, E, S):
    """Conservative single-point guarantee for a remaining budget of one.

    A previously successful position remains visible for a fixed uncleared
    source. A zero-distance point is also visible. Merely being within 5m
    does NOT bypass the simulator's directional halfplane.
    """
    _, hull = _region(F,E)
    if not hull or not F.directions:
        return False
    S = _point(snap(S))
    return any(S == _point((a,b)) for a,b,_ in F.directions) or all(g == S for g in hull)


def _circle_three(a,b,c):
    # This is only a center proposal. Acceptance is an exact separate check.
    bx,by,cx,cy = b[0]-a[0],b[1]-a[1],c[0]-a[0],c[1]-a[1]
    det = 2*(bx*cy-by*cx)
    if det == 0:
        pair = max(((a,b),(a,c),(b,c)), key=lambda p:math.dist(*p))
        center = ((pair[0][0]+pair[1][0])/2,(pair[0][1]+pair[1][1])/2)
    else:
        bl,cl = bx*bx+by*by,cx*cx+cy*cy
        center = (a[0]+(cy*bl-by*cl)/det,a[1]+(bx*cl-cx*bl)/det)
    return center, max(math.dist(center,p) for p in (a,b,c))


def certified_clear_point(F, E):
    """Return an actual millimeter point whose 20m disk covers F, or None.

    Circle construction uses floats only to propose a point. Exact rational
    squared distances certify every vertex of E intersect P after snapping;
    by convexity this certifies all polygons and hence the continuous F.
    A too-large or numerically ambiguous region conservatively returns None.
    """
    _, hull = _region(F,E)
    if not hull:
        return None
    xmin,xmax = min(p[0] for p in hull),max(p[0] for p in hull)
    ymin,ymax = min(p[1] for p in hull),max(p[1] for p in hull)
    if xmax-xmin > 40 or ymax-ymin > 40:
        return None
    points = [tuple(map(float,p)) for p in hull]
    center,radius = points[0],0.
    for i,a in enumerate(points):
        if math.dist(center,a) <= radius:
            continue
        center,radius = a,0.
        for j,b in enumerate(points[:i]):
            if math.dist(center,b) <= radius:
                continue
            center = ((a[0]+b[0])/2,(a[1]+b[1])/2)
            radius = math.dist(center,a)
            for c in points[:j]:
                if math.dist(center,c) > radius:
                    center,radius = _circle_three(a,b,c)
    proposals = (center, ((xmin+xmax)/2,(ymin+ymax)/2))
    for proposal in proposals:
        if not all(math.isfinite(float(v)) for v in proposal):
            continue
        q = snap(proposal)
        if all(_d2(_point(q),g) <= 20**2 for g in hull):
            return q
    return None
