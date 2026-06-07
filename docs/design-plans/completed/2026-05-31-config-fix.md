# 配置修复 设计计划

## 背景

上个 commit (ec888f3) 对配置进行了简化，将 `account` 段重命名为 `mailcode_bot`，引入 SMTP/IMAP 自动识别。但遗留了以下问题：

- `get_agent_type()` 和 `get_default_project_dir()` 仍从已移除的 `email` 段读取，新配置永远返回默认值
- `get_email_config()` 只返回 `{from, to}`，丢失 `from_name`、`check_interval`、`session_expiry_hours`
- `config init` 生成的默认配置缺少这些字段
- `cli validate` 将 auto-detected 的 SMTP/IMAP host/port 标为必填，误报错误
- EmailChannel 错误信息引用旧字段名

## 设计

整体方案：在 `mailcode_bot` 段加入功能字段，getter 从 `mailcode_bot` 读取，向后兼容旧 `email` 段。

### 架构决策

- **不恢复 `email` 配置段**：字段归入 `mailcode_bot`，保持 "精简至两段" 的设计意图
- **向后兼容**：getter 优先读 `mailcode_bot`，未找到时回退到旧 `email` 段
- **validate 使用 merged config**：`_cmd_config_validate` 调用 `get_smtp_config()`/`get_imap_config()` 而非读原始 config 字典

### 数据流

```
default.json / 用户配置
    ↓ load_config()
原始 dict {mailcode_bot, security, ...}
    ↓ get_smtp_config() / get_imap_config()
合并 preset + manual override + identity fill
    ↓
各消费者使用完整配置
```

## 涉及文件

- 修改: `mailcode/config.py`
- 修改: `mailcode/resources/default.json`
- 修改: `mailcode/cli.py`
- 修改: `mailcode/channels/email_channel.py`
- 修改: `tests/unit/test_config.py`
- 修改: `tests/unit/test_claude_code.py`
- 修改: `tests/unit/conftest.py`

## 测试策略

- 单元测试，pytest
- 不涉及 TDD（修复已有行为而非新功能）
- 验收标准：修复后原有 261 个测试全部通过，新增测试覆盖 mailcode_bot 路径

## 波及文档

无（本次不修改 docs/design-final/）

## 风险与注意事项

- 旧格式配置（有 `email` 段的、有 `account` 段的）必须继续可用
- `user_config.json`（untracked 用户文件）不在修改范围内
