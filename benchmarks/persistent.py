"""Benchmark-only, sequential trusted-code interpreter over a private Unix socket."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import sys
import traceback


class Interpreter:
    def __init__(self):
        self.retired = []
        self.namespace = {'__name__': '__main__'}

    def reset(self):
        # Keep old handles alive: clearing variables must not trigger browser cleanup.
        self.retired.append(self.namespace)
        self.namespace = {'__name__': '__main__'}

    def execute(self, source, timeout):
        stdout, stderr = io.StringIO(), io.StringIO()
        def expired(signum, frame):
            raise TimeoutError('Execution deadline exceeded; side effects may have occurred. No retry.')
        previous = signal.signal(signal.SIGALRM, expired)
        ok = True
        try:
            signal.setitimer(signal.ITIMER_REAL, timeout)
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    exec(compile(source, '<benchmark-cell>', 'exec'), self.namespace)
                except BaseException:
                    ok = False
                    traceback.print_exc()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        return {'ok': ok, 'stdout': stdout.getvalue(), 'stderr': stderr.getvalue()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['serve', 'exec', 'reset', 'stop'])
    parser.add_argument('--session', required=True, help='Private session directory under /tmp')
    parser.add_argument('--timeout', type=float, default=30, help='Execution deadline seconds; no retries')
    args = parser.parse_args()
    directory = Path(args.session).resolve()
    if not directory.is_relative_to('/tmp') or directory == Path('/tmp'):
        parser.error('--session must be a subdirectory of /tmp')
    if not 0 < args.timeout <= 300:
        parser.error('--timeout must be > 0 and <= 300')
    path = str(directory / 'worker.sock')
    if args.action == 'serve':
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        interpreter = Interpreter()
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(path)  # Fail on existing sessions, never replace a live worker.
            server.listen(4)
            print(json.dumps({'ready': path}), flush=True)
            try:
                while True:
                    connection, _ = server.accept()
                    with connection, connection.makefile('r') as stream:
                        request = json.loads(stream.readline())
                        action = request['action']
                        if action == 'exec':
                            result = interpreter.execute(request['source'], request['timeout'])
                        elif action == 'reset':
                            interpreter.reset()
                            result = {'ok': True, 'stdout': '', 'stderr': ''}
                        elif action == 'stop':
                            result = {'ok': True, 'stdout': '', 'stderr': ''}
                        else:
                            result = {'ok': False, 'stdout': '', 'stderr': 'Unknown action'}
                        connection.sendall((json.dumps(result) + '\n').encode())
                    if action == 'stop':
                        break
            finally:
                Path(path).unlink(missing_ok=True)
    else:
        request = {'action': args.action, 'timeout': args.timeout,
                   'source': sys.stdin.read() if args.action == 'exec' else ''}
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(args.timeout + 5)
            client.connect(path)
            client.sendall((json.dumps(request) + '\n').encode())
            with client.makefile('r') as stream:
                result = json.loads(stream.readline())
        sys.stdout.write(result['stdout'])
        sys.stderr.write(result['stderr'])
        raise SystemExit(0 if result['ok'] else 1)


if __name__ == '__main__':
    main()
