# MailCode Review Fix 执行计划

> 每个任务按目录划分，同一目录下的所有变更由一个 agent 完成。按依赖顺序执行。

## 上下文引用

参考设计计划：`docs/design-plans/2026-06-03-mailcode-review-fix.md`

## 任务清单

### Task 1: `mailcode/utils/` — 新增 tmux 工具模块（基础）

- **涉及目录**: `mailcode/utils/`
- **涉及文件**:
  - `mailcode/utils/tmux.py` (新建, ~80 行)
- **描述**: 创建 `tmux.py` 公共模块，封装 `has_session` / `kill_session` / `send_keys` / `list_sessions` / `capture_pane` 5 个函数
- **依赖**: 无（必须最先做）
- **验证标准**:
  - [ ] ✅ Manual: `python3 -c "from mailcode.utils.tmux import has_session; print(has_session('nonexistent'))"` 输出 `False`
  - [ ] ✅ Lint: `ruff check mailcode/utils/tmux.py` 通过

### Task 2: `mailcode/relay/` — injector + session_launcher 改用 tmux 工具 + 删 clipboard 死代码

- **涉及目录**: `mailcode/relay/`
- **涉及文件**:
  - `mailcode/relay/injector.py` (修改, 删 95-122 行 + 改 session_exists)
  - `mailcode/relay/session_launcher.py` (修改, 改 session_exists / stop 用 tmux 工具)
- **描述**: 删除 `inject_via_clipboard` + `_detect_clipboard_cmd`；将 `session_exists` 改用 `mailcode.utils.tmux.has_session`；`SessionLauncher.stop` 改用 `kill_session`；`launch_agent` 改用 `send_keys`
- **依赖**: Task 1
- **验证标准**:
  - [ ] ✅ UT: `pytest tests/unit/test_injector.py tests/unit/test_session.py`（如存在）通过
  - [ ] ✅ Lint: `ruff check mailcode/relay/injector.py mailcode/relay/session_launcher.py` 通过
  - [ ] ✅ Manual: `python3 -c "from mailcode.relay.injector import CommandInjector; print(CommandInjector.session_exists('nonexistent'))"` 输出 `False`

### Task 3: `mailcode/relay/scheduler.py` — 修 cron 循环 + 改用 tmux 工具

- **涉及目录**: `mailcode/relay/`
- **涉及文件**:
  - `mailcode/relay/scheduler.py` (修改)
- **描述**: `time.sleep(60)` 改 `self._stop_event.wait(1.0)`；增加 `self._last_fired: dict[str, tuple[int, int]]` 跟踪上次触发的 (minute, hour)；cron 匹配 + 不重复触发
- **依赖**: Task 1
- **验证标准**:
  - [ ] ✅ UT: 现有单测通过
  - [ ] ✅ Lint: `ruff check mailcode/relay/scheduler.py` 通过
  - [ ] ✅ Manual: `python3 -c "from mailcode.relay.scheduler import Scheduler; s = Scheduler('/tmp/test-scheduler'); s._cron_matches_test('* * * * *')"` 能正确判断（新增测试辅助方法）

### Task 4: `mailcode/relay/email_listener.py` — 修 reconnect + 修 sender 正则 + 改用 tmux 工具

- **涉及目录**: `mailcode/relay/`
- **涉及文件**:
  - `mailcode/relay/email_listener.py` (修改)
- **描述**:
  1. `_reconnect` 调用后重置 `_idle_ready` event + 给 IDLE 线程重新发信号
  2. 替换 `_extract_email_sender`（529 行的 `re.sub`）为 `email.utils.parseaddr`
  3. 5 处 tmux subprocess 调用改用 `mailcode.utils.tmux.*`
- **依赖**: Task 1
- **验证标准**:
  - [ ] ✅ UT: `pytest tests/unit/test_email_parser.py`（如存在）通过
  - [ ] ✅ Lint: `ruff check mailcode/relay/email_listener.py` 通过

### Task 5: `mailcode/session/` + `mailcode/cli.py` — 改用 tmux 工具

- **涉及目录**: `mailcode/session/`, `mailcode/`
- **涉及文件**:
  - `mailcode/session/manager.py` (修改, 5 处 tmux 调用)
  - `mailcode/cli.py` (修改, 1 处 tmux kill 调用)
- **描述**: `manager.py` 5 处内联 `tmux has-session / kill-session / list-sessions` 改用工具；`cli.py:253` tmux kill-session 改用工具
- **依赖**: Task 1
- **验证标准**:
  - [ ] ✅ UT: `pytest tests/unit/test_session_manager.py`（如存在）通过
  - [ ] ✅ Lint: `ruff check mailcode/session/manager.py mailcode/cli.py` 通过

### Task 6: `mailcode/channels/email_channel.py` — 删 `_send_raw` 死代码

- **涉及目录**: `mailcode/channels/`
- **涉及文件**:
  - `mailcode/channels/email_channel.py` (修改, 删 37-52 行)
- **描述**: 删除 `_send_raw` 方法（仅集成测试用，不在生产路径）
- **依赖**: 无
- **验证标准**:
  - [ ] ✅ Lint: `ruff check mailcode/channels/email_channel.py` 通过
  - [ ] ✅ Manual: `python3 -c "from mailcode.channels.email_channel import EmailChannel; assert not hasattr(EmailChannel, '_send_raw')"` 通过

### Task 7: `mailcode/plugins/` — 修 `on_after_inject` + 删 `get_templates` 死代码

