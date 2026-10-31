"""Versioned corrections, clarifications, per-member details and progress confirmations."""

import json
import time
from workshop import text, fail, uid

ACTIONS = {
    "person.reminders",
    "step.correct",
    "correction.ack",
    "issue.clarify",
    "issue.details",
    "clarification.ack",
    "skip.request",
    "skip.decide",
    "pulse.start",
    "pulse.ack",
}


def get_step(service, c, code, sid):
    s = c.execute("SELECT * FROM steps WHERE room=? AND id=?", (code, sid)).fetchone()
    if not s:
        fail("步骤不存在", 404)
    return s


def acknowledge(c, code, kind, entity, person):
    c.execute(
        "INSERT OR IGNORE INTO acknowledgements VALUES(?,?,?,?,?)",
        (code, kind, entity, person, time.time()),
    )


def apply(service, c, r, a, action, d):
    code = r["code"]
    now = time.time()
    if action == "person.reminders":
        service.permit(a, "person")
        if not isinstance(d.get("enabled"), bool):
            fail("提示开关无效")
        c.execute(
            "UPDATE people SET reminders=? WHERE id=?", (int(d["enabled"]), a["id"])
        )
    elif action == "step.correct":
        service.permit(a, "host")
        s = service.active(c, code, d.get("step"))
        number = c.execute(
            "SELECT coalesce(max(number),0) FROM corrections WHERE step=?", (s["id"],)
        ).fetchone()[0]
        service.check_version(d, number)
        title = text(d, "title", 200, True)
        criteria = text(d, "criteria", 1000, True)
        note = text(d, "note", 500, True)
        cid = uid()
        before = {"title": s["title"], "criteria": s["criteria"]}
        after = {"title": title, "criteria": criteria}
        if before == after:
            fail("更正内容未发生变化")
        c.execute(
            "INSERT INTO corrections VALUES(?,?,?,?,?,?,?)",
            (
                cid,
                s["id"],
                number + 1,
                json.dumps(before),
                json.dumps(after),
                note,
                now,
            ),
        )
        c.execute(
            "UPDATE steps SET title=?,criteria=? WHERE id=?", (title, criteria, s["id"])
        )
        return {"id": cid}
    elif action == "correction.ack":
        service.permit(a, "person")
        row = c.execute(
            "SELECT corrections.* FROM corrections JOIN steps ON steps.id=corrections.step WHERE corrections.id=? AND steps.room=?",
            (d.get("id"), code),
        ).fetchone()
        if not row:
            fail("更正不存在", 404)
        latest = c.execute(
            "SELECT max(number) FROM corrections WHERE step=?", (row["step"],)
        ).fetchone()[0]
        if row["number"] != latest:
            fail("已有更新的更正，请同步后确认", 409)
        acknowledge(c, code, "correction", row["id"], a["id"])
    elif action == "issue.clarify":
        service.permit(a, "person")
        s = service.active(c, code, d.get("step"))
        title = text(d, "title", 120) or "我没看懂这一步要求"
        old = c.execute(
            "SELECT id FROM issues WHERE step=? AND title=? AND kind='clarification' AND merged_into IS NULL",
            (s["id"], title),
        ).fetchone()
        iid = old[0] if old else uid()
        if not old:
            c.execute(
                "INSERT INTO issues(id,step,title,created,kind) VALUES(?,?,?,?,'clarification')",
                (iid, s["id"], title, now),
            )
        c.execute(
            "INSERT OR IGNORE INTO votes(issue,person,active,updated) VALUES(?,?,1,?)",
            (iid, a["id"], now),
        )
        return {"id": iid}
    elif action == "issue.details":
        service.permit(a, "person")
        i = service.issue(c, code, d.get("id"))
        v = c.execute(
            "SELECT * FROM votes WHERE issue=? AND person=?", (i["id"], a["id"])
        ).fetchone()
        if not v:
            fail("请先加入此卡点", 403)
        service.check_version(d, v["version"])
        env = d.get("environment", "unspecified")
        if env not in ("unspecified", "windows", "macos", "linux", "other"):
            fail("环境类型无效")
        details = {
            "environment": env,
            "error_code": text(d, "error_code", 120),
            "attempted": text(d, "attempted", 1000),
        }
        c.execute(
            "UPDATE votes SET details=?,version=version+1 WHERE issue=? AND person=?",
            (json.dumps(details), i["id"], a["id"]),
        )
    elif action == "clarification.ack":
        service.permit(a, "person")
        i = service.issue(c, code, d.get("id"))
        if i["kind"] != "clarification":
            fail("这不是要求澄清")
        if not c.execute(
            "SELECT 1 FROM votes WHERE issue=? AND person=?", (i["id"], a["id"])
        ).fetchone():
            fail("请先加入此澄清", 403)
        environment = c.execute(
            "SELECT environment FROM people WHERE id=?", (a["id"],)
        ).fetchone()[0]
        answer = c.execute(
            "SELECT id FROM answers WHERE issue=? AND environment IN ('unspecified',?) ORDER BY created DESC,rowid DESC LIMIT 1",
            (i["id"], environment),
        ).fetchone()
        if not answer or d.get("answer") != answer[0]:
            fail("请确认最新澄清答复", 409)
        acknowledge(c, code, "clarification", answer[0], a["id"])
    elif action == "skip.request":
        service.permit(a, "person")
        s = service.active(c, code, d.get("step"))
        reason = text(d, "reason", 300, True)
        existing = c.execute(
            "SELECT decision FROM skips WHERE step=? AND person=?", (s["id"], a["id"])
        ).fetchone()
        if existing and existing["decision"] != "pending":
            fail("申请已处理，如需调整请联系讲解者", 409)
        c.execute(
            "INSERT INTO skips(step,person,reason,decision,created) VALUES(?,?,?,'pending',?) ON CONFLICT(step,person) DO UPDATE SET reason=excluded.reason,version=skips.version+1",
            (s["id"], a["id"], reason, now),
        )
    elif action == "skip.decide":
        service.permit(a, "host")
        s = service.active(c, code, d.get("step"))
        row = c.execute(
            "SELECT * FROM skips WHERE step=? AND person=?", (s["id"], d.get("person"))
        ).fetchone()
        if not row:
            fail("申请不存在", 404)
        service.check_version(d, row["version"])
        decision = d.get("decision")
        if decision not in ("approved", "rejected"):
            fail("决定无效")
        c.execute(
            "UPDATE skips SET decision=?,version=version+1,decided=? WHERE step=? AND person=?",
            (decision, now, s["id"], row["person"]),
        )
    elif action == "pulse.start":
        service.permit(a, "host")
        s = service.active(c, code, d.get("step"))
        last = c.execute(
            "SELECT created FROM pulses WHERE step=? ORDER BY created DESC LIMIT 1",
            (s["id"],),
        ).fetchone()
        if last and now - last[0] < 60:
            fail("每个步骤每分钟最多发起一次确认", 429)
        cohort = [
            x[0]
            for x in c.execute(
                "SELECT id FROM people WHERE room=? AND left_at IS NULL AND id NOT IN (SELECT person FROM skips WHERE step=? AND decision='approved')",
                (code, s["id"]),
            )
        ]
        pid = uid()
        c.execute(
            "INSERT INTO pulses VALUES(?,?,?,?)",
            (pid, s["id"], json.dumps(cohort), now),
        )
        return {"id": pid}
    elif action == "pulse.ack":
        service.permit(a, "person")
        s = service.current(c, code)
        row = c.execute("SELECT * FROM pulses WHERE id=?", (d.get("id"),)).fetchone()
        if not row or not s or row["step"] != s["id"]:
            fail("确认轮次已结束", 409)
        latest = c.execute(
            "SELECT id FROM pulses WHERE step=? ORDER BY created DESC,rowid DESC LIMIT 1",
            (s["id"],),
        ).fetchone()
        if latest[0] != row["id"]:
            fail("请确认最新轮次", 409)
        if a["id"] not in json.loads(row["cohort"]):
            fail("你不在本轮参与范围", 403)
        acknowledge(c, code, "pulse", row["id"], a["id"])
    return {}


