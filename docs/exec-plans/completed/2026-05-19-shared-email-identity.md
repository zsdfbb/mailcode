# 共享邮箱身份方案 — 实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 消除 IMAP/SMTP 重复配置邮箱地址和密码，引入共享 identity 层

**Architecture:**
- 新增 `account` 配置段，存放共享邮箱地址和密码
- `get_imap_config()` / `get_smtp_config()` 返回时自动补充 `user` / `pass`（若缺失则从 `account` 段 fallback）
- `email.from` 默认值从 `smtp.user` 改为 `account.email`
- TUI 配置界面：账户 Tab 新增共享字段，移除 SMTP/IMAP Tab 中的 user/pass
- 向后兼容：旧配置中 `imap.user` / `smtp.user` 显式存在时优先使用

**Tech Stack:** Python 3.x, Textual (TUI), json

---

### Task 1: 新增 `account` 配置段 + fallback 逻辑

**Files:**
- Modify: `mailcode/config.py:107-119`

**改前分析：**

```python
def get_smtp_config() -> Dict[str, Any]:
    config = load_config()
    return config.get("smtp", {})

def get_imap_config() -> Dict[str, Any]:
    config = load_config()
    return config.get("imap", {})
```

两个函数直接返回原始的 `smtp` / `imap` 字典，不含 fallback。

**Step 1: 修改 `get_imap_config` 添加 fallback**

```python
def get_smtp_config() -> Dict[str, Any]:
    config = load_config()
    cfg = config.get("smtp", {})
    if not cfg.get("user") or not cfg.get("pass"):
        account = config.get("account", {})
        if not cfg.get("user") and account.get("email"):
            cfg = dict(cfg, user=account["email"])
        if not cfg.get("pass") and account.get("password"):
            cfg = dict(cfg, pass=account["password"])
    return cfg


def get_imap_config() -> Dict[str, Any]:
    config = load_config()
    cfg = config.get("imap", {})
    if not cfg.get("user") or not cfg.get("pass"):
        account = config.get("account", {})
        if not cfg.get("user") and account.get("email"):
            cfg = dict(cfg, user=account["email"])
        if not cfg.get("pass") and account.get("password"):
            cfg = dict(cfg, pass=account["password"])
    return cfg
```

**Step 2: 更新 `get_email_config()` 的 `from` 默认值**

`mailcode/channels/email_channel.py:58` 当前是：
```python
from_email = self.email_config.get("from", self.smtp_user)
```

但 `smtp_user` 也在变化。集中到 config 层处理：

在 `config.py` 中更新 `get_email_config()`，让 `from` 在缺失时 fallback 到 `account.email`：

```python
def get_email_config() -> Dict[str, Any]:
    config = load_config()
    cfg = config.get("email", {})
    if not cfg.get("from"):
        account = config.get("account", {})
        if account.get("email"):
            cfg = dict(cfg, from=account["email"])
    return cfg
```

这样 `email_channel.py` 无需改动——`email_config.get("from")` 已经拿到正确值。

---

### Task 2: 更新 `default.json`

**Files:**
- Modify: `mailcode/resources/default.json`

**Step 1: 新增 `account` 段，移除 smtp/imap 中的 user/pass**

更改后的结构：

```json
{
  "account": {
    "_notes": {
      "email": "共享邮箱地址，同时用于 IMAP 收件和 SMTP 发件身份认证",
      "password": "共享密码/授权码"
    },
    "email": "",
    "password": ""
  },
  "smtp": {
    "_notes": {
      "host": "SMTP 服务器地址",
      "port": "SMTP 端口（465=SSL, 587=STARTTLS）",
      "secure": "是否使用 SSL 加密连接",
      "user": "（可选）覆写共享身份，留空则使用 account.email",
      "pass": "（可选）覆写共享密码，留空则使用 account.password"
    },
    "host": "smtp.qq.com",
    "port": 465,
    "secure": true,
    "user": "",
    "pass": ""
  },
  "imap": {
    "_notes": {
      "host": "IMAP 服务器地址",
      "port": "IMAP 端口（993=SSL）",
      "secure": "是否使用 SSL 加密连接",
      "user": "（可选）覆写共享身份，留空则使用 account.email",
      "pass": "（可选）覆写共享密码，留空则使用 account.password"
    },
    "host": "imap.qq.com",
    "port": 993,
    "secure": true,
    "user": "",
    "pass": ""
  },
  "email": {
    "_notes": {
      "from": "发件人显示地址，留空则使用 account.email",
      "from_name": "外发邮件显示的发件人名称",
      "to": "通知默认收件地址",
      "agent_type": "远端 AI 代理: opencode | claude",
      "check_interval": "IMAP 轮询间隔（秒）",
      "session_expiry_hours": "会话过期时间（小时）",
      "default_project_dir": "冷启动默认项目目录"
    },
    "from": "",
    "from_name": "Mailcode Remote",
    "agent_type": "opencode",
    "to": "",
    "check_interval": 5,
    "session_expiry_hours": 24,
    "default_project_dir": "~/.config/mailcode/projects/current"
  },
  "security": { ... },
  "notification": { ... }
}
```

**Step 2: 同步更新 `config.py` 中 `_ensure_user_config()` 的硬编码默认字典**

硬编码默认字典也需要加上 `account` 段、更新 notes。

---

### Task 3: 更新 TUI 配置界面

**Files:**
- Modify: `mailcode/tui/screens/config.py`

**Step 1: 账户 Tab 增加共享邮箱字段，移除 SMTP/IMAP Tab 的 user/pass**

账户 Tab 新增：
```python
yield Label("共享邮箱地址 (account.email)")
yield _make_field("", "field-account-email")
yield Label("共享密码 (account.password)")
yield _make_field("", "field-account-password", password=True)
```

