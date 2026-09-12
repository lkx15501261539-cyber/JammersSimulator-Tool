"""Unmodified Baseline v1 ZIP importer and process-isolated simulation bridge."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import subprocess
import sys
import threading
import time
import zipfile
from .world import ScenarioConfig, World
from .runner import save_run
from .strategy import load_q1, localization_data

MODEL_LABELS = {'hexagon_v1': '六边形 7 点 · Baseline 1.0',
                'spiral_v1': '螺旋 12 点 · Baseline 1.0'}
ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_NAME = 'Baseline_v1.0_两模型完整交付.zip'
CACHE = ROOT / '.cache' / 'baselines'


def default_archive():
    candidates = [Path(os.environ.get('BASELINE_ARCHIVE', ROOT/'models'/ARCHIVE_NAME)),
                  Path.home()/'Downloads'/ARCHIVE_NAME]
    return next((p for p in candidates if p.is_file()), candidates[0])


def prepare_model(archive, model):
    """Check ZIP manifest, extract verbatim runtime files, reject altered cache bytes."""
    if model not in MODEL_LABELS:
        raise ValueError('Unknown baseline model')
    archive = Path(archive)
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    directory = CACHE/archive_hash/model
    with zipfile.ZipFile(archive) as z:
        manifest = json.loads(z.read(f'{model}/SHA256SUMS.json'))
        for name, expected in manifest.items():
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Invalid archive path')
            if hashlib.sha256(z.read(f'{model}/{name}')).hexdigest() != expected:
                raise ValueError(f'Original model checksum mismatch: {model}/{name}')
        runtime = ['run.py','solver.py','第一问.py','config.json','VERSION.json','requirements.txt','LICENSE']
        directory.mkdir(parents=True, exist_ok=True)
        hashes = {}
        for name in runtime:
            data = z.read(f'{model}/{name}')
            dest = directory/name
            if dest.exists() and dest.read_bytes() != data:
                raise ValueError(f'Cached baseline changed: {dest}. Select an intact archive/cache.')
            if not dest.exists():
                dest.write_bytes(data)
            hashes[name] = hashlib.sha256(data).hexdigest()
    return directory, dict(archive_sha256=archive_hash, model_files_sha256=hashes,
                           manifest_files_verified=len(manifest))


def _verify_unchanged(directory, hashes):
    for name, digest in hashes.items():
        if hashlib.sha256((directory/name).read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'Baseline source changed during execution: {name}')


def run_baseline(config: ScenarioConfig, archive, model, output, progress=None, cancelled=None):
    report = progress or (lambda value: None)
    cancelled = cancelled or (lambda: False)
    report(dict(phase='校验原模型文件',action_count=0,virtual_time_s=0))
    directory, provenance = prepare_model(archive, model)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError(f'Output already exists: {output}')
    # Reserve run directory before starting the original journal in its own subdirectory.
    output.mkdir(parents=True)
    journal = output/'baseline-original'
    world = World(config)
    localize = load_q1(directory/'第一问.py')
    metadata = dict(schema_version=1, **asdict(config), strategy=MODEL_LABELS[model],
                    strategy_version='baseline-v1.0', baseline_model=model, **provenance,
                    q1_sha256=provenance['model_files_sha256']['第一问.py'])
    env = os.environ.copy()
    cache = CACHE/'runtime-cache'; cache.mkdir(parents=True,exist_ok=True)
    env.update(PYTHONDONTWRITEBYTECODE='1', NUMBA_CACHE_DIR=str(cache/'numba'),
               PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    messages = queue.Queue()
    histories, summary, failure = {}, None, None
    log = (output/'worker.log').open('w',encoding='utf-8')
    proc = subprocess.Popen([sys.executable, '-m', 'enhanced.baseline_worker', str(directory), str(journal)],
                            cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=log, text=True, encoding='utf-8', bufsize=1)
    def read_messages():
        try:
            for line in proc.stdout:
                messages.put(json.loads(line))
        except Exception as exc:
            messages.put(dict(kind='error',error=str(exc)))
        finally:
            messages.put(dict(kind='eof'))
    reader = threading.Thread(target=read_messages, daemon=True); reader.start()
    phase, actions, last_report = '加载原模型',0,0.
    started = time.monotonic()
    try:
        while True:
            if cancelled():
                metadata['completion']='cancelled'; break
            if time.monotonic()-started > 1500:
                failure='原模型运行超过 25 分钟上限'; break
            try:
                message=messages.get(timeout=.2)
            except queue.Empty:
                if time.monotonic()-last_report > 1:
                    report(dict(phase=phase,action_count=actions,virtual_time_s=world.t))
                    last_report=time.monotonic()
                continue
            kind=message['kind']
            if kind=='phase':
                phase=message['phase']
            elif kind=='request':
                path,payload=message['path'],message['payload']
                response=world.request(path,payload)
                if path in ('/measure','/clear') and response.get('accepted'):
                    actions+=1
                if response.get('measure_result')=='direction':
                    c=payload['channel'];p=payload['position']
                    records=histories.setdefault(c,[])
                    records.append((p['x'],p['y'],response['svd_deg']))
                    if len(records)>=2:
                        world.emit('LocalizationUpdate',**localization_data(localize(records),c,records))
                proc.stdin.write(json.dumps(response,ensure_ascii=False,allow_nan=False)+'\n')
                proc.stdin.flush()
                phase='原模型规划下一步（计算耗时不计入虚拟时间）'
                report(dict(phase=phase,action_count=actions,virtual_time_s=world.t))
            elif kind=='result':
                summary=message['result'];failure=message.get('error')
                metadata['unresolved_channels']=summary.get('unresolved_channels',[])
                metadata['completion']=summary['completion'];break
            elif kind=='error':
                failure=message['error'];break
            elif kind=='eof':
                failure='模型进程提前退出，请查看 worker.log';break
    except Exception as exc:
        failure=f'{type(exc).__name__}: {exc}'
    finally:
        if proc.poll() is None:
            if summary is not None:
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.terminate()
            else:
                proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill();proc.wait()
        reader.join(timeout=2)
        proc.stdin.close();proc.stdout.close();log.close()
        _verify_unchanged(directory,provenance['model_files_sha256'])
    metadata['model_files_unchanged']=True
    metadata['wall_runtime_s']=time.monotonic()-started
    if failure: metadata.update(completion='error',error=failure)
    if not world.events or world.events[-1]['type']!='MissionEnd':
        world.emit('MissionEnd',reason=metadata.get('completion','stopped'))
    if summary is not None:
        # Independent reconciliation, not policy input.
        from .replay import metrics
        actual=metrics(world.events,len(world.sources))
        pairs={'virtual_time_s':'virtual_time_s','movement_m':'distance_m','measures':'measure_count',
               'switches':'switch_count','successes':'cleared'}
        differences={k:abs(summary[k]-actual[v]) for k,v in pairs.items()}
        metadata['metrics_reconciled']=all(v<1e-6 for v in differences.values())
        (output/'reconciliation.json').write_text(json.dumps(differences,indent=2),encoding='utf-8')
        if not metadata['metrics_reconciled']:
            failure='原模型统计与模拟器事件不一致';metadata.update(completion='error',error=failure)
    # save_run reserves directories by default; this one was already reserved above.
    save_run(world,output,metadata,existing=True)
    if failure:
        raise RuntimeError(f'{failure}\n日志已保存：{output}')
    return dict(events=world.events,sources=world.sources,metadata=metadata,directory=str(output))
