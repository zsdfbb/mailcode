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
        print("💡 建议: 运行 mailcode health 检查连通性")
        sys.exit(1)

    from mailcode.utils.logging import setup_logging
    _log_file = get_config_path().parent / "relay.log"
    setup_logging(_log_file)
    print("🌐 MailCode 中继已启动")
    print(f"📋 日志文件: {_log_file}")

    from mailcode.server import run_serve
    run_serve(args)


def cmd_session(args):
    """session 子命令: list / show / delete / cleanup / stats。"""
    sub = getattr(args, "session_command", None)
    if sub is None:
        print("用法: mailcode session <list|show|delete|cleanup|stats>", file=sys.stderr)
        sys.exit(1)

    handler = _build_session_handler()

    from mailcode.session_cli import (
        cmd_session_list, cmd_session_show,
        cmd_session_delete, cmd_session_cleanup,
        cmd_session_stats,
    )

    if sub == "list":
        cmd_session_list(handler,
            wide=getattr(args, "wide", False),
            filter_text=getattr(args, "filter", "") or "")
    elif sub == "show":
        cmd_session_show(handler, args.session_id)
    elif sub == "delete":
        cmd_session_delete(handler, args.session_id, assume_yes=getattr(args, "yes", False))
    elif sub == "cleanup":
        cmd_session_cleanup(handler, dry_run=getattr(args, "dry_run", False))
    elif sub == "stats":
        cmd_session_stats(handler)
    else:
        print("用法: mailcode session <list|show|delete|cleanup|stats>", file=sys.stderr)
        sys.exit(1)


def _build_session_handler():
    """构造 ConversationHandler 实例 (用真实 EmailChannel, 即便不发邮件也 OK)。"""
    from mailcode.channels.email_channel import EmailChannel
    from mailcode.relay.conversation_handler import ConversationHandler
    channel = EmailChannel()
    return ConversationHandler(email_channel=channel)


def _build_state_listener():
    """构造 IMAPListener 实例, 供 `mailcode state` 子命令使用。

    注意: listener 构造时会自动 _load_state() 读 state.json, 但不会连接 IMAP。
    `show` 子命令仅读内存状态, 无需网络;
    `rebuild-baseline` 会调用 listener.rebuild_baseline() 触发一次 IMAP 连接。
    """
    from mailcode.relay.email_listener import IMAPListener
    return IMAPListener()


def _build_schedule_store():
    """构造 ScheduleStore 实例（默认路径 ~/.config/mailcode/schedules.json）。"""
    from pathlib import Path

    schedules_path = Path.home() / ".config" / "mailcode" / "schedules.json"
    from mailcode.relay.scheduler import ScheduleStore
    return ScheduleStore(schedules_path)


def cmd_schedule(args):
    """schedule 子命令: list/show/add/enable/disable/delete/run-now/validate。"""
    from mailcode.schedule_cli import (
        cmd_schedule_list, cmd_schedule_show, cmd_schedule_add,
        cmd_schedule_enable, cmd_schedule_disable, cmd_schedule_delete,
        cmd_schedule_run_now, cmd_schedule_validate,
    )
    sub = getattr(args, "schedule_command", None)
    if sub is None:
        print("用法: mailcode schedule <list|show|add|enable|disable|delete|run-now|validate>", file=sys.stderr)
        sys.exit(1)

    store = _build_schedule_store()

    if sub == "list":
        cmd_schedule_list(store)
    elif sub == "show":
        cmd_schedule_show(store, args.name)
    elif sub == "add":
        # 把 CLI 的 "mon" 字符串转为 int(0)，与 parse_schedule 的整数 api 一致
        _DOW_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        cmd_schedule_add(store, args.name,
            schedule_type=args.type,
            interval_seconds=args.interval_seconds,
            time=args.time,
            day_of_week=_DOW_MAP.get(args.day_of_week) if args.day_of_week else None,
            day_of_month=args.day_of_month,
            prompt=args.prompt,
            to_email=args.to_email,
            cwd=args.cwd,
            subject_prefix=args.subject_prefix,
            interactive=False,
        )
    elif sub == "enable":
        cmd_schedule_enable(store, args.name)
    elif sub == "disable":
        cmd_schedule_disable(store, args.name)
    elif sub == "delete":
        cmd_schedule_delete(store, args.name, assume_yes=args.yes)
    elif sub == "run-now":
        from mailcode.utils.claude_runner import call_claude
        from mailcode.channels.email_channel import EmailChannel
        cmd_schedule_run_now(store, args.name,
            email_channel=EmailChannel(),
            call_claude_fn=call_claude,
        )
    elif sub == "validate":
        cmd_schedule_validate(store)
    else:
        print("用法: mailcode schedule <list|show|add|enable|disable|delete|run-now|validate>", file=sys.stderr)
        sys.exit(1)


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
    ok = run_health(send_test=getattr(args, "send", False))
    sys.exit(0 if ok else 1)


