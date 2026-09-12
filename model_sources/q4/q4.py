"""Q4 controller and REST entry point. Python 3.10+, standard library only."""
import argparse
from dataclasses import dataclass, field
import json
import ipaddress
import math
from pathlib import Path
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen, build_opener, ProxyHandler
from urllib.parse import urlparse
from http.client import RemoteDisconnected

from cu import (Feasible, certified_outer, Uhat, C4, path_cost, search_points,
                discovery_certificate, two_opt, snap, optical_routes)


@dataclass
class Channel:
    k: int
    sigma: str = 'UNKNOWN'
    ell: int = 0
    F: Feasible = field(default_factory=Feasible)
    E: tuple = ()
    H_k0: object = None
    r_k: int = 0
    scanned: dict = field(default_factory=dict)
    followups: int = 0

    @property
    def P(self):
        return self.F.P


class RestClient:
    """Serial calls; retries keep identical request_id and body."""
    def __init__(self, url, robot_id='q4-local', retries=2):
        self.url,self.robot_id,self.retries = url.rstrip('/'),robot_id,retries
        host=urlparse(self.url).hostname or ''
        try: local=ipaddress.ip_address(host).is_loopback
        except ValueError: local=host.lower()=='localhost'
        self._open=build_opener(ProxyHandler({})).open if local else urlopen

    def _call(self,path,**fields):
        body = json.dumps(dict(arena_id='default',robot_id=self.robot_id,
                               request_id=str(uuid.uuid4()),**fields)).encode()
        request = Request(self.url+path,data=body,headers={'Content-Type':'application/json'})
        for attempt in range(self.retries+1):
            try:
                with self._open(request,timeout=20) as response:
                    return json.load(response)
            except HTTPError as exc:
                if exc.code < 500:
                    return json.load(exc)
                if attempt == self.retries:
                    raise
            except (URLError,TimeoutError,RemoteDisconnected,ConnectionResetError):
                if attempt == self.retries:
                    raise
            time.sleep(.1*(attempt+1))

    def enter(self): return self._call('/enter')
    def exit(self): return self._call('/exit')
    def measure(self,x,y,channel):
        return self._call('/measure',position=dict(x=x,y=y),channel=channel)
    def clear(self,x,y,channel):
        return self._call('/clear',position=dict(x=x,y=y),channel=channel)


