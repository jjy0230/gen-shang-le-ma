# v0.3 升级、备份与回退

升级前停止服务并保留整个 SQLite 数据库。首次打开旧库会自动生成 `数据库路径.pre-v3.bak`，再追加兼容字段和表，不清空旧数据。备份也含凭证和昵称，不要上传 GitHub。

旧步骤按创建顺序迁移，旧统计尽可能按当时加入范围冻结。旧版本未记录问题恢复或完整状态事件，不能补造历史恢复时间；新增前序步骤跟进记录从新版推进时开始。

可在线使用 Python sqlite3 backup API 备份：

```python
from storage import Database
Database('data/workshop.sqlite3').backup('data/manual-backup.sqlite3')
```

恢复须先停止服务，用备份替换数据库并移除对应旧 WAL/SHM（确保服务已停止并先保留原文件），再启动验证。不要用直接复制正在写入的单个 SQLite 文件作为备份。

源码回退点为本地分支 `backup/pre-v030`，公开旧版本为 GitHub `v0.1.0`。回退应另建旧版检出目录并使用升级前数据库副本启动，避免旧代码操作新增的数据。当前工作区中不要使用 `git reset --hard` 丢弃后续改动。

修改范围：app.py、storage.py、workshop.py、static/、tests/、Dockerfile 和交付文档。未重置用户数据。