def enrich(service, c, code, a, result):
    staff = a["role"] in ("host", "helper")
    person = a["role"] == "person"
    screen = a["role"] == "screen"
    env = (
        c.execute(
            "SELECT environment,reminders FROM people WHERE id=?", (a["id"],)
        ).fetchone()
        if person
        else None
    )
    for s in result["steps"]:
        sid = s["id"]
        corrections = []
        for row in c.execute(
            "SELECT * FROM corrections WHERE step=? ORDER BY number", (sid,)
        ):
            row = dict(row)
            acks = c.execute(
                "SELECT count(*) FROM acknowledgements WHERE kind='correction' AND entity=?",
                (row["id"],),
            ).fetchone()[0]
            corrections.append(
                {
                    "id": row["id"],
                    "number": row["number"],
                    "previous": json.loads(row["previous"]),
                    "current": json.loads(row["current"]),
                    "note": row["note"],
                    "created": row["created"],
                    "acknowledged": acks,
                    "mine": bool(
                        person
                        and c.execute(
                            "SELECT 1 FROM acknowledgements WHERE kind='correction' AND entity=? AND person=?",
                            (row["id"], a["id"]),
                        ).fetchone()
                    ),
                }
            )
        s["corrections"] = corrections
        if screen:
            continue
        skips = [
            dict(x)
            for x in c.execute(
                "SELECT skips.*,people.name FROM skips JOIN people ON people.id=skips.person WHERE skips.step=?",
                (sid,),
            )
        ]
        s["skips"] = (
            skips
            if staff
            else [
                {k: x[k] for k in ("reason", "decision", "version")}
                for x in skips
                if x["person"] == a["id"]
            ]
        )
        s["excluded"] = sum(x["decision"] == "approved" for x in skips)
        for p in s.get("people", []):
            p["excluded"] = any(
                x["person"] == p["id"] and x["decision"] == "approved" for x in skips
            )
        for i in s["issues"]:
            row = c.execute("SELECT kind FROM issues WHERE id=?", (i["id"],)).fetchone()
            i["kind"] = row["kind"]
            if person:
                v = c.execute(
                    "SELECT details FROM votes WHERE issue=? AND person=?",
                    (i["id"], a["id"]),
                ).fetchone()
                i["my_details"] = json.loads(v[0]) if v else {}
                i["answers"] = [
                    ans
                    for ans in i["answers"]
                    if ans.get("environment", "unspecified")
                    in ("unspecified", env["environment"])
                ]
            if staff:
                for m in i.get("members", []):
                    row = c.execute(
                        "SELECT details FROM votes WHERE issue=? AND person=?",
                        (i["id"], m["person"]),
                    ).fetchone()
                    m["details"] = json.loads(row[0]) if row else {}
            if i["kind"] == "clarification":
                ans = i["answers"][-1] if i["answers"] else None
                i["acknowledged"] = (
                    c.execute(
                        "SELECT count(*) FROM acknowledgements WHERE kind='clarification' AND entity=?",
                        (ans["id"],),
                    ).fetchone()[0]
                    if ans
                    else 0
                )
                i["my_ack"] = bool(
                    person
                    and ans
                    and c.execute(
                        "SELECT 1 FROM acknowledgements WHERE kind='clarification' AND entity=? AND person=?",
                        (ans["id"], a["id"]),
                    ).fetchone()
                )
        pulse = c.execute(
            "SELECT * FROM pulses WHERE step=? ORDER BY created DESC,rowid DESC LIMIT 1",
            (sid,),
        ).fetchone()
        if pulse:
            s["pulse"] = {
                "id": pulse["id"],
                "created": pulse["created"],
                "total": len(json.loads(pulse["cohort"])),
                "confirmed": c.execute(
                    "SELECT count(*) FROM acknowledgements WHERE kind='pulse' AND entity=?",
                    (pulse["id"],),
                ).fetchone()[0],
                "mine": bool(
                    person
                    and c.execute(
                        "SELECT 1 FROM acknowledgements WHERE kind='pulse' AND entity=? AND person=?",
                        (pulse["id"], a["id"]),
                    ).fetchone()
                ),
                "eligible": bool(person and a["id"] in json.loads(pulse["cohort"])),
            }
    if person:
        result["me"]["reminders"] = bool(env["reminders"])