class Controller:
    def __init__(self,client):
        self.client = client
        self.channels = {k:Channel(k) for k in range(1,21)}
        self.p,self.c,self.ell = (0.,0.),1,0
        self.Z = search_points()
        self.certificate = discovery_certificate(self.Z)
        if not self.certificate['passed']:
            raise RuntimeError('25-point discovery certificate failed')
        self.remaining = list(range(25))
        self.counts = dict(L_move=0.,N_sw=0,N_meas=0,N_clr=0,N_succ=0)
        self.actions,self.decisions = [],[]
        self._routes = {}

    @property
    def T(self):
        n=self.counts
        return n['L_move']/5+n['N_sw']+5*n['N_meas']+3*n['N_clr']+2*n['N_succ']

    def action(self,kind,k,p):
        p = snap(p)
        r = getattr(self.client,kind)(*p,k)
        if r.get('accepted') is not True:
            raise RuntimeError(f'{kind} rejected: {r}')
        self.counts['L_move'] += math.dist(self.p,p)
        self.p = p
        self.ell += 1
        if kind == 'measure':
            self.counts['N_meas'] += 1
            self.counts['N_sw'] += self.c != k
            self.c = k
            if r.get('measure_result') not in ('direction','near','no_signal'):
                raise RuntimeError(f'unknown measurement response: {r}')
        else:
            self.counts['N_clr'] += 1
            if r.get('clear_result') not in ('success','no_target_in_range'):
                raise RuntimeError(f'unknown clear response: {r}')
            self.counts['N_succ'] += r['clear_result']=='success'
        self.actions.append(dict(kind=kind,k=k,p=p,response=r,T=self.T))
        if not math.isclose(self.T,r['virtual_time_s'],rel_tol=1e-10,abs_tol=1e-7):
            raise RuntimeError('independent timing audit failed')
        return r

    def clear_near(self,s):
        if self.action('clear',s.k,self.p)['clear_result'] != 'success':
            raise RuntimeError('near then clear failed: model/protocol mismatch')
        s.sigma,s.ell = 'CLEARED',self.ell

    def direction(self,s,theta):
        if not isinstance(theta,(float,int)) or not math.isfinite(theta) or not 0<=theta<360:
            raise RuntimeError('invalid direction angle')
        first = s.sigma == 'UNKNOWN'
        s.F.directions.append((*self.p,theta))
        s.sigma,s.ell = 'FOUND',self.ell
        try:
            s.E = certified_outer(s.F)
        except RuntimeError:
            if s.H_k0 is None:
                raise
            # Explicitly retain the first reliable covering set on numerical failure.
            s.E = s.H_k0.cells
            self.decisions.append(dict(k=s.k,event='outer_failed_use_H_k0'))
        if first:
            s.r_k = 3
            s.H_k0 = Uhat(s.F,self.p,E=s.E)
        self._routes.pop(s.k,None)

    def scan(self,j):
        unknown = [k for k,s in self.channels.items() if s.sigma=='UNKNOWN' and j not in s.scanned]
        unknown.sort(key=lambda k:(k!=self.c,k))
        for k in unknown:
            s = self.channels[k]
            r = self.action('measure',k,self.Z[j])
            result = r['measure_result']
            s.scanned[j] = result  # Only after an accepted, completed measure.
            s.ell = self.ell
            if result == 'direction':
                self.direction(s,r['svd_deg'])
            elif result == 'near':
                s.sigma = 'FOUND'
                self.clear_near(s)
            else:
                s.F.negative_history.append(self.p)
                if len(s.scanned)==25 and all(v=='no_signal' for v in s.scanned.values()):
                    s.sigma = 'ABSENT'
        self.remaining.remove(j)

    def optical(self,s,plan,reason):
        if not plan.verifies():
            raise RuntimeError('uncertified optical fallback')
        self.decisions.append(dict(k=s.k,event='U',reason=reason,points=len(plan.route),bound=plan.cost))
        for q in plan.route:
            if self.action('clear',s.k,q)['clear_result']=='success':
                s.sigma,s.ell = 'CLEARED',self.ell
                return
        raise RuntimeError('certified F coverage exhausted without success; mission incomplete')

    def followup(self,s,S,fallback):
        """Public transition handler also tested with adversarial legal replies."""
        if s.sigma!='FOUND' or s.r_k<=0:
            raise RuntimeError('followup budget exhausted or source not FOUND')
        r = self.action('measure',s.k,S)
        s.r_k -= 1
        s.followups += 1
        s.ell = self.ell
        result = r['measure_result']
        if result == 'near':
            self.clear_near(s)
        elif result == 'direction':
            self.direction(s,r['svd_deg'])
        else:
            # Do not shrink F, change P/E, reset r_k, or infer ABSENT.
            self.optical(s,fallback,'no_signal')
        return result

    def service(self,k,b=None):
        s = self.channels[k]
        saved = s.H_k0
        while s.sigma=='FOUND':
            plan = C4(s.F,self.p,self.c,k,s.r_k,b,E=s.E,saved=saved)
            self.decisions.append(dict(k=k,event='C4/U',r_k=s.r_k,C4=plan.cost,
                                       U=plan.U.cost,S=plan.S))
            if plan.S is None:
                self.optical(s,plan.U,'r_k=0' if s.r_k==0 else 'C4>=U')
                return
            # Saving the branch policy prevents rolling replans increasing
            # the previously advertised remaining bound for this fixed b.
            saved = plan.fallback
            self.followup(s,plan.S,saved)

    def _task_U(self,k,p,b):
        s=self.channels[k]
        if k not in self._routes:
            self._routes[k]=optical_routes(s.E)
        return min(path_cost(route,p,b) for route in self._routes[k])

    def schedule_cost(self,tasks):
        """Complete single-source insertion cost; source joins fixed next anchor.

        Remaining UNKNOWNs conservatively all stay UNKNOWN in future scans.
        At most one adaptive task is evaluated, so no guessed source endpoints
        or overlapping C charges. Future unknown discoveries aren't forecast.
        """
        p,c,cost = self.p,self.c,0.
        unknown = [k for k,s in self.channels.items() if s.sigma=='UNKNOWN']
        joined = False
        for i,(kind,key) in enumerate(tasks):
            if kind=='source':
                b = self.Z[tasks[i+1][1]] if i+1<len(tasks) else None
                cost += self._task_U(key,p,b)
                if b is not None: p=b
                joined = True
            else:
                z=self.Z[key]
                if not joined: cost += math.dist(p,z)/5
                p,joined=z,False
                ks=[k for k in unknown if key not in self.channels[k].scanned]
                ks.sort(key=lambda k:(k!=c,k))
                for k in ks:
                    cost += 5+(k!=c)
                    c=k
        return cost

    def next_tasks(self):
        if not any(s.sigma=='UNKNOWN' for s in self.channels.values()):
            self.remaining=[]
        route=[]
        todo=set(self.remaining)
        p=self.p
        while todo:
            j=min(todo,key=lambda j:(math.dist(p,self.Z[j]),j))
            todo.remove(j);route.append(('search',j));p=self.Z[j]
        route=list(two_opt(route,self.schedule_cost,rounds=2))
        base=self.schedule_cost(route)
        candidates=[]
        for k,s in self.channels.items():
            if s.sigma=='FOUND':
                for i in range(len(route)+1):
                    tasks=route[:i]+[('source',k)]+route[i:]
                    candidates.append((self.schedule_cost(tasks)-base,k,i,tasks))
        if candidates:
            delta,k,i,tasks=min(candidates,key=lambda v:v[:3])
            # Keep only one source, and recompute the WHOLE cost at every reversal.
            route=list(two_opt(tasks,self.schedule_cost,rounds=2))
            self.decisions.append(dict(event='insertion',k=k,delta=delta,
                                       evaluated_slots=len(candidates),plan_bound=self.schedule_cost(route)))
        return route

    def run(self):
        entered=False
        failure=None
        try:
            r=self.client.enter()
            if r.get('accepted') is not True: raise RuntimeError(str(r))
            entered=True
            self.scan(0)
            while not all(s.sigma in ('CLEARED','ABSENT') for s in self.channels.values()):
                tasks=self.next_tasks()
                if not tasks: raise RuntimeError('pending channels but no task')
                kind,key=tasks[0]
                if kind=='search': self.scan(key)
                else:
                    b=self.Z[tasks[1][1]] if len(tasks)>1 else None
                    self.service(key,b)
        except Exception as exc:
            failure=f'{type(exc).__name__}: {exc}'
        finally:
            if entered:
                try:
                    r=self.client.exit()
                    if not r.get('accepted'): raise RuntimeError(str(r))
                except Exception as exc:
                    failure=f'{failure or ""}; exit failed: {exc}'
        return dict(completed=failure is None and all(s.sigma in ('CLEARED','ABSENT') for s in self.channels.values()),
                    failure=failure,T=self.T,counts=self.counts,discovery_certificate=self.certificate,
                    channels={k:dict(sigma=s.sigma,r_k=s.r_k,followups=s.followups,
                                     scans=len(s.scanned),directions=len(s.F.directions)) for k,s in self.channels.items()},
                    decisions=self.decisions)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:2027')
    parser.add_argument('--robot-id',default='q4-local')
    parser.add_argument('--output',type=Path,default=Path('q4-run'))
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    controller=Controller(RestClient(args.url,args.robot_id))
    result=controller.run()
    (args.output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (args.output/'actions.jsonl').write_text(''.join(json.dumps(a,ensure_ascii=False)+'\n' for a in controller.actions),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('completed','failure','T','counts')},ensure_ascii=False,indent=2))
    return 0 if result['completed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
