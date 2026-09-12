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


# Route-constrained candidates are additive APIs. The v2 paired controller
# above retains its original functions and behavior.
_SCORE_RUNTIME = {}


def prepare_score_runtime(options=None):
    """Load unchanged Q3 pure-P diameter geometry, optionally before /enter.

    The original ZIP remains the source of truth. Only its verified solver is
    loaded; its omnidirectional negative-history logic is never called here.
    Missing optional NumPy/Numba dependencies leave an exact Q1 fallback.
    """
    import hashlib
    import importlib.util
    from pathlib import Path
    import sys
    import tempfile
    import zipfile
    import json

    options = options or {}
    requested = options.get('q2_solver_path')
    key = str(requested or 'bundled-baseline-v2')
    if key in _SCORE_RUNTIME:
        return dict(_SCORE_RUNTIME[key]['metadata'])
    fallback = dict(backend='q1_fraction', half_angle_deg=ALPHA,
                    score_scope='sampled pure-P diameter; not a continuous worst-case upper bound')
    try:
        import numpy as np
        if requested:
            source_path = Path(requested).resolve()
            source = source_path.read_bytes()
            provenance = dict(solver_path=str(source_path), solver_sha256=hashlib.sha256(source).hexdigest())
        else:
            archive = Path(__file__).resolve().parents[2]/'models'/'Baseline_v2.0_七点六边形.zip'
            prefix = 'Baseline_v2.0_七点六边形'
            with zipfile.ZipFile(archive) as z:
                manifest = json.loads(z.read(prefix+'/SHA256SUMS.json'))
                source = z.read(prefix+'/solver.py')
                digest = hashlib.sha256(source).hexdigest()
                if digest != manifest['solver.py']:
                    raise ValueError('Q2 source hash does not match the frozen archive manifest')
            directory = Path(tempfile.gettempdir())/'JammersLab-route-Q2'/digest
            directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            source_path = directory/'solver.py'
            if source_path.exists():
                if source_path.read_bytes() != source:
                    raise ValueError('Q2 runtime cache differs from the frozen source')
            else:
                source_path.write_bytes(source)
            provenance = dict(archive=str(archive),solver_sha256=digest,
                              archive_entry=prefix+'/solver.py')
        name = '_jammers_route_q2_'+hashlib.sha256(source).hexdigest()[:16]
        module = sys.modules.get(name)
        if module is None:
            spec = importlib.util.spec_from_file_location(name,source_path)
            module = importlib.util.module_from_spec(spec)
            previous = sys.modules.get('第一问')
            sys.modules['第一问'] = q1
            sys.modules[name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(name,None)
                raise
            finally:
                if previous is None:
                    sys.modules.pop('第一问',None)
                else:
                    sys.modules['第一问'] = previous
        # Only the existing pure-P geometry kernel is reused. Halfplanes are
        # supplied by this model's Q1 at 1.005 degrees, not Q3's fixed 1 degree.
        module.pure_diameter(np.array([[1.,0.],[-1.,0.],[0.,1.],[0.,-1.]]),np.ones(4))
        metadata = dict(fallback,backend='q3_pure_diameter',**provenance)
        _SCORE_RUNTIME[key] = dict(module=module,np=np,metadata=metadata)
    except (ImportError,OSError,ValueError,KeyError,zipfile.BadZipFile,RuntimeError) as exc:
        _SCORE_RUNTIME[key] = dict(module=None,np=None,metadata=dict(fallback,fallback_reason=str(exc)))
    return dict(_SCORE_RUNTIME[key]['metadata'])


@dataclass(frozen=True)
class RouteCandidate:
    point: tuple
    level: int
    J_hat: float
    extra_move_s: float
    segment_index: int | None
    certificate: dict
    quality_flags: tuple = ('sampled_J_not_continuous_bound',)


class PreparedRouteGeometry:
    """History-only reception proofs and Q2 loss for a remaining polyline.

    ``segments`` is a sequence of (A,B) pairs in the current plan. Returned
    segment indices refer to that sequence, so task identities stay with the
    controller. Finite searches return conservative certified candidates;
    failure to find one never proves C_rx or its route intersection empty.
    """
    def __init__(self,F,E,measured_points=(),options=None):
        import copy
        # A prepared object belongs to one information state. Subsequent
        # controller observations must create a new object, never mix an old
        # P cache with a newly mutated direction list.
        self.F,self.E,self.options = copy.deepcopy(F),tuple(E),dict(options or {})
        self.polygons,self.hull = _region(self.F,self.E)
        self.successes,self.lower = _distance_data(self.F,self.polygons) if self.F.directions else ((),())
        self.measured = {snap(p) for p in measured_points}|{snap(o[:2]) for o in self.F.directions}
        self.history_key = (tuple(self.F.directions),tuple(getattr(self.F,'negative_history',())),
                            tuple(sorted(self.measured)))
        self._certificates,self._scores = {},{}
        self.source_samples = self._samples(max(1,int(self.options.get('score_source_samples',9))))
        count = max(2,int(self.options.get('score_error_samples',3)))
        self.errors = tuple(-ALPHA+2*ALPHA*i/(count-1) for i in range(count))
        self.metadata = prepare_score_runtime(self.options)
        runtime = _SCORE_RUNTIME[str(self.options.get('q2_solver_path') or 'bundled-baseline-v2')]
        self._kernel,self._np = runtime['module'],runtime['np']
        self._planes = tuple(self.F.P)
        self.metadata.update(source_sample_count=len(self.source_samples),
                             error_sample_count=len(self.errors),
                             reception_scope='continuous rational enclosure of F',
                             hidden_truth_used=False)

    def exclude_measured(self,points):
        """Refresh same-channel exclusions after no_signal without changing F.

        Reception and source-scenario caches remain valid because this model
        deliberately does not shrink F on these directional failures. Updating
        the exclusion before candidate ranking also lets an exhausted Level 1
        set correctly fall through to Level 2 instead of reselecting its old point.
        """
        for point in points:
            q = snap(point)
            self.measured.add(q)
            self._scores[q] = math.inf
        self.history_key = (tuple(self.F.directions),tuple(getattr(self.F,'negative_history',())),
                            tuple(sorted(self.measured)))

    def _samples(self,limit):
        pool = []
        for poly in self.polygons:
            pool.append(tuple(float(sum(p[i] for p in poly)/len(poly)) for i in (0,1)))
            pool.extend(tuple(map(float,p)) for p in poly)
        if len(self.F.directions) == 1:
            x,y,theta = self.F.directions[0]
            for radius in (5.001,20.,100.,300.,600.,1000.,1200.,1499.999):
                for offset in (-.999*ALPHA,0.,.999*ALPHA):
                    a = math.radians(theta+offset)
                    pool.append((x+radius*math.cos(a),y+radius*math.sin(a)))
        legal = tuple(dict.fromkeys(p for p in pool if self.F.contains(p)))
        if len(legal) <= limit:
            return legal
        centre = tuple(sum(p[i] for p in legal)/len(legal) for i in (0,1))
        chosen = [max(legal,key=lambda p:math.dist(p,centre))]
        distances = [math.inf]*len(legal)
        while len(chosen) < limit:
            last = chosen[-1]
            distances = [min(d,(p[0]-last[0])**2+(p[1]-last[1])**2)
                         for d,p in zip(distances,legal)]
            chosen.append(legal[max(range(len(legal)),key=lambda i:distances[i])])
        return tuple(chosen)

    def _prefilter(self,S):
        # A fast rejection proposal only. Passing samples never accepts S.
        return all(math.dist(S,g) <= max(1000.,*(math.dist(g,o[:2]) for o in self.F.directions))+1e-8
                   for g in self.source_samples)

    def _certify_raw(self,S):
        S = _point(S)
        if S not in self._certificates:
            self._certificates[S] = (bool(self.successes) and self._prefilter(tuple(map(float,S)))
                and _distance_safe(S,self.polygons,self.successes,self.lower))
        return self._certificates[S]

    def certify(self,point):
        """Continuous reception proof for the actual millimeter command."""
        return self._certify_raw(_point(snap(point)))

    def score(self,point):
        """The Q2 sampled max of D(P intersect new wedge), in metres.

        Samples are legal positions generated from F/E, never the true source.
        They rank candidates only. Neither sample completeness nor a continuous
        upper bound is claimed. In Q4 this omnidirectional geometric score does
        not assert visibility or assign a probability to no_signal.
        """
        q = snap(point)
        if q in self._scores:
            return self._scores[q]
        if q in self.measured or not self.source_samples or not self.F.directions:
            self._scores[q] = math.inf
            return math.inf
        worst = 0.
        for g in self.source_samples:
            if math.dist(g,q) <= 5:
                continue
            beta = math.degrees(math.atan2(g[1]-q[1],g[0]-q[0]))
            for error in self.errors:
                theta = (beta+error)%360
                if self._kernel is None:
                    result = q1.localize([*self.F.directions,(*q,theta)],error_deg=ALPHA)
                    value = math.inf if result.status in ('空集','无界') else float(result.diameter or 0.)
                else:
                    planes = self._planes+tuple(q1.build_halfplanes([(*q,theta)],error_deg=ALPHA))
                    h = self._np.asarray([[float(a),float(b)] for a,b,_ in planes])
                    c = self._np.asarray([float(c) for _,_,c in planes])
                    value = float(self._kernel.pure_diameter(h,c))
                if not math.isfinite(value):
                    worst = math.inf  # An empty/numerically invalid branch is never zero.
                    break
                worst = max(worst,value)
            if not math.isfinite(worst):
                break
        self._scores[q] = worst
        return worst

    @staticmethod
    def _extra(point,A,B):
        return max(0.,(math.dist(A,point)+math.dist(point,B)-math.dist(A,B))/5.)

    @staticmethod
    def _at(A,B,t):
        A,B,t = _point(A),_point(B),Q(str(t)) if not isinstance(t,Q) else t
        return tuple(a+t*(b-a) for a,b in zip(A,B))

    def _probe_parameters(self,A,B):
        count = max(3,int(self.options.get('path_probe_count',7)))
        parameters = {Q(i,count-1) for i in range(count)}
        dx,dy = B[0]-A[0],B[1]-A[1]
        length2 = dx*dx+dy*dy
        if not length2:
            return [Q(0)]
        # Sample disks propose a tighter parameter range. They are NOT used
        # as certificates, and all eventual endpoints pass exact validation.
        lo,hi = 0.,1.
        for g in self.source_samples:
            radius = max(1000.,*(math.dist(g,o[:2]) for o in self.F.directions))
            vx,vy = A[0]-g[0],A[1]-g[1]
            b = 2*(vx*dx+vy*dy)
            c = vx*vx+vy*vy-radius*radius
            discr = b*b-4*length2*c
            if discr < 0:
                continue
            root = math.sqrt(max(0.,discr))
            lo,hi = max(lo,(-b-root)/(2*length2)),min(hi,(-b+root)/(2*length2))
        if lo <= hi:
            parameters.update(Q(str(lo+(hi-lo)*i/(count-1))) for i in range(count))
        for point in [*self.F.directions[:1],*self.source_samples]:
            t = ((point[0]-A[0])*dx+(point[1]-A[1])*dy)/length2
            parameters.add(Q(str(max(0.,min(1.,t)))))
        return sorted(t for t in parameters if 0 <= t <= 1)

    def certified_intervals(self,segments):
        """Conservative inner intersection intervals, certified continuously.

        C_rx is an intersection of disks, hence convex. Exact reception at
        both endpoints therefore certifies the complete intervening segment,
        even if a sufficient numerical oracle might reject an interior probe.
        """
        intervals = []
        for index,(a,b) in enumerate(segments):
            A,B = tuple(map(float,a)),tuple(map(float,b))
            parameters = self._probe_parameters(A,B)
            accepted = [t for t in parameters if self._certify_raw(self._at(A,B,t))]
            if not accepted:
                continue
            lo,hi = min(accepted),max(accepted)
            left = max((t for t in parameters if t < lo),default=lo)
            right = min((t for t in parameters if t > hi),default=hi)
            for _ in range(max(0,int(self.options.get('path_refine_rounds',4)))):
                if left < lo:
                    middle = (left+lo)/2
                    if self._certify_raw(self._at(A,B,middle)): lo = middle
                    else: left = middle
                if hi < right:
                    middle = (hi+right)/2
                    if self._certify_raw(self._at(A,B,middle)): hi = middle
                    else: right = middle
            intervals.append(dict(segment_index=index,t_min=lo,t_max=hi,
                                  endpoint_a=self._at(A,B,lo),endpoint_b=self._at(A,B,hi),
                                  method='certified endpoints plus convexity of C_rx'))
        return intervals

    def _candidate(self,point,level,index,A=None,B=None,certificate=None):
        q = snap(point)
        if q in self.measured or not self.certify(q):
            return None
        value = self.score(q)
        if not math.isfinite(value):
            return None
        extra = math.inf if A is None else self._extra(q,A,B)
        proof = dict(certified=True,arithmetic='rational continuous F enclosure',
                     actual_command=q,snapped_rechecked=True,**(certificate or {}))
        return RouteCandidate(q,level,value,extra,index,proof)

    def _rank(self,candidates):
        unique = {}
        for item in candidates:
            if item is None:
                continue
            previous = unique.get(item.point)
            if previous is None or (item.extra_move_s,item.segment_index or 0) < (previous.extra_move_s,previous.segment_index or 0):
                unique[item.point] = item
        ranked = sorted(unique.values(),key=lambda x:(x.J_hat,x.extra_move_s,x.segment_index or 0,x.point))
        return ranked[:max(1,int(self.options.get('candidate_limit',24)))]

    def path_candidates(self,segments):
        segments = tuple(segments)
        result = []
        count = max(3,int(self.options.get('path_probe_count',7)))
        for interval in self.certified_intervals(segments):
            index = interval['segment_index']
            A,B = segments[index]
            lo,hi = interval['t_min'],interval['t_max']
            for i in range(count):
                t = lo+(hi-lo)*Q(i,count-1)
                proof = dict(method=interval['method'],t_min=str(lo),t_max=str(hi),
                             raw_segment_certified=True,level='on_route')
                candidate = self._candidate(self._at(A,B,t),1,index,A,B,proof)
                if candidate is not None and candidate.extra_move_s <= math.sqrt(2)/5000+1e-9:
                    result.append(candidate)
        return self._rank(result)

    def nearby_candidates(self,segments,tau_route=None):
        tau = float(self.options.get('tau_route_s',5.) if tau_route is None else tau_route)
        if not math.isfinite(tau) or tau < 0:
            raise ValueError('tau_route must be finite and nonnegative')
        result = []
        for index,(A,B) in enumerate(segments):
            A,B = tuple(map(float,A)),tuple(map(float,B))
            length = math.dist(A,B)
            if not length:
                directions = tuple((math.cos(math.tau*i/8),math.sin(math.tau*i/8)) for i in range(8))
                bases = [A]
                width = 5*tau/2
            else:
                directions = ((-(B[1]-A[1])/length,(B[0]-A[0])/length),)
                bases = [tuple(map(float,self._at(A,B,t))) for t in self._probe_parameters(A,B)]
                width = math.sqrt(max(0.,(length+5*tau)**2-length**2))/2
            for base in bases:
                for direction in directions:
                    for fraction in (0.,.125,.25,.5,.75,1.):
                        for sign in (-1,1):
                            point = snap((base[0]+sign*fraction*width*direction[0],
                                          base[1]+sign*fraction*width*direction[1]))
                            if self._extra(point,A,B) > tau+1e-9:
                                continue
                            item = self._candidate(point,2,index,A,B,
                                dict(method='continuous distance certificate',level='near_route',tau_route_s=tau))
                            if item is not None:
                                result.append(item)
        return self._rank(result)

    def global_candidates(self,segments=None):
        """Finite Q2 search for Level 3/ablation; not a continuous optimum."""
        if not self.source_samples or not self.F.directions:
            return []
        x,y,theta = self.F.directions[0]
        points = []
        for distance in (350.,600.,900.):
            for offset in (-60.,-45.,45.,60.):
                a = math.radians(theta+offset)
                points.append((x+distance*math.cos(a),y+distance*math.sin(a)))
        g = self.source_samples[0]
        radius = max(1000.,*(math.dist(g,o[:2]) for o in self.F.directions))
        step = max(25.,float(self.options.get('global_grid_step_m',300.)))
        count = int(math.ceil(2*radius/step))
        points.extend((g[0]-radius+i*step,g[1]-radius+j*step)
                      for i in range(count+1) for j in range(count+1))
        proof = dict(method='continuous distance certificate',level='global_Q2_finite_search',
                     optimality='best scored finite candidate only')
        result = self._rank(self._candidate(p,3,None,certificate=proof) for p in points)
        refine = max(5.,float(self.options.get('global_refine_step_m',100.)))
        for centre in result[:max(0,int(self.options.get('global_refine_count',2)))]:
            extent = int(math.ceil(step/refine))
            for i in range(-extent,extent+1):
                for j in range(-extent,extent+1):
                    item = self._candidate((centre.point[0]+i*refine,centre.point[1]+j*refine),
                                           3,None,certificate=proof)
                    if item is not None:
                        result.append(item)
        result = self._rank(result)
        if segments:
            segments = tuple(segments)
            attached = []
            for item in result:
                index = min(range(len(segments)),key=lambda i:self._extra(item.point,*segments[i]))
                attached.append(RouteCandidate(item.point,3,item.J_hat,
                    self._extra(item.point,*segments[index]),index,item.certificate,item.quality_flags))
            result = self._rank(attached)
        return result

    def choose(self,segments,*,allow_global=False):
        segments = tuple(segments)
        candidates = self.path_candidates(segments)
        if not candidates:
            candidates = self.nearby_candidates(segments)
        if not candidates and allow_global:
            candidates = self.global_candidates(segments)
        return candidates[0] if candidates else None
