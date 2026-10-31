import importlib
from pathlib import Path
from fastapi.testclient import TestClient


def test_demo_disabled_on_normal_installation(env):
    _, c, b, h, join, step = env
    assert c.post("/api/rooms/demo", json={}).status_code == 404
    assert c.get("/api/config").json()["demo_mode"] is False


def test_demo_separate_database_seed_and_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DEMO_DATABASE_PATH", str(tmp_path / "demo.sqlite3"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "production.sqlite3"))
    import app

    importlib.reload(app)
    c = TestClient(app.app)
    r = c.post("/api/rooms/demo", json={})
    assert r.status_code == 200, r.text
    data = r.json()
    b = "/api/rooms/" + data["code"]
    h = {"Authorization": data["token"]}
    view = c.get(b, headers=h).json()
    assert view["total"] == 3 and len(view["steps"]) == 2
    assert view["steps"][0]["counts"] == {
        "done": 1,
        "working": 1,
        "stuck": 1,
        "waiting": 0,
    }
    assert view["steps"][0]["issues"][0]["affected"] == 1
    assert not (tmp_path / "production.sqlite3").exists()
    assert (
        "token"
        not in c.get(b, headers={"Authorization": data["participant_token"]}).text
    )
    assert c.post("/api/rooms/demo", json={}).status_code == 200
    assert c.post("/api/rooms/demo", json={}).status_code == 200
    assert (
        c.post(
            "/api/rooms/demo", json={}, headers={"Authorization": "made-up"}
        ).status_code
        == 429
    )
