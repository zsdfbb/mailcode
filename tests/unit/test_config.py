"""测试 config 层的 account fallback 逻辑"""

import json
import mailcode.config
from mailcode.config import (get_smtp_config, get_imap_config, get_email_config,
                              is_session_enabled, get_session_config)


def setup_function():
    mailcode.config._config_cache = None


def test_smtp_fallback_to_account(monkeypatch, tmp_path):
    cfg = {
        "account": {"email": "bot@test.com", "password": "secret"},
        "smtp": {"host": "smtp.test.com", "port": 465, "secure": True},
    }
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    smtp = get_smtp_config()
    assert smtp["user"] == "bot@test.com"
    assert smtp["pass"] == "secret"
    assert smtp["host"] == "smtp.test.com"


def test_smtp_explicit_override(monkeypatch, tmp_path):
    cfg = {
        "account": {"email": "bot@test.com", "password": "secret"},
        "smtp": {"host": "smtp.test.com", "port": 465, "secure": True,
                 "user": "override@test.com", "pass": "override_pass"},
    }
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    smtp = get_smtp_config()
    assert smtp["user"] == "override@test.com"
    assert smtp["pass"] == "override_pass"


def test_imap_fallback_to_account(monkeypatch, tmp_path):
    cfg = {
        "account": {"email": "bot@test.com", "password": "secret"},
        "imap": {"host": "imap.test.com", "port": 993, "secure": True},
    }
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    imap = get_imap_config()
    assert imap["user"] == "bot@test.com"
    assert imap["pass"] == "secret"


def test_no_account_fallback_graceful(monkeypatch, tmp_path):
    cfg = {
        "smtp": {"host": "smtp.test.com", "port": 465, "secure": True},
    }
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    smtp = get_smtp_config()
    assert smtp.get("user", "") == ""
    assert smtp.get("pass", "") == ""


def test_email_from_fallback_to_account(monkeypatch, tmp_path):
    cfg = {
        "account": {"email": "bot@test.com", "password": "secret"},
        "email": {"from_name": "Bot"},
    }
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    email_cfg = get_email_config()
    assert email_cfg["from"] == "bot@test.com"


def test_email_from_explicit_no_fallback(monkeypatch, tmp_path):
    cfg = {
        "account": {"email": "bot@test.com", "password": "secret"},
        "email": {"from": "explicit@test.com", "from_name": "Explicit"},
    }
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    email_cfg = get_email_config()
    assert email_cfg["from"] == "bot@test.com"


# ============================================================
# mailcode_bot 新格式测试
# ============================================================


def test_smtp_from_mailcode_bot(monkeypatch, tmp_path):
    """mailcode_bot 段产生 SMTP 配置（含自动识别）"""
    cfg = {
        "mailcode_bot": {"email": "bot@qq.com", "password": "secret"},
        "smtp": {"host": "override.qq.com", "port": 465, "secure": True},
    }
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    smtp = get_smtp_config()
    assert smtp["user"] == "bot@qq.com"
    assert smtp["pass"] == "secret"
    assert smtp["host"] == "override.qq.com"  # 手动覆盖优先


def test_imap_from_mailcode_bot(monkeypatch, tmp_path):
    """mailcode_bot 段产生 IMAP 配置（含自动识别）"""
    cfg = {
        "mailcode_bot": {"email": "bot@qq.com", "password": "secret"},
    }
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    imap = get_imap_config()
    assert imap["user"] == "bot@qq.com"
    assert imap["pass"] == "secret"
    assert imap["host"] == "imap.qq.com"  # 自动识别


def test_get_email_config_with_fields(monkeypatch, tmp_path):
    """get_email_config 返回 from_name / check_interval"""
    cfg = {
        "mailcode_bot": {
            "email": "bot@qq.com",
            "password": "secret",
            "from_name": "MyBot",
            "check_interval": 30,
        },
    }
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    email_cfg = get_email_config()
    assert email_cfg["from"] == "bot@qq.com"
    assert email_cfg["from_name"] == "MyBot"
    assert email_cfg["check_interval"] == 30


def test_get_email_config_fallback_to_legacy(monkeypatch, tmp_path):
    """get_email_config 从旧 email 段回退"""
    cfg = {
        "mailcode_bot": {"email": "bot@qq.com", "password": "secret"},
        "email": {
            "from_name": "Legacy Bot",
            "check_interval": 10,
        },
    }
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    email_cfg = get_email_config()
    assert email_cfg["from_name"] == "Legacy Bot"
    assert email_cfg["check_interval"] == 10


