"""Deterministic local browser tasks; oracle requires a runner-only bearer token."""
import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlsplit

NOTES = 'First line: "quoted" and \'single\'\nLiteral ${HOME}, `tick`, and \\path\nUnicode: café → 海\n\nFinal line.'
RECORDS = [{'id': f'R{i:02}', 'amount': amount, 'region': region}
           for i, (amount, region) in enumerate([(12, 'east'), (35, 'west'), (18, 'east'),
                                                (42, 'west'), (27, 'east'), (31, 'west'),
                                                (56, 'east'), (25, 'west')], 1)]
NOISE = '<section aria-label="Static archive">' + ''.join(
    f'<p>Archive note {i:03}: Routine harbor information; retained for reference, no action required.</p>'
    for i in range(80)) + '</section>'


def make_server(port, token):
    state = {'submissions': [], 'submit_attempts': 0}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value, status=200, content_type='application/json'):
            body = (json.dumps(value) if content_type == 'application/json' else value).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == '/oracle':
                if self.headers.get('Authorization') != 'Bearer ' + token:
                    return self.reply({'error': 'runner only'}, 403)
                with lock:
                    return self.reply({**state, 'submission_count': len(state['submissions']),
                                       'expected': {'heading': 'Harbor Operations', 'link': '/dispatch',
                                                    'notes': NOTES, 'total': 246, 'east_total': 113,
                                                    'maximum_id': 'R07', 'maximum_amount': 56,
                                                    'record_count': 8}})
            if url.path == '/records-data':
                page = int(parse_qs(url.query).get('page', ['1'])[0])
                return self.reply({'records': RECORDS[(page - 1) * 3:page * 3], 'page': page})
            if url.path == '/':
                content = '<h1>Harbor Operations</h1><a href="/dispatch">Dispatch desk</a><p>Daily code: HARBOR-27</p><a href="/form">Handoff form</a> <a href="/records">Records</a>'
            elif url.path == '/dispatch':
                content = '<h1>Dispatch desk</h1><p>Operating window: 09:30–17:00 UTC</p>'
            elif url.path == '/form':
                content = '''<h1>Handoff request</h1><p>Copy the following text exactly into Notes:</p><pre id="source-notes">''' + html.escape(NOTES) + '''</pre>
<p>Requester: Mira Chen. Choose handoff; use the daily code from the home page. Request ID: handoff-001.</p>
<form id="request"><label>Requester <input name="name" required></label>
<label>Request type <select name="kind" onchange="document.getElementById('conditional').hidden=this.value!=='handoff'"><option value="">Choose</option><option value="inquiry">Inquiry</option><option value="handoff">Handoff</option></select></label>
<div id="conditional" hidden><label>Daily code <input name="code"></label><label>Notes <textarea name="notes" rows="7"></textarea></label></div>
<label>Request ID <input name="request_id" required></label><button>Submit request</button></form><p id="result" role="status"></p>
<script>document.getElementById('request').onsubmit=async e=>{e.preventDefault();const r=await fetch('/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});document.getElementById('result').textContent=JSON.stringify(await r.json());};</script>'''
            elif url.path == '/records':
                page = int(parse_qs(url.query).get('page', ['1'])[0])
                rows = RECORDS[(page - 1) * 3:page * 3]
                content = f'<h1>Records page {page}</h1><table><tr><th>ID</th><th>Amount</th><th>Region</th></tr>' + ''.join(
                    f'<tr><td>{r["id"]}</td><td>{r["amount"]}</td><td>{r["region"]}</td></tr>' for r in rows) + '</table>'
                if page < 3:
                    content += f'<a href="/records?page={page + 1}">Next page</a>'
            else:
                return self.reply({'error': 'not found'}, 404)
            self.reply('<!doctype html><html><head><title>Harbor benchmark</title></head><body><nav><a href="/">Home</a></nav>' + content + NOISE + '</body></html>', content_type='text/html')

        def do_POST(self):
            if self.path != '/submit':
                return self.reply({'error': 'not found'}, 404)
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            with lock:
                state['submit_attempts'] += 1
                existing = next((s for s in state['submissions'] if s.get('request_id') == payload.get('request_id')), None)
                if existing is not None and existing != payload:
                    return self.reply({'error': 'idempotency conflict'}, 409)
                if existing is None:
                    state['submissions'].append(payload)
                self.reply({'receipt': 'accepted-' + payload.get('request_id', ''), 'stored': existing or payload})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--info', required=True, help='Runner-only connection JSON under /tmp')
    args = parser.parse_args()
    info = Path(args.info).resolve()
    if not info.is_relative_to('/tmp'):
        parser.error('--info must be under /tmp')
    token = secrets.token_urlsafe(32)
    server = make_server(args.port, token)
    info.write_text(json.dumps({'url': f'http://127.0.0.1:{server.server_port}', 'token': token}))
    info.chmod(0o600)
    print(json.dumps({'url': f'http://127.0.0.1:{server.server_port}'}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
