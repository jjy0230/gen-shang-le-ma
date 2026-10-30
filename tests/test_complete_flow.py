import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import *


def cmd(c, b, h, action, data=None, key=None):
    return c.post(
        b + "/commands",
        headers=h,
        json={
            "action": action,
            "data": data or {},
            "request_id": key or str(uuid.uuid4()),
        },
    )


def role(c, b, h, kind):
    r = cmd(c, b, h, "role.create", {"role": kind, "name": "测试 " + kind})
    assert r.status_code == 200, r.text
    return r.json(), {"Authorization": r.json()["token"]}


def test_idempotency_and_stale_version(env):
    _, c, b, h, join, step = env
    p = join()
    s = step()
    key = str(uuid.uuid4())
    payload = {"step": s, "state": "done", "version": 0}
    assert cmd(c, b, p, "status", payload, key).status_code == 200
    assert cmd(c, b, p, "status", payload, key).status_code == 200
    assert c.get(b, headers=p).json()["steps"][0]["version"] == 1
    assert (
        cmd(c, b, p, "status", {"step": s, "state": "stuck", "version": 0}).status_code
        == 409
    )
    assert (
        cmd(
            c, b, p, "status", {"step": s, "state": "working", "version": 1}, key
        ).status_code
        == 409
    )
    assert cmd(c, b, h, "step.add", {"title": "不应重复"}, key).status_code == 200
    assert cmd(c, b, h, "step.add", {"title": "不应重复"}, key).status_code == 200
    assert len(c.get(b, headers=h).json()["steps"]) == 2


def test_duplicate_agree_cannot_undo_completion(env):
    _, c, b, h, join, step = env
    p = join()
    s = step()
    iid = cmd(c, b, p, "issue.add", {"step": s, "title": "下载失败"}).json()["id"]
    c.post(b + "/status", headers=p, json={"step": s, "state": "done"})
    assert cmd(c, b, p, "issue.agree", {"id": iid}).status_code == 200
    assert c.get(b, headers=p).json()["steps"][0]["mine"] == "done"


def test_answer_recovery_and_freeze(env):
    _, c, b, h, join, step = env
    p = join()
    s1 = step()
    iid = cmd(c, b, p, "issue.add", {"step": s1, "title": "下载失败"}).json()["id"]
    assert (
        cmd(
            c,
            b,
            h,
            "issue.answer",
            {
                "id": iid,
                "body": "使用备用下载地址",
                "link": "https://www.python.org/",
                "public": False,
            },
        ).status_code
        == 200
    )
    s2 = step("下一步")
    before = c.get(b, headers=h).json()["steps"][0]["counts"]
    data = c.get(b, headers=p).json()
    i = data["steps"][0]["issues"][0]
    assert i["my_active"] and data["tails"][0]["recovered"] == 0
    assert (
        cmd(
            c, b, p, "issue.recover", {"id": iid, "version": i["member_version"]}
        ).status_code
        == 200
    )
    assert cmd(c, b, p, "catchup", {"step": s1}).status_code == 200
    after = c.get(b, headers=h).json()
    assert after["steps"][0]["counts"] == before
    assert after["steps"][0]["issues"][0]["recovered"] == 1
    assert after["steps"][1]["counts"]["waiting"] == 1
    assert after["tails"][0]["recovered"] == 1
    assert (
        cmd(
            c, b, p, "issue.still", {"id": iid, "version": i["member_version"]}
        ).status_code
        == 409
    )
    assert (
        cmd(
            c, b, p, "issue.still", {"id": iid, "version": i["member_version"] + 1}
        ).status_code
        == 200
    )
    assert c.get(b, headers=p).json()["steps"][1]["mine"] == "waiting"


def test_tail_without_reason_survives_advance(env):
    _, c, b, h, join, step = env
    p = join()
    s = step()
    c.post(b + "/status", headers=p, json={"step": s, "state": "stuck"})
    step("新步骤")
    data = c.get(b, headers=p).json()
    assert len(data["tails"]) == 1 and data["tails"][0]["step"] == s
    assert "name" not in data["tails"][0]


def test_screen_is_server_filtered_and_revocable(env):
    _, c, b, h, join, step = env
    p = join("绝不能投屏的昵称")
    s = step()
    iid = cmd(c, b, p, "issue.add", {"step": s, "title": "私密原始错误文本"}).json()[
        "id"
    ]
    cmd(c, b, h, "issue.answer", {"id": iid, "body": "不公开的答复", "public": False})
    cmd(
        c, b, h, "issue.answer", {"id": iid, "body": "所有人可看的说明", "public": True}
    )
    rr, screen = role(c, b, h, "screen")
    view = c.get(b, headers=screen)
    assert view.status_code == 200
    assert "所有人可看的说明" in view.text
    for forbidden in (
        "绝不能投屏的昵称",
        "私密原始错误文本",
        "不公开的答复",
        h["Authorization"],
        p["Authorization"],
        '"members"',
        '"tails"',
        '"roles"',
        '"me"',
    ):
        assert forbidden not in view.text
    for action, data in [
        ("step.publish", {"title": "越权"}),
        ("issue.answer", {"id": iid, "body": "越权"}),
        ("room.delete", {}),
    ]:
        assert cmd(c, b, screen, action, data).status_code == 403
    assert c.get(b + "/export", headers=screen).status_code == 403
    cmd(c, b, h, "role.revoke", {"id": rr["id"]})
    assert c.get(b, headers=screen).status_code == 403