def test_get_email_config_defaults(monkeypatch, tmp_path):
    """get_email_config 无字段时返回默认值"""
    cfg = {"mailcode_bot": {"email": "bot@test.com", "password": "secret"}}
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    email_cfg = get_email_config()
    assert email_cfg["from_name"] == "Mailcode Remote"
    assert email_cfg["check_interval"] == 5


# ============================================================
# session.enabled 默认值翻转 (Phase 3c)
# ============================================================


def test_is_session_enabled_default_true(monkeypatch, tmp_path):
    """无 session 段时, is_session_enabled() 默认 True (Phase 3c 翻默认)。"""
    cfg = {"mailcode_bot": {"email": "bot@qq.com", "password": "secret"}}
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    assert is_session_enabled() is True


def test_get_session_config_merges_user_over_default(monkeypatch, tmp_path):
    """用户显式 session.enabled: false 覆盖默认 True。"""
    cfg = {
        "mailcode_bot": {"email": "bot@qq.com", "password": "secret"},
        "session": {"enabled": False, "session_ttl_days": 30},
    }
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    sess = get_session_config()
    # 用户覆盖生效
    assert sess["enabled"] is False
    # 未指定字段沿用 SESSION_DEFAULTS
    assert sess["response_timeout_seconds"] == 180
    assert sess["idle_timeout_hours"] == 4
    # 用户显式指定
    assert sess["session_ttl_days"] == 30
    # is_session_enabled 也跟着返回 False
    assert is_session_enabled() is False


# ============================================================
# agent 配置测试
# ============================================================


def test_get_default_agent_returns_claude_default(monkeypatch, tmp_path):
    """空 config 无 default_agent 时返回 'claude'"""
    cfg = {"mailcode_bot": {"email": "bot@test.com", "password": "secret"}}
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    from mailcode.config import get_default_agent
    assert get_default_agent() == "claude"


def test_get_default_agent_custom(monkeypatch, tmp_path):
    """config 含 default_agent 时返回自定义值"""
    cfg = {
        "mailcode_bot": {"email": "bot@test.com", "password": "secret"},
        "default_agent": "pi",
    }
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    from mailcode.config import get_default_agent
    assert get_default_agent() == "pi"


def test_get_agent_config_found(monkeypatch, tmp_path):
    """找到指定 agent 时返回其配置 dict"""
    cfg = {
        "mailcode_bot": {"email": "bot@test.com", "password": "secret"},
        "agents": {
            "claude": {"command": "claude", "extra_args": ["--dangerously-skip-permissions"]},
        },
    }
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    from mailcode.config import get_agent_config
    result = get_agent_config("claude")
    assert result["command"] == "claude"
    assert "--dangerously-skip-permissions" in result["extra_args"]


def test_get_agent_config_missing(monkeypatch, tmp_path):
    """找不到指定 agent 时返回空 dict"""
    cfg = {"mailcode_bot": {"email": "bot@test.com", "password": "secret"}}
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    from mailcode.config import get_agent_config
    assert get_agent_config("nonexistent") == {}


def test_validate_default_agent_in_agents(monkeypatch, tmp_path):
    """default_agent 不在 agents 中时 errors 包含相关消息"""
    cfg = {
        "mailcode_bot": {"email": "bot@test.com", "password": "secret"},
        "security": {"allowed_senders": ["admin@test.com"], "auth_policy": "off"},
        "default_agent": "nonexistent",
        "agents": {"claude": {"command": "claude"}},
    }
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    from mailcode.config import validate_serve_config
    errors = validate_serve_config()
    agent_errors = [e for e in errors if "default_agent" in e]
    assert len(agent_errors) == 1
    assert "nonexistent" in agent_errors[0]


def test_validate_default_agent_ok(monkeypatch, tmp_path):
    """default_agent 在 agents 中时 errors 不含 agent 相关条目"""
    cfg = {
        "mailcode_bot": {"email": "bot@test.com", "password": "secret"},
        "security": {"allowed_senders": ["admin@test.com"], "auth_policy": "off"},
        "default_agent": "claude",
        "agents": {"claude": {"command": "claude"}},
    }
    p = tmp_path / "config.json"
    with open(p, "w") as f:
        json.dump(cfg, f)
    monkeypatch.setattr("mailcode.config.USER_CONFIG_PATH", p)
    monkeypatch.setattr("mailcode.config._config_cache", None)

    from mailcode.config import validate_serve_config
    errors = validate_serve_config()
    agent_errors = [e for e in errors if "default_agent" in e]
    assert agent_errors == []
