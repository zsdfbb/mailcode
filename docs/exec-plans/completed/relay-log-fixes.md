# Relay Log 修复 — 执行计划

## 任务清单

### T1: claude_runner stdin 改造

- type: impl
- file: `mailcode/utils/claude_runner.py`
- 变更: `-p` → `input=prompt`，更新 log 消息

### T2: claude_runner stdin 测试

- type: test
- file: `tests/unit/test_claude_runner.py`
- 变更: 更新 `test_success` 断言，新增 `test_multiline_prompt`、`test_stdin_instead_of_dash_p`

### T3: _wait_for_idle_old 超时清理

- type: impl
- file: `mailcode/relay/email_listener.py`
- 变更: `_wait_for_idle_old()` 中 `got_event=False` 时 clear + idle_done + join

### T4: _wait_for_idle_old 清理测试

- type: test
- file: `tests/unit/test_listener_lifecycle.py`
- 变更: 新增 `test_wait_for_idle_old_cleans_up_on_timeout`，mock mail 验证 idle_done 调用 + 线程退出

### T5: 预判性重连

- type: impl
- file: `mailcode/relay/email_listener.py`
- 变更: `__init__` 加 `FORCED_RECONNECT_INTERVAL` + `_last_connect_time`；`_listen_idle` 中在 NOOP 检查后加入阈值检查 + 主动重连；每次重连后更新 `_last_connect_time`

### T6: 预判重连测试

- type: test
- file: `tests/unit/test_listener_lifecycle.py`
- 变更: 新增 `test_forced_reconnect_triggers_after_interval`、`test_forced_reconnect_skipped_when_got_event`

### T7: NOOP flush 同步

- type: impl
- file: `mailcode/relay/email_listener.py`
- 变更: `_listen_idle` 的 got_event 重连 + backoff 重连后加 `mail.noop()`；`fetch_unread_emails` 中 `mail.select("INBOX")` 后加 `mail.noop()`

### T8: NOOP flush 测试

- type: test
- file: `tests/unit/test_listener_lifecycle.py`
- 变更: 新增 `test_noop_after_select_in_fetch`，`test_noop_after_reconnect_select`

### T9: 全量回归

- type: test
- 命令: `source .venv/bin/activate && python3 -m pytest tests/unit/ -q && python3 -m ruff check mailcode/ tests/`

## 执行顺序

批次 1（无冲突，可并发）:
- T1 + T2 (claude_runner)

批次 2（同一文件，串行或按依赖）:
- T3 (impl) → T4 (test)
- T5 (impl) → T6 (test)
- T7 (impl) → T8 (test)

批次 3:
- T9 (回归)

强制要求:
1. **必须**真正调用 Write 工具 — 不要把 markdown 当作回复文本返回
2. 调用后, 用 Read 工具回读一次, 确认内容完整
3. 在你的回复里报告 Write 是否成功、文件大小

Plan mode 退化分支:
- 如果你的 Write 工具不可用 (被 plan 模式限制), 在回复中明确说明
  'Write tool unavailable due to plan mode', 并返回完整 markdown 内容
- orchestrator 会根据你的报告决定: 让你重试 (先让主 agent ExitPlanMode)
  还是由 orchestrator 自行 Write 兜底
