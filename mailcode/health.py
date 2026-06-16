import smtplib
import imaplib
import logging

from mailcode.config import load_config, get_smtp_config, get_imap_config, get_email_config

logger = logging.getLogger("mailcode")


def _check(label: str, ok: bool, detail: str = ""):
    icon = "✓" if ok else "✗"
    print(f"  {icon} {label}")
    if detail:
        print(f"      {detail}")
    return ok


def run_health(send_test: bool = True) -> bool:
    """运行邮件连通性检查"""
    print("MailCode Health Check\n")

    all_ok = True

    # ── 配置检查 ──
    print("配置检查:")
    smtp_cfg = get_smtp_config()
    imap_cfg = get_imap_config()
    email_cfg = get_email_config()

    all_ok &= _check("SMTP 用户", bool(smtp_cfg.get("user")),
                      f"host={smtp_cfg.get('host')} port={smtp_cfg.get('port')} user={smtp_cfg.get('user')}")
    all_ok &= _check("SMTP 密码", bool(smtp_cfg.get("pass")))
    all_ok &= _check("IMAP 用户", bool(imap_cfg.get("user")),
                      f"host={imap_cfg.get('host')} port={imap_cfg.get('port')} user={imap_cfg.get('user')}")
    all_ok &= _check("IMAP 密码", bool(imap_cfg.get("pass")))
    all_ok &= _check("发件地址", bool(email_cfg.get("from")),
                      f"from={email_cfg.get('from')}")
    config = load_config()
    allowed = config.get("security", {}).get("allowed_senders", [])
    all_ok &= _check("发件人白名单", bool(allowed),
                      f"{len(allowed)} 个" if allowed else "空列表（serve 会拒绝所有邮件）")

    if not smtp_cfg.get("user") or not smtp_cfg.get("pass") or not imap_cfg.get("user") or not imap_cfg.get("pass"):
        print("\n  配置不完整，跳过网络检查")
        return all_ok

    # ── SMTP 检查 ──
    print("\nSMTP 检查:")
    host = smtp_cfg.get("host", "smtp.qq.com")
    port = smtp_cfg.get("port", 465)
    secure = smtp_cfg.get("secure", False)

    server = None
    try:
        server = smtplib.SMTP_SSL(host, port, timeout=10) if secure else smtplib.SMTP(host, port, timeout=10)
        if not secure:
            server.starttls()
        all_ok &= _check("连接", True, f"{host}:{port}")
    except Exception as e:
        all_ok &= _check("连接", False, str(e))
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass
        print("\n  跳过后续 SMTP 检查")
        return all_ok

    try:
        server.login(smtp_cfg["user"], smtp_cfg["pass"])
        all_ok &= _check("登录", True)
    except Exception as e:
        all_ok &= _check("登录", False, str(e))
        server.quit()
        return all_ok

    to_email = email_cfg.get("from", "")
    if to_email:
        try:
            if send_test:
                from_email = email_cfg.get("from", smtp_cfg["user"])
                server.sendmail(smtp_cfg["user"], [to_email],
                                f"From: {from_email}\nSubject: MailCode Health Check\n\nThis is a test email from MailCode health check.")
                all_ok &= _check("发信", True, f"to={to_email}")
            else:
                _check("发信", True, "跳过 (未指定 --send)")
        except Exception as e:
            all_ok &= _check("发信", False, str(e))
    else:
        _check("发信", False, "未配置 mailcode_bot.email")
        return all_ok

    server.quit()

    # ── IMAP 检查 ──
    print("\nIMAP 检查:")
    host = imap_cfg.get("host", "imap.qq.com")
    port = imap_cfg.get("port", 993)

    try:
        mail = imaplib.IMAP4_SSL(host, port, timeout=10)
        all_ok &= _check("连接", True, f"{host}:{port}")
    except Exception as e:
        all_ok &= _check("连接", False, str(e))
        print("\n  跳过后续 IMAP 检查")
        return all_ok

    try:
        mail.login(imap_cfg["user"], imap_cfg["pass"])
        all_ok &= _check("登录", True)
    except Exception as e:
        all_ok &= _check("登录", False, str(e))
        mail.logout()
        return all_ok

    try:
        imaplib.Commands["ID"] = ("NONAUTH", "AUTH", "SELECTED")
        mail._simple_command("ID", '("name" "mailcode" "version" "1.0")')
    except Exception:
        pass

    try:
        typ, dat = mail.select("INBOX")
        ok = typ == "OK"
        count = len(dat[0].split()) if dat and dat[0] else 0
        all_ok &= _check("收件箱", ok, f"select={typ} 邮件数={count}")
    except Exception as e:
        all_ok &= _check("收件箱", False, str(e))

    mail.logout()

    print(f"\n{'='*30}")
    print(f"结果: {'全部正常' if all_ok else '存在问题'}")
    return all_ok
