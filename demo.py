"""Isolated, explicitly synthetic demo rooms; disabled on normal installations."""

import time
from workshop import fail


def create_demo(service):
    with service.database.connect(True) as c:
        c.execute("DELETE FROM rooms WHERE created<?", (time.time() - 86400,))
        if c.execute("SELECT count(*) FROM rooms").fetchone()[0] >= 100:
            fail("演示房间暂满，请稍后再试", 429)
    room = service.create("演示带练 · Python 安装（模拟参与者）", 3)
    code = room["code"]
    host = room["token"]
    first = service.command(
        code,
        host,
        "step.publish",
        {
            "title": "课前：下载并确认安装包",
            "criteria": "下载完成，能找到安装文件",
            "options": {
                "kind": "precheck",
                "optional_task": "提前打开终端窗口",
                "environment_help": {
                    "windows": {
                        "body": "选择适合 Windows 的官方安装包",
                        "link": "https://www.python.org/downloads/",
                    }
                },
            },
        },
    )["id"]
    people = [service.join(code, "模拟参与者 " + str(i + 1))["token"] for i in range(3)]
    for token, status in zip(people, ["done", "working", "stuck"]):
        service.command(code, token, "status", {"step": first, "state": status})
    issue = service.command(
        code, people[2], "issue.add", {"step": first, "title": "演示：下载中断"}
    )["id"]
    service.command(
        code,
        host,
        "issue.answer",
        {
            "id": issue,
            "body": "这是演示答复：请检查网络后重新下载。确认恢复后再点完成。",
        },
    )
    service.command(
        code,
        host,
        "step.add",
        {"title": "安装并验证版本", "criteria": "在新的终端查看 Python 版本号"},
    )
    return {**room, "participant_token": people[2]}
