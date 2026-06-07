# MailCode 对话模式（Conversation Mode）实现报告

## 变更摘要

本次变更为 MailCode 增加了「对话模式」（Conversation Mode），使其能够像电子邮件机器人一样接收邮件、用 Claude Code 自然回复、并通过邮件线程保持多轮对话。

### 核心能力
- **自然对话**：收到邮件后转发给 Claude Code，Claude Code 理解后用自然语言回复，MailCode 将回复作为邮件发送回去
- **编程能力**：在对话中也能执行代码（Claude Code 天然支持）
- **多轮对话**：邮件线程追踪（In-Reply-To / References 头），同一个线程内的邮件自动关联到同一个 Claude Code 实例
- **Hook 驱动**：利用 Claude Code 的 `After` hook 在每次回复完成后触发 `mailcode capture-response`
- **向后兼容**：默认关闭（`conversation.enabled: false`），通过 `--conversation` 启动参数开启

## 修改文件清单

| 文件 | 变更类型 | 变更内容 |
|------|---------|---------|
| `mailcode/relay/conversation_handler.py` | **新增** | 对话处理核心模块（429 行） |
| `mailcode/templates/email_templates/conversation_reply.txt` | **新增** | 对话回复邮件模板 |
| `mailcode/utils/tmux_monitor.py` | 修改 | 新增 `strip_terminal_escapes()`、`get_new_content()`、`wait_for_completion()` |
| `mailcode/config.py` | 修改 | 新增 conversation 配置段和访问函数 |
| `mailcode/relay/session_launcher.py` | 修改 | 新增 `launch_conversation_session()` |
| `mailcode/relay/injector.py` | 修改 | 新增 `inject_and_capture()` |
| `mailcode/channels/email_channel.py` | 修改 | 新增 `send_reply()` 邮件线程支持 |
| `mailcode/relay/email_listener.py` | 修改 | process_email() 新增对话分支路由（4 路分支） |
| `mailcode/cli.py` | 修改 | 新增 `--conversation` 参数、`capture-response`、`conversation` 子命令 |
| `mailcode/resources/claude-code-hooks.json` | 修改 | 新增 After hook 配置 |
| `mailcode/notify.py` | 修改 | 对话模式下跳过通知摘要 |
| `mailcode/resources/default.json` | 修改 | 新增 conversation 默认配置段 |

## 测试结果

- **全量单元测试**: 351 passed, 0 failed (0.64s)
- **Lint 检查**: ruff — All checks passed
- **新增测试文件**: `tests/unit/test_conversation_handler.py` (49 个测试用例)
- **新增测试用例**: 
  - TmuxMonitor: 15 个（ANSI stripping / content diff / completion polling）
  - EmailChannel: 7 个（send_reply 线程头 / 兼容性）
  - CLI: 11 个（参数解析 / 子命令注册）
  - SessionLauncher: 在现有测试中扩展
  - Notify: 在现有测试中扩展

## 架构要点

### 对话数据流
```
邮件 → IMAPListener → process_email:
  1. 确认码？→ _process_confirm
  2. Token？→ _process_reply
  3. 对话模式？→ ConversationHandler
  4. 默认 → _process_new_session

ConversationHandler.handle_conversation():
  → 解析邮件线程（In-Reply-To / References）
  → 查找/创建 Claude Code tmux session
  → 格式化为对话提示
  → injector.inject_and_capture()
  → After hook → capture-response → EmailChannel.send_reply()
  → 保持 session 用于下一封邮件
```

### 线程追踪
- 使用邮件标准 `In-Reply-To` / `References` 头
- 映射文件: `~/.local/share/mailcode/data/conversations/threads.json`
- 快照文件: `~/.local/share/mailcode/data/conversations/snapshots/<token>.snapshot`

### 配置
```json
{
  "conversation": {
    "enabled": false,
    "response_timeout_seconds": 180,
    "idle_timeout_hours": 4,
    "system_prompt": "你正在通过电子邮件与用户交流。每次用户发来邮件，你的回复将作为邮件发送回去。请用自然语言、友好、完整地回复。回复内容直接就是邮件正文，不要用terminal格式，不要用markdown代码块包裹。"
  }
}
```

### 向后兼容
- 默认 `conversation.enabled: false`，现有命令模式完全不变
- 对话 session 使用 `mailcode-conv-` 前缀，与命令 session 隔离
- 4 路 process_email 分支确保优先级：确认码 > Token > 对话 > 冷启动

## 使用方式

```bash
# 启动对话模式
mailcode serve --conversation

# 管理对话会话
mailcode conversation list
mailcode conversation status <token>
mailcode conversation terminate <token>

# capture-response (由 After hook 自动调用)
mailcode capture-response
```

## 未来工作
- **OpenCode 支持**: 当前仅实现了 Claude Code 的 After hook。OpenCode 需要对应的 bridge 事件处理
- **响应质量优化**: `capture-response` 的文本提取逻辑（diff + ANSI stripping）可能需要根据实际使用情况调优
- **Webhook 通知**: 对话模式下可选的实时通知通道
- **多 session 负载均衡**: 目前每个对话一个 Claude Code 实例，长期运行的对话可能需要上下文窗口管理
