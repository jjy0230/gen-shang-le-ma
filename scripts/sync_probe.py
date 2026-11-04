"""Measure real status-submit to all SSE observers; isolated loopback only."""

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx


async def run(output):
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='pulse-sync-') as tmp:
        with open(Path(tmp) / 'server.log', 'w+', encoding='utf-8') as log:
            server = subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1',
                 '--port', str(port), '--log-level', 'error'], cwd=root,
                env={**os.environ, 'DATABASE_PATH': str(Path(tmp) / 'test.sqlite3'),
                     'DEMO_MODE': '0'}, stdout=log, stderr=log)
            try:
                async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',
                                            timeout=20, limits=httpx.Limits(max_connections=80)) as client:
                    for _ in range(100):
                        if server.poll() is not None:
                            raise RuntimeError('Native service exited before readiness')
                        try:
                            (await client.get('/health')).raise_for_status()
                            break
                        except httpx.HTTPError:
                            await asyncio.sleep(.1)
                    else:
                        raise RuntimeError('Native service readiness timeout')

                    async def post(path, body, token=''):
                        response = await client.post(path, json=body, headers={'Authorization': token})
                        response.raise_for_status()
                        return response.json()

                    room = await post('/api/rooms', {'title': 'Isolated synchronization probe'})
                    base = '/api/rooms/' + room['code']
                    people = [await post(base + '/join', {'name': f'Probe {n}'}) for n in range(30)]
                    await post(base + '/steps', {'title': 'Probe checkpoint'}, room['token'])
                    snapshot = (await client.get(base, headers={'Authorization': room['token']})).json()
                    sid = snapshot['current']
                    ready = [asyncio.Event() for _ in people]
                    received = [asyncio.Event() for _ in people]
                    measurements = []
                    expected = 0
                    submitted = 0
                    errors = []

                    async def observe(n, token):
                        try:
                            async with client.stream('GET', base + '/events',
                                                     headers={'Authorization': token}, timeout=None) as response:
                                response.raise_for_status()
                                async for line in response.aiter_lines():
                                    if not line.startswith('data: '):
                                        continue
                                    state = json.loads(line[6:])
                                    step = next(s for s in state['steps'] if s['id'] == sid)
                                    if step['counts']['done'] == 0:
                                        ready[n].set()
                                    if expected and step['counts']['done'] == expected and not received[n].is_set():
                                        measurements.append(time.perf_counter() - submitted)
                                        received[n].set()
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            errors.append(type(exc).__name__)

                    tasks = [asyncio.create_task(observe(n, p['token'])) for n, p in enumerate(people)]
                    try:
                        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in ready)), 20)
                        for number in range(1, 11):
                            for event in received:
                                event.clear()
                            expected = number
                            submitted = time.perf_counter()
                            await post(base + '/status', {'step': sid, 'state': 'done'}, people[number - 1]['token'])
                            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in received)), 10)
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                    p95 = sorted(measurements)[math.ceil(len(measurements) * .95) - 1]
                    result = {'observers': 30, 'writes': 10, 'samples': len(measurements),
                              'submit_to_observer_p95_seconds': p95,
                              'max_seconds': max(measurements), 'errors': errors,
                              'passed': len(measurements) == 300 and not errors and p95 <= 2,
                              'scope': 'Native loopback HTTP + SSE; measured from before POST to each observer reading matching completion count. Not browser rendering, real phones, or weak-network proof.'}
                    Path(output).parent.mkdir(parents=True, exist_ok=True)
                    Path(output).write_text(json.dumps(result, indent=2), encoding='utf-8')
                    print(json.dumps(result), flush=True)
                    if not result['passed']:
                        raise RuntimeError('Synchronization acceptance failed; see report')
            finally:
                server.terminate()
                server.wait(timeout=20)
                log.seek(0)
                diagnostics = log.read()
                if diagnostics:
                    print(diagnostics, file=sys.stderr)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='data/sync-probe.json')
    asyncio.run(run(parser.parse_args().output))
