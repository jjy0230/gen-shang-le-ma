# G01–G14 完成审计（2026-11-04）

结论：总目标尚未完成。已发布 v0.5.0，开发功能和本机证据不能替代公开入口、真实跨设备和课堂试用。

本次核对了 roadmap.md 的验收定义、测试函数、verification 下的实际报告和 GitHub 发布/任务状态。浏览器验收沿用此前已记录的操作与截图，本次未重新执行手机或课堂测试。

| 目标 | 判定 | 可复核证据与缺口 |
| --- | --- | --- |
| G01 在线体验 | 用户暂缓 | demo 测试覆盖关闭默认入口、独立库和配额；没有公开地址。用户明确不公开部署，不能勾选。 |
| G02 原生部署 | 部分验证 | native-smoke-20261031.json 记录新虚拟环境、HTTP/SSE、重启和恢复；未验证真实手机扫码跨设备。 |
| G03 升级恢复 | 已有证据 | test_upgrade_real_v030_schema_and_restore_permissions 验证历史完成状态、主持/参与/助教凭证及恢复；原生恢复报告在 verification。 |
| G04 CI | 已有证据 | GitHub CI 41845122365 和 7369346 对应 CI 已通过；检查后端回归、JS 与原生健康。 |
| G05 多人故障 | 部分验证 | 旧版 30 分钟报告已归档；新版持续运行 41845196735 尚待终态。同步探针 300 样本 P95 1.035 秒；幂等、并发推进、迟到请求和前端输入保留有回归。真实浏览器网络恢复、手机锁屏及弱网仍未验证。 |
| G06 真实试用 | 外部依赖 | 用户确认暂时无活动和设备。trial-kit.md 是待执行表格，没有真实反馈率或复用结果。 |
| G07 课前自检 | 开发验收通过 | test_preparation_platform_and_optional_task_roundtrip；已记录双端自检发布与状态操作，截图 v040-checkpoints.png。 |
| G08 环境分流 | 开发验收通过 | 上述选项往返及 test_private_details_and_environment_answers；按环境最新澄清答复确认另有回归，已记录环境帮助浏览器流程。 |
| G09 要求澄清 | 开发验收通过 | test_clarification_never_changes_operation_progress 和按环境答复确认；已记录双端提问、答复、确认，不计操作失败。 |
| G10 求助补充 | 开发验收通过 | test_private_details_and_environment_answers 与跨房间/角色测试；已记录先求助后补错误及尝试信息，非工作人员不可见私密补充。 |
| G11 更正确认 | 开发验收通过 | test_correction_preserves_progress_and_requires_latest_ack 验证保留进度、历史版本、拒绝旧确认；浏览器更正与确认有既有记录。 |
| G12 可选任务 | 开发验收通过 | 选项往返回归；已记录完成后展开任务的浏览器操作，主线统计独立。 |
| G13 暂不参与 | 开发验收通过 | test_skip_denominator_approval_and_frozen_history 验证批准前后分母与冻结统计；浏览器申请/批准有既有记录。 |
| G14 进度确认 | 开发验收通过 | test_pulse_is_rate_limited_idempotent_and_preserves_done 验证频率、幂等与完成状态；浏览器发起/确认已记录。 |

发布：v0.5.0 对应 049716e；后续提交增加验证脚本、回归及验收文档。持续测试 41845196735 固定在 a77f1a2，之后的测试/文档提交不改变产品代码。不得把尚未归档的运行称为成功。

后续只在任务结束后读取最终产物；真实条件具备时按 trial-kit.md 补验。没有待验证证据时不通过增加无关功能来声称目标完成。
