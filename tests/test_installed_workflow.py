"""Opt-in acceptance test against a copied skill and built/released dependency.

Set SURF_INSTALLED_SKILL to the installed surf directory and
SURF_AGENT_DEPENDENCY to its validation wheel requirement (omit for release).
Requires Chrome and a graphical session; never uses a user's Surf profile.
"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest


@pytest.mark.skipif(not os.environ.get("SURF_INSTALLED_SKILL"), reason="installed skill acceptance is opt-in")
def test_installed_skill_across_fresh_invocations(tmp_path):
    skill = Path(os.environ["SURF_INSTALLED_SKILL"]).resolve()
    launcher = skill / "scripts" / "run.py"
    checkout = Path(__file__).resolve().parents[1]
    assert not skill.is_relative_to(checkout), "validate a copied installation, not the checkout"
    site = tmp_path / "site"
    site.mkdir()
    records = "".join(f"<p>Record {i}: stable supporting information for snapshot differences.</p>" for i in range(100))
    (site / "index.html").write_text(
        '<title>Installed Surf</title><h1>Installed workflow</h1>'
        '<label>Name <textarea id="name"></textarea></label>'
        '<button onclick="document.getElementById(\'status\').textContent=\'Saved\'">Save</button>'
        '<p id="status">Not saved</p><a href="next.html">Next</a>' + records
    )
    (site / "next.html").write_text('<title>Next page</title><h1>Destination</h1>')
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(site)))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        bridge_port = reservation.getsockname()[1]
    environment = {
        **os.environ,
        "SURF_AGENT_HOME": str(tmp_path / "surf-home"),
        "SURF_AGENT_BACKEND": "patchright",
        "SURF_AGENT_PATCHRIGHT_PORT": str(bridge_port),
        "PYTHONPATH": "",
    }
    workdir = tmp_path / "unrelated project"
    workdir.mkdir()
    # An unrelated project must not participate in launcher dependency resolution.
    (workdir / "pyproject.toml").write_text('[project]\nname="unrelated"\nversion="0.0.0"\ndependencies=["does-not-exist-surf-test"]\n')
    base_url = f"http://127.0.0.1:{server.server_port}"

    def invoke(source, *args, file=False):
        target = "-"
        if file:
            target = str(workdir / "task with spaces.py")
            Path(target).write_text(source)
        result = subprocess.run(
            [sys.executable, str(launcher), target, *args],
            input=None if file else source, capture_output=True, text=True,
            cwd=workdir, env=environment, timeout=90,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    try:
        first = invoke('''
import importlib.util, json, surf_agent, sys
from surf_agent import Thread
assert importlib.util.find_spec('surf_agent.cli') is None
t = Thread('installed-acceptance')
t.open(sys.argv[1] + '/index.html')
print(json.dumps({'module': surf_agent.__file__, 'title': t.evaluate('document.title')}))
''', base_url)
        installed = json.loads(first)
        assert not Path(installed["module"]).resolve().is_relative_to(checkout)
        assert installed["title"] == "Installed Surf"
        second = invoke('''
import io, json, re, sys
from surf_agent import Thread
assert sys.argv[2] == 'literal ${HOME} and spaces'
t = Thread('installed-acceptance')
assert t.is_open(), 'browser disappeared after the previous interpreter exited'
before = t.snapshot()
baseline = io.StringIO()
t.emit(before, sink=baseline)
before_body = before.text if before.text.endswith('\\n') else before.text + '\\n'
assert baseline.getvalue() == '--- BEGIN observation 1 ---\\n' + before_body + '--- END observation 1 ---\\n'
name = re.search(r'textbox[^\\n]*?\\[ref=([^] ]+)', before.text).group(1)
button = re.search(r'button "Save"[^\\n]*?\\[ref=([^] ]+)', before.text).group(1)
t.fill(name, 'Exact "quote" and \\nnewline')
assert t.evaluate("document.querySelector('#name').value") == 'Exact "quote" and \\nnewline'
t.click(button)
t.wait('Saved')
after = t.snapshot()
diff = io.StringIO()
t.emit(after, sink=diff)
assert len(diff.getvalue()) < len(after.text)
assert diff.getvalue().startswith('--- BEGIN observation 2 ---\\n--- observation 1\\n+++ observation 2\\n')
assert diff.getvalue().endswith('--- END observation 2 ---\\n')
full = io.StringIO()
t.emit(after, full=True, sink=full)
after_body = after.text if after.text.endswith('\\n') else after.text + '\\n'
assert full.getvalue() == '--- BEGIN observation 3 ---\\n' + after_body + '--- END observation 3 ---\\n'
t.screenshot(sys.argv[1])
print(json.dumps({'full_chars': len(after.text), 'diff_chars': len(diff.getvalue())}))
''', str(tmp_path / "shot.png"), "literal ${HOME} and spaces", file=True)
        sizes = json.loads(second)
        assert sizes["diff_chars"] < sizes["full_chars"]
        assert (tmp_path / "shot.png").stat().st_size > 0
        assert invoke('''
import sys
from surf_agent import Thread
t = Thread('installed-acceptance')
t.open(sys.argv[1] + '/next.html')
t.back()
assert t.evaluate('document.title') == 'Installed Surf'
print('back passed')
''', base_url).strip() == "back passed"
        assert invoke('''
from surf_agent import Browser, Thread
b = Browser()
assert any(row.name == 'installed-acceptance' for row in b.threads())
b.close_matching('installed-*')
assert not Thread('installed-acceptance').is_open()
print('cleanup passed')
''').strip() == "cleanup passed"

        session = {"id": None}

        def invoke_session(source, *arguments, expect=0, create=False):
            options = ["--new-session"] if create else ["--session", session["id"]]
            result = subprocess.run(
                [sys.executable, str(launcher), *options, "-", *arguments],
                input=source, capture_output=True, text=True, cwd=workdir, env=environment, timeout=90,
            )
            assert result.returncode == expect, f"{result.stdout}\n{result.stderr}"
            if create:
                reported = re.search(r"^session_id: (\S+)$", result.stdout, re.MULTILINE)
                assert reported, result.stdout
                session["id"] = reported.group(1)
            return result

        def stop_session_worker(pid):
            try:
                command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
            except OSError:
                return
            if session["id"] is not None and session["id"].encode() in command:
                os.kill(pid, signal.SIGKILL)

        first_cell = invoke_session('''
import sys
from surf_agent import Thread
t = Thread('installed-acceptance-session')
t.open(sys.argv[1] + '/index.html')
records = 100
t.emit(t.snapshot())
''', base_url, create=True)
        created = re.search(r"--- interpreter (\d+) \(cell #1, created", first_cell.stderr)
        assert created, first_cell.stderr
        assert "+++ observation" not in first_cell.stdout, "first emission must be full"

        second_cell = invoke_session('''
from surf_agent import Thread
assert records == 100, 'bindings did not survive the previous cell'
assert t.evaluate('document.title') == 'Installed Surf'
t.emit(t.snapshot())
''')
        # The handle and its baseline survived: observation 2 diffs against observation 1.
        assert second_cell.stdout.startswith(
            "--- BEGIN observation 2 ---\n--- observation 1\n+++ observation 2\n"
        ), second_cell.stdout[:200]

        worker_pid = int(created.group(1))
        os.kill(worker_pid, signal.SIGKILL)
        deadline = time.time() + 5
        while time.time() < deadline and os.path.exists(f"/proc/{worker_pid}"):
            time.sleep(0.05)
        assert not os.path.exists(f"/proc/{worker_pid}")

        # A killed interpreter takes its session id with it: the bindings are gone
        # and the id is refused rather than quietly remade.
        lost = invoke_session("print(records)\n", expect=2)
        assert "unknown session" in lost.stderr, lost.stderr

        reattached = invoke_session('''
import io
from surf_agent import Thread
t = Thread('installed-acceptance-session')
assert t.is_open(), 'the browser must survive interpreter replacement'
capture = io.StringIO()
t.emit(t.snapshot(), sink=capture)
frame = capture.getvalue()
assert frame.startswith('--- BEGIN observation 1 ---\\n'), frame[:80]
assert '+++ observation' not in frame, 'the new interpreter must emit full output first'
print('reattached')
''', create=True)
        assert reattached.stdout == "reattached\n"
        new_worker = int(re.search(r"--- interpreter (\d+) ", reattached.stderr).group(1))

        # A later cell must be able to start the bridge process itself: the
        # session worker cannot rely on the temporary environment of its creator.
        restarted = invoke_session('''
import subprocess, sys
from surf_agent import Browser
Browser().stop_bridge()
subprocess.run([sys.executable, '-c', 'import surf_agent'], check=True)
from surf_agent import Thread
t = Thread('installed-acceptance-session')
t.open(sys.argv[1] + '/index.html')
assert t.evaluate('document.title') == 'Installed Surf'
print('bridge restarted')
''', base_url)
        assert restarted.stdout == "bridge restarted\n"

        closed = invoke_session('''
from surf_agent import Thread
Thread('installed-acceptance-session').close()
print('session thread closed')
''')
        assert closed.stdout == "session thread closed\n"
        stop_session_worker(new_worker)
    finally:
        try:
            invoke("from surf_agent import Browser\nBrowser().stop_bridge()\n")
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)
