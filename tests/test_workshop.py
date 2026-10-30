import asyncio
import importlib
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.sqlite3"))
    import app

    importlib.reload(app)
    app.buckets.clear()
    client = TestClient(app.app)
    r = client.post("/api/rooms", json={"title": "Python 带练"}).json()
    base = "/api/rooms/" + r["code"]
    h = {"Authorization": r["token"]}

    def join(name="参与者"):
        return {
            "Authorization": client.post(base + "/join", json={"name": name}).json()[
                "token"
            ]
        }

    def step(title="打开首页"):
        assert (
            client.post(base + "/steps", headers=h, json={"title": title}).status_code
            == 200
        )
        return client.get(base, headers=h).json()["steps"][-1]["id"]

    return app, client, base, h, join, step


def test_role_privacy(env):
    _, c, b, h, join, step = env
    p = join("私密昵称")
    step()
    assert c.get(b).status_code == 403
    assert c.post(b + "/steps", headers=p, json={"title": "越权"}).status_code == 403
    data = c.get(b, headers=p).json()
    assert "私密昵称" not in str(data) and "token" not in str(data)
    assert "people" not in data["steps"][0]
    assert c.get(b + "/export", headers=p).status_code == 403


def test_steps_and_late_joiners(env):
    _, c, b, h, join, step = env
    p = join()
    s1 = step()
    c.post(b + "/status", headers=p, json={"step": s1, "state": "done"})
    s2 = step("下一步")
    join("迟到者")
    data = c.get(b, headers=h).json()
    assert data["steps"][0]["counts"] == dict(done=1, working=0, stuck=0, waiting=0)
    assert data["steps"][1]["counts"]["waiting"] == 2
    assert (
        c.post(
            b + "/status", headers=p, json={"step": s1, "state": "stuck"}
        ).status_code
        == 409
    )
    assert (
        c.post(
            b + "/status", headers=p, json={"step": s2, "state": "nonsense"}
        ).status_code
        == 400
    )


def test_issue_dedup_and_confirmation(env):
    _, c, b, h, join, step = env
    p = join()
    q = join()
    s = step()
    for _ in range(2):
        c.post(b + "/issues", headers=p, json={"step": s, "title": "下载失败"})
    issue = c.get(b, headers=h).json()["steps"][0]["issues"][0]
    assert issue["count"] == 1
    for _ in range(2):
        assert (
            c.post(b + "/issues/" + issue["id"] + "/agree", headers=q).status_code
            == 200
        )
    c.post(b + "/issues/" + issue["id"] + "/resolve", headers=h)
    data = c.get(b, headers=h).json()["steps"][0]
    assert len(data["issues"]) == 1 and data["issues"][0]["count"] == 2
    assert data["counts"]["stuck"] == 2
    assert data["issues"][0]["resolved"] == 1
    c.post(b + "/status", headers=p, json={"step": s, "state": "done"})
    assert c.get(b, headers=h).json()["steps"][0]["counts"]["done"] == 1
    c.post(b + "/issues/" + issue["id"] + "/withdraw", headers=q)
    assert c.get(b, headers=h).json()["steps"][0]["issues"][0]["count"] == 1


def test_cross_room_issue_attack(env):
    _, c, b, h, join, step = env
    p = join()
    s = step()
    c.post(b + "/issues", headers=p, json={"step": s, "title": "失败"})
    iid = c.get(b, headers=h).json()["steps"][0]["issues"][0]["id"]
    other = c.post("/api/rooms", json={"title": "其他"}).json()
    assert (
        c.post(
            "/api/rooms/" + other["code"] + "/issues/" + iid + "/resolve",
            headers={"Authorization": other["token"]},
        ).status_code
        == 404
    )


def test_close_export_delete(env):
    _, c, b, h, join, step = env
    p = join()
    s = step()
    c.post(b + "/close", headers=h)
    assert (
        c.post(b + "/status", headers=p, json={"step": s, "state": "done"}).status_code
        == 409
    )
    assert c.post(b + "/join", json={"name": "迟到"}).status_code == 409
    assert c.get(b + "/export", headers=h).json()["closed"]
    assert c.delete(b, headers=p).status_code == 403
    assert c.delete(b, headers=h).status_code == 200
    assert c.get(b, headers=h).status_code == 404


def test_persistence(env):
    module, c, b, h, join, step = env
    join()
    step()
    importlib.reload(module)
    fresh = TestClient(module.app)
    assert fresh.get(b, headers=h).json()["total"] == 1


def test_ten_participants_concurrent(env):
    _, c, b, h, join, step = env
    people = [join(str(i)) for i in range(10)]
    s = step()

    def submit(p):
        return c.post(
            b + "/status", headers=p, json={"step": s, "state": "done"}
        ).status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        assert list(pool.map(submit, people)) == [200] * 10
    assert c.get(b, headers=h).json()["steps"][0]["counts"]["done"] == 10


def test_input_qr_and_static(env):
    _, c, b, h, join, step = env
    assert c.post("/api/rooms", json={"title": " "}).status_code == 400
    assert c.post(b + "/join", json={"name": "x" * 25}).status_code == 422
    assert (
        c.get("/qr?code=" + b.rsplit("/", 1)[1])
        .headers["content-type"]
        .startswith("image/svg+xml")
    )
    assert c.get("/qr?code=bad").status_code == 400
    r = c.get("/")
    assert (
        r.status_code == 200
        and "frame-ancestors" in r.headers["content-security-policy"]
    )


def test_sse_frame(env):
    module, _, b, h, join, step = env
    step()

    class Request:
        async def is_disconnected(self):
            return False

    async def first():
        result = await module.events(b.rsplit("/", 1)[1], Request(), h["Authorization"])
        stream = result.body_iterator
        frame = await anext(stream)
        await stream.aclose()
        return frame

    frame = asyncio.run(first())
    assert frame.startswith("data: ") and frame.endswith("\n\n")


def test_heartbeat_and_stale_issue(env):
    _, c, b, h, join, step = env
    p = join()
    s = step()
    assert c.post(b + "/heartbeat", headers=p).status_code == 200
    c.post(b + "/issues", headers=p, json={"step": s, "title": "错误"})
    iid = c.get(b, headers=h).json()["steps"][0]["issues"][0]["id"]
    step("下一步")
    assert c.post(b + "/issues/" + iid + "/agree", headers=p).status_code == 409
    assert (
        c.post(
            b + "/issues", headers=p, json={"step": s, "title": "旧页面"}
        ).status_code
        == 409
    )
