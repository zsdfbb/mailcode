# 配置简化 — 执行计划

> 每个任务按目录划分，同一目录下的所有变更由一个 agent 完成。按依赖顺序分批执行。

## 上下文引用

参考设计计划：`docs/design-plans/2026-05-30-config-simplify.md`

## 任务清单

### Batch 1：核心逻辑（并发执行，不同文件）

#### Task 1: mailcode/config.py + default.json
- **涉及目录**: `mailcode/`
- **涉及文件**: `mailcode/config.py`, `mailcode/resources/default.json`
- **描述**:
  1. `config.py` 新增：
     - `DOMAIN_PROVIDER_MAP` — 域名→provider 映射
     - `PROVIDER_PRESETS` — provider→SMTP/IMAP 默认值
     - `_detect_provider(email: str) -> str` — 从邮箱解析域名返回 provider
     - `_get_bot_config(config)` — 读 mailcode_bot，兼容 account
  2. `config.py` 修改：
     - `_merge_identity()` → `_merge_bot_identity()`：从 mailcode_bot（兼容 account）合并
     - `get_smtp_config()`：检测并应用 provider 预设
     - `get_imap_config()`：同上
     - `get_email_config()`：删除 email.to/from 引用，改为从 mailcode_bot 读
     - `_ensure_user_config()` 内联默认配置更新
  3. `default.json`：更新为 mailcode_bot 结构，精简可选字段
- **验证**: `pytest tests/unit/test_config.py -q`

#### Task 2: mailcode/cli.py
- **涉及目录**: `mailcode/`
- **涉及文件**: `mailcode/cli.py`
- **描述**:
  - `_mask_sensitive()`：适配 mailcode_bot 脱敏（mailcode_bot.password → ***）
  - `_cmd_config_validate()`：移除 email.to 检查，增加 email 和 provider 验证
  - 配置校验：mailcode_bot.email 不能为空；provider 非法则报错
- **验证**: `pytest tests/unit/test_cli.py -q`

#### Task 3: mailcode/health.py + email_channel.py
- **涉及目录**: `mailcode/`
- **涉及文件**: `mailcode/health.py`, `mailcode/channels/email_channel.py`
- **描述**:
  - `health.py`：测试邮件收件地址改为 mailcode_bot.email
  - `email_channel.py`：`send()` 方法 to_email 为空时去掉 email.to fallback，调用者都传了
- **验证**: `pytest tests/unit/test_health.py tests/unit/test_email_channel.py -q`

### Batch 2：测试与文档

#### Task 4: tests/unit/test_config.py
- **涉及目录**: `tests/unit/`
- **涉及文件**: `tests/unit/test_config.py`
- **描述**:
  - 更新现有测试适配 mailcode_bot 替代 account
  - 新增 provider 自动识别测试（qq.com→qq, gmail.com→gmail 等）
  - 新增域名无法识别时的降级测试
  - 新增 mailcode_bot 缺失时从 account 回退的兼容测试
- **验证**: `pytest tests/unit/test_config.py -q`

#### Task 5: README 文档
- **涉及目录**: 项目根目录
- **涉及文件**: `README.md`, `README.en.md`
- **描述**:
  - 配置示例改为 mailcode_bot 简化版
  - 移除 email.to、email.from 等说明
- **验证**: 手动检查

### Batch 3：最终验证

#### Task 6: 全量测试 + 归档
- **描述**: 运行全量测试，归档 plans，生成报告
- **验证**: `pytest tests/unit/ -q` 全部通过
