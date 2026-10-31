"""Real HTTP/SSE soak test in an isolated subprocess and temporary database."""

import argparse, asyncio, json, os, statistics, subprocess, sys, tempfile, time, uuid
from pathlib import Path
import httpx


async def run(seconds, output):
    latencies = []
    sync = []
    errors = []
    frames = 0
    reconnects = 0
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="pulse-soak-") as tmp:
        env = {**os.environ, "DATABASE_PATH": str(Path(tmp) / "soak.sqlite3")}
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "18081",
                "--log-level",
                "error",
            ],
            cwd=root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            async with httpx.AsyncClient(
                base_url="http://127.0.0.1:18081",
                timeout=20,
                limits=httpx.Limits(max_connections=180, max_keepalive_connections=100),
            ) as client:
                for _ in range(80):
                    try:
                        if (await client.get("/health")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.25)

                async def post(path, data, token=""):
                    started = time.perf_counter()
                    r = await client.post(
                        path, json=data, headers={"Authorization": token}
                    )
                    latencies.append(time.perf_counter() - started)
                    if r.status_code != 200:
                        errors.append(
                            {"path": path.split("/")[-1], "status": r.status_code}
                        )
                        r.raise_for_status()
                    return r.json()

                room = await post("/api/rooms", {"title": "Isolated load validation"})
                base = "/api/rooms/" + room["code"]
                await post(base + "/steps", {"title": "Load checkpoint"}, room["token"])
                state = (
                    await client.get(base, headers={"Authorization": room["token"]})
                ).json()
                sid = state["current"]
                tokens = [
                    (await post(base + "/join", {"name": f"Load {n}"}))["token"]
                    for n in range(60)
                ]
                running = True
                active = 10

                async def stream(n, token):
                    nonlocal frames, reconnects
                    while running:
                        if n >= active:
                            await asyncio.sleep(1)
                            continue
                        try:
                            async with client.stream(
                                "GET",
                                base + "/events",
                                headers={"Authorization": token},
                                timeout=30,
                            ) as r:
                                r.raise_for_status()
                                opened = time.monotonic()
                                async for line in r.aiter_lines():
                                    if line.startswith("data: "):
                                        data = json.loads(line[6:])
                                        frames += 1
                                        sync.append(
                                            max(0, time.time() - data["server_time"])
                                        )
                                    if not running or n >= active:
                                        break
                                    if time.monotonic() - opened > 120:
                                        reconnects += 1
                                        break
                        except Exception as exc:
                            if running:
                                errors.append(
                                    {"stream": n, "error": type(exc).__name__}
                                )
                            await asyncio.sleep(1)

                tasks = [
                    asyncio.create_task(stream(n, t)) for n, t in enumerate(tokens)
                ]
                started = time.monotonic()
                cycle = 0
                last_report = 0
                while time.monotonic() - started < seconds:
                    elapsed = time.monotonic() - started
                    active = [10, 30, 60][min(2, int(elapsed / max(1, seconds / 3)))]
                    cycle += 1

                    async def tick(t):
                        await post(base + "/heartbeat", {}, t)
                        await post(
                            base + "/status",
                            {
                                "step": sid,
                                "state": ["working", "done", "stuck"][cycle % 3],
                            },
                            t,
                        )

                    results = await asyncio.gather(
                        *(tick(t) for t in tokens[:active]), return_exceptions=True
                    )
                    for x in results:
                        if isinstance(x, Exception):
                            errors.append({"write": type(x).__name__})
                    if elapsed - last_report >= 60:
                        print(
                            json.dumps(
                                {
                                    "elapsed": round(elapsed),
                                    "active": active,
                                    "frames": frames,
                                    "errors": len(errors),
                                }
                            ),
                            flush=True,
                        )
                        last_report = elapsed
                    await asyncio.sleep(
                        min(15, max(0, seconds - (time.monotonic() - started)))
                    )
                running = False
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                result = {
                    "duration_seconds": round(time.monotonic() - started, 1),
                    "identities": 60,
                    "phases": [10, 30, 60],
                    "http_requests": len(latencies),
                    "sse_frames": frames,
                    "planned_reconnects": reconnects,
                    "http_p95_seconds": sorted(latencies)[int(len(latencies) * 0.95)],
                    "snapshot_to_receive_p95_seconds": sorted(sync)[
                        int(len(sync) * 0.95)
                    ]
                    if sync
                    else None,
                    "errors": errors,
                    "limits": "SSE latency measures snapshot serialization to receive, not mutation-to-observer latency. Loopback synthetic clients, not real phones/network.",
                }
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                Path(output).write_text(json.dumps(result, indent=2), encoding="utf-8")
                print(json.dumps(result), flush=True)
                if errors or not frames:
                    raise RuntimeError("Soak test reported errors or no SSE frames; see output JSON")
        finally:
            server.terminate()
            server.wait(timeout=20)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=int, default=1800)
    p.add_argument("--output", default="data/soak-result.json")
    a = p.parse_args()
    asyncio.run(run(a.seconds, a.output))
