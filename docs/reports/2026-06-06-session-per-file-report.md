# Session-Per-File 重构 修改报告

## 变更摘要

将单文件 `threads.json` 重构为 per-session `session_<uuid>.json` + `index.json` 索引。MailCode 改造为 "dumb pipe": 收邮件 → 存盘 → 极简 prompt 调 `claude -p` → 发邮件, 不参与任何上下文管理决策。新增 `cwd: <path>` 邮件内嵌控制指令机制, 让用户指定 Claude 启动目录, 完全砍掉 `project_dir` 概念和 `system_prompt` 配置 (走 Claude Code 原生 `CLAUDE.md`)。CLI `conversation` → `session`, 加 `cleanup [--dry-run]`, TTL 90 天自动清理。

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| M | mailcode/relay/conversation_handler.py | 完全重写 (~570 行): per-session 文件 + index.json + cwd 提取 + TTL 清理 + 错误邮件 |
| M | mailcode/relay/email_listener.py | 调用 `is_session_enabled()` 替代 `is_conversation_enabled()` |
| M | mailcode/cli.py | `cmd_conversation` → `cmd_session` + list/show/delete/cleanup 完整实现 |
| M | mailcode/config.py | 删 `system_prompt`, 加 `session_ttl_days`, 函数重命名 |
| M | mailcode/resources/default.json | 顶层 `conversation` → `session` |
| M | tests/unit/test_conversation_handler.py | 完全重写, 80 测试覆盖新 API |
| M | tests/unit/test_cli.py | 更新 serve `--session/-S` 标志, TestSession 替代 TestConversation |
| A | docs/design-final/design.md | 追加 §12 "Session 管理" 章节 (~187 行) |
| M | CLAUDE.md | 命令清单 `conversation` → `session`, 目录结构图同步 |

## 测试结果

| 类型 | 命令 | 结果 |
|------|------|------|
| UT (新) | `pytest tests/unit/test_conversation_handler.py` | ✅ 80 passed |
| UT (改) | `pytest tests/unit/test_cli.py` | ✅ 32 passed |
| 全量 | `pytest tests/unit/` | ✅ 209 passed |
| Lint | `ruff check mailcode/ tests/` | ✅ All checks passed |

## 关键决策与实现

1. **数据模型**: `session_<uuid>.json` 存邮件流 (incoming + outgoing), `index.json` 存 `msg_id → session_id` 映射。原子写 (tmp + replace) 防止半写状态。
2. **Cwd 机制**: 正则 `^cwd:\s*(.+?)\s*$` 多行+大小写不敏感, `~` 展开, 相对路径用 `Path.cwd()` 补全, `is_dir()` 验证, 无效 warn + 忽略。粘性: 整个 session 沿用, 除非新邮件重新指定。
3. **Prompt 极简化**: MailCode 只传"用户最新邮件已写入 {session_file}", Claude 用 Read 工具自己读。MailCode 不再拼历史/摘要/截断, 上下文管理完全交给 Claude。
4. **错误邮件**: 空 response / claude 失败 → 写 ERROR 日志 + 用 `send_reply` 发礼貌通知邮件 (subject 自动加 Re: 前缀, body 中文不漏技术细节)。
5. **Index 同步**: 写入时增量更新, 损坏回退到扫描兜底 (不影响正确性)。
6. **TTL 清理**: 读 `session.session_ttl_days` (默认 90, 0/负数禁用), 按 `last_interaction` 删过期。损坏文件 warn 但不删。
7. **System Prompt**: 彻底砍掉 MailCode config, 走 Claude Code 原生 `cwd/CLAUDE.md`。
8. **project_dir**: 概念完全删除 (per-instance/per-session/per-config 三层全删), 永远 `cwd = Path.home()`。

## 实施流程

- Phase 1: 扫描 + 确认文件清单
- Phase 2: 写 design-plan + exec-plan
- Phase 3:
  - Task 1 (单 subagent): 重写 conversation_handler.py
  - Task 2 (单 subagent): CLI + config 清理
  - Task 3 (单 subagent): 重写 test_conversation_handler.py
  - test_cli.py 同步更新 (orchestrator 修, 1 个测试需要适配 `sys.exit(1)` 行为)
  - Task 4 (单 subagent): 文档更新
- Phase 4: 归档 plans
- Phase 5: 跳过反思 (执行流畅, 无 subagent 中断)

## 没做的事 (后续可加)

- `mailcode session export <id>` 导出某次对话为 markdown
- 启动时自动调用 `_cleanup_expired_sessions` (config 字段已加 `cleanup_on_startup`, 但 server.py 未挂上, 留待后续)
- 用户邮件 `cwd:` 路径白名单 (限制在 `$HOME` 子目录) 作为 hardening
- 旧 `~/.local/share/mailcode/data/conversations/threads.json` 一次性迁移脚本 (无生产用户, 暂不需要)

## 手动验证

- [x] `python3 -c "from mailcode.cli import build_parser; build_parser().parse_args(['session', '--help'])"` 输出 4 个子命令
- [x] `python3 -c "from mailcode.config import get_session_config; print(get_session_config()['session_ttl_days'])"` 输出 90
- [x] `python3 -m pytest tests/unit/` 全过
- [x] `python3 -m ruff check mailcode/ tests/` 全过
- [ ] 端到端: 真实发邮件 → Claude 风格回复 (需要 IMAP/SMTP 配置, 留作手动)
