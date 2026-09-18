import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
