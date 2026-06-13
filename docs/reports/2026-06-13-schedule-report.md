# Schedule 定时任务 修改报告

## 变更摘要

为 MailCode 新增定时任务子系统 `mailcode schedule`。在 `mailcode serve` 守护进程中挂载 Scheduler 线程, 按用户配置的 interval / daily / weekly / monthly 四种调度触发, 调用 Claude 单轮推理后将结果通过 SMTP 邮件发送给用户。同时抽出 `call_claude` 到公共模块避免双份维护。

共 **13 个文件** (6 新建 / 7 修改), 单元测试从 ~209 增加到 **273 个** (新增 64 个测试, 测试覆盖率足够)。

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| A | mailcode/utils/claude_runner.py | 抽出 call_claude 函数 + CLAUDE_TIMEOUT_SECONDS 常量 |
| A | mailcode/relay/scheduler.py | Scheduler 线程 + ScheduleSpec/Task 数据类 + ScheduleStore 加锁 CRUD + parse_schedule/compute_next_run 纯函数 |
| A | mailcode/schedule_cli.py | 8 个 CLI 子命令 (list/show/add/enable/disable/delete/run-now/validate) |
| A | tests/unit/test_claude_runner.py | 7 个 call_claude 测试 (从 test_conversation_handler 迁移) |
| A | tests/unit/test_scheduler.py | 36 个 scheduler 测试 (parse/compute_next_run/store/lifecycle/trigger) |
| A | tests/unit/test_schedule_cli.py | 20 个 CLI 测试 (8 命令 × happy + error path) |
| M | mailcode/relay/conversation_handler.py | 改 import `from mailcode.utils.claude_runner import call_claude`, 删除本地实现 |
| M | mailcode/relay/stateless_handler.py | 改 import 路径指向 claude_runner |
| M | mailcode/cli.py | 加 `schedule` 子解析器 + `cmd_schedule` 路由 + main 分支 |
| M | mailcode/server.py | run_serve 注入 Scheduler 线程, signal_handler 同步停, try/finally join |
| M | mailcode/config.py | 加 SCHEDULE_DEFAULTS + get_schedule_config + validate_serve_config 可选校验 |
| M | mailcode/resources/default.json | 顶层加 schedule 配置段 |
| M | tests/unit/test_conversation_handler.py | 删 TestCallClaude, patch 路径从 ch_module 改为 cr_module |

## 测试结果

| 测试套件 | 结果 |
|---------|------|
| tests/unit/ | ✅ 273 passed |
| tests/unit/test_claude_runner.py | ✅ 7 passed |
| tests/unit/test_scheduler.py | ✅ 36 passed |
| tests/unit/test_schedule_cli.py | ✅ 20 passed |
| tests/unit/test_conversation_handler.py | ✅ 74 passed (无回归) |
| ruff check mailcode/ tests/ | ✅ All checks passed |

## 关键决策

- **调度算法**: 复用现有 `_now_local()` (datetime.now().astimezone())返回 offset-aware datetime，与 `_parse_dt` ／ `_format_dt` 保持一致，避免 naive/aware 比较时报错。`_format_dt` 输出 ISO8601 含时区偏移，`_parse_dt` 解析后保持 aware 状态
- **触发策略**: 错过不补跑 (skip)，避免邮件风暴。跑漏的任务用 `mailcode schedule run-now <name>` 手动触发
- **并发安全**: `_STORE_LOCK` 用 `threading.RLock` 防止同线程内 save → load 自死锁；Scheduler 用 `_running_task_ids` set 防止同任务重复触发
- **call_claude 抽取**: `test_conversation_handler.py` 原有 `patch.object(ch_module, "call_claude")` 共 23 处，全部迁移为 `patch.object(cr_module, "call_claude")`。`conversation_handler` 内部改为用 `cr_module.call_claude(...)` 属性访问而非裸名导入，确保 patch 有效
- **配置热更新**: Scheduler 每 tick 重读 `ScheduleStore.list_tasks()`，CLI 增删立即生效，无需 IPC 通知

## 没做的事 (v2 候选)

- 集成测试 (需真实 SMTP + Claude, 当前 `tests/integration/` 目录不存在)
- `systemd timer` / `launchd plist` 独立守护进程
- PID 文件锁防止多 serve 实例并发
- 任务级超时配置 (claude_timeout_seconds per-task)
- 绑定 session 模式 (`schedule add --into-session`)
- IPv6 / SOCKS5 代理适配

## MANUAL_ACK_REQUIRED

以下为需用户手工勾选确认项:

- [ ] 加 60s interval 任务 `mailcode schedule add demo --type interval --interval-seconds 60 --prompt "现在几点？" --to-email your@email.com` → 启动 `mailcode serve` → 60s 内收到邮件
- [ ] `mailcode schedule run-now demo` 不开 serve 也能触发, 收件箱立刻收到邮件
- [ ] Ctrl-C 后 scheduler 优雅退出, `relay.log` 出现 "Scheduler stopped"
- [ ] daily/weekly/monthly 跨边界行为符合预期 (2 月没 31 号跳过到 3 月)
- [ ] `mailcode schedule list` / `show` / `add` / `enable` / `disable` / `delete` / `validate` 输出格式符合设计