def test_helper_claim_and_permission(env):
    _, c, b, h, join, step = env
    p = join()
    s = step()
    iid = cmd(c, b, p, "issue.add", {"step": s, "title": "错误"}).json()["id"]
    rr, helper = role(c, b, h, "helper")
    rr2, other = role(c, b, h, "helper")
    i = c.get(b, headers=helper).json()["steps"][0]["issues"][0]
    assert (
        cmd(
            c, b, helper, "issue.claim", {"id": iid, "version": i["version"]}
        ).status_code
        == 200
    )
    assert (
        cmd(
            c, b, other, "issue.claim", {"id": iid, "version": i["version"]}
        ).status_code
        == 409
    )
    assert (
        cmd(
            c, b, helper, "issue.answer", {"id": iid, "body": "请重新打开终端"}
        ).status_code
        == 200
    )
    for action, data in [
        ("room.delete", {}),
        ("room.close", {}),
        ("step.publish", {"title": "越权"}),
        ("role.create", {"role": "host"}),
        ("status", {"step": s, "state": "done", "version": 0}),
    ]:
        assert cmd(c, b, helper, action, data).status_code == 403
    cmd(c, b, h, "role.revoke", {"id": rr["id"]})
    assert c.get(b, headers=helper).status_code == 403
    assert c.get(b, headers=h).json()["steps"][0]["issues"][0]["assignee"] == ""


def test_merge_deduplicates_and_keeps_sources(env):
    _, c, b, h, join, step = env
    p = join()
    q = join()
    s = step()
    one = cmd(c, b, p, "issue.add", {"step": s, "title": "下载失败"}).json()["id"]
    two = cmd(c, b, p, "issue.add", {"step": s, "title": "无法下载"}).json()["id"]
    cmd(c, b, q, "issue.agree", {"id": two})
    cmd(c, b, h, "issue.answer", {"id": two, "body": "备用地址"})
    assert cmd(c, b, h, "issue.merge", {"id": two, "target": one}).status_code == 200
    data = c.get(b, headers=h).json()["steps"][0]["issues"]
    assert len(data) == 1 and data[0]["count"] == 2 and data[0]["affected"] == 2
    assert (
        data[0]["sources"] == ["无法下载"]
        and data[0]["answers"][0]["body"] == "备用地址"
    )
    assert cmd(c, b, p, "issue.agree", {"id": two}).status_code == 409
    assert cmd(c, b, h, "issue.close", {"id": one}).status_code == 409


def test_template_roundtrip_and_room_copy_privacy(env):
    _, c, b, h, join, step = env
    join("不复制的名字")
    template = {
        "format": "workshop-pulse/v1",
        "steps": [
            {
                "title": "一",
                "criteria": "标准",
                "reasons": ["原因"],
                "tips": "有效处理办法",
            },
            {"title": "二", "link": "https://www.python.org/"},
        ],
    }
    assert cmd(c, b, h, "template.import", {"template": template}).status_code == 200
    state = c.get(b, headers=h).json()
    ids = [s["id"] for s in state["steps"]]
    assert all(s["phase"] == "draft" for s in state["steps"])
    assert cmd(c, b, h, "step.reorder", {"ids": ids[::-1]}).status_code == 200
    exported = c.get(b + "/template", headers=h).json()
    assert [s["title"] for s in exported["steps"]] == ["二", "一"]
    assert exported["steps"][1]["tips"] == "有效处理办法"
    copied = cmd(c, b, h, "room.copy").json()
    new = "/api/rooms/" + copied["code"]
    data = c.get(new, headers={"Authorization": copied["token"]}).json()
    assert data["total"] == 0 and "不复制的名字" not in str(data)
    assert all(s["phase"] == "draft" and not s["issues"] for s in data["steps"])
    assert cmd(c, b, h, "step.start", {"id": ids[0]}).status_code == 200
    assert c.get(b, headers=h).json()["current"] == ids[0]
    assert (
        cmd(c, b, h, "step.edit", {"id": ids[0], "title": "不能改"}).status_code == 409
    )


def test_atomic_template_validation(env):
    _, c, b, h, join, step = env
    t = {
        "format": "workshop-pulse/v1",
        "steps": [{"title": "正常"}, {"title": "错误", "link": "javascript:alert(1)"}],
    }
    assert cmd(c, b, h, "template.import", {"template": t}).status_code == 400
    assert c.get(b, headers=h).json()["steps"] == []


