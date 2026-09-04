# 2026-09-04 Multi-Agent Support

## 概述

把 MailCode 从"Claude Code 单一 agent"改造为"可插拔多 agent 架构"，v1 接入 Claude Code + Pi，通过 ABC 抽象层 + 注册表实现 agent 路由。

## 设计文档

- `docs/arch/multi-agent/design.md` — 完整设计方案（Hybrid 方案，含代码示例）
- `docs/arch/multi-agent/context.md` — 需求澄清与现状分析
- `docs/arch/multi-agent/review.md` — 架构质量评审（8 维度评分，总评 GREEN 可进入实现）
- `docs/arch/multi-agent/adr-001` — ABC + 模块级 dict 注册表
- `docs/arch/multi-agent/adr-002` — Session 命名空间（per-agent 子目录）
- `docs/arch/multi-agent/adr-003` — Config schema（v1 精简为 3 字段）
- `docs/arch/multi-agent/adr-004` — call_claude() shim 保留策略
- `docs/arch/multi-agent/adr-005` — 测试分层（L1 unit / L2 fake binary / L3 金丝雀）

## 改动范围

### PR1（零行为变化，~250 行 diff）

| 文件 | 操作 | 说明 |
|------|------|------|
| `mailcode/utils/agent.py` | 新建 | BaseAgentRunner ABC + AGENTS 注册表 + AgentNotFoundError + _which_lock |
| `mailcode/utils/claude_runner.py` | 改 | ClaudeRunner(BaseAgentRunner) + call_claude() shim 标 deprecated |
| `mailcode/config.py` | 改 | get_default_agent() / get_agent_config() / validate 校验 |
| `mailcode/resources/default.json` | 改 | 新增 default_agent + agents 字段 |
| `tests/unit/test_agent_runner.py` | 新建 | 模板方法 + 缓存 + 并发 + 错误处理 |
| `tests/unit/test_agent_runner_concurrency.py` | 新建 | ThreadPoolExecutor 并发 is_available() |

### PR2（功能上线，~550 行新 + ~150 行改）

| 文件 | 操作 | 说明 |
|------|------|------|
| `mailcode/utils/pi_runner.py` | 新建 | PiRunner(BaseAgentRunner) |
| `mailcode/utils/paths.py` | 新建 | agent_home() / transcripts_dir() / sessions_file() / conversations_dir() |
| `mailcode/utils/migrate.py` | 新建 | migrate_legacy() + symlink grace + dangling 检测 |
| `mailcode/relay/conversation_handler.py` | 改 | 接 runner + 路径命名空间化 |
| `mailcode/relay/resume_handler.py` | 改 | 接 runner + 路径命名空间化 |
| `mailcode/relay/stateless_handler.py` | 改 | shim 路径兼容 |
| `mailcode/relay/email_listener.py` | 改 | claude_sessions.json fallback 路径更新 |
| `mailcode/relay/scheduler.py` | 改 | Task.agent + AgentNotFoundError 捕获 + runner 路由 |
| `mailcode/cli.py` | 改 | agents list / migrate-agents / --agent |
| `mailcode/cli_chat.py` | 改 | --agent 参数 |
| `mailcode/health.py` | 改 | per-agent 可用性探测 |
| `mailcode/schedule_cli.py` | 改 | fail-fast 校验 task.agent |
| `tests/unit/test_pi_runner.py` | 新建 | _build_args 精确 argv 形状 |
| `tests/unit/test_paths.py` | 新建 | 路径函数正确性 |
| `tests/unit/test_migration.py` | 新建 | dry-run / apply / symlink / dangling |
| `tests/unit/conftest.py` | 改 | mock_runner fixture |

## 关键风险（review.md top 3）

1. **R1 (中)** — `task.agent` 配置拼错 → scheduler 线程崩溃。修复: `_run_task` 加 `AgentNotFoundError` 捕获 + 写 `task.last_error` + 发错误邮件。
2. **R6 (中)** — design.md §4.2 代码示例有 bugs（漏 import os、`_build_args` dead params、缺 `_command()`）。修复: 实现前修正示例。
3. **R5 (中)** — transcripts / conversations 长期无限增长（现状问题，非本次引入）。v1 不处理，留 backlog。

## 测试分层（ADR-005）

| 层级 | 覆盖范围 | 方式 |
|------|----------|------|
| L1 Unit | ABC 模板、缓存、并发安全、argv 形状、config 校验 | pytest mock |
| L2 Fake Binary | runner 端到端（PATH 注入 fake binary，不依赖真 API） | subprocess + env PATH |
| L3 Integration | 真 binary + 真 API（默认 skip，需 `MAILCODE_TEST_REAL_AGENT=1`） | 金丝雀测试 |

## 预估工作量

~700 行新增 + ~150 行修改，~10 个新文件 + ~9 个改文件，~5 个测试文件。PR1 预计 2-3 天，PR2 预计 1-1.5 周。
