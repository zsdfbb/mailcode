# IDLE 死连接健康检查与累积未读处理

## 背景

`mailcode serve` 在 IMAP IDLE 长连接模式下，QQ 邮箱会**单方面关闭 IDLE 长连接**（不发 EXISTS 通知、直接 EOF），但 Python 端 `mail.idle(duration=5)` 看不到这个 EOF，撑过 5s 正常 `return False`。

`mailcode/relay/email_listener.py` 的 IDLE 循环仅在两种情况下重连：

1. `_wait_for_idle` 抛连接级异常（`IMAP4.abort` 等，line 656）
2. `got_event=True`（line 628）

死连接两种都不触发，于是死循环在"5s IDLE 等待 → 空 fetch"里转。**日志没 ERROR，UNSEEN 邮件永远进不来**，重启服务后才被处理。

## 设计

### 核心修复

1. **60s 周期性 NOOP 健康检查**：在 IDLE 主循环里每 60s 发一次 `mail.noop()`，失败时 `raise` 给既有外层 `except (ConnectionError, EOFError, socket.timeout, imaplib.IMAP4.abort)`（line 656）走退避重连——**复用既有路径，零新逻辑**。
2. **3 行可观测日志**：让重连生效在日志里显式可见。
3. **`BODY.PEEK[]` 替代 `RFC822`**：避免服务器在 fetch 时打 `\\Seen` 标志（RFC 3501 §6.4.5）。

### 关键约束

**NOOP 位置必须放在 `if got_event:` 分支之后**（line 632 区域）——验证发现老 API 路径 `_wait_for_idle_old`（line 207-231）有后台 IDLE daemon 线程，超时返回 `False` 时 IDLE 状态未完全清理。若在超时分支调用 `mail.noop()` 会和 IDLE daemon 线程撞协议。重连后的 `mail` 一定是新连接，无此 race。

### 涉及文件

| 目录 | 文件 | 变更 |
|------|------|------|
| `mailcode/relay/` | `email_listener.py` | line 366: `(RFC822)` → `(BODY.PEEK[])`；line 610-611: 加 `HEALTH_CHECK_INTERVAL=60` 常量；line 632 后: 加 `"IDLE 收到事件, 已重连"` 日志；line 638 前: 加 NOOP 块；line 638 后: 加 `"重连后首轮 fetch 拉到 N 封累积未读"` 日志；line 665 后: 加 `"退避重连成功, 准备拉取累积未读"` 日志 |
| `tests/unit/` | `test_listener_lifecycle.py` | 新增 R1-R4 测试用例 |

### 波及文档

- `docs/design-final/design.md` — 无需更新（不涉及架构变更）

## 测试策略

| ID | 需求 | 验收方法 | 优先级 |
|---|---|---|---|
| R1 | 60s NOOP 健康检查触发重连 | 单元测试：mock `mail.noop()` 抛 `imaplib.IMAP4.abort`，断言 `_reconnect()` 被调用、backoff 递增 | 必测 |
| R2 | 重连后首轮 fetch 拉到累积未读 | 单元测试：mock `fetch_unread_emails` 返回 N 条，断言日志含 `"重连后首轮 fetch 拉到 N 封累积未读"` | 必测 |
| R3 | `fetch_unread_emails` 用 `BODY.PEEK[]` 而非 `RFC822` | 单元测试：断言 `mail.fetch` 调用参数含 `BODY.PEEK` 不含 `RFC822` | 必测 |
| R4 | 3 条重连日志在正确位置出现 | 单元测试：`caplog` 断言 3 条日志文本（"IDLE 收到事件, 已重连" / "退避重连成功, 准备拉取累积未读" / "重连后首轮 fetch 拉到 N 封累积未读"） | 必测 |
| R5 | 现有单元测试不回归 | `pytest tests/unit/ -q` 全部通过 | 必测 |
| R6 | 真实 QQ 邮箱环境下 IDLE 死连接被自动恢复 | 手工跑 `mailcode serve` 等待 QQ 静默断开 IDLE，观察自动重连 | MANUAL_ACK_REQUIRED |
