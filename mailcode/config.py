import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional, Dict, Any
from importlib import resources

from mailcode.provider_presets import PROVIDER_PRESETS, detect_provider

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = resources.files("mailcode") / "resources" / "default.json"

# 配置路径优先级：MAILCODE_CONFIG 环境变量 > 默认 ~/.config/mailcode/config.json
# 可通过 set_config_path() 运行时覆盖
_env_config = os.environ.get("MAILCODE_CONFIG")
USER_CONFIG_PATH = Path(_env_config) if _env_config else Path(os.environ.get("HOME", "~")) / ".config" / "mailcode" / "config.json"

_config_cache: Optional[Dict[str, Any]] = None


def set_config_path(path: str):
    """运行时设置自定义配置文件路径，覆盖环境变量和默认值。清除缓存强制重载。"""
    global USER_CONFIG_PATH, _config_cache
    USER_CONFIG_PATH = Path(path)
    _config_cache = None


def get_config_path() -> Path:
    """返回当前有效的配置文件路径。"""
    return USER_CONFIG_PATH


def _ensure_user_config():
    if USER_CONFIG_PATH.exists():
        return

    USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if DEFAULT_CONFIG_PATH.exists():
        shutil.copy(DEFAULT_CONFIG_PATH, USER_CONFIG_PATH)
        print(f"已创建用户配置文件: {USER_CONFIG_PATH}")
        print("请编辑此文件填入您的邮箱和密码配置")
    else:
        default_config = {
            "mailcode_bot": {
                "_notes": {
                    "email": "MailCode 机器人管理的邮箱地址。MailCode 会监听此邮箱的收件箱，也用此邮箱发信",
                    "password": "邮箱授权码/应用专用密码，不是登录密码。QQ邮箱: 设置→账户→POP3/IMAP→生成授权码",
                    "from_name": "发件人显示名称",
                    "check_interval": "检查新邮件的间隔（秒），建议 30 秒"
                },
                "email": "",
                "password": "",
                "from_name": "Mailcode Remote",
                "check_interval": 30
            },
            "security": {
                "_notes": {
                    "allowed_senders": "哪些邮箱可以给 MailCode 发命令，填你自己的邮箱。多个用逗号分隔。示例: your@qq.com",
                    "auth_policy": "邮件认证策略。warn=仅警告, strict=严格拒绝, off=关闭"
                },
                "allowed_senders": [],
                "auth_policy": "warn"
            },
            "session": {
                "_notes": {
                    "enabled": "是否启用 session 模式",
                    "response_timeout_seconds": "等待 AI 回复的超时时间（秒）",
                    "idle_timeout_hours": "空闲超时时间（小时）",
                    "session_ttl_days": "session 过期天数, 0 或负数不清理",
                    "cleanup_on_startup": "启动时自动清理过期 session"
                },
                "enabled": True,
                "response_timeout_seconds": 180,
                "idle_timeout_hours": 4,
                "session_ttl_days": 90,
                "cleanup_on_startup": True
            }
        }
        with open(USER_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(default_config, f, ensure_ascii=False, indent=2)
        print(f"已创建用户配置文件: {USER_CONFIG_PATH}")
        print("请编辑此文件填入您的邮箱和密码配置")


def load_config(force_reload: bool = False) -> Dict[str, Any]:
    global _config_cache
    if _config_cache is not None and not force_reload:
        return _config_cache

    _ensure_user_config()

    with open(USER_CONFIG_PATH, "r", encoding="utf-8") as f:
        try:
            _config_cache = json.load(f)
        except json.JSONDecodeError:
            print(f"❌ 配置文件 {USER_CONFIG_PATH} 格式错误（不是有效的 JSON）", file=sys.stderr)
            print("   请手动检查并修复该文件，或运行 mailcode config init --force 重新创建", file=sys.stderr)
            sys.exit(1)

    return _config_cache


def _get_bot_config(config):
    bot = config.get("mailcode_bot") or {}
    if not bot:
        bot = config.get("account") or {}
    if not bot:
        bot = config.get("bot") or {}
    return bot


def _merge_identity(section_cfg: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """若 section 中缺少 user/pass，从 bot 段补充"""
    if section_cfg.get("user") and section_cfg.get("pass"):
        return section_cfg
    bot = _get_bot_config(config)
    result = dict(section_cfg)
    if not result.get("user") and bot.get("email"):
        result["user"] = bot["email"]
    if not result.get("pass") and bot.get("password"):
        result["pass"] = bot["password"]
    return result


def get_smtp_config():
    config = load_config()
    bot = _get_bot_config(config)
    smtp_manual = config.get("smtp", {})
    provider = bot.get("provider", "") or detect_provider(bot.get("email", ""))
    preset = PROVIDER_PRESETS.get(provider, {}).get("smtp", {})
    merged = dict(preset)
    merged.update(smtp_manual)
    merged = _merge_identity(merged, config)
    return merged


def get_imap_config():
    config = load_config()
    bot = _get_bot_config(config)
    imap_manual = config.get("imap", {})
    provider = bot.get("provider", "") or detect_provider(bot.get("email", ""))
    preset = PROVIDER_PRESETS.get(provider, {}).get("imap", {})
    merged = dict(preset)
    merged.update(imap_manual)
    merged = _merge_identity(merged, config)
    return merged


def get_email_config():
    config = load_config()
    bot = _get_bot_config(config)
    bot_email = bot.get("email", "")
    # 从 mailcode_bot 读新字段，回退到旧 email 段
    legacy_email = config.get("email", {})
    return {
        "from": bot_email,
        "from_name": bot.get("from_name") or legacy_email.get("from_name", "Mailcode Remote"),
        "check_interval": bot.get("check_interval") or legacy_email.get("check_interval", 5),
    }


def get_security_config() -> Dict[str, Any]:
    config = load_config()
    return config.get("security", {})


def get_auth_policy() -> str:
    config = load_config()
    return config.get("security", {}).get("auth_policy", "warn")


def get_notification_config() -> Dict[str, Any]:
    config = load_config()
    return config.get("notification", {})


SESSION_DEFAULTS = {
    "enabled": True,
    "response_timeout_seconds": 180,
    "idle_timeout_hours": 4,
    "session_ttl_days": 90,
    "cleanup_on_startup": True,
}


def get_session_config() -> Dict[str, Any]:
    config = load_config()
    raw = config.get("session", {})
    result = dict(SESSION_DEFAULTS)
    result.update(raw)
    return result


def is_session_enabled() -> bool:
    return get_session_config().get("enabled", True)


SCHEDULE_DEFAULTS = {
    "enabled": True,
    "tick_seconds": 30,
    "max_concurrent": 1,
    "missed_run_policy": "skip",  # v1 仅支持 skip, v2 加 run_immediately
    "default_timeout_seconds": 1800,  # 子任务 claude 子进程默认超时 (30 分钟)
}


def get_schedule_config() -> Dict[str, Any]:
    config = load_config()
    raw = config.get("schedule", {})
    result = dict(SCHEDULE_DEFAULTS)
    result.update(raw)
    return result


def validate_serve_config() -> list[str]:
    """校验 serve 启动所需配置项, 返回错误消息列表 (空列表 = 通过)。

    检查 5 类必填项:
    - mailcode_bot.email / password 非空
    - SMTP host / user / pass 非空 (依赖 _merge_identity 自动补全)
    - IMAP host / user / pass 非空 (依赖 _merge_identity 自动补全)
    - security.allowed_senders 非空列表

    配置读取失败时 (文件不存在 / JSON 损坏) 返回包含 "无法读取配置" 的单元素列表。
    """
    errors: list[str] = []

    try:
        config = load_config()
    except Exception as e:
        return [f"无法读取配置: {e}"]

    try:
        bot = _get_bot_config(config)
        security = config.get("security", {})
        smtp = get_smtp_config()
        imap = get_imap_config()

        if not bot.get("email"):
            errors.append("mailcode_bot.email 未设置")
        if not bot.get("password"):
            errors.append("mailcode_bot.password 未设置")
        if not smtp.get("host"):
            errors.append("SMTP host 未设置（自动识别失败）")
        if not smtp.get("user"):
            errors.append("SMTP 用户或 mailcode_bot.email 未设置")
        if not smtp.get("pass"):
            errors.append("SMTP 密码或 mailcode_bot.password 未设置")
        if not imap.get("host"):
            errors.append("IMAP host 未设置（自动识别失败）")
        if not imap.get("user"):
            errors.append("IMAP 用户或 mailcode_bot.email 未设置")
        if not imap.get("pass"):
            errors.append("IMAP 密码或 mailcode_bot.password 未设置")

        allowed = security.get("allowed_senders", [])
        if not allowed:
            errors.append("security.allowed_senders 为空（至少应包含自己的邮箱）")

        # schedule 段可选校验 (warn 而非阻塞)
        schedule_cfg = config.get("schedule", {})
        if schedule_cfg.get("enabled", True):
            tick = schedule_cfg.get("tick_seconds", 30)
            if not isinstance(tick, int) or tick <= 0:
                errors.append(f"schedule.tick_seconds 应为正整数, 当前: {tick}")
            max_c = schedule_cfg.get("max_concurrent", 1)
            if not isinstance(max_c, int) or max_c < 1:
                errors.append(f"schedule.max_concurrent 应为 >=1 整数, 当前: {max_c}")
    except Exception as e:
        logger.warning(f"配置校验过程中出错: {e}")
        errors.append(f"配置校验失败: {e}")

    return errors