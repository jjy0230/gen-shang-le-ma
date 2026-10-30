"""HTTP entry points. Run one worker; SQLite is the authority for every write."""

import asyncio
import csv
import io
import json
import os
import time
from pathlib import Path
from collections import defaultdict, deque

from fastapi import FastAPI, Header, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import qrcode
import qrcode.image.svg

from storage import Database
from workshop import Workshop, fail

DB = os.getenv("DATABASE_PATH", "data/workshop.sqlite3")
database = Database(DB)
service = Workshop(database)
db = database.connect
app = FastAPI(title="跟上了吗", version="0.3.0")
buckets = {}


class RoomInput(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    expected: int = Field(default=0, ge=0, le=200)


class PersonInput(BaseModel):
    name: str = Field(default="", max_length=24)
    seat: str = Field(default="", max_length=16)


class StepInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    criteria: str = Field(default="", max_length=1000)
    link: str = Field(default="", max_length=1000)
    reasons: list[str] = Field(default_factory=list, max_length=12)


class StateInput(BaseModel):
    step: str
    state: str
    version: int | None = None


class IssueInput(BaseModel):
    step: str
    title: str = Field(min_length=1, max_length=120)


class Command(BaseModel):
    action: str = Field(max_length=40)
    data: dict = Field(default_factory=dict)
    request_id: str = Field(min_length=8, max_length=100)


class Recovery(BaseModel):
    token: str = Field(min_length=20, max_length=200)


@app.middleware("http")
async def protections(request: Request, call_next):
    if request.method in ("POST", "DELETE"):
        if request.url.path.endswith("/heartbeat"):
            category, maximum = "heartbeat", 12
        elif (
            request.url.path.endswith("/join")
            or request.url.path == "/api/rooms"
            or request.url.path.endswith("/recover")
        ):
            category, maximum = "entry", 300
        else:
            category, maximum = "write", 180
        # Unknown/bogus credentials cannot consume a real participant's quota.
        token = request.headers.get("authorization", "")
        key = (
            category,
            token
            if token and category != "entry"
            else (request.client.host if request.client else "unknown"),
        )
        now = time.monotonic()
        if len(buckets) > 20000:
            for k in list(buckets):
                if not buckets[k] or now - buckets[k][-1] >= 60:
                    del buckets[k]
            if len(buckets) > 20000:
                return Response("繁忙，请稍后重试", 429)
        hits = buckets.setdefault(key, deque())
        while hits and now - hits[0] >= 60:
            hits.popleft()
        if len(hits) >= maximum:
            return Response(
                json.dumps({"detail": "操作过于频繁，请稍后重试"}),
                429,
                media_type="application/json",
                headers={"Retry-After": "60"},
            )
        hits.append(now)
        try:
            size = int(request.headers.get("content-length", "0"))
        except ValueError:
            return Response("请求格式无效", 400)
        if size > 131072:
            return Response("请求过大", 413)
        if request.url.path.startswith("/api/"):
            body = await request.body()
            if len(body) > 131072:
                return Response("请求过大", 413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    return response


@app.get("/health")
def health():
    with db() as c:
        c.execute("SELECT 1")
    return {"ok": True, "version": "0.3.0"}


@app.post("/api/rooms")
def create(body: RoomInput):
    return service.create(body.title, body.expected)


@app.post("/api/rooms/{code}/join")
def join(code: str, body: PersonInput):
    return service.join(code, body.name, body.seat)


@app.post("/api/rooms/{code}/recover")
def recover(code: str, body: Recovery):
    with db() as c:
        a = service.auth(c, code, body.token)
        service.permit(a, "host", "helper", "screen", "person")
    return {"ok": True, "role": a["role"]}


@app.get("/api/rooms/{code}")
def get(code: str, authorization: str = Header(default="")):
    return service.snapshot(code, authorization)


@app.post("/api/rooms/{code}/heartbeat")
def heartbeat(code: str, authorization: str = Header(default="")):
    return service.heartbeat(code, authorization)


@app.post("/api/rooms/{code}/commands")
def commands(code: str, body: Command, authorization: str = Header(default="")):
    if (
        body.action in ("status", "issue.recover", "issue.still", "issue.claim")
        and "version" not in body.data
    ):
        fail("此操作需要当前状态版本", 409)
    return service.command(code, authorization, body.action, body.data, body.request_id)


# Compatibility for the original API. New UI uses versioned, idempotent commands.
@app.post("/api/rooms/{code}/steps")
def step(code: str, body: StepInput, authorization: str = Header(default="")):
    return service.command(code, authorization, "step.publish", body.model_dump())


@app.post("/api/rooms/{code}/status")
def status(code: str, body: StateInput, authorization: str = Header(default="")):
    return service.command(
        code, authorization, "status", body.model_dump(exclude_none=True)
    )


@app.post("/api/rooms/{code}/issues")
def issue(code: str, body: IssueInput, authorization: str = Header(default="")):
    return service.command(code, authorization, "issue.add", body.model_dump())


@app.post("/api/rooms/{code}/issues/{iid}/{action}")
def issue_action(
    code: str, iid: str, action: str, authorization: str = Header(default="")
):
    if action not in ("agree", "withdraw", "resolve"):
        fail("未知操作")
    return service.command(code, authorization, "issue." + action, {"id": iid})


@app.post("/api/rooms/{code}/close")
def close(code: str, authorization: str = Header(default="")):
    return service.command(code, authorization, "room.close", {})


@app.delete("/api/rooms/{code}")
def delete(code: str, authorization: str = Header(default="")):
    return service.command(code, authorization, "room.delete", {})


@app.get("/api/rooms/{code}/template")
def template(code: str, authorization: str = Header(default="")):
    with db() as c:
        service.permit(service.auth(c, code, authorization), "host")
        return service.template(c, code)


@app.get("/api/rooms/{code}/report")
def report(code: str, authorization: str = Header(default="")):
    with db() as c:
        service.permit(service.auth(c, code, authorization), "host")
    state = service.snapshot(code, authorization)
    rows = []
    for s in state["steps"]:
        if s["phase"] == "draft":
            continue
        rows.append(
            {
                "step": s["title"],
                "criteria": s["criteria"],
                "total": s["total"],
                **s["counts"],
                "seconds": max(
                    0, int((s["ended"] or time.time()) - (s["opened"] or time.time()))
                ),
                "issues": [
                    {
                        "title": i["title"],
                        "ever_affected": i["count"],
                        "still_affected": i["affected"],
                        "recovered": i["recovered"],
                        "answers": i["answers"],
                    }
                    for i in s["issues"]
                ],
            }
        )
    with db() as c:
        events = [
            dict(x)
            for x in c.execute(
                "SELECT kind,entity,actor,created,detail FROM events WHERE room=? ORDER BY id",
                (code,),
            )
        ]
    return {
        "title": state["title"],
        "code": code,
        "closed": state["closed"],
        "expected": state["expected"],
        "active_people": state["total"],
        "joined_total": state["joined_total"],
        "steps": rows,
        "events": events,
        "note": "人数为主动反馈；耗时不代表理解程度；历史统计在推进时冻结。",
    }


@app.get("/api/rooms/{code}/export")
def export(code: str, authorization: str = Header(default=""), format: str = "json"):
    result = report(code, authorization)
    if format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(
            [
                "步骤",
                "完成标准",
                "人数",
                "完成",
                "进行中",
                "卡住",
                "未反馈",
                "秒数",
                "卡点",
                "累计遇到",
                "仍受影响",
                "已恢复",
                "处理办法",
            ]
        )

        def cell(value):
            s = str(value)
            return (
                "'" + s
                if s.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
                else s
            )

        for s in result["steps"]:
            for i in s["issues"] or [{}]:
                writer.writerow(
                    [
                        cell(x)
                        for x in [
                            s["step"],
                            s["criteria"],
                            s["total"],
                            s["done"],
                            s["working"],
                            s["stuck"],
                            s["waiting"],
                            s["seconds"],
                            i.get("title", ""),
                            i.get("ever_affected", ""),
                            i.get("still_affected", ""),
                            i.get("recovered", ""),
                            " | ".join(x["body"] for x in i.get("answers", [])),
                        ]
                    ]
                )
        return Response(
            "\ufeff" + stream.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="workshop.csv"'},
        )
    if format != "json":
        fail("仅支持 json 或 csv")
    # Keep the v0.1 export shape while adding readable report data; credentials are never exported.
    result = {**service.snapshot(code, authorization), "report": result}
    return Response(
        json.dumps(result, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="workshop.json"'},
    )


@app.get("/qr")
def qr(request: Request):
    code = request.query_params.get("code", "")
    if len(code) != 6 or not code.isascii() or not code.isalnum():
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
    await asyncio.to_thread(service.snapshot, code, authorization)

    async def stream():
        previous = None
        while not await request.is_disconnected():
            try:
                state = await asyncio.to_thread(service.snapshot, code, authorization)
                signature = (state["revision"], int(time.time() / 10))
                if signature != previous:
                    yield "data: " + json.dumps(state, ensure_ascii=False) + "\n\n"
                    previous = signature
                else:
                    yield ": keepalive\n\n"
            except Exception as exc:
                from fastapi import HTTPException

                if not isinstance(exc, HTTPException):
                    raise
                yield (
                    "event: gone\ndata: " + json.dumps({"detail": exc.detail}) + "\n\n"
                )
                return
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
    )


app.mount(
    "/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="web"
)
