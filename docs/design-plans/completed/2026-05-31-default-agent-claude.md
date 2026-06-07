# 默认 Agent 切换为 Claude Code 设计计划

## 背景
- 当前默认 agent_type 为 "opencode"
- 用户实际使用 Claude Code，希望默认值改为 "claude"

## 设计
- 将 4 处默认值从 "opencode" 改为 "claude"
- opencode 仍作为受支持的选项保留（可在配置中显式指定）
- 仅修改默认值，不改变任何功能逻辑

## 涉及文件
- 修改: `mailcode/resources/default.json`（默认配置模板）
- 修改: `mailcode/config.py`（inline fallback + get_agent_type() 回退值）
- 修改: `mailcode/relay/session_launcher.py`（命令回退值）
- 修改: `tests/unit/test_claude_code.py`（5 处断言）
- 修改: `tests/unit/test_config.py`（1 处断言 + docstring）
- 修改: `tests/unit/conftest.py`（1 处夹具默认值）

## 测试策略
- 运行 `pytest tests/unit/ -q` 验证所有单元测试通过
- 运行 `ruff check mailcode/ tests/` 验证 lint 通过

## 波及文档
- `docs/design-final/design.md` — 约 13 处引用 "opencode"，需确认是否需要更新

## 风险与注意事项
- 风险低：仅修改默认值，不影响显式配置的用户
- opencode 相关集成代码（email_listener.py 中的桥接逻辑）保持不变
