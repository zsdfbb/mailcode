# 移除 tmux 重构执行计划

> 每个任务按目录划分，同一目录下的所有变更由一个 agent 完成。按依赖顺序执行。

## 上下文引用
参考设计计划：`docs/design-plans/2026-06-03-tmux-removal.md`

## 任务清单

### Batch 1: 删除无依赖文件（可并发）
这些文件直接删除即可，无其他文件依赖。

#### Task 1: 删除工具模块
- **涉及目录**: `mailcode/utils/`, `mailcode/session/`
- **涉及文件**: 
  - `mailcode/utils/tmux.py` — 🗑️ 删除
  - `mailcode/utils/tmux_monitor.py` — 🗑️ 删除
  - `mailcode/session/manager.py` — 🗑️ 删除
  - `mailcode/session/__init__.py` — 🗑️ 删除
  - `mailcode/relay/__init__.py` — ✂️ 移除 SessionLauncher 导出
- **验证标准**:
  - [ ] 确认文件已从磁盘删除

#### Task 2: 删除 relay 模块
- **涉及目录**: `mailcode/relay/`
- **涉及文件**:
  - `mailcode/relay/injector.py` — 🗑️ 删除
  - `mailcode/relay/session_launcher.py` — 🗑️ 删除
  - `mailcode/relay/scheduler.py` — 🗑️ 删除
  - `mailcode/notify.py` — 🗑️ 删除
- **验证标准**:
  - [ ] 确认文件已从磁盘删除

#### Task 3: 删除资源和插件
- **涉及目录**: `mailcode/resources/`, `mailcode/plugins/`
- **涉及文件**:
  - `mailcode/resources/mailcode-bridge.js` — 🗑️ 删除
  - `mailcode/resources/claude-code-hooks.json` — 🗑️ 删除
  - `mailcode/plugins/` — 🗑️ 整个目录删除
- **验证标准**:
  - [ ] 确认文件已从磁盘删除

#### Task 4: 删除旧测试
- **涉及目录**: `tests/`
- **涉及文件**（直接删除）:
  - `tests/unit/test_tmux_monitor.py`
  - `tests/unit/test_conversation_handler.py`
  - `tests/unit/test_session_manager.py`
  - `tests/unit/test_notify.py`
  - `tests/unit/test_plugins.py`
  - `tests/unit/test_bridge_plugin.py`
  - `tests/unit/test_claude_code.py`
  - `tests/unit/test_scheduler.py`
  - `tests/unit/test_coldstart.py`
  - `tests/unit/test_serve_pipeline.py`
  - `tests/integration/` — 整个目录
  - `tests/binary/` — 整个目录
- **验证标准**:
  - [ ] 确认文件已从磁盘删除

### Batch 2: 核心重写（依赖 Batch 1 删除完成）

#### Task 5: ConversationHandler 重写
- **涉及文件**: `mailcode/relay/conversation_handler.py`（重写）
- **描述**: 用 claude -p 替代 tmux 注入。新的 ConversationHandler:
  - `handle_email(from_email, subject, body, references, in_reply_to)` — 主入口
  - `_load_thread(in_reply_to)` — 从 threads.json 读取对话历史
  - `_build_prompt(history, body)` — 拼接完整 prompt
  - `_call_claude(prompt, cwd)` — subprocess.run(["claude", "-p", ...])
  - `_save_thread(thread_id, history)` — 持久化对话历史
  - `list_conversations()` / `get_conversation_status()` / `terminate_conversation()`
- **验证标准**:
  - [ ] ✅ claude -p 调用成功时返回正确的 stdout
  - [ ] ✅ 超时/错误时正确处理
  - [ ] ✅ 对话历史正确读写
  - [ ] ✅ 邮件线程追踪正确（In-Reply-To）

#### Task 6: email_listener 简化
- **涉及文件**: `mailcode/relay/email_listener.py`
- **描述**: 
  - 去掉 process_email 的 4 路路由 → 简化为单一 ConversationHandler 路径
  - 去掉 SessionLauncher / CommandInjector 引用
  - 去掉桥部署（_deploy_bridge_plugin）
  - 去掉 SessionManager.recover_orphans
  - 去掉冷启动相关逻辑
- **验证标准**:
  - [ ] ✅ 去掉所有 tmux/session/injector 引用
  - [ ] ✅ 安全检查仍然完整
  - [ ] ✅ 对话路由正确

#### Task 7: CLI 大幅简化
- **涉及文件**: `mailcode/cli.py`
- **描述**: 
  - 去掉 session 子命令组
  - 去掉 scheduler 子命令组
  - 去掉 setup 子命令
  - 去掉 plugin 子命令
  - 去掉 capture-response 子命令
  - 去掉 --dry-run / --once 等不再需要的参数
  - conversation 子命令组保持（list/status/terminate）
- **验证标准**:
  - [ ] ✅ CLI 解析器无错误
  - [ ] ✅ conversation 子命令可用

#### Task 8: 配置简化
- **涉及文件**: `mailcode/config.py`
- **描述**: 
  - 去掉 session_expiry_hours、agent_type 等 tmux 相关配置
  - conversation 配置段保持
  - 保留 mailcode_bot 基本配置（email/password/default_project）
- **验证标准**:
  - [ ] ✅ 配置加载无错误
  - [ ] ✅ conversation 配置仍然可用

#### Task 9: server 和 email_channel 清理
- **涉及文件**: `mailcode/relay/server.py`, `mailcode/channels/email_channel.py`
- **描述**:
  - server.py: 去掉 SessionManager 引用，去掉会话恢复逻辑
  - email_channel.py: 去掉 send_notification 方法（不再需要模板通知）
- **验证标准**:
  - [ ] ✅ 编译无错误

### Batch 3: 测试和清理

#### Task 10: 其他文件清理
- **涉及文件**: `install.sh`, `CLAUDE.md`, `requirements-dev.txt`
- **描述**:
  - install.sh: 去掉 tmux 检查、桥部署、钩子部署
  - CLAUDE.md: 更新目录结构和技术栈描述
  - requirements-dev.txt: 去掉 libtmux 依赖
- **验证标准**:
  - [ ] ✅ install.sh 无语法错误

#### Task 11: 全量测试
- **涉及文件**: 所有保留的测试文件 + 可能的新测试
- **描述**: 
  - 运行剩余的单元测试
  - 确保 ConversationHandler 的 mock 测试通过
  - 运行 ruff lint
- **验证标准**:
  - [ ] ✅ `python3 -m pytest tests/unit/ -q` 通过
  - [ ] ✅ `python3 -m ruff check mailcode/ tests/` 通过

## 验证清单
- [ ] 运行 `python3 -m pytest tests/unit/ -q` — 通过
- [ ] 运行 `python3 -m ruff check mailcode/ tests/` — 通过
- [ ] python 导入无错误（`python3 -c "import mailcode"`）
- [ ] CLI 帮助输出正确（`mailcode --help`）
