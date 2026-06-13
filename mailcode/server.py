"""MailCode IMAP 监听服务 — 由 cli.py:cmd_serve 调用"""

import sys
import signal
import logging

from mailcode.relay.email_listener import IMAPListener

logger = logging.getLogger("mailcode")


def run_serve(args):
    """启动 IMAP 监听器，根据 args 运行（单次轮询 / IDLE / 普通监听）。

    Args:
        args: 具有 dry_run、once、idle 属性的 Namespace 对象。
    """
    listener = IMAPListener()

    # ---- 启动调度器 (--once 模式不启动) ----
    scheduler = None
    if not args.once:
        try:
            from mailcode.config import get_schedule_config
            sc = get_schedule_config()
        except Exception:
            sc = {}
        if sc.get("enabled", True):
            from mailcode.relay.scheduler import Scheduler, ScheduleStore
            from pathlib import Path
            sched_path = Path.home() / ".config" / "mailcode" / "schedules.json"
            sched_store = ScheduleStore(sched_path)
            scheduler = Scheduler(
                listener.email_channel,
                sched_store,
                dry_run=args.dry_run,
                tick_seconds=sc.get("tick_seconds", 30),
            )
            scheduler.start()
            logger.info("调度器已启动 (tick=%ss, dry_run=%s)",
                        sc.get("tick_seconds", 30), args.dry_run)
    # ---- 结束 ----

    def signal_handler(signum, frame):
        print("\n🛑 收到关闭信号，正在停止...", flush=True)
        listener.stop()
        if scheduler:
            scheduler.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        if args.once:
            emails = listener.fetch_unread_emails(dry_run=args.dry_run)
            logger.info(f"发现 {len(emails)} 封新邮件")
            for entry in emails:
                success, message = listener.process_email(
                    entry, dry_run=args.dry_run, force_session=args.session or None,
                )
                logger.info(f"{'✅' if success else '❌'} [{entry.get('token')}] {message}")
        else:
            listener.listen(dry_run=args.dry_run, use_idle=not args.no_idle)
    except Exception:
        logger.exception("监听器主循环异常退出")
        sys.exit(1)
    finally:
        if scheduler:
            scheduler.stop()
            scheduler.join(timeout=10)
