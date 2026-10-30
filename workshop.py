"""Room lifecycle, progress and assistance rules. All mutations are atomic."""

import copy
import hashlib
import json
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import HTTPException


def fail(message, code=400):
    raise HTTPException(code, message)


def uid():
    return secrets.token_urlsafe(16)


def text(data, key, maximum=200, required=False):
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > maximum:
        fail(f"{key} 格式无效或过长")
    value = value.strip()
    if required and not value:
        fail(f"请填写 {key}")
    return value


def link(data):
    value = text(data, "link", 1000)
    parsed = urlsplit(value)
    if value and (
        parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username
    ):
        fail("帮助链接只支持完整的 http/https 地址")
    return value


def integer(data, key, maximum=200, default=0):
    value = data.get(key, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= maximum
    ):
        fail(f"{key} 必须是 0–{maximum} 的整数")
    return value


class Workshop:
    def __init__(self, database):
        self.database = database
        self.cache = {}
        self.lock = threading.RLock()

    def room(self, c, code):
        r = c.execute("SELECT * FROM rooms WHERE code=?", (code,)).fetchone()
        if not r:
            fail("房间不存在", 404)
        return r

    def auth(self, c, code, token):
        r = self.room(c, code)
        if token and secrets.compare_digest(r["token"].encode(), token.encode()):
            return {"id": "host", "role": "host", "name": "讲解者"}
        a = c.execute(
            "SELECT id,name,role FROM roles WHERE room=? AND token=? AND revoked=0",
            (code, token),
        ).fetchone()
        if a:
            return dict(a)
        a = c.execute(
            "SELECT id,name FROM people WHERE room=? AND token=? AND left_at IS NULL",
            (code, token),
        ).fetchone()
        if a:
            return dict(a, role="person")
        fail("凭证无效、已撤销或已离开，请重新加入", 403)

    def permit(self, actor, *roles):
        if actor["role"] not in roles:
            fail("当前身份没有此操作权限", 403)

    def current(self, c, code):
        return c.execute(
            "SELECT * FROM steps WHERE room=? AND phase='active'", (code,)
        ).fetchone()

    def active(self, c, code, sid):
        s = self.current(c, code)
        if not s or s["id"] != sid:
            fail("步骤已切换，请同步后再操作", 409)
        return s

    def issue(self, c, code, iid):
        i = c.execute(
            "SELECT issues.* FROM issues JOIN steps ON steps.id=issues.step WHERE issues.id=? AND steps.room=?",
            (iid, code),
        ).fetchone()
        if not i:
            fail("卡点不存在", 404)
        if i["merged_into"]:
            fail("卡点已合并，请使用合并后的卡点", 409)
        return i

    def check_version(self, data, actual):
        if "version" in data and data["version"] != actual:
            fail("状态已更新，请同步后重新操作", 409)

    def write_state(self, c, step, person, state):
        c.execute(
            "INSERT INTO statuses(step,person,state,updated,version) VALUES(?,?,?,?,1) ON CONFLICT(step,person) DO UPDATE SET state=excluded.state,updated=excluded.updated,version=statuses.version+1",
            (step, person, state, time.time()),
        )

    def freeze(self, c, code):
        s = self.current(c, code)
        if s:
            people = [
                r[0]
                for r in c.execute(
                    "SELECT id FROM people WHERE room=? AND left_at IS NULL", (code,)
                )
            ]
            excluded = {
                x[0]
                for x in c.execute(
                    "SELECT person FROM skips WHERE step=? AND decision='approved'",
                    (s["id"],),
                )
            }
            people = [p for p in people if p not in excluded]
            states = dict(
                c.execute("SELECT person,state FROM statuses WHERE step=?", (s["id"],))
            )
            for person in people:
                if states.get(person, "waiting") != "done":
                    c.execute(
                        "INSERT OR IGNORE INTO tails VALUES(?,?,0,?)",
                        (s["id"], person, time.time()),
                    )
            frozen = {
                "total": len(people),
                "counts": {
                    k: sum(states.get(p, "waiting") == k for p in people)
                    for k in ("done", "working", "stuck", "waiting")
                },
                "ended": time.time(),
            }
            c.execute(
                "UPDATE steps SET phase='frozen',frozen=? WHERE id=?",
                (json.dumps(frozen), s["id"]),
            )

    def create(self, title, expected=0):
        title = text({"title": title}, "title", 80, True)
        expected = integer({"expected": expected}, "expected")
        with self.database.connect(True) as c:
            while True:
                code = "".join(
                    secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(6)
                )
                if not c.execute(
                    "SELECT 1 FROM rooms WHERE code=?", (code,)
                ).fetchone():
                    break
            token = secrets.token_urlsafe(32)
            c.execute(
                "INSERT INTO rooms(code,title,token,created,expected) VALUES(?,?,?,?,?)",
                (code, title, token, time.time(), expected),
            )
            return {"code": code, "token": token}

    def join(self, code, name, seat=""):
        with self.database.connect(True) as c:
            r = self.room(c, code)
            if r["closed"]:
                fail("房间已结束", 409)
            total = c.execute(
                "SELECT count(*) FROM people WHERE room=? AND left_at IS NULL", (code,)
            ).fetchone()[0]
            if total >= 200:
                fail("房间已满")
            name = text({"name": name}, "name", 24) or f"参与者 {total + 1}"
            seat = text({"seat": seat}, "seat", 16)
            if (
                seat
                and c.execute(
                    "SELECT 1 FROM people WHERE room=? AND seat=? AND left_at IS NULL",
                    (code, seat),
                ).fetchone()
            ):
                fail("该座位号已有人使用，请核对或留空", 409)
            token = secrets.token_urlsafe(32)
            c.execute(
                "INSERT INTO people(id,room,name,token,seen,joined,seat) VALUES(?,?,?,?,?,?,?)",
                (uid(), code, name, token, time.time(), time.time(), seat),
            )
            c.execute("UPDATE rooms SET revision=revision+1 WHERE code=?", (code,))
            return {"token": token}

    def heartbeat(self, code, token):
        with self.database.connect(True) as c:
            a = self.auth(c, code, token)
            self.permit(a, "person")
            c.execute("UPDATE people SET seen=? WHERE id=?", (time.time(), a["id"]))
        return {"ok": True}

    def step_data(self, data):
        reasons = data.get("reasons", [])
        if (
            not isinstance(reasons, list)
            or len(reasons) > 12
            or any(
                not isinstance(x, str) or len(x) > 120 or not x.strip() for x in reasons
            )
        ):
            fail("常见原因最多 12 条，每条 120 字")
        return (
            text(data, "title", 200, True),
            text(data, "criteria", 1000),
            link(data),
            json.dumps(
                list(dict.fromkeys(x.strip() for x in reasons)), ensure_ascii=False
            ),
        )

    def template(self, c, code):
        return {
            "format": "workshop-pulse/v1",
            "title": self.room(c, code)["title"],
            "steps": [
                {
                    "title": r["title"],
                    "criteria": r["criteria"],
                    "link": r["link"],
                    "reasons": json.loads(r["reasons"]),
                    "tips": r["tips"],
                    "options": json.loads(r["options"]),
                }
                for r in c.execute(
                    "SELECT * FROM steps WHERE room=? ORDER BY position", (code,)
                )
            ],
        }

    def command(self, code, token, action, data, request_id=""):
        if request_id and (len(request_id) > 100 or not isinstance(request_id, str)):
            fail("请求 ID 无效")
        with self.database.connect(True) as c:
            r = self.room(c, code)
            a = self.auth(c, code, token)
            fingerprint = hashlib.sha256(
                json.dumps([action, data], sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            if request_id:
                previous = c.execute(
                    "SELECT * FROM requests WHERE room=? AND actor=? AND key=?",
                    (code, a["id"], request_id),
                ).fetchone()
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        fail("同一请求 ID 不能用于不同操作", 409)
                    return json.loads(previous["result"])
            if r["closed"] and action not in (
                "role.create",
                "role.revoke",
                "template.export",
                "room.copy",
                "room.delete",
                "step.notes",
            ):
                fail("房间已结束，记录只读", 409)
            result = self.apply(c, r, a, action, data)
            if action == "room.delete":
                return result
            c.execute("UPDATE rooms SET revision=revision+1 WHERE code=?", (code,))
            c.execute(
                "INSERT INTO events(room,kind,entity,actor,created,detail) VALUES(?,?,?,?,?,?)",
                (
                    code,
                    action,
                    str(data.get("id", data.get("step", ""))),
                    a["id"],
                    time.time(),
                    json.dumps({"state": data.get("state")}, ensure_ascii=False),
                ),
            )
            result = {"ok": True, **(result or {})}
            if request_id:
                c.execute(
                    "INSERT INTO requests VALUES(?,?,?,?,?)",
                    (code, a["id"], request_id, fingerprint, json.dumps(result)),
                )
            return result

    def apply(self, c, r, a, action, d):
        code = r["code"]
        now = time.time()
        from checkpoint_features import apply as apply_feature, options

        from progress_features import ACTIONS, apply as apply_progress

        if action in ACTIONS:
            return apply_progress(self, c, r, a, action, d)
        if action == "person.environment":
            return apply_feature(self, c, r, a, action, d)
        if action.startswith("room."):
            self.permit(a, "host")
            if action == "room.settings":
                c.execute(
                    "UPDATE rooms SET expected=?,announcement=? WHERE code=?",
                    (integer(d, "expected"), text(d, "announcement", 500), code),
                )
            elif action == "room.timer":
                seconds = integer(d, "seconds", 7200)
                c.execute(
                    "UPDATE rooms SET timer_end=? WHERE code=?",
                    (now + seconds if seconds else 0, code),
                )
            elif action == "room.close":
                self.freeze(c, code)
                c.execute("UPDATE rooms SET closed=1,timer_end=0 WHERE code=?", (code,))
            elif action == "room.delete":
                c.execute("DELETE FROM rooms WHERE code=?", (code,))
                return {"ok": True}
            elif action == "room.copy":
                newcode = "".join(
                    secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(6)
                )
                token = secrets.token_urlsafe(32)
                c.execute(
                    "INSERT INTO rooms(code,title,token,created,expected) VALUES(?,?,?,?,?)",
                    (newcode, r["title"] + " · 副本", token, now, r["expected"]),
                )
                for n, s in enumerate(self.template(c, code)["steps"]):
                    c.execute(
                        "INSERT INTO steps(id,room,title,created,criteria,link,reasons,tips,phase,position,options) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            uid(),
                            newcode,
                            *[
                                s["title"],
                                now,
                                s["criteria"],
                                s["link"],
                                json.dumps(s["reasons"]),
                                s["tips"],
                            ],
                            "draft",
                            n,
                            json.dumps(options(s)),
                        ),
                    )
                return {"code": newcode, "token": token}
            else:
                fail("未知房间操作")
        elif action.startswith("step."):
            self.permit(a, "host")
            if action == "step.notes":
                if not c.execute(
                    "SELECT 1 FROM steps WHERE id=? AND room=?", (d.get("id"), code)
                ).fetchone():
                    fail("步骤不存在", 404)
                c.execute(
                    "UPDATE steps SET tips=? WHERE id=?",
                    (text(d, "tips", 2000), d["id"]),
                )
                return
            if action in ("step.add", "step.publish"):
                fields = self.step_data(d)
                sid = uid()
                position = c.execute(
                    "SELECT coalesce(max(position),-1)+1 FROM steps WHERE room=?",
                    (code,),
                ).fetchone()[0]
                if action == "step.publish":
                    self.freeze(c, code)
                c.execute(
                    "INSERT INTO steps(id,room,title,criteria,link,reasons,created,phase,position,opened) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        sid,
                        code,
                        *fields,
                        now,
                        "active" if action == "step.publish" else "draft",
                        position,
                        now if action == "step.publish" else None,
                    ),
                )
                c.execute(
                    "UPDATE steps SET tips=? WHERE id=?", (text(d, "tips", 2000), sid)
                )
                c.execute(
                    "UPDATE steps SET options=? WHERE id=?",
                    (json.dumps(options(d)), sid),
                )
                return {"id": sid}
            if action == "step.reorder":
                ids = d.get("ids")
                existing = [
                    x[0]
                    for x in c.execute(
                        "SELECT id FROM steps WHERE room=? AND phase='draft' ORDER BY position",
                        (code,),
                    )
                ]
                if (
                    not isinstance(ids, list)
                    or len(ids) != len(existing)
                    or set(ids) != set(existing)
                ):
                    fail("排序必须包含全部待发布步骤")
                base = c.execute(
                    "SELECT coalesce(max(position),-1)+1 FROM steps WHERE room=? AND phase!='draft'",
                    (code,),
                ).fetchone()[0]
                for n, sid in enumerate(ids):
                    c.execute("UPDATE steps SET position=? WHERE id=?", (base + n, sid))
            else:
                s = c.execute(
                    "SELECT * FROM steps WHERE id=? AND room=?", (d.get("id"), code)
                ).fetchone()
                if not s or s["phase"] != "draft":
                    fail("只能修改或发布待发布步骤", 409)
                if action == "step.start":
                    self.freeze(c, code)
                    # A started step follows the previous checkpoints, even when a later draft was selected.
                    base = c.execute(
                        "SELECT coalesce(max(position),-1)+1 FROM steps WHERE room=? AND phase!='draft'",
                        (code,),
                    ).fetchone()[0]
                    c.execute(
                        "UPDATE steps SET phase='active',opened=?,position=? WHERE id=?",
                        (now, base, s["id"]),
                    )
                    for n, x in enumerate(
                        c.execute(
                            "SELECT id FROM steps WHERE room=? AND phase='draft' ORDER BY position",
                            (code,),
                        ).fetchall()
                    ):
                        c.execute(
                            "UPDATE steps SET position=? WHERE id=?",
                            (base + n + 1, x[0]),
                        )
                elif action == "step.edit":
                    c.execute(
                        "UPDATE steps SET title=?,criteria=?,link=?,reasons=? WHERE id=?",
                        (*self.step_data(d), s["id"]),
                    )
                    c.execute(
                        "UPDATE steps SET tips=? WHERE id=?",
                        (text(d, "tips", 2000), s["id"]),
                    )
                    c.execute(
                        "UPDATE steps SET options=? WHERE id=?",
                        (json.dumps(options(d)), s["id"]),
                    )
                elif action == "step.delete":
                    c.execute("DELETE FROM steps WHERE id=?", (s["id"],))
                else:
                    fail("未知步骤操作")
        elif action == "status":
            self.permit(a, "person")
            s = self.active(c, code, d.get("step"))
            if d.get("state") not in ("done", "working", "stuck"):
                fail("状态无效")
            old = c.execute(
                "SELECT version FROM statuses WHERE step=? AND person=?",
                (s["id"], a["id"]),
            ).fetchone()
            self.check_version(d, old[0] if old else 0)
            if c.execute(
                "SELECT 1 FROM skips WHERE step=? AND person=? AND decision='approved'",
                (s["id"], a["id"]),
            ).fetchone():
                fail("本步已排除参与，如需恢复请联系讲解者", 409)
            self.write_state(c, s["id"], a["id"], d["state"])
        elif action == "person.leave" or action == "person.remove":
            if action == "person.remove":
                self.permit(a, "host")
                pid = d.get("id")
            else:
                self.permit(a, "person")
                pid = a["id"]
            if not c.execute(
                "SELECT 1 FROM people WHERE id=? AND room=? AND left_at IS NULL",
                (pid, code),
            ).fetchone():
                fail("成员不存在", 404)
            c.execute("UPDATE people SET left_at=? WHERE id=?", (now, pid))
        elif action == "person.seat":
            self.permit(a, "person")
            seat = text(d, "seat", 16)
            if (
                seat
                and c.execute(
                    "SELECT 1 FROM people WHERE room=? AND seat=? AND id!=? AND left_at IS NULL",
                    (code, seat, a["id"]),
                ).fetchone()
            ):
                fail("座位号已被使用", 409)
            c.execute("UPDATE people SET seat=? WHERE id=?", (seat, a["id"]))
        elif action == "catchup":
            self.permit(a, "person")
            if not c.execute(
                "SELECT 1 FROM tails JOIN steps ON steps.id=tails.step WHERE tails.step=? AND tails.person=? AND steps.room=?",
                (d.get("step"), a["id"], code),
            ).fetchone():
                fail("没有这条前序跟进记录", 404)
            c.execute(
                "UPDATE tails SET recovered=1,updated=? WHERE step=? AND person=?",
                (now, d["step"], a["id"]),
            )
        elif action.startswith("role."):
            self.permit(a, "host")
            if action == "role.create":
                role = d.get("role")
                if role not in ("helper", "screen"):
                    fail("身份无效")
                rid = uid()
                token = secrets.token_urlsafe(32)
                c.execute(
                    "INSERT INTO roles(id,room,name,token,role) VALUES(?,?,?,?,?)",
                    (
                        rid,
                        code,
                        text(d, "name", 24) or ("助教" if role == "helper" else "投屏"),
                        token,
                        role,
                    ),
                )
                return {"id": rid, "token": token, "role": role}
            elif action == "role.revoke":
                c.execute(
                    "UPDATE roles SET revoked=1 WHERE id=? AND room=?",
                    (d.get("id"), code),
                )
                c.execute(
                    "UPDATE issues SET assignee='' WHERE assignee=? AND step IN (SELECT id FROM steps WHERE room=?)",
                    (d.get("id"), code),
                )
            else:
                fail("未知身份操作")
        elif action == "template.import":
            self.permit(a, "host")
            template = d.get("template", {})
            if (
                not isinstance(template, dict)
                or template.get("format") != "workshop-pulse/v1"
            ):
                fail("模板格式无效")
            rows = template.get("steps")
            if (
                not isinstance(rows, list)
                or not 1 <= len(rows) <= 50
                or any(not isinstance(x, dict) for x in rows)
            ):
                fail("模板须有 1–50 个步骤")
            validated = [self.step_data(row) for row in rows]
            n = c.execute(
                "SELECT coalesce(max(position),-1)+1 FROM steps WHERE room=?", (code,)
            ).fetchone()[0]
            for idx, row in enumerate(validated):
                sid = uid()
                c.execute(
                    "INSERT INTO steps(id,room,title,criteria,link,reasons,created,phase,position,tips) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        sid,
                        code,
                        *row,
                        now,
                        "draft",
                        n + idx,
                        text(rows[idx], "tips", 2000),
                    ),
                )
                c.execute(
                    "UPDATE steps SET options=? WHERE id=?",
                    (json.dumps(options(rows[idx])), sid),
                )
        elif action == "issue.add":
            self.permit(a, "person")
            self.active(c, code, d.get("step"))
            title = text(d, "title", 120, True)
            old = c.execute(
                "SELECT id FROM issues WHERE step=? AND title=? AND merged_into IS NULL AND kind='blocker'",
                (d["step"], title),
            ).fetchone()
            iid = old[0] if old else uid()
            if not old:
                c.execute(
                    "INSERT INTO issues(id,step,title,created) VALUES(?,?,?,?)",
                    (iid, d["step"], title, now),
                )
            self.agree(c, code, a, iid)
            return {"id": iid}
        elif action.startswith("issue."):
            i = self.issue(c, code, d.get("id"))
            iid = i["id"]
            if action in (
                "issue.agree",
                "issue.withdraw",
                "issue.recover",
                "issue.still",
            ):
                self.permit(a, "person")
                v = c.execute(
                    "SELECT * FROM votes WHERE issue=? AND person=?", (iid, a["id"])
                ).fetchone()
                if action == "issue.agree":
                    self.active(c, code, i["step"])
                    self.agree(c, code, a, iid)
                else:
                    if not v:
                        fail("你未加入此卡点", 403)
                    self.check_version(d, v["version"])
                    if action == "issue.withdraw":
                        c.execute(
                            "DELETE FROM votes WHERE issue=? AND person=?",
                            (iid, a["id"]),
                        )
                    else:
                        active = action == "issue.still"
                        c.execute(
                            "UPDATE votes SET active=?,updated=?,version=version+1 WHERE issue=? AND person=?",
                            (int(active), now, iid, a["id"]),
                        )
                        if active:
                            c.execute(
                                "UPDATE issues SET stage='open',resolved=0,version=version+1 WHERE id=?",
                                (iid,),
                            )
                            if (
                                self.current(c, code)
                                and self.current(c, code)["id"] == i["step"]
                                and i["kind"] == "blocker"
                            ):
                                if i["kind"] == "blocker":
                                    self.write_state(c, i["step"], a["id"], "stuck")
                        elif (
                            self.current(c, code)
                            and self.current(c, code)["id"] == i["step"]
                            and i["kind"] == "blocker"
                        ):
                            remaining = c.execute(
                                "SELECT count(*) FROM votes JOIN issues ON issues.id=votes.issue WHERE issues.step=? AND votes.person=? AND votes.active=1 AND issues.merged_into IS NULL AND issues.kind='blocker'",
                                (i["step"], a["id"]),
                            ).fetchone()[0]
                            status = c.execute(
                                "SELECT state FROM statuses WHERE step=? AND person=?",
                                (i["step"], a["id"]),
                            ).fetchone()
                            if not remaining and status and status[0] == "stuck":
                                self.write_state(c, i["step"], a["id"], "working")
            else:
                self.permit(a, "host", "helper")
                if action == "issue.answer":
                    from checkpoint_features import PLATFORMS

                    environment = d.get("environment", "unspecified")
                    if environment not in PLATFORMS:
                        fail("回答适用环境无效")
                    body = text(d, "body", 2000, True)
                    url = link(d)
                    c.execute(
                        "INSERT INTO answers(id,issue,body,link,public,created,author) VALUES(?,?,?,?,?,?,?)",
                        (
                            uid(),
                            iid,
                            body,
                            url,
                            int(bool(d.get("public", False))),
                            now,
                            a["id"],
                        ),
                    )
                    c.execute(
                        "UPDATE issues SET stage='answered',resolved=1,version=version+1 WHERE id=?",
                        (iid,),
                    )
                    c.execute(
                        "UPDATE answers SET environment=? WHERE rowid=last_insert_rowid()",
                        (environment,),
                    )
                elif action == "issue.resolve":
                    c.execute(
                        "UPDATE issues SET stage='answered',resolved=1,version=version+1 WHERE id=?",
                        (iid,),
                    )
                elif action == "issue.claim":
                    self.check_version(d, i["version"])
                    target = d.get("assignee", a["id"])
                    if a["role"] == "helper" and target not in ("", a["id"]):
                        fail("助教只能认领或释放自己的任务", 403)
                    if a["role"] == "helper" and i["assignee"] not in ("", a["id"]):
                        fail("已由其他人认领", 409)
                    if (
                        target
                        and target != "host"
                        and not c.execute(
                            "SELECT 1 FROM roles WHERE id=? AND room=? AND role='helper' AND revoked=0",
                            (target, code),
                        ).fetchone()
                    ):
                        fail("助教已失效")
                    c.execute(
                        "UPDATE issues SET assignee=?,stage=?,version=version+1 WHERE id=?",
                        (target, "working" if target else "open", iid),
                    )
                elif action == "issue.close":
                    n = c.execute(
                        "SELECT count(*) FROM votes JOIN people ON people.id=votes.person WHERE issue=? AND active=1 AND left_at IS NULL",
                        (iid,),
                    ).fetchone()[0]
                    if n:
                        fail("仍有人受影响，不能关闭卡点", 409)
                    c.execute(
                        "UPDATE issues SET stage='closed',resolved=1,version=version+1 WHERE id=?",
                        (iid,),
                    )
                elif action == "issue.merge":
                    self.permit(a, "host")
                    target = self.issue(c, code, d.get("target"))
                    if (
                        target["step"] != i["step"]
                        or target["id"] == iid
                        or target["kind"] != i["kind"]
                    ):
                        fail("只能合并同一步骤的不同卡点")
                    for v in c.execute(
                        "SELECT * FROM votes WHERE issue=?", (iid,)
                    ).fetchall():
                        c.execute(
                            "INSERT INTO votes(issue,person,active,updated,version) VALUES(?,?,?,?,?) ON CONFLICT(issue,person) DO UPDATE SET active=max(votes.active,excluded.active),updated=max(votes.updated,excluded.updated),version=votes.version+1",
                            (
                                target["id"],
                                v["person"],
                                v["active"],
                                v["updated"],
                                v["version"],
                            ),
                        )
                    c.execute(
                        "UPDATE answers SET issue=? WHERE issue=?", (target["id"], iid)
                    )
                    c.execute(
                        "UPDATE issues SET merged_into=?,version=version+1 WHERE id=?",
                        (target["id"], iid),
                    )
                    c.execute(
                        "UPDATE issues SET stage='open',resolved=0,version=version+1 WHERE id=?",
                        (target["id"],),
                    )
                else:
                    fail("未知卡点操作")
        else:
            fail("未知操作")

    def agree(self, c, code, a, iid):
        i = self.issue(c, code, iid)
        old = c.execute(
            "SELECT 1 FROM votes WHERE issue=? AND person=?", (iid, a["id"])
        ).fetchone()
        if old:
            return  # Repeated agreement never overwrites later progress or recovery.
        c.execute(
            "INSERT INTO votes(issue,person,active,updated) VALUES(?,?,1,?)",
            (iid, a["id"], time.time()),
        )
        c.execute(
            "UPDATE issues SET stage='open',resolved=0,version=version+1 WHERE id=?",
            (iid,),
        )
        if i["kind"] == "blocker":
            self.write_state(c, i["step"], a["id"], "stuck")

    def base(self, c, code):
        r = dict(self.room(c, code))
        key = (r["revision"], int(time.time() / 10))
        with self.lock:
            cached = self.cache.get(code)
            if cached and cached[0] == key:
                return cached[1]
            people = [
                dict(x)
                for x in c.execute(
                    "SELECT id,name,seat,seen,joined,left_at,environment FROM people WHERE room=?",
                    (code,),
                )
            ]
            roles = [
                dict(x)
                for x in c.execute(
                    "SELECT id,name,role,revoked FROM roles WHERE room=?", (code,)
                )
            ]
            steps = []
            for row in c.execute(
                "SELECT * FROM steps WHERE room=? ORDER BY position,created", (code,)
            ).fetchall():
                s = dict(row)
                s["reasons"] = json.loads(s["reasons"])
                s["states"] = {
                    x["person"]: dict(x)
                    for x in c.execute(
                        "SELECT * FROM statuses WHERE step=?", (s["id"],)
                    )
                }
                excluded = {
                    x[0]
                    for x in c.execute(
                        "SELECT person FROM skips WHERE step=? AND decision='approved'",
                        (s["id"],),
                    )
                }
                cohort = [
                    p
                    for p in people
                    if p["left_at"] is None and p["id"] not in excluded
                ]
                frozen = json.loads(s["frozen"]) if s["frozen"] else None
                s["total"] = frozen["total"] if frozen else len(cohort)
                s["counts"] = (
                    frozen["counts"]
                    if frozen
                    else {
                        k: sum(
                            s["states"].get(p["id"], {}).get("state", "waiting") == k
                            for p in cohort
                        )
                        for k in ("done", "working", "stuck", "waiting")
                    }
                )
                s["ended"] = frozen["ended"] if frozen else None
                s["issues"] = []
                for row in c.execute(
                    "SELECT * FROM issues WHERE step=?", (s["id"],)
                ).fetchall():
                    i = dict(row)
                    i["members"] = [
                        dict(x)
                        for x in c.execute(
                            "SELECT votes.*,people.name,people.left_at FROM votes JOIN people ON people.id=votes.person WHERE issue=?",
                            (i["id"],),
                        )
                    ]
                    i["answers"] = [
                        dict(x)
                        for x in c.execute(
                            "SELECT id,body,link,public,created,author,environment FROM answers WHERE issue=? ORDER BY created",
                            (i["id"],),
                        )
                    ]
                    i["count"] = len(i["members"])
                    i["affected"] = sum(
                        x["active"] and x["left_at"] is None for x in i["members"]
                    )
                    i["recovered"] = sum(not x["active"] for x in i["members"])
                    i["sources"] = [
                        x[0]
                        for x in c.execute(
                            "SELECT title FROM issues WHERE merged_into=?", (i["id"],)
                        )
                    ]
                    s["issues"].append(i)
                steps.append(s)
            base = {"room": r, "people": people, "roles": roles, "steps": steps}
            if len(self.cache) > 256:
                self.cache.clear()
            self.cache[code] = (key, base)
            return base

    def snapshot(self, code, token):
        with self.database.connect() as c:
            a = self.auth(c, code, token)
            b = self.base(c, code)
            r = b["room"]
            active = [p for p in b["people"] if p["left_at"] is None]
            staff = a["role"] in ("host", "helper")
            screen = a["role"] == "screen"
            steps = []
            for raw in b["steps"]:
                if raw["phase"] == "draft" and not staff:
                    continue
                s = {
                    k: copy.deepcopy(raw[k])
                    for k in (
                        "id",
                        "title",
                        "criteria",
                        "link",
                        "reasons",
                        "tips",
                        "phase",
                        "position",
                        "opened",
                        "total",
                        "counts",
                        "ended",
                    )
                }
                s["options"] = json.loads(raw["options"])
                mine = raw["states"].get(a["id"], {})
                s.update(
                    mine=mine.get("state", "waiting")
                    if not staff and not screen
                    else None,
                    version=mine.get("version", 0),
                    issues=[],
                )
                if staff:
                    s["people"] = [
                        dict(
                            p,
                            state=raw["states"]
                            .get(p["id"], {})
                            .get("state", "waiting"),
                            online=time.time() - p["seen"] < 45,
                        )
                        for p in active
                    ]
                for raw_i in raw["issues"]:
                    if raw_i["merged_into"]:
                        continue
                    if screen:
                        # Explicit allowlist: no raw issue text, source titles, names or internal IDs.
                        s["issues"].extend(
                            {"body": x["body"], "link": x["link"]}
                            for x in raw_i["answers"]
                            if x["public"]
                        )
                        continue
                    i = {
                        k: copy.deepcopy(raw_i[k])
                        for k in (
                            "id",
                            "title",
                            "resolved",
                            "stage",
                            "assignee",
                            "count",
                            "affected",
                            "recovered",
                            "version",
                            "sources",
                            "answers",
                            "created",
                        )
                    }
                    member = next(
                        (m for m in raw_i["members"] if m["person"] == a["id"]), None
                    )
                    i.update(
                        mine=bool(member),
                        my_active=bool(member and member["active"]),
                        member_version=member["version"] if member else 0,
                    )
                    if staff:
                        i["members"] = [
                            {
                                k: m[k]
                                for k in (
                                    "person",
                                    "name",
                                    "active",
                                    "updated",
                                    "left_at",
                                )
                            }
                            for m in raw_i["members"]
                        ]
                    s["issues"].append(i)
                steps.append(s)
            result = {
                "code": code,
                "title": r["title"],
                "closed": bool(r["closed"]),
                "host": a["role"] == "host",
                "role": a["role"],
                "revision": r["revision"],
                "total": len(active),
                "joined_total": len(b["people"]),
                "expected": r["expected"],
                "missing": max(0, r["expected"] - len(active)),
                "announcement": r["announcement"],
                "timer_end": r["timer_end"],
                "server_time": time.time(),
                "steps": steps,
                "current": next(
                    (s["id"] for s in steps if s["phase"] == "active"), None
                ),
            }
            if not screen:
                result["me"] = {
                    "id": a["id"],
                    "seat": next((p["seat"] for p in active if p["id"] == a["id"]), ""),
                }
            if not screen:
                result["me"]["environment"] = next(
                    (p["environment"] for p in active if p["id"] == a["id"]),
                    "unspecified",
                )
            if staff:
                result["environments"] = {
                    k: sum(p["environment"] == k for p in active)
                    for k in ("unspecified", "windows", "macos", "linux", "other")
                }
                result["roles"] = b["roles"]
                result["departed"] = [
                    p for p in b["people"] if p["left_at"] is not None
                ]
            if not screen:
                tails = [
                    dict(x)
                    for x in c.execute(
                        "SELECT tails.step,tails.person,tails.recovered,tails.updated,people.name,steps.title FROM tails JOIN people ON people.id=tails.person JOIN steps ON steps.id=tails.step WHERE steps.room=? AND people.left_at IS NULL ORDER BY steps.position",
                        (code,),
                    )
                ]
                result["tails"] = (
                    tails
                    if staff
                    else [
                        {k: x[k] for k in ("step", "recovered", "updated", "title")}
                        for x in tails
                        if x["person"] == a["id"]
                    ]
                )
            from progress_features import enrich

            enrich(self, c, code, a, result)
            return result
