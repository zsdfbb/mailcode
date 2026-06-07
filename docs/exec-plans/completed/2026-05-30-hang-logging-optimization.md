# 系统防挂死与错误日志优化 — 执行计划

## 上下文引用

参考设计计划：`docs/design-plans/2026-05-30-hang-logging-optimization.md`

## 任务清单

所有任务目录互斥，同一批次并发执行。

### Task 1: utils/logging.py — 日志轮转 + 环境变量级别控制
- **涉及文件**: `mailcode/utils/logging.py`
- **变更**:
  - `FileHandler` → `RotatingFileHandler`（5MB, 保留 3 份）
  - 从环境变量 `MAILCODE_LOG_LEVEL` 读取日志级别（默认 INFO）
  - 修复重复调用 `setup_logging()` 导致重复 handler 的问题
- **验证**: `pytest tests/unit/test_logging.py -q`

### Task 2: channels/email_channel.py — SMTP timeout + print→logger
- **涉及文件**: `mailcode/channels/email_channel.py`
- **变更**:
  - `SMTP(host, port)` → `SMTP(host, port, timeout=15)`
  - `SMTP_SSL(host, port)` → `SMTP_SSL(host, port, timeout=15)`
  - 所有 `print()` → `logger.error/warning`
  - `hasattr(self, "_server")` → `getattr(self, "_server", None)`
- **验证**: `pytest tests/unit/test_email_channel.py -q`

### Task 3: session/manager.py — 替换所有 except pass 为 logger
- **涉及文件**: `mailcode/session/manager.py`
- **变更**:
  - 第 216-217, 258-259, 260-261, 280-281, 298-299 行：`except Exception: pass` → `except Exception: logger.exception("...")`
- **验证**: `pytest tests/unit/test_session_manager.py -q`

### Task 4: relay/ 全部文件 — 集中修复
- **涉及文件**: `mailcode/relay/email_listener.py`, `injector.py`, `session_launcher.py`, `server.py`, `scheduler.py`
- **变更**:

  **email_listener.py**:
  - `IMAP4_SSL(host, port)` 后立即 `mail.sock.settimeout(15)`
  - IDLE 线程 `except Exception: pass` → `logger.exception`
  - `print()` → `logger.error/warning`
  - 信号处理移除 `sys.exit(0)`，改为 `listener.stop()`

  **injector.py**:
  - 所有 `subprocess.run(..., timeout=10)`
  - 检查 returncode，失败时返回 False
  - 修复 `"'".replace("'", "'")` → 正确的单引号转义
  - `print()` → `logger.error/warning`

  **session_launcher.py**:
  - `tmux set-option` 和 `set-window-option` 的 `subprocess.run` 加 `timeout=5`

  **server.py**:
  - 信号处理调用 `listener.stop()` + 正常退出，而非 `sys.exit(0)`
  - `main()` 加外层 try/except

  **scheduler.py**:
  - `_run_loop` 中的 `_execute()` 加 try/except，失败记录日志后继续循环
  - `_execute` 中注入失败时正确记录 `last_result`
- **验证**: `pytest tests/unit/ -q`

### Task 5: mailcode/ 根目录文件 — config.py + health.py
- **涉及文件**: `mailcode/config.py`, `mailcode/health.py`
- **变更**:
  - `config.py`: `json.load(f)` 加 `try/except json.JSONDecodeError`，报错后重设默认配置
  - `health.py`: `IMAP4_SSL(host, port)` 提前设置 `sock.settimeout(10)`
- **验证**: `pytest tests/unit/test_config.py tests/unit/test_health.py -q`

## 验证清单

- [ ] `pytest tests/unit/ -q` 全部通过
- [ ] 所有 `except Exception: pass` 已替换为日志记录
- [ ] 所有 `subprocess.run` 有 timeout 参数
- [ ] IMAP/SMTP 连接有超时
- [ ] `print()` 在非测试代码中已改为 `logger`
- [ ] 日志有轮转配置
