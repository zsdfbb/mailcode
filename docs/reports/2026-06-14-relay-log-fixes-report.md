# Relay Log 错误修复报告

**日期**: 2026-06-14

## 修复概览

分析 `/Users/zs/.config/mailcode/relay.log` 发现 4 类错误，全部修复。

| 修复 | 问题 | 文件 | 状态 |
|------|------|------|------|
| Fix 1 | `claude -p` 不支持多行 prompt (含 YAML frontmatter) | `mailcode/utils/claude_runner.py` | ✅ |
| Fix 2 | `_wait_for_idle_old` 超时后 idle_thread 与主线程撞协议 | `mailcode/relay/email_listener.py` | ✅ |
| Fix 3 | QQ 邮箱 ~2h 静默断连, 缺少防御性预判重连 | `mailcode/relay/email_listener.py` | ✅ |
| Fix 4 | 重连后残留 tagged response 导致协议流偏移 | `mailcode/relay/email_listener.py` | ✅ |

## 测试结果

- **单元测试**: 284 passed, 0 failed
- **Ruff lint**: All checks passed
- **新增测试**: 8 个 (2 claude_runner + 6 email_listener)

### 测试验收

| 测试点 | 优先级 | 结果 |
|--------|--------|------|
| call_claude 不再用 `-p` | 必测 | ✅ 自动化 |
| call_claude 通过 `input=` 传 prompt | 必测 | ✅ 自动化 |
| 含 `---` frontmatter 的多行 prompt 不报错 | 必测 | ✅ 自动化 |
| _wait_for_idle_old 超时后调 idle_done | 必测 | ✅ 自动化 |
| _wait_for_idle_old 超时后 idle_thread 退出 | 必测 | ✅ 自动化 |
| _wait_for_idle_old 超时后 _idle_ready 清除 | 必测 | ✅ 自动化 |
| 超过 90min 触发预判重连 | 必测 | ✅ 自动化 |
| got_event=True 时不额外触发预判重连 | 必测 | ✅ 自动化 |
| 重连后 NOOP 在 SELECT 后立即调用 | 必测 | ✅ 自动化 |
| fetch_unread_emails 中 SELECT 后 NOOP | 必测 | ✅ 自动化 |
| 实际竞态消除 | MANUAL | ⏳ 需 Python<3.13 实跑 |

## 详细改动

### Fix 1: claude_runner stdin

```python
# Before
result = subprocess.run(
    ["claude", "-p", prompt, "--dangerously-skip-permissions"],
    capture_output=True, text=True, timeout=300, cwd=cwd)

# After
result = subprocess.run(
    ["claude", "--dangerously-skip-permissions"],
    input=prompt,
    capture_output=True, text=True, timeout=86400, cwd=cwd)
```

### Fix 1b: 超时从 300s 改为 86400s (24h)

定时任务写长文可能超过 5 分钟，防止被 `subprocess.TimeoutExpired` 截断。

### Fix 2: _wait_for_idle_old 清理 idle_thread

`_wait_for_idle_old()` 超时后 (`got_event=False`) 增加清理步骤:
1. `self._idle_ready.clear()`
2. `mail.idle_done()`
3. 等待 idle_thread 退出

### Fix 3: 预判性重连

- 属性: `FORCED_RECONNECT_INTERVAL = 5400` (90分钟)
- 跟踪: `_last_connect_time` 在每次连接/重连后刷新
- 触发: 循环中检测 `got_event=False` 且超过间隔时主动 reconnect

### Fix 4: 重连后 NOOP 同步

3 个重连点统一模式：`_reconnect()` → `mail.select("INBOX")` → `mail.noop()`

## 归档

- [x] Design plan: `docs/design-plans/relay-log-fixes.md`
- [x] Exec plan: `docs/exec-plans/relay-log-fixes.md`
- [x] Report: `docs/reports/2026-06-14-relay-log-fixes-report.md`
