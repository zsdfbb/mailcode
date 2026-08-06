"""MailCode IMAP 监听服务 — 由 cli.py:cmd_serve 调用"""

import os
import sys
import fcntl
import signal
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from mailcode.relay.email_listener import IMAPListener

logger = logging.getLogger("mailcode")

# 常驻中继互斥锁: 多个 mailcode serve 进程共享同一 state.json, 若第二个常驻
# 中继同时启动, 会与已有中继抢写 state.json (原子写竞态, 见 email_listener
# ._save_state)。flock 保证同一时刻只有一个常驻中继。
_SERVE_LOCK_PATH = Path.home() / ".config" / "mailcode" / "serve.lock"


def acquire_serve_lock(lock_path: Optional[Path] = None) -> Optional[int]:
    """抢占 serve.lock (flock 非阻塞), 防止第二个常驻中继启动。

    返回持有锁的 fd —— 调用方必须持有引用直至进程结束 (进程退出自动释放锁);
    已有中继在跑时返回 None。
    --once 一次性模式不抢占, 可与常驻中继并存 (它只做单轮拉取, 不写 state.json)。
    """
    path = lock_path or _SERVE_LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(f"{os.getpid()}\n")
        fd.flush()
        return fd
    except OSError:
        fd.close()
        return None


def run_serve(args):
    """启动 IMAP 监听器，根据 args 运行（单次轮询 / IDLE / 普通监听）。

    Args:
        args: 具有 dry_run、once、idle 属性的 Namespace 对象。
    """
    listener = IMAPListener()

    # ---- 事件回调: 控制台实时输出 ----
    _last_hb_print = 0.0
    HB_PRINT_INTERVAL = 300  # 秒 — 心跳控制台打印间隔

    def _console_echo(event, **data):
        """打印事件到控制台。"""
        nonlocal _last_hb_print
        now = datetime.now().strftime("%H:%M:%S")
        if event == "email_received":
            sender = data.get("sender_email", "")
            subject = data.get("subject", "")
            print(f"[{now}] 📬 收到  {sender}  →  {subject}", flush=True)
        elif event == "claude_start":
            sender = data.get("from_email", "")
            subject = data.get("subject", "")
            print(f"[{now}] 🤖 调 Claude  ({sender})", flush=True)
        elif event == "reply_sent":
            dur = data.get("duration", 0)
            print(f"[{now}] ✅ 回复已发送  (耗时 {dur:.0f}s)", flush=True)
        elif event == "claude_failed":
            print(f"[{now}] ❌ Claude 处理失败", flush=True)
        elif event == "heartbeat":
            # 后台健康检查仍在 60s 执行, 但控制台不打印 — 仅写入日志, 避免刷屏
            import time
            t = time.monotonic()
            if t - _last_hb_print >= HB_PRINT_INTERVAL:
                _last_hb_print = t
                logger.info("🔄 IDLE 心跳正常  (每 %ss 记录一次)", HB_PRINT_INTERVAL)

    listener.on("email_received", _console_echo)
    listener.on("claude_start", _console_echo)
    listener.on("reply_sent", _console_echo)
    listener.on("claude_failed", _console_echo)
    listener.on("heartbeat", _console_echo)

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
