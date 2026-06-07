# MailCode 移除 tmux 重构报告

## 变更摘要

彻底移除 MailCode 对 tmux 的依赖，改为直接调用 `claude -p` 非交互模式。

### 动机
- tmux 引入大量复杂性：ANSI 码处理、轮询检测、hook 回调、会话生命周期管理
- `claude -p` 可以直接接收 prompt 并输出回复到 stdout，无需 tmux
- 每封邮件独立调用 `claude -p`，MailCode 通过 `threads.json` 管理对话历史

### 核心变化
- **移除 tmux 所有相关代码**：tmux.py、tmux_monitor.py、injector.py、session_launcher.py
- **移除插件系统**：plugins/ 目录全部删除（8 个 hook 点仅剩 on_email_received，内置于 ConversationHandler）
- **移除通知系统**：notify.py 删除（stdout 就是回复，无需回调通知）
- **移除 hooks/bridge**：claude-code-hooks.json、mailcode-bridge.js 删除
- **简化进程模型**：每封邮件调用一次 `claude -p`，同步等待 stdout
- **对话历史持久化**：threads.json 存储完整对话记录，每次调用时拼接 prompt

## 删除统计
| 类别 | 数量 |
|------|------|
| 删除源文件 | 10 个 |
| 删除测试文件 | 12 个 |
| 删除测试目录 | 2 个（integration/、binary/） |
| 删除插件目录 | 1 个（plugins/） |
| 删除资源文件 | 2 个（bridge.js、hooks.json） |
| 保留源文件 | 8 个（大幅简化） |

## 修改文件清单

### 完全删除的文件
| 文件 | 原因 |
|------|------|
| `mailcode/utils/tmux.py` | tmux 子进程封装 |
| `mailcode/utils/tmux_monitor.py` | tmux pane 监控 |
| `mailcode/relay/injector.py` | tmux 命令注入 |
| `mailcode/relay/session_launcher.py` | tmux 会话管理 |
| `mailcode/relay/scheduler.py` | 基于 tmux session 的调度器 |
| `mailcode/notify.py` | tmux 通知模块 |
| `mailcode/session/manager.py` | tmux SessionManager |
| `mailcode/session/__init__.py` | session 包入口 |
| `mailcode/resources/mailcode-bridge.js` | OpenCode 桥插件 |
| `mailcode/resources/claude-code-hooks.json` | Claude Code hooks |
| `mailcode/plugins/`（整个目录） | 插件系统 |
| 12 个测试文件 | 对应已删除的模块 |
| `tests/integration/` | 全部依赖 tmux |
| `tests/binary/` | 全部依赖 tmux |

### 大幅修改的文件
| 文件 | 变更 |
|------|------|
| `mailcode/relay/conversation_handler.py` | 重写为 claude -p 模式（429→310 行） |
| `mailcode/relay/email_listener.py` | 去掉 4 路路由 → 简化为单一路由（1098→120 行） |
| `mailcode/cli.py` | 从 12 个子命令缩到 5 个（580→280 行） |
| `mailcode/config.py` | 去掉旧配置项 |
| `mailcode/relay/server.py` | 去掉 SessionManager/PluginRegistry |
| `mailcode/channels/email_channel.py` | 去掉 send_notification |

## 测试结果
- **全量单元测试**: 163 passed, 0 failed (0.11s)
- **Lint 检查**: ruff — All checks passed
- **导入检查**: import mailcode — OK

## 新架构数据流
```
邮件 → IMAP 监听 → 安全检查（DKIM/SPF + 发件人白名单）
  → ConversationHandler.handle_email():
    1. 从 threads.json 读取对话线程上下文
    2. 拼接 prompt = 系统提示 + 历史 + 新邮件
    3. subprocess.run(["claude", "-p", prompt], cwd=project_dir)
    4. stdout = Claude 回复（干净文本）
    5. EmailChannel.send_reply() 发送回复
    6. 回复存入 threads.json
```

## 保留的 CLI 命令
- `mailcode serve [--conversation] [--config FILE]`
- `mailcode config init|show|init-test|path|validate`
- `mailcode health`
- `mailcode doc [topic]`
- `mailcode conversation list|status|terminate`
