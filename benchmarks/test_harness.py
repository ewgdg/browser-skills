import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_interpreter_persistence_reset_and_timeout():
    worker = load('persistent').Interpreter()
    assert worker.execute('value = 41', 1)['ok']
    assert worker.execute('print(value + 1)', 1)['stdout'] == '42\n'
    assert not worker.execute('raise ValueError("bad")', 1)['ok']
    assert worker.execute('print(value)', 1)['stdout'] == '41\n'
    assert not worker.execute('while True: pass', 0.05)['ok']
    worker.reset()
    assert not worker.execute('print(value)', 1)['ok']


def test_worker_cli_roundtrip():
    with tempfile.TemporaryDirectory(prefix='surf-benchmark-', dir='/tmp') as directory:
        command = [sys.executable, str(Path(__file__).with_name('persistent.py'))]
        environment = {**os.environ, 'SURF_AGENT_HOME': directory + '/surf-home'}
        process = subprocess.Popen(command + ['serve', '--session', directory],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        try:
            deadline = time.monotonic() + 5
            while not Path(directory, 'worker.sock').exists():
                assert process.poll() is None
                assert time.monotonic() < deadline
                time.sleep(0.02)
            def run(action, source=''):
                return subprocess.run(command + [action, '--session', directory], input=source,
                                      capture_output=True, text=True, timeout=5)
            assert run('exec', 'x = 7').returncode == 0
            assert run('exec', 'print(x * 6)').stdout == '42\n'
            assert run('reset').returncode == 0
            assert run('exec', 'print(x)').returncode == 1
            assert run('stop').returncode == 0
            assert process.wait(timeout=5) == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_fixture_oracle_and_exact_submission():
    fixture = load('fixture')
    server = fixture.make_server(0, 'secret')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        page = urllib.request.urlopen(base + '/').read().decode()
        assert 'Harbor Operations' in page
        assert 'Archive note 079' in page
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(base + '/oracle')
        assert denied.value.code == 403
        payload = {'name': 'Mira Chen', 'kind': 'handoff', 'notes': fixture.NOTES,
                   'code': 'HARBOR-27', 'request_id': 'handoff-001'}
        def post():
            request = urllib.request.Request(base + '/submit', json.dumps(payload).encode(),
                                             {'Content-Type': 'application/json'})
            return json.load(urllib.request.urlopen(request))
        assert post() == post()
        request = urllib.request.Request(base + '/oracle', headers={'Authorization': 'Bearer secret'})
        state = json.load(urllib.request.urlopen(request))
        assert state['submission_count'] == 1
        assert state['submit_attempts'] == 2
        assert state['submissions'][0] == payload
        assert state['expected']['total'] == 246
        assert json.load(urllib.request.urlopen(base + '/records-data?page=2'))['records'][0]['id'] == 'R04'
    finally:
        server.shutdown()
        server.server_close()