def cmd_state(args):
    """state 子命令: show 当前水线 / rebuild-baseline 重建基线。"""
    sub = getattr(args, "state_command", None)
    if sub is None:
        print("用法: mailcode state <show|rebuild-baseline>", file=sys.stderr)
        sys.exit(1)

    listener = _build_state_listener()

    if sub == "show":
        summary = listener.get_state_summary()
        print("📊 MailCode 监听状态")
        print(f"   UID watermark:    {summary['watermark']}")
        print(f"   UIDVALIDITY:      {summary['uid_validity']}")
        print(f"   processed_uids:   {summary['processed_uids_count']} 个")
        print(f"   sent_messages:    {summary['sent_messages_count']} 个")
        print(f"   state 文件:       {summary['state_path']}")
    elif sub == "rebuild-baseline":
        # 交互式确认 (避免误操作清空所有 processed_uids)
        if not getattr(args, "yes", False):
            if not sys.stdin.isatty():
                print("❌ 非交互环境必须加 --yes 确认", file=sys.stderr)
                sys.exit(1)
            print("⚠️  即将清空 watermark 和 processed_uids, 并重新扫描邮箱建基线。")
            print("    历史邮件会被视为已处理 (不会重发回复)。")
            try:
                answer = input("    确认执行? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer != "y":
                print("已取消")
                return
        result = listener.rebuild_baseline(assume_yes=True)
        print("✅ 基线已重建")
        print(f"   清空 processed_uids: {result['cleared_uids']} 个")
        print(f"   新 watermark:        {result['watermark']}")
        print(f"   新 UIDVALIDITY:      {result['uid_validity']}")
    else:
        print("用法: mailcode state <show|rebuild-baseline>", file=sys.stderr)
        sys.exit(1)


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
            "本 CLI 主要用于: 配置管理、连通性检查、启动中继（含定时任务调度器）、维护 Agent 对话 session。"
        ),
        epilog=(
            "典型使用流程:\n"
            "  1) 首次部署   mailcode config init           # 生成默认配置\n"
            "  2) 编辑授权码 mailcode config path && $EDITOR .../config.json\n"
            "  3) 校验配置   mailcode config validate\n"
            "  4) 自检连通性 mailcode health\n"
            "  5) 启动中继   mailcode serve                  # 长期后台运行 (默认 IDLE 长连接, 含定时任务调度器)\n"
            "  6) 维护会话   mailcode session list|show|delete|cleanup|stats\n"
            "  7) 定时任务   mailcode schedule add/list|enable|disable|run-now\n"
            "\n"
            "调试提示:\n"
            "  • 想看收到什么邮件但不想执行: mailcode serve --once --dry-run\n"
            "  • 想用临时配置:                  mailcode --config /tmp/x.json <子命令>\n"
            "  • 集成测试配置:                  mailcode config init-test\n"
            "  • 重建监听水线:                  mailcode state rebuild-baseline"
        ),
    )
    parser.add_argument("--version", action="version", version=f"mailcode {__version__}")
    parser.add_argument("--config", "-c", metavar="PATH",
                        help="指定配置文件路径（默认: ~/.config/mailcode/config.json）")

    subparsers = parser.add_subparsers(dest="command", title="子命令", metavar="<子命令>")

    # ── serve ──
    p_serve = subparsers.add_parser(
        "serve",
        help="启动 IMAP 监听中继 (前台常驻, 含定时任务调度器)",
        description=(
            "启动 IMAP 监听中继: 拉取 bot 邮箱里的未读邮件, 注入本地 AI Agent, 把回复通过 SMTP 转发回发件人。\n"
            "同时运行定时任务调度器: 按 schedules.json 中的配置在后台周期性执行 claude -p 并邮件通知结果。\n"
            "默认使用 IMAP IDLE 长连接 (实时推送, 适用于 QQ / Gmail / Outlook)。\n"
            "126/163 邮箱不支持 IDLE, 自动回退到轮询, 需将 check_interval 调到 60-120 秒避免反滥用。\n"
            "Ctrl-C 退出, 日志写入 ~/.config/mailcode/relay.log。"
        ),
    )
    p_serve.add_argument("--dry-run", action="store_true",
                        help="干跑模式: 只打印邮件内容, 不注入 Agent, 不发送回复（用于排查邮件解析）")
    p_serve.add_argument("--once", action="store_true",
                        help="处理完一轮未读后退出 (调度器不启动, 用于脚本/调试, 默认持续监听)")
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

    # ── state ──
    p_state = subparsers.add_parser(
        "state",
        help="查看/重建监听水线 (show|rebuild-baseline)",
        description=(
            "管理 IMAP 监听状态 (UID watermark / UIDVALIDITY / processed_uids)。\n"
            "日常用 `show` 查看; UIDVALIDITY 跳变后或怀疑状态污染时用 `rebuild-baseline`。"
        ),
    )
    p_state_sub = p_state.add_subparsers(dest="state_command", title="state 动作", metavar="<动作>")
    p_state_sub.add_parser("show", help="显示当前 watermark / UIDVALIDITY / processed_uids 数量")
    p_state_rebuild = p_state_sub.add_parser(
        "rebuild-baseline",
        help="清空 watermark + processed_uids 并重新扫描邮箱建基线",
        description=(
            "清空 UID watermark 和 processed_uids, 重新扫描邮箱建立新基线。\n"
            "历史邮件会被视为已处理 (不会重发回复); 后续新邮件正常处理。\n"
            "适用: UIDVALIDITY 跳变后恢复、怀疑 processed_uids 被污染。\n"
            "默认要求交互确认; --yes 跳过 (用于脚本)。"
        ),
    )
    p_state_rebuild.add_argument("-y", "--yes", action="store_true",
                                 help="跳过交互式确认")

    # ── session ──
    p_session = subparsers.add_parser(
        "session",
        help="对话 session 管理 (list|show|delete|cleanup|stats)",
        description=(
            "Session = 同一邮件主题下的多轮对话上下文, 以独立文件持久化。\n"
            "用子命令 list/show/delete/cleanup/stats 来维护这些 session。"
        ),
    )
    p_session_sub = p_session.add_subparsers(dest="session_command", title="session 动作", metavar="<动作>")

    p_session_list = p_session_sub.add_parser("list", help="列出全部 session (ID/发件人/主题/最近活动/消息数/工作目录)")
    p_session_list.add_argument("--wide", action="store_true", help="不截断显示")
    p_session_list.add_argument("--filter", help="按发件人或主题关键词过滤")

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

    p_session_sub.add_parser("stats", help="显示 session 统计信息")

    # ── schedule ──
    p_schedule = subparsers.add_parser(
        "schedule",
        help="定时任务管理 (list|show|add|enable|disable|delete|run-now|validate)",
        description=(
            "管理 ~/.config/mailcode/schedules.json 中的定时任务 (无需外部 cron)。\n"
            "支持 interval（固定间隔）/ daily（每天定点）/ weekly（每周某天）/ monthly（每月某天）四种类型。\n"
            "运行 mailcode serve 时调度器自动在后台运行, 到期触发 claude -p <prompt> 并邮件通知。\n"
            "missed_run 自动跳过不追赶; 任务配置热加载, 增删改立即生效无需重启 serve。"
        ),
    )
    p_schedule_sub = p_schedule.add_subparsers(
        dest="schedule_command", title="schedule 动作", metavar="<动作>"
    )

    p_schedule_sub.add_parser("list", help="列出全部定时任务 (名称/类型/调度/状态/下次运行时间)")

    p_schedule_sub.add_parser("validate", help="校验 schedules.json 完整性 (名称唯一/调度合法/邮箱有效/prompt 非空, 只读不改)")

    p_schedule_show = p_schedule_sub.add_parser("show", help="查看单个定时任务详情")
    p_schedule_show.add_argument("name", help="任务名称")

    p_schedule_add = p_schedule_sub.add_parser("add", help="添加新定时任务")
    p_schedule_add.add_argument("name", help="任务名称 (唯一)")
    p_schedule_add.add_argument("--type", choices=["interval", "daily", "weekly", "monthly"],
                                required=True)
    p_schedule_add.add_argument("--interval-seconds", type=int,
                                help="type=interval 时必填")
    p_schedule_add.add_argument("--time", help="type=daily/weekly/monthly 时必填, HH:MM 格式")
    p_schedule_add.add_argument("--day-of-week",
                                choices=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                                help="type=weekly 时必填")
    p_schedule_add.add_argument("--day-of-month", type=int, choices=range(1, 32),
                                metavar="[1-31]", help="type=monthly 时必填")
    p_schedule_add.add_argument("--prompt", required=True, help="Claude prompt 文本")
    p_schedule_add.add_argument("--to-email", required=True, help="结果邮件的收件人")
    p_schedule_add.add_argument("--cwd", help="Claude 工作目录 (可选)")
    p_schedule_add.add_argument("--subject-prefix", help="邮件主题前缀 (可选)")

    p_schedule_enable = p_schedule_sub.add_parser("enable", help="启用一个定时任务")
    p_schedule_enable.add_argument("name", help="任务名称")

    p_schedule_disable = p_schedule_sub.add_parser("disable", help="禁用一个定时任务")
    p_schedule_disable.add_argument("name", help="任务名称")

    p_schedule_delete = p_schedule_sub.add_parser("delete", help="删除一个定时任务")
    p_schedule_delete.add_argument("name", help="任务名称")
    p_schedule_delete.add_argument("-y", "--yes", action="store_true",
                                   help="跳过确认")

    p_schedule_run = p_schedule_sub.add_parser(
        "run-now",
        help="立即同步执行指定任务 (不依赖 serve, 不污染 last_run_at / next_run_at)",
    )
    p_schedule_run.add_argument("name", help="任务名称")

    # -- chat --
    p_chat = subparsers.add_parser(
        "chat",
        help="终端交互模式，直接与 Claude 对话（不经过邮件）",
        description="启动交互式 REPL，输入内容直接发送给 Claude。支持 --session-id 恢复已有对话。",
    )
    p_chat.add_argument("--session-id", help="恢复已有对话的 session ID")
    p_chat.add_argument("--cwd", default="", help="Claude 工作目录（默认当前目录）")

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
    elif args.command == "schedule":
        cmd_schedule(args)
    elif args.command == "state":
        cmd_state(args)
    elif args.command == "chat":
        from mailcode.cli_chat import cmd_chat
        cmd_chat(args)

if __name__ == "__main__":
    main()
