"""Concurrent publish and late feedback must preserve step boundaries."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from test_complete_flow import cmd


def test_same_draft_concurrent_publish_and_late_feedback(env):
    _, client, base, host, join, step = env
    person = join()
    first = step()
    assert client.post(base + '/status', headers=person,
                       json={'step': first, 'state': 'working'}).status_code == 200
    draft = cmd(client, base, host, 'step.add', {'title': 'Next checkpoint'}).json()['id']
    barrier = Barrier(2)

    def publish():
        barrier.wait(timeout=10)
        return cmd(client, base, host, 'step.start', {'id': draft}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: publish(), range(2)))
    assert sorted(results) == [200, 409]
    before = client.get(base, headers=host).json()
    assert before['current'] == draft
    assert len([s for s in before['steps'] if s['phase'] == 'active']) == 1
    frozen = next(s for s in before['steps'] if s['id'] == first)
    assert frozen['counts']['working'] == 1
    late = client.post(base + '/status', headers=person,
                       json={'step': first, 'state': 'done', 'version': 1})
    assert late.status_code == 409
    after = client.get(base, headers=host).json()
    assert next(s for s in after['steps'] if s['id'] == first)['counts'] == frozen['counts']
    assert next(s for s in after['steps'] if s['id'] == draft)['counts']['waiting'] == 1
