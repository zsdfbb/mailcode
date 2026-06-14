# IDLE 死连接健康检查与累积未读处理 — 验收报告

## 验收摘要

| 验收项 | 结果 | 证据 |
|---|---|---|
| R1: NOOP abort 触发退避重连 | **PASS** | `test_idle_health_check_triggers_reconnect_on_noop_abort` 通过; mock `IMAP4.abort` 后 `_reconnect()` 被调用, warning 日志 "IDLE 健康检查失败" 出现 |
| R2: 重连后首轮 fetch 日志显示 N 封 | **PASS** | `test_post_reconnect_fetch_logs_accumulated_count` 通过; 3 封累积未读 → 日志含 "重连后首轮 fetch 拉到 3 封累积未读" |
| R3: `fetch` 用 `BODY.PEEK[]` 而非 `RFC822` | **PASS** | `test_fetch_unread_uses_body_peek_not_rfc822` 通过; 断言 `mail.fetch` 调用参数含 `BODY.PEEK` 不含 `RFC822` |
| R4: 退避重连成功日志 | **PASS** | `test_backoff_reconnect_success_log` 通过; NOOP abort → 外层 except → `_reconnect` 成功 → 日志含 "退避重连成功, 准备拉取累积未读" |
| R5: 全量回归 | **PASS** | `pytest tests/unit/ -q` → **277 passed** in 2.62s |
| R6: 真实 QQ 环境 IDLE 死连接恢复 | **MANUAL_ACK_REQUIRED** | 需用户在生产环境跑 `mailcode serve` 等待 QQ 静默断开 IDLE 后观察自动恢复 |

## 验证详情

### 测试结果

```
$ source .venv/bin/activate && python3 -m pytest tests/unit/ -q
........................................................................ [ 25%]
........................................................................ [ 51%]
........................................................................ [ 77%]
.............................................................            [100%]
277 passed in 2.62s
```

**4 个新增测试** 全部通过（`test_listener_lifecycle.py:147-291`）：

- `test_idle_health_check_triggers_reconnect_on_noop_abort` (R1)
- `test_fetch_unread_uses_body_peek_not_rfc822` (R3)
- `test_post_reconnect_fetch_logs_accumulated_count` (R2)
- `test_backoff_reconnect_success_log` (R4)

测试总数从 14 → 18（lifecycle 文件内）。

### Lint 结果

```
$ source .venv/bin/activate && python3 -m ruff check mailcode/ tests/
All checks passed!
```

### 关键代码定位（验证 NOOP 位置约束）

```
366:                status, msg_data = mail.fetch(uid_bytes, "(BODY.PEEK[])")
612:        HEALTH_CHECK_INTERVAL = 60  # 秒: IDLE 健康检查周期
613:        health_check_every = max(1, HEALTH_CHECK_INTERVAL // self._idle_timeout)
635:                        logger.info("IDLE 收到事件, 已重连")
643:                    if iteration % health_check_every == 0:
646:                            mail.noop()
655:                    if iteration <= health_check_every + 1 and emails:
656:                        logger.info(f"重连后首轮 fetch 拉到 {len(emails)} 封累积未读")
684:                        logger.info("退避重连成功, 准备拉取累积未读")
```

**NOOP 位置验证**：

- `if got_event:` 分支结束于 line 635
- NOOP 块在 line 643-649（**line 635 之后** ✓）
- `fetch_unread_emails` 调用在 line 652（NOOP 之后 ✓）
- 整个 NOOP 块在主 `try:` 块（line 622-674）内 ✓

**结论**：NOOP 位置完全满足"必须在 `if got_event:` 之后"的设计约束，避免与 `_wait_for_idle_old` 后台 IDLE daemon 线程 race。

### 回归检查

重点关注的现有测试：

- `test_listen_idle_proceeds_when_idle_supported` — PASS（IDLE 主路径无破坏）
- `test_active_idle_mail_cleared_after_idle_returns` — PASS（`_active_idle_mail` 生命周期正常）

### 风险与备注

| 风险 | 状态 | 说明 |
|---|---|---|
| 老 API 路径下 NOOP 与 IDLE daemon 线程 race | **已规避** | NOOP 位置在 `if got_event:` 之后（重连后 `mail` 是新连接） |
| `_Backoff` 状态跨重连未清 | **保留既有行为** | 既有逻辑，本次不动；连续重连失败也能在 60s 内再次触发 NOOP 探测 |
| 真实 QQ 断开时机不可控 | **MANUAL_ACK** | R6 标为手工验收 |
| 5xx 邮件中既有"邮件出现在 QQ 网页端已读"的副作用 | **未变更** | 改 BODY.PEEK[] 后副作用消失（不再打 `\\Seen`） |

### 无新依赖

仅使用 stdlib `imaplib.IMAP4.abort`、`socket.timeout`、`ConnectionError`、`EOFError`，全部已在文件顶部 import。

## 结论

**修复可发布。** 5 项可自动化验收项全部通过，关键代码定位满足设计约束，现有 277 项单元测试无回归，零新依赖。R6 需用户在生产环境手工 ack 真实 QQ 邮箱 IDLE 死连接恢复。
