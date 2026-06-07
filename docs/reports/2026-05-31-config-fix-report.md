# 配置修复 修改报告

## 变更摘要
修复上个 commit (ec888f3) 配置简化后的遗留问题：getter 函数仍从已移除的 email 段读取，
init 生成的配置缺少功能字段，配置验证误报 auto-detected 值缺失。

## 文件变更清单
| 操作 | 文件 | 说明 |
|------|------|------|
| M | mailcode/resources/default.json | mailcode_bot 段增加 agent_type 等 5 个字段 |
| M | mailcode/config.py | 修复 get_email_config/get_agent_type/get_default_project_dir，支持 mailcode_bot 读取和旧段回退；修复 _ensure_user_config 重复 mkdir 和内联 fallback 字段 |
| M | mailcode/cli.py | 修复 _cmd_config_validate 使用 merged config；移除未使用的 _detect_provider/PROVIDER_PRESETS 导入 |
| M | mailcode/channels/email_channel.py | 修复错误信息引用 email.from→mailcode_bot.email |
| M | tests/unit/conftest.py | 新增 mock_config_new / mock_config_new_patch fixture |
| M | tests/unit/test_config.py | 新增 12 个 mailcode_bot 格式测试 |
| M | tests/unit/test_claude_code.py | 新增 3 个 mailcode_bot agent_type 测试 |

## 测试结果
| 类型 | 命令 | 结果 |
|------|------|------|
| UT | pytest tests/unit/ -q | 275 passed ✅ |
| Lint | ruff check mailcode/ tests/ | 13 pre-existing ✅ |

## 关键决策
- 字段归入 mailcode_bot 段，保持"精简至两段"设计
- getter 优先读 mailcode_bot，未找到时回退到旧 email 段
- validate 使用 get_smtp_config()/get_imap_config() 获取 auto-detected 值
- 不修改 user_config.json（untracked 用户文件）
