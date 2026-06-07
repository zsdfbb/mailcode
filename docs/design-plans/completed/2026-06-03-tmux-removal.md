# 移除 tmux 重构设计计划

## 背景
- MailCode 当前重度依赖 tmux：通过 tmux 管理 Claude Code 会话、注入命令、轮询输出、解析 pane 内容
- 这种架构引入大量复杂性：ANSI 码处理、轮询检测、hook 回调、会话生命周期管理、持久化连接
- Claude Code 的 `-p/--print` 非交互模式可以直接接收 prompt 并输出回复到 stdout，无需 tmux
- 每个邮件请求可以独立调用 `claude -p`，MailCode 在 `threads.json` 中维护对话历史

## 设计

### 新架构
```
收到邮件 → 安全检查（DKIM/SPF + 发件人白名单）
  → ConversationHandler:
    1. 从 threads.json 读取/创建线程上下文
    2. 拼接 prompt = 系统提示 + 对话历史 + 新邮件正文
    3. subprocess.run(["claude", "-p", prompt,
                       "--dangerously-skip-permissions"],
                      cwd=project_dir, timeout=300)
    4. stdout = Claude 回复（干净文本）
    5. EmailChannel.send_reply() 发送回复
    6. 回复存入 threads.json
```

### 核心变化
- **不再需要 tmux**：每封邮件对应一次 `claude -p` 子进程，同步等待输出
- **不再需要 hooks/bridge**：stdout 就是回复，无需回调通知
- **不再需要 injector/session_launcher**：没有 tmux session 要管理
- **不再需要 notify**：回复在 process 返回时已经拿到，直接发邮件
- **不再需要插件系统**：唯一有用的 on_email_received 钩子可以由 ConversationHandler 内置实现
- **对话历史由 MailCode 管理**：threads.json 存储每线程的完整对话记录

### 数据流
```
IMAPListener.fetch_unread_emails()
  → SecurityChecker: DKIM/SPF + 发件人白名单
  → ConversationHandler.handle_email():
    → _load_thread() — 从 threads.json 读取历史
    → _build_prompt() — 拼接系统提示 + 历史 + 新邮件
    → subprocess.run(["claude", "-p", prompt, "--dangerously-skip-permissions"],
                     cwd=project_dir, capture_output=True, timeout=300)
    → response = proc.stdout.strip()
    → EmailChannel.send_reply() — 设置 In-Reply-To / References
    → _save_thread() — 对话历史持久化
```

### Session 概念变更
- 旧：tmux session（持久进程）+ SessionManager（JSON 元数据）
- 新：email thread（对话线索）+ threads.json（对话历史）
- SessionManager 完全移除，其功能由 ConversationHandler 接管

## 删除文件清单
| 操作 | 文件 |
|------|------|
| 🗑️ 删除 | `mailcode/utils/tmux.py` |
| 🗑️ 删除 | `mailcode/utils/tmux_monitor.py` |
| 🗑️ 删除 | `mailcode/relay/injector.py` |
| 🗑️ 删除 | `mailcode/relay/session_launcher.py` |
| 🗑️ 删除 | `mailcode/notify.py` |
| 🗑️ 删除 | `mailcode/session/manager.py` |
| 🗑️ 删除 | `mailcode/session/__init__.py` |
| 🗑️ 删除 | `mailcode/resources/mailcode-bridge.js` |
| 🗑️ 删除 | `mailcode/resources/claude-code-hooks.json` |
| 🗑️ 删除 | `mailcode/plugins/`（整个目录） |
| 🗑️ 删除 | `mailcode/relay/scheduler.py` |
| 🗑️ 删除 | `tests/unit/test_tmux_monitor.py` |
| 🗑️ 删除 | `tests/unit/test_conversation_handler.py` |
| 🗑️ 删除 | `tests/unit/test_session_manager.py` |
| 🗑️ 删除 | `tests/unit/test_notify.py` |
| 🗑️ 删除 | `tests/unit/test_plugins.py` |
| 🗑️ 删除 | `tests/unit/test_bridge_plugin.py` |
| 🗑️ 删除 | `tests/unit/test_claude_code.py` |
| 🗑️ 删除 | `tests/unit/test_scheduler.py` |
| 🗑️ 删除 | `tests/unit/test_coldstart.py` |
| 🗑️ 删除 | `tests/unit/test_serve_pipeline.py` |
| 🗑️ 删除 | `tests/integration/`（全部，需要真实邮件账号，全部依赖 tmux） |
| 🗑️ 删除 | `tests/binary/`（全部，依赖 Nuitka 构建 + tmux） |

## 修改文件清单
| 操作 | 文件 | 变更内容 |
|------|------|---------|
| 📝 重写 | `mailcode/relay/conversation_handler.py` | 用 claude -p 替代 tmux 注入 |
| ✂️ 简化 | `mailcode/relay/email_listener.py` | 去掉冷启动、session 管理、桥部署 |
| ✂️ 简化 | `mailcode/cli.py` | 去掉 session/scheduler/setup/plugin 子命令 |
| ✂️ 简化 | `mailcode/config.py` | 去掉旧 session/tmux 配置项 |
| ✂️ 简化 | `mailcode/relay/server.py` | 去掉 SessionManager 引用 |
| ✂️ 简化 | `mailcode/channels/email_channel.py` | 去掉 send_notification（不再需要） |
| ✂️ 简化 | `mailcode/relay/__init__.py` | 去掉 SessionLauncher 导出 |
| ✂️ 简化 | `install.sh` | 去掉 tmux 检查、桥部署 |
| ✅ 保留不变 | `mailcode/relay/security.py` | 安全检查逻辑不变 |
| 📝 更新 | `docs/design-final/design.md` | 反映新架构 |

## 保留功能
- IMAP 监听和邮件处理
- 安全检查（DKIM/SPF + 发件人白名单）
- 邮件发送（EmailChannel）
- 对话线程追踪（threads.json）
- 配置系统
- CLI 入口（简化为核心命令）
- 调度器（Scheduler）— **后面讨论决定保留，但需要改为调用 claude -p**

## 测试策略
- 单元测试覆盖新的 ConversationHandler + claude -p 逻辑
- Mock subprocess.run 返回值验证对话流程
- Mock EmailChannel 验证回复发送
- Mock threads.json 验证历史持久化

## 波及文档
- `docs/design-final/design.md` — 大幅更新反映新架构

## 风险与注意事项
- `claude -p` 不支持 --dangerously-skip-permissions 时可能需要交互权限确认；需要测试实际环境
- 长耗时任务（>5 分钟）需要扩展 subprocess.run 的 timeout
- 需要验证 claude -p 的退出码和 stderr 处理
