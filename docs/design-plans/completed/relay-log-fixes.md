# Relay Log 修复 — 设计方案

## 背景

分析 `/Users/zs/.config/mailcode/relay.log` 发现 4 类错误：
1. `claude -p` 不支持多行 prompt 含 YAML frontmatter → 定时任务 100% 失败
2. IMAP IDLE 旧 API 路径下 idle_thread 与主线程协议冲突 → 约 27 次 SELECT EOF
3. QQ 邮箱 2h 硬超时静默断连
4. IMAP FETCH 收到意外 SEARCH 响应（协议流偏移）

## 涉及文件

| 文件 | 改动 |
|------|------|
| `mailcode/utils/claude_runner.py` | stdin 替代 `-p` |
| `mailcode/relay/email_listener.py` | IMAP 连接健壮性 3 项改进 |

## 设计决策

### D1: stdin 替代 `-p`

**问题**: `subprocess.run(["claude", "-p", prompt, ...])` 将 prompt 作为 CLI 参数传递。当 prompt 含 YAML frontmatter（以 `---` 开头）时，claude CLI 将其解析为未知 option。

**方案**: 改用 `input=prompt` 通过 stdin 传递：
```python
subprocess.run(["claude", "--dangerously-skip-permissions"],
               input=prompt, capture_output=True, text=True, ...)
```
- 避免 shell 解析 prompt 内容
- 所有调用者（scheduler、conversation_handler、stateless_handler）自动受益

### D2: `_wait_for_idle_old` 超时后清理 idle_thread

**问题**: Python <3.13 路径下，`_wait_for_idle_old()` 启动后台 idle_thread 执行 `mail.idle()/idle_response()`。超时（`got_event=False`）后 idle_thread 仍在阻塞，主线程随后执行 NOOP/SELECT 导致双线程协议冲突。

**方案**: 超时后 `got_event=False` 时：
1. `_idle_ready.clear()` — 让 idle_thread 退出 while 循环
2. `mail.idle_done()` — 发送 DONE，解除 `idle_response()` 阻塞
3. `thread.join(timeout=3)` — 等线程退出

### D3: 预判性重连

**问题**: QQ 邮箱约 2h 后静默断连。NOOP 仅能探测死连，但无法预防。

**方案**: 记录 `_last_connect_time`，循环中检测超过 `FORCED_RECONNECT_INTERVAL = 5400`（90min）且 `got_event=False` 时主动重连。

### D4: 重连后 NOOP 同步

**问题**: 重连后残留的 tagged response 可能被后续命令错误消费。

**方案**: 每次 `_reconnect()` + `mail.select("INBOX")` 后执行 `mail.noop()` 刷新挂起响应。

## 测试策略

| 测试点 | 优先级 | 验收方法 |
|--------|--------|----------|
| call_claude 不再用 `-p` | 必测 | mock subprocess.run, 断言 args 不含 `-p` |
| call_claude 通过 `input=` 传 prompt | 必测 | mock subprocess.run, 断言 `input` 参数正确 |
| call_claude 可接受含 `---` 的多行 prompt | 必测 | 传 `"---\ntitle: x\n---"` 不应报 unknown option |
| _wait_for_idle_old 超时后调 idle_done | 必测 | mock mail, 断言 idle_done 被调用 |
| _wait_for_idle_old 超时后 idle_thread 退出 | 必测 | 断言 thread.is_alive() == False |
| _wait_for_idle_old 超时后 _idle_ready 清除 | 必测 | 断言 `_idle_ready.is_set() == False` |
| 超过 90min 触发预判重连 | 必测 | mock _reconnect + time.monotonic, 断言重连触发 |
| got_event=True 时不额外触发预判重连 | 必测 | 同上分支验证 |
| 重连后 NOOP 在 SELECT 后立即调用 | 必测 | mock mail, 断言调用顺序 |
| fetch_unread_emails 中 NOOP 在 SELECT 后 | 必测 | mock mail, 断言调用顺序 |
| 实际竞态消除 | MANUAL | Python<3.13 实跑 serve --idle 30min 无 EOF |

## 验证

```bash
source .venv/bin/activate && python3 -m pytest tests/unit/ -q
source .venv/bin/activate && python3 -m ruff check mailcode/ tests/
```
长时间验证: `mailcode serve --idle` 跑 >2h 观察日志无 EOF。
