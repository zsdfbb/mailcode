#!/usr/bin/env python3
"""mailcode 统一 CLI 入口"""

import argparse
import sys

from mailcode import __version__
from mailcode.config import get_smtp_config, get_imap_config


def _mask_sensitive(config: dict) -> dict:
    import json
    result = json.loads(json.dumps(config))
    for section in ("smtp", "imap"):
        if section in result and "pass" in result[section]:
            if result[section]["pass"]:
                result[section]["pass"] = "***"
    if "mailcode_bot" in result and "password" in result["mailcode_bot"]:
        if result["mailcode_bot"]["password"]:
            result["mailcode_bot"]["password"] = "***"
    return result


def cmd_serve(args):
    from mailcode.config import get_config_path, validate_serve_config

    # 启动预检 —— 必须在 setup_logging 之前, 避免给无效配置写 relay.log
    errors = validate_serve_config()
    if errors:
        print("❌ MailCode 中继启动失败:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    from mailcode.utils.logging import setup_logging
    _log_file = get_config_path().parent / "relay.log"
    setup_logging(_log_file)
    print("🌐 MailCode 中继已启动")
    print(f"📋 日志文件: {_log_file}")

    from mailcode.server import run_serve
    run_serve(args)


def cmd_session(args):
    """session 子命令: list / show / delete / cleanup。"""
    sub = getattr(args, "session_command", None)
    if sub is None:
        print("用法: mailcode session <list|show|delete|cleanup>", file=sys.stderr)
        sys.exit(1)

    handler = _build_session_handler()

    from mailcode.session_cli import (
        cmd_session_list, cmd_session_show,
        cmd_session_delete, cmd_session_cleanup,
    )

    if sub == "list":
        cmd_session_list(handler)
    elif sub == "show":
        cmd_session_show(handler, args.session_id)
    elif sub == "delete":
        cmd_session_delete(handler, args.session_id, assume_yes=getattr(args, "yes", False))
    elif sub == "cleanup":
        cmd_session_cleanup(handler, dry_run=getattr(args, "dry_run", False))
    else:
        print("用法: mailcode session <list|show|delete|cleanup>", file=sys.stderr)
        sys.exit(1)


def _build_session_handler():
    """构造 ConversationHandler 实例 (用真实 EmailChannel, 即便不发邮件也 OK)。"""
    from mailcode.channels.email_channel import EmailChannel
    from mailcode.relay.conversation_handler import ConversationHandler
    channel = EmailChannel()
    return ConversationHandler(email_channel=channel)


def cmd_config(args):
    import json
    from mailcode.config import load_config, _ensure_user_config, get_config_path
    config_path = get_config_path()
    if args.config_command == "show":
        config = load_config()
        masked = _mask_sensitive(config)
        print(json.dumps(masked, ensure_ascii=False, indent=2))
    elif args.config_command == "init":
        if config_path.exists():
            if args.force:
                config_path.unlink()
                _ensure_user_config()
                load_config(force_reload=True)
                print(f"配置已重新初始化: {config_path}")
            else:
                print(f"配置已存在: {config_path}")
                print("使用 --force 可强制重新创建")
        else:
            _ensure_user_config()
            load_config(force_reload=True)
            print(f"配置已创建: {config_path}")
    elif args.config_command == "init-test":
        _cmd_config_init_test(force=getattr(args, "force", False))
    elif args.config_command == "path":
        print(config_path)
    elif args.config_command == "validate":
        _cmd_config_validate(load_config())
    else:
        print("用法: mailcode config <show|init|init-test|path|validate>", file=sys.stderr)
        sys.exit(1)


def _cmd_config_init_test(force: bool = False):
    """初始化集成测试配置 ~/.config/mailcode/test_config.json"""
    import json
    from pathlib import Path

    test_config_path = Path.home() / ".config" / "mailcode" / "test_config.json"
    if test_config_path.exists() and not force:
        print(f"测试配置已存在: {test_config_path}")
        print("使用 --force 可强制重新创建")
        return
    elif test_config_path.exists() and force:
        test_config_path.unlink()

    test_config_path.parent.mkdir(parents=True, exist_ok=True)

    template = {
        "_comment": "MailCode 集成测试配置。由 mailcode config init-test 生成",
        "_init_command": "mailcode config init-test",

        "sender": {
            "_notes": "发件人邮箱配置 —— 测试框架使用此账号发送命令邮件给 bot",
            "smtp": {
                "host": "smtp.163.com",
                "port": 465,
                "secure": True,
                "user": "mailcode_test@163.com",
                "pass": "请输入授权码"
            },
            "email": {
                "from": "mailcode_test@163.com",
                "from_name": "MailCode Test"
            }
        },

        "bot": {
            "_notes": "机器人邮箱配置 —— MailCode 监听的账号，接收命令并回复通知",
            "smtp": {
                "host": "smtp.163.com",
                "port": 465,
                "secure": True,
                "user": "mailcode_bot@163.com",
                "pass": "请输入授权码"
            },
            "imap": {
                "host": "imap.163.com",
                "port": 993,
                "secure": True,
                "user": "mailcode_bot@163.com",
                "pass": "请输入授权码"
            },
            "email": {
                "from": "mailcode_bot@163.com",
                "from_name": "MailCode Bot",
                "check_interval": 5
            }
        },

        "security": {
            "allowed_senders": [
                "mailcode_test@163.com"
            ],
            "auth_policy": "off"
        },
        "notification": {
            "desktop": False,
            "desktop_sound": ""
        },
        "test": {
            "imap_folder": "INBOX",
            "wait_timeout_seconds": 120,
            "cleanup_after_test": True,
            "verbose": False
        }
    }

    with open(test_config_path, "w", encoding="utf-8") as f:
        json.dump(template, f, ensure_ascii=False, indent=2)

    print(f"测试配置已创建: {test_config_path}")
    print("请编辑此文件填入 sender 和 bot 的授权码（pass 字段）")
    print("编辑完成后执行: bash tests/run_tests.sh --integration")


def _cmd_config_validate(config: dict):
    from mailcode.config import validate_serve_config
    errors = validate_serve_config()

    if errors:
        print("❌ 配置校验失败:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        smtp = get_smtp_config()   # 含 auto-detected 值
        imap = get_imap_config()   # 含 auto-detected 值
        allowed = config.get("security", {}).get("allowed_senders", [])
        print("✅ 配置校验通过")
        print(f"   SMTP: {smtp.get('host')}:{smtp.get('port')} (user: {smtp.get('user')})")
        print(f"   IMAP: {imap.get('host')}:{imap.get('port')} (user: {imap.get('user')})")
        print(f"   发件人白名单: {len(allowed)} 个")


def cmd_health(args):
    from mailcode.health import run_health
    ok = run_health()
    sys.exit(0 if ok else 1)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mailcode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "MailCode: 通过 IMAP 邮件远程操控本地 AI Agent (OpenCode / Claude Code)。\n"
            "\n"
            "工作流: 发件人给机器人邮箱发邮件 → MailCode 在 IMAP 拉取 → 注入到本地 Agent\n"
            "       → Agent 回复内容回写到同一主题 → MailCode 通过 SMTP 把回复转发给发件人。\n"
            "\n"
            "本 CLI 主要用于: 配置管理、连通性检查、启动中继、以及维护 Agent 对话 session。"
        ),
        epilog=(
            "典型使用流程:\n"
            "  1) 首次部署   mailcode config init           # 生成默认配置\n"
            "  2) 编辑授权码 mailcode config path && $EDITOR .../config.json\n"
            "  3) 校验配置   mailcode config validate\n"
            "  4) 自检连通性 mailcode health\n"
            "  5) 启动中继   mailcode serve                  # 长期后台运行 (默认 IDLE 长连接)\n"
            "  6) 维护会话   mailcode session list|show|delete|cleanup\n"
            "\n"
            "调试提示:\n"
            "  • 想看收到什么邮件但不想执行: mailcode serve --once --dry-run\n"
            "  • 想用临时配置:                  mailcode --config /tmp/x.json <子命令>\n"
            "  • 集成测试配置:                  mailcode config init-test"
        ),
    )
    parser.add_argument("--version", action="version", version=f"mailcode {__version__}")
    parser.add_argument("--config", "-c", metavar="PATH",
                        help="指定配置文件路径（默认: ~/.config/mailcode/config.json）")

    subparsers = parser.add_subparsers(dest="command", title="子命令", metavar="<子命令>")

    # ── serve ──
    p_serve = subparsers.add_parser(
        "serve",
        help="启动 IMAP 监听中继 (前台常驻)",
        description=(
            "启动 IMAP 监听中继: 拉取 bot 邮箱里的未读邮件, 注入本地 AI Agent, 把回复通过 SMTP 转发回发件人。\n"
            "默认使用 IMAP IDLE 长连接 (实时推送, 适用于 QQ / Gmail / Outlook)。\n"
            "126/163 邮箱不支持 IDLE, 自动回退到轮询, 需将 check_interval 调到 60-120 秒避免反滥用。\n"
            "Ctrl-C 退出, 日志写入 ~/.config/mailcode/relay.log。"
        ),
    )
    p_serve.add_argument("--dry-run", action="store_true",
                        help="干跑模式: 只打印邮件内容, 不注入 Agent, 不发送回复（用于排查邮件解析）")
    p_serve.add_argument("--once", action="store_true",
                        help="处理完一轮未读后退出 (用于脚本/调试, 默认持续监听)")
    p_serve.add_argument("--no-idle", action="store_true",
                        help="禁用 IMAP IDLE 长连接, 改用固定间隔轮询 (默认启用 IDLE)")
    p_serve.add_argument("--session", "-S", action="store_true",
                        help="按邮件主题维护多轮对话 session (覆盖 config 中 session.enabled)")

    # ── config ──
    p_config = subparsers.add_parser(
        "config",
        help="配置管理 (init/show/validate/path/init-test)",
        description="管理 ~/.config/mailcode/ 下的配置文件: 初始化、查看、校验、定位。",
    )
    p_config_sub = p_config.add_subparsers(dest="config_command", title="配置动作", metavar="<动作>")

    p_config_sub.add_parser("show", help="打印当前配置 (密码字段自动脱敏为 ***)")
    p_config_sub.add_parser("path", help="打印配置文件绝对路径 (便于编辑器/查看)")
    p_config_sub.add_parser("validate", help="校验配置完整性: 检查 SMTP/IMAP/白名单等必填项")

    p_init = p_config_sub.add_parser("init",
        help="首次部署: 生成默认配置到 ~/.config/mailcode/config.json",
        description="如果配置已存在则跳过, 加上 --force 会先删除再重建 (注意: 会丢失已有授权码)。")
    p_init.add_argument("--force", action="store_true",
                        help="强制重新创建 (先删除已有配置, 谨慎使用)")

    p_init_test = p_config_sub.add_parser("init-test",
        help="生成集成测试配置 ~/.config/mailcode/test_config.json",
        description=(
            "集成测试需要 sender + bot 两个邮箱, 与正式配置完全隔离。\n"
            "生成后请填入两个邮箱的授权码, 然后跑: bash tests/run_tests.sh --integration"
        ))
    p_init_test.add_argument("--force", action="store_true",
                        help="强制重新创建 (先删除已有测试配置)")

    # ── health ──
    p_health = subparsers.add_parser(
        "health",
        help="邮件连通性自检 (SMTP + IMAP)",
        description="按顺序检查: 配置完整性 → SMTP 连接/登录/发信 → IMAP 连接/登录/收件箱, 最后汇总。",
    )
    p_health.add_argument("--send", action="store_true",
                        help="额外发一封自检邮件到 bot 自身 (默认只检查连接与登录)")

    # ── session ──
    p_session = subparsers.add_parser(
        "session",
        help="对话 session 管理 (list|show|delete|cleanup)",
        description=(
            "Session = 同一邮件主题下的多轮对话上下文, 以独立文件持久化。\n"
            "用子命令 list/show/delete/cleanup 来维护这些 session。"
        ),
    )
    p_session_sub = p_session.add_subparsers(dest="session_command", title="session 动作", metavar="<动作>")

    p_session_sub.add_parser("list", help="列出全部 session (ID/发件人/主题/最近活动/消息数/工作目录)")
    p_session_show = p_session_sub.add_parser("show",
        help="查看单个 session 的完整邮件流 (in/out 交替)")
    p_session_show.add_argument("session_id", help="12 位 hex session ID (从 list 中获取)")

    p_session_delete = p_session_sub.add_parser("delete",
        help="删除单个 session (会先打印详情并提示确认, --yes 跳过)")
    p_session_delete.add_argument("session_id", help="12 位 hex session ID")
    p_session_delete.add_argument("-y", "--yes", action="store_true",
                                  help="跳过交互式确认, 直接删除")

    p_session_cleanup = p_session_sub.add_parser("cleanup",
        help="按 TTL 清理过期 session (用 --dry-run 先预览)")
    p_session_cleanup.add_argument("--dry-run", action="store_true",
                                   help="只列出将被清理的 session, 不实际删除")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.config:
        from mailcode.config import set_config_path
        set_config_path(args.config)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "config":
        cmd_config(args)
    elif args.command == "health":
        cmd_health(args)
    elif args.command == "session":
        cmd_session(args)

if __name__ == "__main__":
    main()
