"""Sequential REST mock; default 2027 leaves official 2026 untouched."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from .world import World, ScenarioConfig
from .runner import save_run
from dataclasses import asdict


def make_server(config=ScenarioConfig(), port=2027, output=None):
    world = World(config)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def send_json(self, status, data):
            body = json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            self.send_json(200 if self.path in ('/', '/ping', '/status') else 404, {'service':'EnhancedMock', 'online':True})
        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024*1024: raise ValueError('invalid_body_size')
                payload = json.loads(self.rfile.read(size))
                result = world.request(self.path, payload)
            except (ValueError, TypeError) as exc:
                result = dict(accepted=False, error=str(exc))
            if self.path == '/exit' and result.get('accepted') and output and not getattr(self.server, 'saved', False):
                save_run(world, output, dict(schema_version=1, **asdict(config), strategy='external-client', strategy_version='unknown'))
                self.server.saved = True
            status = 200 if result.get('accepted') else 403 if 'session_not_active' in result.get('error','') else 400
            self.send_json(status, result)
    return HTTPServer(('127.0.0.1', port), Handler)
