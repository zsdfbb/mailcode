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

    def signal_handler(signum, frame):
        print("\n🛑 收到关闭信号，正在停止监听器...", flush=True)
        listener.stop()

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
