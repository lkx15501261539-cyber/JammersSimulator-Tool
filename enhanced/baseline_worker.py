"""Process bridge to an unchanged baseline package. No world truth enters here."""
import importlib.util
import json
from pathlib import Path
import sys
import traceback


def main():
    model_dir, output = map(Path, sys.argv[1:3])
    wire_out = sys.stdout
    observer = None
    sys.stdout = sys.stderr  # Third-party prints must never corrupt the JSON pipe.
    def send(message):
        wire_out.write(json.dumps(message, ensure_ascii=False, allow_nan=False)+'\n')
        wire_out.flush()
    class PipeBackend:
        def exchange(self, path, payload):
            if observer is not None:
                observer.requested(path, payload)
            send(dict(kind='request', path=path, payload=payload))
            line = sys.stdin.readline()
            if not line:
                raise ConnectionError('Simulation bridge closed')
            return json.loads(line)
    try:
        sys.path.insert(0, str(model_dir))
        spec = importlib.util.spec_from_file_location('baseline_original_run', model_dir/'run.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        config = json.loads((model_dir/'config.json').read_text(encoding='utf-8'))
        send(dict(kind='phase', phase='预热原模型计算模块'))
        module.warmup()
        send(dict(kind='phase', phase='执行原模型路线与测点规划'))
        from .strategy_observer import observe_controller
        with observe_controller(module, lambda data: send(dict(kind='strategy_state', data=data))) as observer:
            result, error = module.execute(config, output, PipeBackend(), robot_id='BASELINE-SIM', live=False)
        send(dict(kind='result', result=module.jsonable(result), error=error))
    except Exception:
        send(dict(kind='error', error=traceback.format_exc()))
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
