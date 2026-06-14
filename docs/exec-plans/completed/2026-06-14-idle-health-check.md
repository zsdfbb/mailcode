# IDLE 死连接健康检查与累积未读处理 — 执行计划

## 上下文引用

参考设计计划：`docs/design-plans/2026-06-14-idle-health-check.md`

## 任务清单

impl 和 test 在不同文件，impl 批次（Task 1-3）可并发派，test 批次（Task 4-7）需在对应 impl 完成后执行。

### Task 1: email_listener.py — fetch 改用 BODY.PEEK[] (impl, 配对 R3)
- **涉及文件**: `mailcode/relay/email_listener.py`
- **变更**: line 366: `mail.fetch(uid_bytes, "(RFC822)")` → `mail.fetch(uid_bytes, "(BODY.PEEK[])")`
- **验证**: Task 4 的 R3 测试通过

### Task 2: email_listener.py — NOOP 健康检查机制 (impl, 配对 R1)
- **涉及文件**: `mailcode/relay/email_listener.py`
- **变更**:
  - line 610-611 区域加常量：
    ```python
    HEALTH_CHECK_INTERVAL = 60  # 秒
    health_check_every = max(1, HEALTH_CHECK_INTERVAL // self._idle_timeout)
    ```
  - line 638 前（`fetch_unread_emails` 调用之前）加 NOOP 块：
    ```python
    if iteration % health_check_every == 0:
        logger.debug(f"IDLE 健康检查 iter={iteration}")
        try:
            mail.noop()
        except (ConnectionError, EOFError, socket.timeout, imaplib.IMAP4.abort) as e:
            logger.warning(f"IDLE 健康检查失败 ({type(e).__name__}), 触发重连")
            raise
    ```
- **关键**: NOOP 必须在 `if got_event:` 分支之后（line 632 之后），`mail` 一定是重连后的新连接
- **验证**: Task 4 的 R1 测试通过

### Task 3: email_listener.py — 3 处可观测日志 (impl, 配对 R2/R4)
- **涉及文件**: `mailcode/relay/email_listener.py`
- **变更**:
  - line 632 之后（`if got_event:` 分支内，`mail.select("INBOX")` 之后）：
    ```python
    logger.info("IDLE 收到事件, 已重连")
    ```
  - line 638 之后（`fetch_unread_emails` 之后）：
    ```python
    if iteration <= health_check_every + 1 and emails:
        logger.info(f"重连后首轮 fetch 拉到 {len(emails)} 封累积未读")
    ```
  - line 665 之后（退避重连成功路径内）：
    ```python
    logger.info("退避重连成功, 准备拉取累积未读")
    ```
- **验证**: Task 5 的 R2 测试 + Task 7 的 R4 测试通过

### Task 4: test_listener_lifecycle.py — R1 + R3 测试用例 (test)
- **涉及文件**: `tests/unit/test_listener_lifecycle.py`
- **新增测试**:
  - `test_idle_health_check_triggers_reconnect_on_noop_abort`: mock `mail.noop()` 抛 `imaplib.IMAP4.abort`，断言主循环进入退避路径并调用 `_reconnect()`
  - `test_fetch_uses_body_peek_not_rfc822`: mock `mail.fetch`，断言调用参数含 `BODY.PEEK` 不含 `RFC822`
- **验证**: `pytest tests/unit/test_listener_lifecycle.py -q` 全部通过

### Task 5: test_listener_lifecycle.py — R2 测试用例 (test)
- **涉及文件**: `tests/unit/test_listener_lifecycle.py`
- **新增测试**:
  - `test_post_reconnect_fetch_logs_accumulated_count`: mock `_wait_for_idle` 返回 `True`（走 `got_event` 分支）→ `_reconnect` → `fetch_unread_emails` 返回 N 条，断言日志含 `"重连后首轮 fetch 拉到 N 封累积未读"`
- **验证**: `pytest tests/unit/test_listener_lifecycle.py -q` 全部通过

### Task 6: test_listener_lifecycle.py — 退避重连日志测试 (test, 配对 R4)
- **涉及文件**: `tests/unit/test_listener_lifecycle.py`
- **新增测试**:
  - `test_backoff_reconnect_success_log`: 模拟外层 `except (ConnectionError, ...)` 路径触发 `_reconnect()` 成功，断言日志含 `"退避重连成功, 准备拉取累积未读"`
- **验证**: `pytest tests/unit/test_listener_lifecycle.py -q` 全部通过

### Task 7: 全量回归 (verification)
- **涉及文件**: 全部
- **验证**:
  - `pytest tests/unit/ -q` 全部通过（R5）
  - `ruff check mailcode/ tests/` 0 警告

## 验证清单

- [ ] Task 1-3 impl 改动符合 line 号定位
- [ ] R1: NOOP 抛 abort 时 `_reconnect` 被调用
- [ ] R2: 重连后首轮 fetch 日志正确
- [ ] R3: `mail.fetch` 用 `BODY.PEEK[]`
- [ ] R4: 3 条重连日志均出现
- [ ] R5: `pytest tests/unit/ -q` 全部通过
- [ ] R6: 真实 QQ 邮箱环境下 IDLE 死连接被自动恢复（MANUAL_ACK_REQUIRED）
- [ ] `ruff check mailcode/ tests/` 0 警告
