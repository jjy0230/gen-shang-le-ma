from test_complete_flow import cmd


def test_handoff_is_private_and_preserves_received_help(env):
    _, c, b, h, join, step = env
    p = join("自己的昵称")
    q = join("另一个成员")
    sid = step()
    one = cmd(c, b, p, "issue.add", {"step": sid, "title": "自己的问题"}).json()["id"]
    two = cmd(c, b, q, "issue.add", {"step": sid, "title": "其他人的问题"}).json()["id"]
    cmd(c, b, h, "issue.answer", {"id": one, "body": "给你的办法"})
    cmd(c, b, h, "room.close")
    r = c.get(b + "/handoff", headers=p)
    assert r.status_code == 200, r.text
    assert "自己的问题" in r.text and "给你的办法" in r.text
    assert "其他人的问题" not in r.text and "另一个成员" not in r.text
    assert p["Authorization"] not in r.text and h["Authorization"] not in r.text
    assert r.json()["expires_at"] is not None
    assert c.get(b + "/followup", headers=p).status_code == 403
    assert c.get(b + "/handoff", headers=h).status_code == 403
    result = c.get(b + "/followup", headers=h).json()
    assert len(result["people"]) == 2


def test_handoff_expiry_and_deletion_revoke_access(env, monkeypatch):
    app, c, b, h, join, step = env
    p = join()
    step()
    cmd(c, b, h, "room.close")
    until = c.get(b, headers=h).json()["handoff_until"]
    monkeypatch.setattr(app.time, "time", lambda: until + 1)
    assert c.get(b + "/handoff", headers=p).status_code == 403
    assert c.get(b, headers=p).status_code == 403
    assert c.get(b + "/followup", headers=h).status_code == 200
    assert cmd(c, b, h, "room.delete").status_code == 200
    assert c.get(b + "/handoff", headers=p).status_code == 404


def test_quick_template_start_is_real_and_editable(env):
    _, c, b, h, join, step = env
    r = c.post("/api/rooms", json={"title": "从模板开始", "template_name": "python"})
    assert r.status_code == 200
    room = r.json()
    state = c.get(
        "/api/rooms/" + room["code"], headers={"Authorization": room["token"]}
    ).json()
    assert len(state["steps"]) == 4 and all(
        s["phase"] == "draft" for s in state["steps"]
    )
    assert state["total"] == 0 and state["current"] is None
    assert (
        c.post(
            "/api/rooms", json={"title": "错误模板", "template_name": "not-a-template"}
        ).status_code
        == 422
    )


def test_followup_clarification_tracks_latest_relevant_answer(env):
    _, c, b, h, join, step = env
    p = join()
    sid = step()
    iid = cmd(c, b, p, "issue.clarify", {"step": sid}).json()["id"]
    assert c.get(b + "/followup", headers=h).json()["people"]
    cmd(c, b, h, "issue.answer", {"id": iid, "body": "说明一"})
    answer = c.get(b, headers=p).json()["steps"][0]["issues"][0]["answers"][-1]["id"]
    assert cmd(c, b, p, "clarification.ack", {"id": iid, "answer": answer}).status_code == 200
    assert c.post(b + "/status", headers=p, json={"step": sid, "state": "done"}).status_code == 200
    assert not c.get(b + "/followup", headers=h).json()["people"]
    cmd(c, b, h, "issue.answer", {"id": iid, "body": "说明二"})
    assert c.get(b + "/followup", headers=h).json()["people"]
    assert c.get(b + "/handoff", headers=p).json()["items"]


def test_handoff_lost_identity_cannot_export(env):
    _, c, b, h, join, step = env
    p = join()
    q = join()
    step()
    pid = c.get(b, headers=p).json()["me"]["id"]
    assert cmd(c, b, h, "person.remove", {"id": pid}).status_code == 200
    assert c.get(b + "/handoff", headers=p).status_code == 403
    assert cmd(c, b, q, "person.leave").status_code == 200
    assert c.get(b + "/handoff", headers=q).status_code == 403
