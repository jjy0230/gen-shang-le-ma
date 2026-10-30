from test_complete_flow import cmd


def test_preparation_platform_and_optional_task_roundtrip(env):
    _, c, b, h, join, _ = env
    p = join()
    options = {
        "kind": "precheck",
        "optional_task": "试运行一行代码",
        "environment_help": {
            "windows": {"body": "Windows 操作", "link": "https://www.python.org/"},
            "macos": {"body": "macOS 操作"},
        },
    }
    added = cmd(c, b, h, "step.add", {"title": "课前准备", "options": options})
    assert added.status_code == 200, added.text
    sid = added.json()["id"]
    assert cmd(c, b, h, "step.start", {"id": sid}).status_code == 200
    assert (
        cmd(c, b, p, "person.environment", {"environment": "windows"}).status_code
        == 200
    )
    view = c.get(b, headers=p).json()
    assert view["me"]["environment"] == "windows"
    assert view["steps"][0]["options"]["kind"] == "precheck"
    assert view["steps"][0]["options"]["optional_task"] == "试运行一行代码"
    assert c.get(b, headers=h).json()["environments"]["windows"] == 1
    template = c.get(b + "/template", headers=h).json()
    assert template["steps"][0]["options"]["kind"] == "precheck"
    copied = cmd(c, b, h, "room.copy").json()
    new = c.get(
        "/api/rooms/" + copied["code"], headers={"Authorization": copied["token"]}
    ).json()
    assert (
        new["steps"][0]["options"]["environment_help"]["windows"]["body"]
        == "Windows 操作"
    )
    assert new["total"] == 0
    assert (
        cmd(c, b, p, "person.environment", {"environment": "macos"}).status_code == 200
    )
    assert c.get(b, headers=p).json()["steps"][0]["mine"] == "waiting"


def test_platform_validation_and_options_atomicity(env):
    _, c, b, h, join, _ = env
    p = join()
    assert (
        cmd(
            c,
            b,
            h,
            "step.add",
            {
                "title": "错误帮助链接",
                "options": {
                    "environment_help": {"windows": {"link": "javascript:alert(1)"}}
                },
            },
        ).status_code
        == 400
    )
    assert c.get(b, headers=h).json()["steps"] == []
    assert (
        cmd(
            c, b, p, "person.environment", {"environment": "detected-secret"}
        ).status_code
        == 400
    )
    assert (
        cmd(
            c, b, p, "step.add", {"title": "越权", "options": {"kind": "precheck"}}
        ).status_code
        == 403
    )
