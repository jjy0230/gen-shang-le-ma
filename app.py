"""跟上了吗 — single-process, persistent workshop progress service."""

import os, secrets, sqlite3, time, json, io, asyncio
from contextlib import contextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import qrcode
import qrcode.image.svg

DB = os.getenv("DATABASE_PATH", "data/workshop.sqlite3")
Path(DB).parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def db():
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


with db() as c:
    c.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS rooms(code TEXT PRIMARY KEY,title TEXT,token TEXT,created REAL,closed INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS people(id TEXT PRIMARY KEY,room TEXT REFERENCES rooms(code) ON DELETE CASCADE,name TEXT,token TEXT,seen REAL,joined REAL);
    CREATE TABLE IF NOT EXISTS steps(id TEXT PRIMARY KEY,room TEXT REFERENCES rooms(code) ON DELETE CASCADE,title TEXT,created REAL);
    CREATE TABLE IF NOT EXISTS statuses(step TEXT REFERENCES steps(id) ON DELETE CASCADE,person TEXT REFERENCES people(id) ON DELETE CASCADE,state TEXT,updated REAL,PRIMARY KEY(step,person));
    CREATE TABLE IF NOT EXISTS issues(id TEXT PRIMARY KEY,step TEXT REFERENCES steps(id) ON DELETE CASCADE,title TEXT,resolved INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS votes(issue TEXT REFERENCES issues(id) ON DELETE CASCADE,person TEXT REFERENCES people(id) ON DELETE CASCADE,PRIMARY KEY(issue,person));
    """)
    if "joined" not in [x[1] for x in c.execute("PRAGMA table_info(people)")]:
        c.execute("ALTER TABLE people ADD COLUMN joined REAL DEFAULT 0")
app = FastAPI(title="跟上了吗", version="1.0.0")


class RoomInput(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class PersonInput(BaseModel):
    name: str = Field(min_length=1, max_length=24)


class StepInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class StateInput(BaseModel):
    step: str
    state: str


class IssueInput(BaseModel):
    step: str
    title: str = Field(min_length=1, max_length=120)


def fail(msg, code=400):
    raise HTTPException(code, msg)


def room(c, code):
    r = c.execute("SELECT * FROM rooms WHERE code=?", (code,)).fetchone()
    if not r:
        fail("房间不存在", 404)
    return r


def host(c, code, token):
    r = room(c, code)
    if not secrets.compare_digest(r["token"], token or ""):
        fail("需要讲解者凭证", 403)
    return r


def person(c, code, token):
    r = room(c, code)
    p = c.execute(
        "SELECT * FROM people WHERE room=? AND token=?", (code, token)
    ).fetchone()
    if not p:
        fail("请先加入房间", 403)
    return p


def active(c, code, step):
    r = room(c, code)
    if r["closed"]:
        fail("房间已结束", 409)
    s = c.execute(
        "SELECT id FROM steps WHERE room=? ORDER BY created DESC,rowid DESC LIMIT 1",
        (code,),
    ).fetchone()
    if not s or s["id"] != step:
        fail("步骤已更新，请刷新后操作", 409)


def snapshot(c, code, token):
    r = room(c, code)
    ishost = secrets.compare_digest(r["token"], token or "")
    p = None if ishost else person(c, code, token)
    people = [
        dict(x)
        for x in c.execute(
            "SELECT id,name,seen,joined FROM people WHERE room=?", (code,)
        )
    ]
    steps = []
    for s in c.execute(
        "SELECT id,title,created FROM steps WHERE room=? ORDER BY created,rowid",
        (code,),
    ).fetchall():
        s = dict(s)
        boundary = c.execute(
            "SELECT created FROM steps WHERE room=? AND (created>? OR (created=? AND rowid>(SELECT rowid FROM steps WHERE id=?))) ORDER BY created,rowid LIMIT 1",
            (code, s["created"], s["created"], s["id"]),
        ).fetchone()
        cohort = [x for x in people if not boundary or x["joined"] <= boundary[0]]
        s["total"] = len(cohort)
        states = {
            x["person"]: dict(x)
            for x in c.execute(
                "SELECT person,state,updated FROM statuses WHERE step=?", (s["id"],)
            )
        }
        s["counts"] = {
            k: sum(states.get(x["id"], {}).get("state", "waiting") == k for x in cohort)
            for k in ["done", "working", "stuck", "waiting"]
        }
        s["mine"] = states.get(p["id"], {}).get("state", "waiting") if p else None
        s["issues"] = []
        for i in c.execute("SELECT * FROM issues WHERE step=?", (s["id"],)).fetchall():
            i = dict(i)
            ids = [
                v[0]
                for v in c.execute("SELECT person FROM votes WHERE issue=?", (i["id"],))
            ]
            i["count"] = len(ids)
            i["mine"] = bool(p and p["id"] in ids)
            s["issues"].append(i)
        if ishost:
            s["people"] = [
                dict(
                    x,
                    state=states.get(x["id"], {}).get("state", "waiting"),
                    online=time.time() - x["seen"] < 45,
                )
                for x in cohort
            ]
        steps.append(s)
    return dict(
        code=code,
        title=r["title"],
        closed=bool(r["closed"]),
        host=ishost,
        total=len(people),
        steps=steps,
    )


# Bounded in-memory abuse protection; intended for one process, behind HTTPS.
buckets = {}


@app.middleware("http")
async def limits(request: Request, call_next):
    if request.method in ("POST", "DELETE"):
        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        if len(buckets) > 10000:
            buckets.clear()
        hits = [t for t in buckets.get(key, []) if now - t < 60]
        if len(hits) >= 240:
            return Response("操作过于频繁", 429)
        buckets[key] = hits + [now]
        try:
            size = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            return Response("请求格式无效", 400)
        if size > 8192:
            return Response("请求过大", 413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    )
    return response


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/rooms")
def create(body: RoomInput):
    title = body.title.strip()
    if not title:
        fail("请输入房间名称")
    with db() as c:
        code = "".join(
            secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(6)
        )
        token = secrets.token_urlsafe(32)
        c.execute(
            "INSERT INTO rooms(code,title,token,created) VALUES(?,?,?,?)",
            (code, title, token, time.time()),
        )
    return dict(code=code, token=token)


@app.post("/api/rooms/{code}/join")
def join(code: str, body: PersonInput):
    with db() as c:
        r = room(c, code)
        if r["closed"]:
            fail("房间已结束", 409)
        if not body.name.strip():
            fail("请输入昵称")
        if (
            c.execute("SELECT count(*) FROM people WHERE room=?", (code,)).fetchone()[0]
            >= 200
        ):
            fail("房间已满")
        pid = secrets.token_urlsafe(12)
        token = secrets.token_urlsafe(32)
        c.execute(
            "INSERT INTO people VALUES(?,?,?,?,?,?)",
            (pid, code, body.name.strip(), token, time.time(), time.time()),
        )
    return dict(token=token)


@app.get("/api/rooms/{code}")
def get(code: str, authorization: str = Header(default="")):
    with db() as c:
        return snapshot(c, code, authorization)


@app.post("/api/rooms/{code}/heartbeat")
def heartbeat(code: str, authorization: str = Header(default="")):
    with db() as c:
        p = person(c, code, authorization)
        c.execute("UPDATE people SET seen=? WHERE id=?", (time.time(), p["id"]))
    return {"ok": True}


@app.post("/api/rooms/{code}/steps")
def step(code: str, body: StepInput, authorization: str = Header(default="")):
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        r = host(c, code, authorization)
        if r["closed"]:
            fail("房间已结束", 409)
        if not body.title.strip():
            fail("请输入步骤")
        c.execute(
            "INSERT INTO steps VALUES(?,?,?,?)",
            (secrets.token_urlsafe(12), code, body.title.strip(), time.time()),
        )
    return {"ok": True}


@app.post("/api/rooms/{code}/status")
def status(code: str, body: StateInput, authorization: str = Header(default="")):
    if body.state not in ("done", "working", "stuck"):
        fail("状态无效")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        p = person(c, code, authorization)
        active(c, code, body.step)
        c.execute(
            "INSERT OR REPLACE INTO statuses VALUES(?,?,?,?)",
            (body.step, p["id"], body.state, time.time()),
        )
    return {"ok": True}


@app.post("/api/rooms/{code}/issues")
def issue(code: str, body: IssueInput, authorization: str = Header(default="")):
    title = body.title.strip()
    if not title:
        fail("请输入卡点")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        p = person(c, code, authorization)
        active(c, code, body.step)
        existing = c.execute(
            "SELECT id FROM issues WHERE step=? AND title=?", (body.step, title)
        ).fetchone()
        iid = existing["id"] if existing else secrets.token_urlsafe(12)
        if not existing:
            c.execute(
                "INSERT INTO issues(id,step,title) VALUES(?,?,?)",
                (iid, body.step, title),
            )
        c.execute("UPDATE issues SET resolved=0 WHERE id=?", (iid,))
        c.execute("INSERT OR IGNORE INTO votes VALUES(?,?)", (iid, p["id"]))
        c.execute(
            "INSERT OR REPLACE INTO statuses VALUES(?,?,?,?)",
            (body.step, p["id"], "stuck", time.time()),
        )
    return {"ok": True}


@app.post("/api/rooms/{code}/issues/{iid}/{action}")
def issue_action(
    code: str, iid: str, action: str, authorization: str = Header(default="")
):
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        i = c.execute(
            "SELECT issues.* FROM issues JOIN steps ON steps.id=issues.step WHERE issues.id=? AND steps.room=?",
            (iid, code),
        ).fetchone()
        if not i:
            fail("卡点不存在", 404)
        active(c, code, i["step"])
        if action == "resolve":
            host(c, code, authorization)
            c.execute("UPDATE issues SET resolved=1 WHERE id=?", (iid,))
        elif action in ("agree", "withdraw"):
            p = person(c, code, authorization)
            if action == "agree":
                c.execute("INSERT OR IGNORE INTO votes VALUES(?,?)", (iid, p["id"]))
                c.execute("UPDATE issues SET resolved=0 WHERE id=?", (iid,))
                c.execute(
                    "INSERT OR REPLACE INTO statuses VALUES(?,?,?,?)",
                    (i["step"], p["id"], "stuck", time.time()),
                )
            else:
                c.execute(
                    "DELETE FROM votes WHERE issue=? AND person=?", (iid, p["id"])
                )
        else:
            fail("未知操作")
    return {"ok": True}


@app.post("/api/rooms/{code}/close")
def close(code: str, authorization: str = Header(default="")):
    with db() as c:
        host(c, code, authorization)
        c.execute("UPDATE rooms SET closed=1 WHERE code=?", (code,))
    return {"ok": True}


@app.delete("/api/rooms/{code}")
def delete(code: str, authorization: str = Header(default="")):
    with db() as c:
        host(c, code, authorization)
        c.execute("DELETE FROM rooms WHERE code=?", (code,))
    return {"ok": True}


@app.get("/api/rooms/{code}/export")
def export(code: str, authorization: str = Header(default="")):
    with db() as c:
        host(c, code, authorization)
        result = snapshot(c, code, authorization)
    return Response(
        json.dumps(result, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="workshop.json"'},
    )


@app.get("/qr")
def qr(request: Request):
    # Only encode this installation's join page; no external QR service.
    code = request.query_params.get("code", "")
    if len(code) != 6 or not code.isalnum():
        fail("房间码无效")
    origin = os.getenv("PUBLIC_URL", str(request.base_url).rstrip("/")).rstrip("/")
    img = qrcode.make(
        origin + "/?room=" + code, image_factory=qrcode.image.svg.SvgPathImage
    )
    b = io.BytesIO()
    img.save(b)
    return Response(b.getvalue(), media_type="image/svg+xml")


@app.get("/api/rooms/{code}/events")
async def events(code: str, request: Request, authorization: str = Header(default="")):
    with db() as c:
        snapshot(c, code, authorization)

    async def stream():
        previous = None
        while not await request.is_disconnected():
            try:
                with db() as c:
                    state = snapshot(c, code, authorization)
                payload = json.dumps(state, ensure_ascii=False)
                if payload != previous:
                    yield "data: " + payload + "\n\n"
                    previous = payload
                else:
                    yield ": keepalive\n\n"
            except HTTPException:
                yield "event: gone\ndata: {}\n\n"
                return
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
    )


app.mount(
    "/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="web"
)
