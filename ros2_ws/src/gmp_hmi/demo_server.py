#!/usr/bin/env python3
"""ROS·Flask 없이 화면 확인. 실제 API/장치에 연결하지 않는 데모 전용 서버."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class DemoHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split('?')[0] in ('/', '/demo'):
            html = (ROOT / 'templates/index.html').read_text(encoding='utf-8')
            html = html.replace('{{ hmi_config | tojson }}', '{"demo":true,"recipes":["recipe-01","recipe-02","recipe-03"]}')
            body = html.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5001)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), partial(DemoHandler, directory=str(ROOT)))
    print(f'데모 화면: http://127.0.0.1:{args.port} (실제 장치 미연결)', flush=True)
    server.serve_forever()
