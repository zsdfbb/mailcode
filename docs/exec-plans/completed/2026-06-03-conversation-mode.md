# MailCode 对话模式 执行计划

> 每个任务按目录划分，同一目录下的所有变更由一个 agent 完成。按依赖顺序执行。

## 上下文引用

参考设计计划：`docs/design-plans/2026-06-03-conversation-mode.md`
参考 grill-me 详细设计：`/Users/zs/.claude/plans/parsed-launching-harbor.md`

## 任务清单

### Task 1: TmuxMonitor 基础工具
- **涉及目录**: `mailcode/utils/`
- **涉及文件**: `mailcode/utils/tmux_monitor.py`
- **描述**: 新增 `strip_terminal_escapes()` (ANSI stripping), `get_new_content()` (diff-based content extraction), `wait_for_completion()` (polling fallback for response detection)
- **验证标准**:
  - [ ] ✅ UT: mock tmux 输出，验证 ANSI stripping 正确
  - [ ] ✅ UT: 验证 get_new_content 正确提取新增内容
  - [ ] ✅ UT: 验证 wait_for_completion 超时和成功路径

### Task 2: 配置系统
- **涉及目录**: `mailcode/`
- **涉及文件**: `mailcode/config.py`
- **描述**: 新增 conversation 配置段（enabled、response_timeout_seconds、idle_timeout_hours、system_prompt），同时保持向后兼容
- **验证标准**:
  - [ ] ✅ UT: 验证 conversation 配置加载正确
  - [ ] ✅ UT: 验证默认值为 conversation.enabled = false

### Task 3: CLI 命令
- **涉及目录**: `mailcode/`
- **涉及文件**: `mailcode/cli.py`
- **描述**: 新增 `serve --conversation` 参数、`capture-response` 子命令、`conversation` 子命令组（list/status/terminate）
- **验证标准**:
  - [ ] ✅ UT: 验证 `serve --conversation` 参数解析
  - [ ] ✅ UT: 验证 `capture-response` 子命令注册
  - [ ] ✅ UT: 验证 `conversation` 子命令组注册

### Task 4: 邮件通道 — 线程支持
- **涉及目录**: `mailcode/channels/`
- **涉及文件**: `mailcode/channels/email_channel.py`
- **描述**: 新增 `send_reply()` 方法，支持设置 In-Reply-To、References 邮件头实现线程追踪
- **验证标准**:
  - [ ] ✅ UT: 验证 send_reply 正确设置邮件线程头
  - [ ] ✅ UT: 验证与现有 send()/send_notification() 的兼容性

### Task 5: Session 启动器 — 对话模式支持
- **涉及目录**: `mailcode/relay/`
- **涉及文件**: `mailcode/relay/session_launcher.py`
- **描述**: 新增 `launch_conversation_session()` 方法，设置 MAILCODE_CONVERSATION=1 环境变量，使用 `mailcode-conv-` 前缀命名
- **验证标准**:
  - [ ] ✅ UT: 验证对话 session 命名正确
  - [ ] ✅ UT: 验证环境变量设置正确

### Task 6: Hook 配置 — After hook
- **涉及目录**: `mailcode/resources/`
- **涉及文件**: `mailcode/resources/claude-code-hooks.json`
- **描述**: 新增 After hook 配置，只在 MAILCODE_CONVERSATION=1 时触发 `mailcode capture-response`
- **验证标准**:
  - [ ] ✅ Manual: 验证 JSON 格式正确

### Task 7: 核心逻辑 — ConversationHandler
- **涉及目录**: `mailcode/relay/`
- **涉及文件**: `mailcode/relay/conversation_handler.py` (新增)
- **描述**: 实现 ConversationHandler 类，处理对话的创建、提示格式化、响应捕获、线程追踪
- **验证标准**:
  - [ ] ✅ UT: 验证新邮件 → 创建 session → 注入提示 → 捕获回复 → 发送邮件的完整流程
  - [ ] ✅ UT: 验证跟进邮件 → 复用 session → 注入 → 回复
  - [ ] ✅ UT: 验证线程追踪正确

### Task 8: 集成 — email_listener 路由
- **涉及目录**: `mailcode/relay/`
- **涉及文件**: `mailcode/relay/email_listener.py`
- **描述**: process_email() 新增对话分支路由，检测对话模式启用且邮件属于对话线程时走 ConversationHandler
- **验证标准**:
  - [ ] ✅ UT: 验证对话模式启用时邮件路由到 ConversationHandler
  - [ ] ✅ UT: 验证对话模式关闭时走现有流程
  - [ ] ✅ UT: 验证非对话邮件走现有流程

### Task 9: Injector 增强
- **涉及目录**: `mailcode/relay/`
- **涉及文件**: `mailcode/relay/injector.py`
- **描述**: 新增 `inject_and_capture()` 方法，支持注入后等待回复被捕获
- **验证标准**:
  - [ ] ✅ UT: 验证 inject_and_capture 调用了现有注入逻辑
  - [ ] ✅ UT: 验证 catchup 逻辑正确

### Task 10: Notify 跳过
- **涉及目录**: `mailcode/`
- **涉及文件**: `mailcode/notify.py`
- **描述**: 对话模式下（MAILCODE_CONVERSATION=1），run_notify() 跳过通知发送
- **验证标准**:
  - [ ] ✅ UT: 验证对话模式下 notify 跳过
  - [ ] ✅ UT: 验证非对话模式 notify 正常

### Task 11: 模板
- **涉及目录**: `mailcode/templates/`
- **涉及文件**: `mailcode/templates/conversation_reply.txt` (新增)
- **描述**: 对话回复邮件模板
- **验证标准**:
  - [ ] ✅ Manual: 模板文件存在

### Task 12: 测试和 lint
- **涉及目录**: `tests/`, `mailcode/`
- **涉及文件**: 所有修改/新增文件的对应测试文件
- **描述**: 运行全量测试套件 + lint 检查，确保全部通过
- **验证标准**:
  - [ ] ✅ 运行 `python3 -m pytest tests/unit/ -q` 通过
  - [ ] ✅ 运行 `python3 -m ruff check mailcode/ tests/` 通过

## 验证清单
- [ ] 运行 `python3 -m pytest tests/unit/ -q` — 全部通过
- [ ] 运行 `python3 -m ruff check mailcode/ tests/` — 全部通过
