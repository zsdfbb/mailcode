"""mailcode session 子命令的 CLI 格式化与呈现"""

import sys


def fmt_ts(ts) -> str:
    """time.time() 浮点 → 'YYYY-MM-DD HH:MM' 本地时间。"""
    import datetime
    try:
        ts = float(ts or 0)
    except (TypeError, ValueError):
        return "-"
    if ts <= 0:
        return "-"
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def shorten(text: str, width: int) -> str:
    """按显示宽度截断（CJK 字符按 2 算，简化处理）。"""
    if text is None:
        return ""
    text = str(text).replace("\n", " ").replace("\r", " ").strip()
    if width <= 0:
        return text
    if len(text) <= width:
        return text
    return text[: max(width - 1, 1)] + "…"


def first_incoming(emails):
    """从 emails 列表中找出首封 incoming 邮件（作为 session 的代表）。"""
    if not emails:
        return None
    for e in emails:
        if e.get("direction") == "incoming":
            return e
    return emails[0]


def cmd_session_list(handler):
    """列出所有 session。"""
    sessions = handler.list_sessions()
    if not sessions:
        print("暂无 session")
        return

    rows = []
    for s in sessions:
        sid = s.get("session_id", "")
        detail = handler.get_session_status(sid)
        first = first_incoming(detail.get("emails", [])) if detail else None
        from_email = first.get("from", "-") if first else "-"
        subject = first.get("subject", "-") if first else "-"
        rows.append({
            "id": sid,
            "from": from_email,
            "subject": subject,
            "last": fmt_ts(s.get("last_interaction")),
            "count": s.get("email_count", 0),
            "cwd": s.get("cwd", "") or "-",
        })

    header = f"{'SESSION ID':<14}  {'FROM':<28}  {'SUBJECT':<24}  {'LAST INTERACTION':<17}  {'MSGS':>4}  CWD"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{shorten(r['id'], 14):<14}  "
            f"{shorten(r['from'], 28):<28}  "
            f"{shorten(r['subject'], 24):<24}  "
            f"{r['last']:<17}  "
            f"{r['count']:>4}  "
            f"{shorten(r['cwd'], 40)}"
        )


def cmd_session_show(handler, session_id: str):
    """查看单个 session 详情。"""
    detail = handler.get_session_status(session_id)
    if detail is None:
        print(f"未找到 session: {session_id}", file=sys.stderr)
        sys.exit(1)

    emails = detail.get("emails", [])
    first = first_incoming(emails)
    from_email = first.get("from", "-") if first else "-"
    subject = first.get("subject", "-") if first else "-"

    print(f"Session:    {detail.get('session_id', session_id)}")
    print(f"From:       {from_email}")
    print(f"Subject:    {subject}")
    print(f"Created:    {fmt_ts(detail.get('created_at'))}")
    print(f"Last seen:  {fmt_ts(detail.get('last_interaction'))}")
    print(f"Cwd:        {detail.get('cwd', '') or '-'}")
    print(f"Messages:   {detail.get('email_count', len(emails))}")
    print()
    print(f"Emails ({len(emails)}):")
    if not emails:
        print("  (空)")
        return
    for e in emails:
        direction = e.get("direction", "?")
        tag = "[in]" if direction == "incoming" else "[out]" if direction == "outgoing" else f"[{direction}]"
        ts = fmt_ts(e.get("ts") or e.get("date"))
        addr = e.get("from", "-")
        body = shorten(e.get("body", ""), 60)
        print(f"  {tag:<5} {ts:<17}  {shorten(addr, 28):<28}  {body}")


def cmd_session_delete(handler, session_id: str, assume_yes: bool = False):
    """删除 session，含确认提示。"""
    detail = handler.get_session_status(session_id)
    if detail is None:
        print(f"未找到 session: {session_id}", file=sys.stderr)
        sys.exit(1)

    if not assume_yes:
        emails = detail.get("emails", [])
        first = first_incoming(emails)
        from_email = first.get("from", "-") if first else "-"
        subject = first.get("subject", "-") if first else "-"
        print(f"即将删除 session: {session_id}")
        print(f"  From:    {from_email}")
        print(f"  Subject: {subject}")
        print(f"  Emails:  {len(emails)}")
        try:
            confirm = input("确认删除? [y/N]: ").strip().lower()
        except EOFError:
            confirm = ""
        if confirm not in ("y", "yes"):
            print("已取消")
            return

    ok = handler.terminate_session(session_id)
    if ok:
        print(f"已删除 session: {session_id}")
    else:
        print(f"删除失败: {session_id}", file=sys.stderr)
        sys.exit(1)


def cmd_session_cleanup(handler, dry_run: bool = False):
    """按 TTL 清理过期 session。"""
    if dry_run:
        count = handler._cleanup_expired_sessions(dry_run=True)
        print(f"[dry-run] 将清理 {count} 个过期 session (实际未删除)")
    else:
        count = handler._cleanup_expired_sessions(dry_run=False)
        print(f"已清理 {count} 个过期 session")
