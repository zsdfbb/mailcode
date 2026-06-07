# 默认 Agent 切换为 Claude Code 修改报告

## 变更摘要
将 mailcode 的默认 agent_type 从 "opencode" 改为 "claude"。opencode 仍然作为受支持的选项保留，用户可在配置中显式指定。

## 文件变更清单
| 操作 | 文件 | 说明 |
|------|------|------|
| M | mailcode/resources/default.json | 默认配置模板 agent_type 改为 claude |
| M | mailcode/config.py | inline fallback + get_agent_type() 回退值 + _notes 注释 |
| M | mailcode/relay/session_launcher.py | _get_agent_command() 回退值 |
| M | tests/unit/test_claude_code.py | 5 处断言 + 3 个测试名称更新 |
| M | tests/unit/test_config.py | 1 处断言 + docstring 更新 |
| M | tests/unit/conftest.py | mock_config_full 夹具默认值更新 |
| M | tests/integration/test_config.example.json | 注解更新 |

## 测试结果
| 类型 | 命令 | 结果 |
|------|------|------|
| UT | pytest tests/unit/ -q | 276 passed |
| Lint | ruff check mailcode/ tests/ | 仅预存 E401/E402（非本次引入） |

## 关键决策
- 仅修改默认值，opencode 仍作为受支持的选项保留
- session_launcher.py 的 _AGENT_COMMANDS 字典保持不变
- email_listener.py 中的桥接逻辑保持不变