SMTP Tab 移除：
```python
# 删除以下两行
yield Label("用户名")
yield _make_field("", "field-smtp-user")
yield Label("密码")
yield _make_field("", "field-smtp-pass", password=True)
```

IMAP Tab 移除：
```python
# 删除以下两行
yield Label("用户名")
yield _make_field("", "field-imap-user")
yield Label("密码")
yield _make_field("", "field-imap-pass", password=True)
```

**Step 2: 更新 `_coerce_types` — `account` 段无需特殊转换**

`account.email` / `account.password` 都是字符串，`_coerce_types` 里已有的 int/bool/list 转换不会有影响。不需要额外改动。

---

### Task 4: 更新测试

**Files:**
- Modify: `tests/unit/test_health.py` — 更新 mock 配置结构，增加 `account` 段
- Modify: `tests/unit/test_email_channel.py` — 验证 `from` fallback 到 `account.email`
- Create: `tests/unit/test_config.py` — 新增 fallback 逻辑的单元测试
- Modify: `tests/unit/test_serve_pipeline.py` — mock 配置加上 `account` 段

**Step 1: 新增 `test_config.py`**

```python
"""测试 config 层的 account fallback 逻辑"""

from mailcode.config import get_smtp_config, get_imap_config, get_email_config

# 清零缓存（每个测试需单独做）
def setup_function():
    from mailcode.config import _config_cache
    global _config_cache
    _config_cache = None


def test_smtp_fallback_to_account(monkeypatch, tmp_path):
    config = {
        "account": {"email": "bot@test.com", "password": "secret"},
        "smtp": {"host": "smtp.test.com", "port": 465, "secure": True},
    }
    config_path = tmp_path / "config.json"
    import json
    with open(config_path, "w") as f:
        json.dump(config, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", config_path)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    smtp = get_smtp_config()
    assert smtp["user"] == "bot@test.com"
    assert smtp["pass"] == "secret"
    assert smtp["host"] == "smtp.test.com"


def test_smtp_explicit_override(monkeypatch, tmp_path):
    config = {
        "account": {"email": "bot@test.com", "password": "secret"},
        "smtp": {"host": "smtp.test.com", "port": 465, "secure": True, "user": "override@test.com", "pass": "override_pass"},
    }
    config_path = tmp_path / "config.json"
    import json
    with open(config_path, "w") as f:
        json.dump(config, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", config_path)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    smtp = get_smtp_config()
    assert smtp["user"] == "override@test.com"
    assert smtp["pass"] == "override_pass"


def test_imap_fallback_to_account(monkeypatch, tmp_path):
    config = {
        "account": {"email": "bot@test.com", "password": "secret"},
        "imap": {"host": "imap.test.com", "port": 993, "secure": True},
    }
    config_path = tmp_path / "config.json"
    import json
    with open(config_path, "w") as f:
        json.dump(config, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", config_path)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    imap = get_imap_config()
    assert imap["user"] == "bot@test.com"
    assert imap["pass"] == "secret"


def test_smtp_no_account_fallback(monkeypatch, tmp_path):
    """没有 account 段时不应报错"""
    config = {
        "smtp": {"host": "smtp.test.com", "port": 465, "secure": True},
    }
    config_path = tmp_path / "config.json"
    import json
    with open(config_path, "w") as f:
        json.dump(config, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", config_path)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    smtp = get_smtp_config()
    assert smtp.get("user", "") == ""


def test_email_from_fallback_to_account(monkeypatch, tmp_path):
    config = {
        "account": {"email": "bot@test.com", "password": "secret"},
        "email": {"from_name": "Bot"},
    }
    config_path = tmp_path / "config.json"
    import json
    with open(config_path, "w") as f:
        json.dump(config, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", config_path)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    email_cfg = get_email_config()
    assert email_cfg["from"] == "bot@test.com"
    assert email_cfg["from_name"] == "Bot"
```

**Step 2: 更新 `test_health.py` 的 mock 配置**

所有 mock 配置字典需加上 `"account": {"email": "test@qq.com", "password": "abc"}` 段。同时 `imap` / `smtp` 段可以移除 `user` 和 `pass`（验证 fallback 也能工作）。

**Step 3: 更新 `test_email_channel.py`**

检查 mock 配置包含 `account` 段，并验证 `from` 正确 fallback。

**Step 4: 更新 `test_serve_pipeline.py`**

`conftest` 或 `mock_config_patch` 中引用配置的地方需加上 `account` 段。

---

### Task 5: 更新健康检查

**Files:**
- Modify: `mailcode/tui/models.py:280-285`

健康检查用 `smtp_cfg.get("user")` 和 `imap_cfg.get("user")` 判断配置完整性。fallback 生效后即使 `imap.user` 为空、`account.email` 有值也能通过，所以健康检查标签需要调整：

```python
# 账户检查逻辑保持不变（fallback 后 smtp_cfg["user"] 已就绪）
# 标签改为指向 account 段：
results.append({"category": "配置", "label": "邮箱地址", "ok": bool(account_cfg.get("email"))})
results.append({"category": "配置", "label": "邮箱密码", "ok": bool(account_cfg.get("password"))})
```

---

### 执行顺序

1. `default.json` schema 更新（Task 2）
2. `config.py` fallback 逻辑 + `email.from` 默认值（Task 1）
3. 单元测试（Task 4 Step 1）
4. 运行测试确认通过
5. 提交
6. TUI 配置界面（Task 3）
7. 更新现有测试 mock（Task 4 Step 2-4）
8. 健康检查（Task 5）
9. 运行全量单元测试确认通过
10. 最终提交
