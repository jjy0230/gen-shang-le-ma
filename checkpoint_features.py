"""Preparation checkpoints and participant-selected platform help."""

import json
from workshop import text, link, fail

PLATFORMS = ("unspecified", "windows", "macos", "linux", "other")


def options(data):
    source = data.get("options", {})
    if not isinstance(source, dict):
        fail("步骤选项格式无效")
    kind = source.get("kind", "practice")
    if kind not in ("practice", "precheck"):
        fail("检查点类型无效")
    branches = source.get("environment_help", {})
    if not isinstance(branches, dict) or set(branches) - set(PLATFORMS):
        fail("环境帮助格式无效")
    help_data = {}
    for platform, entry in branches.items():
        if not isinstance(entry, dict):
            fail("环境帮助格式无效")
        help_data[platform] = {"body": text(entry, "body", 1500), "link": link(entry)}
    return {
        "kind": kind,
        "optional_task": text(source, "optional_task", 1500),
        "environment_help": help_data,
    }


def apply(service, c, r, a, action, data):
    if action == "person.environment":
        service.permit(a, "person")
        environment = data.get("environment", "unspecified")
        if environment not in PLATFORMS:
            fail("环境类型无效")
        c.execute("UPDATE people SET environment=? WHERE id=?", (environment, a["id"]))
        return {}
    return None
