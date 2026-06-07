# Execution Plan: Codebase Architecture Refactoring

Date: 2026-06-06
Status: Draft

## Tasks

### Task 1: 删除 templates/ 死代码
- [ ] 删除 mailcode/templates/ 目录
- [ ] pyproject.toml 移除 "templates/email_templates/*.txt"
- [ ] pytest tests/unit/ -q 验证

### Task 2: 提取 provider_presets.py
- [ ] 创建 mailcode/provider_presets.py
  - 内容: DOMAIN_PROVIDER_MAP, PROVIDER_PRESETS, detect_provider()
- [ ] mailcode/config.py:
  - 删除 DOMAIN_PROVIDER_MAP, PROVIDER_PRESETS, _detect_provider
  - 加 from mailcode.provider_presets import PROVIDER_PRESETS, detect_provider
  - _detect_provider 调用处改为 detect_provider
- [ ] pytest tests/unit/ -q 验证

### Task 3: server.py 提到顶层
- [ ] 创建 mailcode/server.py:
  - 从 relay/server.py 复制核心逻辑
  - 去掉 __main__ 和自身 argparse
  - 暴露 run_serve(args)
  - 不包含日志设置（由 cmd_serve 负责）
- [ ] 修改 mailcode/cli.py:cmd_serve
  - 加日志设置
  - from mailcode.server import run_serve; run_serve(args)
- [ ] 删除 mailcode/relay/server.py
- [ ] 更新 tests/unit/test_cli.py 第 185 行 import
- [ ] 更新 mailcode/relay/__init__.py（删除不再需要的 server.py 导出——实际上 relay/__init__ 只 export SecurityChecker，所以无需改动）
- [ ] pytest tests/unit/ -q 验证

### Task 4: 提取 session_cli.py
- [ ] 创建 mailcode/session_cli.py，从 cli.py 移入:
  - cmd_session_list(handler)
  - cmd_session_show(handler, session_id)
  - cmd_session_delete(handler, session_id, assume_yes)
  - cmd_session_cleanup(handler, dry_run)
  - shorten(text, width)
  - fmt_ts(ts)
  - first_incoming(emails)
- [ ] mailcode/cli.py:
  - 删除上述 7 个函数
  - cmd_session 从 session_cli import 转发
  - 保留 _build_session_handler
- [ ] pytest tests/unit/ -q 验证

### Task 5: 更新文档
- [ ] CLAUDE.md 更新目录树
- [ ] docs/design-final/design.md 更新包结构描述
- [ ] pytest + ruff 全量验证

## 依赖关系
Task 1-4 无交叉依赖，可串行执行，每步可回退。
