"""Private participant handoff and host follow-up export."""

def participant_handoff(service, code, token):
    with service.database.connect() as c:
        actor = service.auth(c, code, token)
        service.permit(actor, "person")
    state = service.snapshot(code, token)
    tails = {x["step"] for x in state.get("tails", []) if not x["recovered"]}
    items = []
    for s in state["steps"]:
        own = [
            i
            for i in s["issues"]
            if i["mine"]
            and (
                (i["kind"] == "blocker" and i["my_active"])
                or (i["kind"] == "clarification" and not i.get("my_ack"))
            )
        ]
        if (
            s["id"] in tails
            or (
                s["id"] == state["current"]
                and s["mine"] != "done"
                and not any(x["decision"] == "approved" for x in s["skips"])
            )
            or own
        ):
            items.append(
                {
                    "step": s["title"],
                    "criteria": s["criteria"],
                    "state": s["mine"],
                    "needs_catchup": s["id"] in tails,
                    "issues": [
                        {
                            "title": i["title"],
                            "kind": i["kind"],
                            "answers": [
                                {"body": a["body"], "link": a["link"]}
                                for a in i["answers"]
                            ],
                        }
                        for i in own
                    ],
                }
            )
    return {
        "title": state["title"],
        "closed": state["closed"],
        "expires_at": state["handoff_until"] or None,
        "items": items,
        "notice": "结束后参与者访问保留 7 天；房间删除、身份撤销或正式离开会提前失效。导出文件由你自行保管。",
    }


def host_followup(service, code, token):
    with service.database.connect() as c:
        actor = service.auth(c, code, token)
        service.permit(actor, "host")
    state = service.snapshot(code, token)
    entries = {}

    def add(pid, name, step, reason):
        entries.setdefault(pid, {"name": name, "items": []})["items"].append(
            {"step": step, "reason": reason}
        )

    for tail in state["tails"]:
        if not tail["recovered"]:
            add(tail["person"], tail["name"], tail["title"], "前序步骤尚未确认跟上")
    for s in state["steps"]:
        if s["id"] == state["current"]:
            for p in s["people"]:
                if not p.get("excluded") and p["state"] != "done":
                    add(
                        p["id"],
                        p["name"],
                        s["title"],
                        {
                            "waiting": "本步未反馈",
                            "working": "本步进行中",
                            "stuck": "本步受阻",
                        }[p["state"]],
                    )
        for i in s["issues"]:
            if i["kind"] == "clarification":
                for p in i.get("members", []):
                    if p["left_at"]:
                        continue
                    with service.database.connect() as c:
                        environment = c.execute(
                            "SELECT environment FROM people WHERE id=?", (p["person"],)
                        ).fetchone()[0]
                        answer = c.execute(
                            "SELECT id FROM answers WHERE issue=? AND environment IN ('unspecified',?) ORDER BY created DESC,rowid DESC LIMIT 1",
                            (i["id"], environment),
                        ).fetchone()
                        acknowledged = (
                            answer
                            and c.execute(
                                "SELECT 1 FROM acknowledgements WHERE kind='clarification' AND entity=? AND person=?",
                                (answer[0], p["person"]),
                            ).fetchone()
                        )
                    if not acknowledged:
                        add(
                            p["person"],
                            p["name"],
                            s["title"],
                            "要求澄清：" + i["title"],
                        )
            if i["kind"] == "blocker":
                for p in i.get("members", []):
                    if p["active"] and not p["left_at"]:
                        add(p["person"], p["name"], s["title"], i["title"])
    return {
        "title": state["title"],
        "closed": state["closed"],
        "people": list(entries.values()),
        "notice": "仅供主持后续协助，不代表成绩或能力评价。",
    }
