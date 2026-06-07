# MailCode 对话模式（Conversation Mode）设计计划

## 背景
- 当前 MailCode 是「命令中继」模式：收到邮件 → 提取命令 → 注入 Claude Code tmux 会话 → 命令执行完发通知邮件。用户收到的是「任务完成」摘要而非 Claude Code 的自然回复。
- 目标是增加「对话模式」：MailCode 通过邮箱与用户自然对话，用 Claude Code 作为大脑生成回复，并通过邮件线程保持多轮对话。

## 设计
- **使用 Claude Code 作为 AI 大脑**：不引入外部 LLM API 依赖，Claude Code 天然具备自然对话和代码执行双重能力。
- **Hook 驱动的事件模型**：使用 Claude Code 的 `After` hook 在每次回复完成后触发 `mailcode capture-response`，捕获回复内容并发送邮件。
- **邮件线程追踪**：通过 `In-Reply-To` / `References` 标准邮件头实现多轮对话关联。
- **持久会话**：每个邮件线程对应一个持续运行的 Claude Code tmux 会话，保持上下文。
- **向后兼容**：默认关闭（`conversation.enabled: false`），通过 `--conversation` 启动参数开启。对话模式的 session 使用 `mailcode-conv-` 前缀与现有命令 session 隔离。

### 数据流
```
收到邮件（线程 A）
  → 安全检查（现有流程）
  → 对话模式路由
    → 查找线程 A 的 tmux session
      → 存在：复用
      → 不存在：创建新的 Claude Code 实例
    → 格式化对话提示
    → 注入 Claude Code
    → After hook 触发 → mailcode capture-response
    → 提取回复文本（去 ANSI 码）
    → 设置 In-Reply-To / References 邮件头
    → 发送回复邮件
    → 保持会话等待下一封邮件
```

## 涉及文件
- **新增**: `mailcode/relay/conversation_handler.py`
- **新增**: `mailcode/templates/conversation_reply.txt`
- **修改**: `mailcode/cli.py` — 新增 `--conversation` 参数、`capture-response` 子命令、`conversation` 管理子命令
- **修改**: `mailcode/config.py` — 新增 conversation 配置段
- **修改**: `mailcode/relay/email_listener.py` — 新增对话分支路由
- **修改**: `mailcode/relay/injector.py` — 新增 inject_and_capture() 方法
- **修改**: `mailcode/utils/tmux_monitor.py` — 新增 ANSI stripping、new content extraction、wait_for_completion
- **修改**: `mailcode/channels/email_channel.py` — 新增 send_reply() 线程支持
- **修改**: `mailcode/relay/session_launcher.py` — 新增 launch_conversation_session()
- **修改**: `mailcode/resources/claude-code-hooks.json` — 增加 After hook
- **修改**: `mailcode/notify.py` — 对话模式下跳过通知摘要

## 测试策略
- **单元测试**: mock tmux + IMAP 验证对话处理全流程
- **集成测试**: 使用测试邮箱账号，发对话邮件 → 验证收到自然语言回复
- **手动测试**: `mailcode serve --conversation` 启动后用真实邮件测试多轮对话

## 波及文档
- `docs/design-final/design.md` — 追加对话模式章节

## 风险与注意事项
- Claude Code 的 `After` hook 是否在目标版本中可用需验证；如不可用，需退化为 polling 模式
- 回复文本提取（diff-based）对 ANSI 转义码敏感，需要可靠的 stripping 逻辑
- After hook 可能在每次 tool call 后触发，不只在整个回复完成后触发，需去重
- 对话 session 的 idle 超时管理：长期不使用的 session 需要合理清理