- **涉及目录**: `mailcode/plugins/`
- **涉及文件**:
  - `mailcode/plugins/_base.py` (修改)
  - `mailcode/plugins/_registry.py` (修改)
- **描述**:
  1. `_base.py`: `on_after_inject(ctx, success: bool = True)` 默认参数；删除 `get_templates`
  2. `_registry.py`: `dispatch_void(hook_name, ctx, **kwargs)` 透传 kwargs
  3. `injector.py` 已有 `dispatch_void("on_after_inject", ctx)` 调用，改为 `dispatch_void("on_after_inject", ctx, success=success)`
- **依赖**: 无（独立）
- **验证标准**:
  - [ ] ✅ UT: `pytest tests/unit/test_plugins.py` 通过
  - [ ] ✅ Lint: `ruff check mailcode/plugins/` 通过
  - [ ] ✅ Manual: `python3 -c "from mailcode.plugins._base import PluginBase; p = PluginBase(); p.on_after_inject(None, True)"` 不抛 TypeError

### Task 8: `mailcode/notify.py` + `mailcode/health.py` + `mailcode/relay/server.py` — 独立 bug 修复

- **涉及目录**: `mailcode/`, `mailcode/relay/`
- **涉及文件**:
  - `mailcode/notify.py` (修改, 84-87 行)
  - `mailcode/health.py` (修改, 84-85 行)
  - `mailcode/relay/server.py` (修改, 27-30 行)
- **描述**:
  1. `notify.py`: 简化配置读取，永远用 `get_smtp_config()` / `get_email_config()`
  2. `health.py:84-85`: 补 `return all_ok`
  3. `server.py`: 把 `setup_logging` + `print` 移到 `main()`
- **依赖**: 无（3 个独立修复）
- **验证标准**:
  - [ ] ✅ UT: 现有单测通过
  - [ ] ✅ Lint: `ruff check mailcode/notify.py mailcode/health.py mailcode/relay/server.py` 通过
  - [ ] ✅ Manual: `python3 -c "import mailcode.relay.server"` 不打印 banner，不创建日志文件

### Task 9: `mailcode/relay/security.py` — 修白名单 false positive

- **涉及目录**: `mailcode/relay/`
- **涉及文件**:
  - `mailcode/relay/security.py` (修改, 43-53 行)
- **描述**: 重写 `is_sender_allowed`，支持 `@domain` 后缀匹配 + 全邮箱精确匹配，删除双向 `in` 误中
- **依赖**: 无（独立）
- **验证标准**:
  - [ ] ✅ UT: `pytest tests/unit/test_security.py`（如存在）通过
  - [ ] ✅ Lint: `ruff check mailcode/relay/security.py` 通过
  - [ ] ✅ Manual: 验证 `allowed=["you@example.com"]` 时 `bob@notyou@example.com` 不通过

### Task 10: `tests/integration/` + `tests/unit/test_plugins.py` — 改测试适配删除的死代码

- **涉及目录**: `tests/integration/`, `tests/unit/`
- **涉及文件**:
  - `tests/integration/test_email_roundtrip.py` (修改, 5 处 `_send_raw` 调用)
  - `tests/integration/test_opencode_execution.py` (修改)
  - `tests/integration/test_coldstart_real.py` (修改, 3 处)
  - `tests/integration/test_smoke.py` (修改, 2 处)
  - `tests/unit/test_plugins.py` (修改, 删 `get_templates` 断言)
- **描述**:
  1. 集成测试改用 `EmailChannel().send()` 配合手工 `MIMEMultipart` 构造（如果生产代码中已有 helper 改更简单，否则 raw `sendmail` 路径用 `MIMEMultipart` + `as_bytes()` 等价替换）
  2. `test_plugins.py:103` 删 `assert p.get_templates() == []`
- **依赖**: Task 6（_send_raw 已删）, Task 7（get_templates 已删）
- **验证标准**:
  - [ ] ✅ UT: `pytest tests/unit/test_plugins.py` 通过
  - [ ] ✅ Lint: `ruff check tests/integration/ tests/unit/` 通过
  - [ ] ✅ Manual: 集成测试不要求跑通（需真实邮箱），但 import / 解析阶段需通过

## 验证清单

- [ ] 运行 `source .venv/bin/activate && python3 -m pytest tests/unit/ -q` — 全过
- [ ] 运行 `source .venv/bin/activate && python3 -m ruff check mailcode/ tests/` — 全过

## 拆分逻辑

| 任务 | 目录 | 依赖 | 批次 |
|------|------|------|------|
| Task 1 | `mailcode/utils/` | 无 | 1 (串行) |
| Task 2 | `mailcode/relay/injector + session_launcher` | Task 1 | 2 (并发) |
| Task 3 | `mailcode/relay/scheduler` | Task 1 | 2 (并发) |
| Task 4 | `mailcode/relay/email_listener` | Task 1 | 2 (并发) |
| Task 5 | `mailcode/session/ + mailcode/cli.py` | Task 1 | 2 (并发) |
| Task 6 | `mailcode/channels/` | 无 | 2 (并发) |
| Task 7 | `mailcode/plugins/` | 无 | 2 (并发) |
| Task 8 | `mailcode/notify + health + relay/server` | 无 | 2 (并发) |
| Task 9 | `mailcode/relay/security` | 无 | 2 (并发) |
| Task 10 | `tests/integration + tests/unit` | Task 6, 7 | 3 (串行) |

- 批次 1：Task 1（1 个并发）
- 批次 2：Task 2-9（8 个并发；不同目录）
- 批次 3：Task 10（等 Task 6、7 完成）
