"""Process bridge to an unchanged baseline package. No world truth enters here."""
import importlib.util
import faulthandler
import json
from pathlib import Path
import sys
import time
import traceback


def execute_model(module, config, output, backend, robot_id='BASELINE-SIM'):
    """Use each package's public controller; v2 has no injectable run_case backend.

    The v2 run_case entry constructs its own OfflineBackend, so the bridge
    instantiates precisely the same JournalClient and Controller with the pipe.
    In particular baseline_runtime.execute must not be used: it runs v1 policy.
    """
    if config.get('version') != 'baseline-v2.0':
        return module.execute(config, output, backend, robot_id=robot_id, live=False)
    client = module.JournalClient(backend, output, robot_id, config, live=False)
    controller = module.Controller(client, config)
    started = time.monotonic()
    error = None
    try:
        result = controller.run()
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        result = controller.summary()
        result['error'] = error
    finally:
        module.save_json(Path(output)/'decisions.json', controller.events)
        client.close()
    result['wall_runtime_s'] = time.monotonic()-started
    module.save_json(Path(output)/'summary.json', result)
    return result, error


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
        # A stalled dependency import used to leave an empty worker.log and
        # an unexplained zero-action spinner. Capture a stack if preparation
        # takes unusually long; this does not interrupt or change the model.
        faulthandler.dump_traceback_later(60, repeat=True, file=sys.stderr)
        sys.path.insert(0, str(model_dir))
        spec = importlib.util.spec_from_file_location('baseline_original_run', model_dir/'run.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        config = json.loads((model_dir/'config.json').read_text(encoding='utf-8'))
        send(dict(kind='phase', phase='预热原模型计算模块'))
        module.warmup()
        if config.get('version') == 'baseline-v2.0':
            # Match v2 main: certify the complete response grid before /enter.
            send(dict(kind='phase', phase='认证七点搜索骨架与几何'))
            module.prepare_geometry(config)
        faulthandler.cancel_dump_traceback_later()
        send(dict(kind='phase', phase='执行原模型路线与测点规划'))
        from .strategy_observer import observe_controller
        with observe_controller(module, lambda data: send(dict(kind='strategy_state', data=data))) as observer:
            result, error = execute_model(module, config, output, PipeBackend())
        send(dict(kind='result', result=module.jsonable(result), error=error))
    except Exception:
        send(dict(kind='error', error=traceback.format_exc()))
        return 1
    finally:
        faulthandler.cancel_dump_traceback_later()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