def test_departure_and_expected_denominator(env):
    _, c, b, h, join, step = env
    p = join()
    q = join()
    s = step()
    cmd(c, b, h, "room.settings", {"expected": 10, "announcement": "所有人可见"})
    assert c.get(b, headers=h).json()["missing"] == 8
    step("第二步")
    before = c.get(b, headers=h).json()["steps"][0]["total"]
    pid = c.get(b, headers=p).json()["me"]["id"]
    assert cmd(c, b, h, "person.remove", {"id": pid}).status_code == 200
    data = c.get(b, headers=h).json()
    assert data["total"] == 1 and data["joined_total"] == 2
    assert data["steps"][0]["total"] == before == 2
    assert data["steps"][1]["total"] == 1
    assert c.get(b, headers=p).status_code == 403
    assert cmd(c, b, q, "person.leave").status_code == 200
    assert c.get(b, headers=q).status_code == 403


def test_identity_recovery_and_seat_conflict(env):
    _, c, b, h, join, step = env
    assert (
        c.post(b + "/recover", json={"token": h["Authorization"]}).json()["role"]
        == "host"
    )
    p = c.post(b + "/join", json={"seat": "A03"})
    assert p.status_code == 200
    assert c.post(b + "/join", json={"name": "重复", "seat": "A03"}).status_code == 409
    assert c.post(b + "/join", json={}).status_code == 200
    assert c.post(b + "/recover", json={"token": "x" * 32}).status_code == 403


def test_same_nat_60_people_heartbeats_do_not_block_status(env):
    app, c, b, h, join, step = env
    people = [join(str(n)) for n in range(60)]
    s = step()
    app.buckets.clear()
    for _ in range(5):
        for p in people:
            assert c.post(b + "/heartbeat", headers=p).status_code == 200
    with ThreadPoolExecutor(max_workers=12) as pool:
        replies = list(
            pool.map(
                lambda p: cmd(
                    c, b, p, "status", {"step": s, "state": "done", "version": 0}
                ).status_code,
                people,
            )
        )
    assert replies == [200] * 60
    assert c.get(b, headers=h).json()["steps"][0]["counts"]["done"] == 60


def test_export_csv_sanitizes_formula_and_no_credentials(env):
    _, c, b, h, join, step = env
    join()
    step("=DANGEROUS()")
    r = c.get(b + "/export?format=csv", headers=h)
    assert r.status_code == 200 and "'=DANGEROUS()" in r.text
    assert h["Authorization"] not in c.get(b + "/export", headers=h).text
    assert c.get(b + "/report", headers=h).json()["steps"][0]["total"] == 1
    assert c.get("/examples/python-setup.json").status_code == 200


def test_closed_room_freezes_current_and_rejects_writes(env):
    _, c, b, h, join, step = env
    p = join()
    s = step()
    assert cmd(c, b, h, "room.close").status_code == 200
    data = c.get(b, headers=h).json()
    assert data["current"] is None and data["steps"][0]["phase"] == "frozen"
    assert (
        cmd(c, b, p, "status", {"step": s, "state": "done", "version": 0}).status_code
        == 409
    )
    assert cmd(c, b, h, "room.copy").status_code == 200


def test_legacy_database_migration_and_backup(tmp_path):
    from storage import Database
    from workshop import Workshop

    path = tmp_path / "legacy.sqlite3"
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE rooms(code TEXT PRIMARY KEY,title TEXT,token TEXT,created REAL,closed INTEGER DEFAULT 0);
    CREATE TABLE people(id TEXT PRIMARY KEY,room TEXT,name TEXT,token TEXT,seen REAL,joined REAL);
    CREATE TABLE steps(id TEXT PRIMARY KEY,room TEXT,title TEXT,created REAL);
    CREATE TABLE statuses(step TEXT,person TEXT,state TEXT,updated REAL,PRIMARY KEY(step,person));
    CREATE TABLE issues(id TEXT PRIMARY KEY,step TEXT,title TEXT,resolved INTEGER DEFAULT 0);
    CREATE TABLE votes(issue TEXT,person TEXT,PRIMARY KEY(issue,person));
    INSERT INTO rooms VALUES('ABC234','历史房间','host-secret',1,0);
    INSERT INTO people VALUES('p','ABC234','历史成员','person-secret',2,1);
    INSERT INTO steps VALUES('s1','ABC234','第一步',2);
    INSERT INTO steps VALUES('s2','ABC234','第二步',3);
    INSERT INTO statuses VALUES('s1','p','done',2);
    """)
    c.close()
    db = Database(path)
    state = Workshop(db).snapshot("ABC234", "host-secret")
    assert state["total"] == 1 and state["steps"][0]["counts"]["done"] == 1
    assert (
        state["current"] == "s2" and (tmp_path / "legacy.sqlite3.pre-v3.bak").exists()
    )
    backup = tmp_path / "copy.sqlite3"
    db.backup(backup)
    assert Workshop(Database(backup)).snapshot("ABC234", "host-secret")["total"] == 1
