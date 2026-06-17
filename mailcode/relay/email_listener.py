"""IMAP 邮件监听器（简化版 — 仅支持对话路由）"""

import imaplib
import email
import json
import random
import re
import socket
import sys
import time
import threading
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple
from email.header import decode_header
from email.utils import parseaddr

from mailcode.config import get_imap_config, get_email_config, get_auth_policy, is_session_enabled
from mailcode.channels.email_channel import EmailChannel
from mailcode.relay.security import SecurityChecker

if TYPE_CHECKING:
    from mailcode.relay.conversation_handler import ConversationHandler
    from mailcode.relay.resume_handler import ResumeConversationHandler  # NEW
    from mailcode.relay.stateless_handler import StatelessHandler

logger = logging.getLogger("mailcode")

imaplib.Commands["ID"] = ("NONAUTH", "AUTH", "SELECTED")

# Python 3.13+ 把 IMAP IDLE 改成 context manager (`with M.idle() as idler:`),
# 旧的 `mail.idle_response()` / `mail.idle_done()` 被移除. 这里用特征方法探测.
_NEW_IDLE_API: bool = not hasattr(imaplib.IMAP4, "idle_response")

_MAILCODE_HOME = Path.home() / ".config" / "mailcode"


class _Backoff:
    """指数退避: 1, 2, 4, 8, 16, 32, 60, 60, ... 秒 + ±20% jitter。

    连接成功时调用 reset() 重置回 1。
    """

    BASE = 1
    CAP = 60
    FACTOR = 2
    JITTER = 0.2

    def __init__(self):
        self._n = 0

    def reset(self) -> None:
        self._n = 0

    def next_delay(self) -> float:
        base = min(self.CAP, self.BASE * (self.FACTOR ** self._n))
        self._n += 1
        spread = base * self.JITTER
        return base + random.uniform(-spread, spread)


class IMAPListener:
    def __init__(self, imap_config=None, email_config=None, smtp_config=None):
        self.imap_config = imap_config or get_imap_config()
        self.email_config = email_config or get_email_config()
        self.smtp_config = smtp_config

        _MAILCODE_HOME.mkdir(parents=True, exist_ok=True)
        self.state_path = _MAILCODE_HOME / "state.json"

        self.security_checker = SecurityChecker()
        self.email_channel = EmailChannel(smtp_config=self.smtp_config, email_config=self.email_config)
        self._conv_handler: Optional["ConversationHandler"] = None
        self._resume_handler: Optional["ResumeConversationHandler"] = None  # NEW
        self._stateless_handler: Optional["StatelessHandler"] = None
        self._use_resume: bool = True  # NEW: default to new handler
        # 事件回调注册表: event_type -> [callable, ...]
        self._event_listeners: Dict[str, List[Callable]] = {}

        self.check_interval = self.email_config.get("check_interval", 5)
        self.processed_uids: set = set()
        self.sent_messages: list = []
        self._load_state()

        self._idle_timeout = 5
        self._idle_ready = threading.Event()
        self._idle_thread: Optional[threading.Thread] = None
        self._mail: Optional[imaplib.IMAP4_SSL] = None
        self._active_idle_mail: Optional[imaplib.IMAP4_SSL] = None
        self._stopped = threading.Event()

        # 预判性重连: QQ 邮箱约 2h 静默断连, 提前至 90min 主动重建连接
        self.FORCED_RECONNECT_INTERVAL = 5400  # 秒, 可被测试覆盖改写
        self._last_connect_time = time.monotonic()

    def stop(self):
        """向监听循环发出干净退出信号。从信号处理程序调用。

        - 旧 imaplib API (<3.13): 主循环在 IDLE 线程里阻塞, 调用
          `mail.idle_done()` 立刻打破阻塞, SIGINT < 1s 生效.
        - 新 imaplib API (>=3.13): IDLE 在主线程同步等待 (`with mail.idle()`),
          旧 `idle_done()` 已移除, signal handler 不能可靠打断 socket read.
          改成只设 `_stopped`, 由 `_wait_for_idle_new` 的 `duration` 超时
          自然返回, 然后外层循环检测 `_stopped` 退出. 最长延迟 `_idle_timeout`.
        """
        self._stopped.set()
        if self._active_idle_mail is not None:
            mail = self._active_idle_mail
            self._active_idle_mail = None
            if not _NEW_IDLE_API:
                try:
                    mail.idle_done()
                except Exception:
                    pass

    def on(self, event: str, callback: Callable):
        """注册事件回调。event: 'email_received'|'claude_start'|'claude_done'|'reply_sent'|'heartbeat'"""
        self._event_listeners.setdefault(event, []).append(callback)

    def _emit(self, event: str, **data):
        """触发事件回调。"""
        for cb in self._event_listeners.get(event, []):
            try:
                cb(event=event, **data)
            except Exception as e:
                logger.debug("事件回调异常 (%s): %s", event, e)

    def _load_state(self):
        """加载 state.json, 同步 self.processed_uids 和 self.sent_messages。"""
        if not self.state_path.exists():
            self.processed_uids = set()
            self.sent_messages = []
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.processed_uids = set(data.get("processed_uids", []))
            self.sent_messages = list(data.get("sent_messages", []))
        except Exception:
            self.processed_uids = set()
            self.sent_messages = []

    def _save_state(self, state: dict):
        """原子写 state 到 state.json。state 应包含 processed_uids + sent_messages。"""
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp_path.replace(self.state_path)

    def _prune_old_sent_messages(self):
        """清理 7 天前的 sent_messages, 限制 processed_uids 上限。"""
        cutoff = datetime.now() - timedelta(days=7)
        filtered = []
        for msg in self.sent_messages:
            sent_at = msg.get("sent_at", "")
            if sent_at:
                try:
                    msg_time = datetime.fromisoformat(sent_at)
                    if msg_time > cutoff:
                        filtered.append(msg)
                except Exception:
                    filtered.append(msg)
        self.sent_messages = filtered
        if len(self.processed_uids) > 10000:
            self.processed_uids = set()

    def _log_connection_error(self, e: Exception, attempt: int, next_delay: Optional[float]) -> None:
        """结构化输出 IMAP 连接错误, 包含 host:port / 异常类名 / 重试次数 / 下次延迟。"""
        host = self.imap_config.get("host", "?")
        port = self.imap_config.get("port", "?")
        type_name = type(e).__name__
        if next_delay is not None:
            logger.error(
                f"IMAP 连接失败 [{host}:{port}, 尝试 #{attempt}]: "
                f"{type_name}, {next_delay:.1f}s 后重试"
            )
        else:
            logger.error(
                f"IMAP 连接失败 [{host}:{port}]: {type_name}: {e}"
            )

    def _init_baseline(self):
        """建立启动基线：将当前所有 UNSEEN 邮件标记为已处理，后续只响应新邮件"""
        try:
            mail = self._connect()
            mail.select("INBOX")
            status, messages = mail.search(None, "UNSEEN")
            if status == "OK" and messages[0]:
                count = 0
                for uid_bytes in messages[0].split():
                    self.processed_uids.add(uid_bytes.decode())
                    count += 1
                logger.info(f"邮件基线已建立: {count} 封历史未读邮件不处理")
            mail.logout()
        except Exception as e:
            logger.warning(f"建立邮件基线失败: {e}")

    def _connect(self) -> imaplib.IMAP4_SSL:
        host = self.imap_config.get("host", "imap.qq.com")
        port = self.imap_config.get("port", 993)
        user = self.imap_config.get("user", "")
        password = self.imap_config.get("pass", "")

        try:
            mail = imaplib.IMAP4_SSL(host, port)
            mail.sock.settimeout(15)
            # 部分邮件服务商（如网易）要求在登录前发送 ID 指令
            try:
                mail._simple_command("ID", '("name" "mailcode" "vendor" "mailcode" "support-email" "' + user + '")')
            except Exception:
                pass
            mail.login(user, password)
            try:
                mail._simple_command("ID", '("name" "mailcode" "vendor" "mailcode" "support-email" "' + user + '")')
            except Exception:
                pass
            self._mail = mail
            return mail
        except Exception as e:
            logger.error("IMAP 连接失败: %s", e)
            print(f"  ❌ IMAP 连接失败: {e}", file=sys.stderr)
            raise

    def _wait_for_idle(self, mail: imaplib.IMAP4_SSL) -> bool:
        """阻塞等待 IMAP IDLE 事件. 返回 True=收到事件, False=超时/异常.

        按 imaplib 版本分两条路径:
        - 旧 API: 起一个守护线程跑 `mail.idle()` + `idle_response()`, 主线程 wait event.
        - 新 API (3.13+): 主线程直接 `with mail.idle(duration=...)` 同步等待,
          超时由 duration 控制, SIGINT 最多延迟 _idle_timeout 秒.
        """
        if _NEW_IDLE_API:
            return self._wait_for_idle_new(mail)
        return self._wait_for_idle_old(mail)

    def _wait_for_idle_old(self, mail: imaplib.IMAP4_SSL) -> bool:
        if self._idle_thread and self._idle_thread.is_alive():
            self._idle_thread.join(timeout=3)

        def idle_thread():
            try:
                while self._idle_ready.is_set():
                    mail.idle()
                    response = mail.idle_response()
                    if response:
                        self._idle_mail = mail
                        self._idle_ready.clear()
            except Exception:
                logger.exception("IDLE 线程异常")

        self._idle_ready.set()
        self._idle_thread = threading.Thread(target=idle_thread, daemon=True)
        self._idle_thread.start()

        try:
            self._idle_ready.wait(timeout=self._idle_timeout)
        except Exception:
            pass

        got_event = not self._idle_ready.is_set()

        # 超时后必须退出 idle_thread, 否则主线程后续的 NOOP/SELECT 会与
        # idle_thread 中阻塞的 mail.idle_response() 撞协议, 导致 socket error.
        if not got_event:
            self._idle_ready.clear()
            try:
                mail.idle_done()
            except Exception:
                pass
            _t = self._idle_thread
            self._idle_thread = None
            if _t and _t.is_alive():
                _t.join(timeout=3)

        return got_event

    def _wait_for_idle_new(self, mail: imaplib.IMAP4_SSL) -> bool:
        """Py3.13+ IDLE 路径: 同步 `with mail.idle(duration=...)` 等待.

        duration 让 IDLE 迭代器在最多 `_idle_timeout` 秒后自然 StopIteration;
        连接级异常向上抛, 由 `_listen_idle` 的退避循环重连.
        """
        try:
            with mail.idle(duration=self._idle_timeout) as idler:
                for _typ, _data in idler:
                    return True
            return False
        except (ConnectionError, EOFError, socket.timeout, imaplib.IMAP4.abort):
            raise
        except imaplib.IMAP4.error:
            # 服务器拒绝 IDLE 指令（如 "System busy!"）是瞬时错误,
            # 包装为 ConnectionError 让 _listen_idle 的退避+重连逻辑处理
            raise ConnectionError("IDLE 被服务器拒绝")
        except Exception:
            logger.exception("IDLE 异常")
            return False

    def _reconnect(self) -> imaplib.IMAP4_SSL:
        if self._mail is not None:
            try:
                self._mail.logout()
            except Exception:
                pass
            self._mail = None
        # 先清旧 event，让旧 idle_thread 的 while 循环看到 is_set()==False 后退出
        self._idle_ready.clear()
        # 重建 event 并 set，让 _wait_for_idle 能重新启动 IDLE 线程
        self._idle_ready = threading.Event()
        self._idle_ready.set()
        return self._connect()

    def _decode_email_header(self, header_value: str) -> str:
        if not header_value:
            return ""
        decoded_parts = []
        for part, encoding in decode_header(header_value):
            if isinstance(part, bytes):
                try:
                    decoded_parts.append(part.decode(encoding or "utf-8", errors="replace"))
                except Exception:
                    decoded_parts.append(part.decode("utf-8", errors="replace"))
            else:
                decoded_parts.append(part)
        return "".join(decoded_parts)

    def _extract_body(self, msg) -> str:
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body = payload.decode(charset, errors="replace")
                        break
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="replace")
        return body

    def _clean_body(self, body: str) -> str:
        lines = body.split("\n")
        cleaned_lines = []

        for line in lines:
            if re.match(r"-+ ?Original Message", line) or re.match(r".* On .* wrote:", line):
                break

            if line.startswith(">"):
                continue

            if line.strip().startswith("-- "):
                break

            if any(greeting in line.lower() for greeting in ["sent from", "best regards", "thanks", "regards", "sincerely"]):
                if len(line.strip()) < 50:
                    break

            cleaned_lines.append(line)

        body = "\n".join(cleaned_lines)
        body = re.sub(r"\n{3,}", "\n\n", body)
        return body.strip()

    def _is_own_message(self, msg) -> bool:
        if msg.get("X-MailCode-Remote-Token"):
            return True
        if msg.get("X-OpenCode-Remote-Token"):
            return True
        return False

    def _is_duplicate(self, msg_id: str, uid: str) -> bool:
        if uid in self.processed_uids:
            return True

        for msg in self.sent_messages:
            if msg.get("message_id") == msg_id:
                return True

        return False

    def fetch_unread_emails(
        self,
        dry_run: bool = False,
        mail: Optional[imaplib.IMAP4_SSL] = None,
    ) -> List[Dict]:
        """拉取未读邮件。

        Args:
            dry_run: 干跑模式, 仅记录日志, 不更新 processed_uids。
            mail: 可选已建立的 IMAP 连接。传入时复用 (不负责 logout);
                  传 None 则本次调用内自建自毁 (适合 --once / 轮询路径)。
        """
        results = []
        owns_connection = mail is None
        try:
            if owns_connection:
                mail = self._connect()
            mail.select("INBOX")
            # NOOP 刷新任何残留的挂起响应, 避免后续 SEARCH 读到错误响应
            try:
                mail.noop()
            except Exception:
                pass

            status, messages = mail.search(None, "UNSEEN")
            if status != "OK":
                return results

            uids = messages[0].split()
            if not uids:
                logger.info("SEARCH UNSEEN 无结果 (可能收到非新邮件的 IMAP 通知)")
                return results

            for uid_bytes in uids:
                uid = uid_bytes.decode()
                status, msg_data = mail.fetch(uid_bytes, "(BODY.PEEK[])")
                if status != "OK":
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                msg_id = msg.get("Message-ID", "") or msg.get("Message-Id", "") or uid
                from_header = self._decode_email_header(msg.get("From", ""))
                subject = self._decode_email_header(msg.get("Subject", ""))
                references = msg.get("References", "")
                in_reply_to = msg.get("In-Reply-To", "")

                if self._is_own_message(msg):
                    logger.debug(f"跳过自身邮件: {subject}")
                    continue

                if self._is_duplicate(msg_id, uid):
                    logger.debug(f"跳过重复邮件: UID={uid} MsgID={msg_id}")
                    continue

                sender_email = parseaddr(from_header)[1].lower() or from_header.lower()

                auth_header = self._decode_email_header(msg.get("Authentication-Results", ""))
                policy = get_auth_policy()
                auth_valid, auth_reason = SecurityChecker.verify_auth_results(auth_header, policy)
                if not auth_valid:
                    logger.warning(f"邮件认证失败 [{sender_email}]: {auth_reason}")
                    continue
                elif auth_reason != "OK":
                    logger.info(f"邮件认证状态 [{sender_email}]: {auth_reason}")

                if not self.security_checker.is_sender_allowed(sender_email):
                    logger.info(f"发件人不在白名单中 [{sender_email}], 已跳过")
                    continue

                body = self._extract_body(msg)
                cleaned_body = self._clean_body(body)

                entry = {
                    "uid": uid,
                    "message_id": msg_id,
                    "from": from_header,
                    "sender_email": sender_email,
                    "subject": subject,
                    "body": cleaned_body,
                    "references": references,
                    "in_reply_to": in_reply_to,
                }

                if dry_run:
                    logger.info(f"DRY RUN - UID: {uid}")
                    logger.info(f"DRY RUN - From: {from_header}")
                    logger.info(f"DRY RUN - Subject: {subject}")
                    logger.info(f"DRY RUN - Auth-Results: {auth_header[:200] if auth_header else '(无)'}")
                    logger.info(f"DRY RUN - Auth-Status: {auth_reason}")
                    logger.info(f"DRY RUN - Cleaned Body:\n{cleaned_body[:500]}")
                    results.append(entry)
                    continue

                results.append(entry)
                self.processed_uids.add(uid)
                self._emit("email_received", sender_email=sender_email, subject=subject, message_id=msg_id)

        except (ConnectionError, EOFError, socket.timeout, imaplib.IMAP4.abort) as e:
            # 瞬时网络/连接错误, 抛出由上层 _listen_idle 的退避循环处理
            self._log_connection_error(e, attempt=0, next_delay=None)
            raise
        except imaplib.IMAP4.error as e:
            host = self.imap_config.get("host", "?")
            logger.error(f"IMAP 协议错误 [{host}]: {e}")
            raise
        except Exception as e:
            logger.exception(f"IMAP 监听未知错误: {e}")
            raise
        finally:
            if owns_connection and mail is not None:
                try:
                    mail.logout()
                except Exception:
                    pass

        return results

    @staticmethod
    def _is_system_command(subject: str) -> Optional[str]:
        """检查邮件主题是否为系统命令。返回命令名或 None。"""
        if not subject:
            return None
        s = subject.strip().lower()
        if s in ("status", "status?"):
            return "status"
        if s in ("help", "帮助", "?"):
            return "help"
        if s in ("sessions", "sessions?"):
            return "sessions"
        return None

    def _handle_system_command(self, command: str, email_entry: Dict) -> Tuple[bool, str]:
        """处理系统命令邮件, 直接回复不调 Claude。"""
        from_email = email_entry.get("sender_email", "")
        subject = email_entry.get("subject", "")

        if command == "status":
            # Get active session count
            try:
                mapping_file = _MAILCODE_HOME / "claude_sessions.json"
                session_count = 0
                if mapping_file.exists():
                    import json
                    with open(mapping_file) as f:
                        data = json.load(f)
                        session_count = len(data.get("threads", {}))
            except Exception:
                session_count = 0

            body = (
                "📊 MailCode 系统状态\n\n"
                f"• 活跃对话: {session_count}\n"
                f"• IMAP 监听: 运行中\n"
                f"• 系统时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                "发「help」查看使用帮助"
            )
        elif command == "help":
            body = (
                "📖 MailCode 使用指南\n\n"
                "发送邮件到机器人邮箱, AI 会自动回复。\n\n"
                "特殊命令（将主题设为以下内容）：\n"
                "  status   — 查看系统状态\n"
                "  help     — 查看本帮助\n"
                "  sessions — 查看活跃对话列表\n\n"
                "邮件正文支持:\n"
                "  cwd: <路径> — 设置工作目录\n\n"
                "更多信息: https://github.com/zsdfbb/mailcode"
            )
        elif command == "sessions":
            try:
                mapping_file = _MAILCODE_HOME / "claude_sessions.json"
                if mapping_file.exists():
                    import json
                    with open(mapping_file) as f:
                        data = json.load(f)
                    threads = data.get("threads", {})
                    if threads:
                        lines = ["💬 活跃对话:\n"]
                        for msg_id, info in sorted(
                            threads.items(),
                            key=lambda x: x[1].get("last_interaction", 0),
                            reverse=True,
                        ):
                            subj = info.get("subject", "(无主题)")
                            email = info.get("user_email", "")
                            sid = info.get("claude_session_id", "")[:12]
                            lines.append(f"  [{sid}] {subj}")
                            lines.append(f"        {email}")
                        body = "\n".join(lines)
                    else:
                        body = "💬 暂无活跃对话。\n\n发送任意邮件给机器人即可开始新对话。"
                else:
                    body = "💬 暂无活跃对话。"
            except Exception:
                body = "💬 读取对话列表时出错。"
        else:
            body = "未知命令。发「help」查看可用命令。"

        try:
            self.email_channel.send_reply(
                to_email=from_email,
                subject=f"Re: {subject}",
                body=body,
            )
            return True, f"system_{command}"
        except Exception as e:
            logger.error("系统命令回复发送失败: %s", e)
            return False, f"system_{command}_failed"

    def process_email(self, email_entry: Dict, dry_run: bool = False,
                      force_session: Optional[bool] = None,
                      force_resume: Optional[bool] = None) -> Tuple[bool, str]:
        """处理一封邮件: 路由到 resume / conversation / stateless handler。

        路由表:
            dry_run=True                                       → (True, "dry_run")
            force_session=False                                → _handle_via_stateless
            force_session=None + is_session_enabled()=False    → _handle_via_stateless
            force_session=True/None + session enabled:
                force_resume=True/None + self._use_resume=True → _handle_via_resume
                force_resume=False                             → _handle_via_conversation

        Args:
            email_entry: fetch_unread_emails 产出的 entry dict
            dry_run: dry-run 模式, 仅日志, 不真发
            force_session: 显式覆盖 config 中 session.enabled
            force_resume: 显式覆盖 self._use_resume 开关

        Returns:
            (success: bool, mode: str), mode ∈ {"conversation", "resume", "stateless", "dry_run"}
        """
        if dry_run:
            logger.info(
                f"DRY RUN - 邮件: from={email_entry.get('sender_email')} subject={email_entry.get('subject')}"
            )
            return True, "dry_run"

        # System command check (before Claude routing)
        cmd = self._is_system_command(email_entry.get("subject", ""))
        if cmd:
            return self._handle_system_command(cmd, email_entry)

        # 决定走 conversation 还是 stateless
        if force_session is None:
            use_conversation = is_session_enabled()
        else:
            use_conversation = force_session

        if not use_conversation:
            return self._handle_via_stateless(email_entry)

        # 在会话模式下, 决定走 resume 还是旧 conversation
        if force_resume is None:
            use_resume = self._use_resume  # 默认 True
        else:
            use_resume = force_resume

        if use_resume:
            return self._handle_via_resume(email_entry)
        return self._handle_via_conversation(email_entry)

    def _handle_via_conversation(self, email_entry: Dict) -> Tuple[bool, str]:
        """路由到 ConversationHandler, 处理多轮对话邮件。"""
        from_email = email_entry["sender_email"]
        subject = email_entry.get("subject", "")

        t0 = time.monotonic()
        self._emit("claude_start", from_email=from_email, subject=subject)

        if self._conv_handler is None:
            from mailcode.relay.conversation_handler import ConversationHandler
            self._conv_handler = ConversationHandler(
                email_channel=self.email_channel,
            )

        success = self._conv_handler.handle_email(
            from_email=from_email,
            subject=subject,
            body=email_entry.get("body", ""),
            references=email_entry.get("references", ""),
            in_reply_to=email_entry.get("in_reply_to", ""),
        )

        duration = time.monotonic() - t0
        if success:
            self._emit("reply_sent", to_email=from_email, duration=duration)
        else:
            self._emit("claude_failed", to_email=from_email, duration=duration)

        mode = "conversation" if success else "conversation_failed"
        return (success, mode)

    def _handle_via_resume(self, email_entry: Dict) -> Tuple[bool, str]:
        """路由到 ResumeConversationHandler, 使用 claude --resume。"""
        from_email = email_entry["sender_email"]
        subject = email_entry.get("subject", "")

        t0 = time.monotonic()
        self._emit("claude_start", from_email=from_email, subject=subject)

        if self._resume_handler is None:
            from mailcode.relay.resume_handler import ResumeConversationHandler
            self._resume_handler = ResumeConversationHandler(
                email_channel=self.email_channel,
            )

        success = self._resume_handler.handle_email(
            from_email=from_email,
            subject=subject,
            body=email_entry.get("body", ""),
            references=email_entry.get("references", ""),
            in_reply_to=email_entry.get("in_reply_to", ""),
        )

        duration = time.monotonic() - t0
        if success:
            self._emit("reply_sent", to_email=from_email, duration=duration)
        else:
            self._emit("claude_failed", to_email=from_email, duration=duration)

        mode = "resume" if success else "resume_failed"
        return (success, mode)

    def _handle_via_stateless(self, email_entry: Dict) -> Tuple[bool, str]:
        """路由到 StatelessHandler, 处理单次回复邮件。

        - ``is_session_enabled()=False`` 时: 走 fallback (新默认)
        - ``force_session=False`` 时: 显式单次回复 (CLI 调试)
        """
        from_email = email_entry["sender_email"]
        subject = email_entry.get("subject", "")

        # 提示 fallback 路径, 但仅在"配置没开 session"时 (force_session=False 显式无歧义)
        if not is_session_enabled():
            logger.info("session 关闭, 使用单次回复")

        t0 = time.monotonic()
        self._emit("claude_start", from_email=from_email, subject=subject)

        if self._stateless_handler is None:
            from mailcode.relay.stateless_handler import StatelessHandler
            self._stateless_handler = StatelessHandler(
                email_channel=self.email_channel,
            )

        success = self._stateless_handler.handle_email(
            from_email=from_email,
            subject=subject,
            body=email_entry.get("body", ""),
            references=email_entry.get("references", ""),
            in_reply_to=email_entry.get("in_reply_to", ""),
        )

        duration = time.monotonic() - t0
        if success:
            self._emit("reply_sent", to_email=from_email, duration=duration)
        else:
            self._emit("claude_failed", to_email=from_email, duration=duration)

        mode = "stateless" if success else "stateless_failed"
        return (success, mode)

    def listen(self, dry_run: bool = False, max_iterations: Optional[int] = None, use_idle: bool = False):
        mode = "IDLE 长连接" if use_idle else f"轮询({self.check_interval}秒)"
        print(f"IMAP 监听器启动 ({mode})")
        if dry_run:
            print("Dry-run 模式: 仅显示邮件，不注入命令")
        print("按 Ctrl+C 停止")

        try:
            if use_idle:
                self._listen_idle(dry_run, max_iterations)
            else:
                self._listen_poll(dry_run, max_iterations)
        except KeyboardInterrupt:
            pass
        finally:
            print("监听器已停止")
            if not dry_run:
                self._prune_old_sent_messages()
                self._save_state({
                    "processed_uids": list(self.processed_uids),
                    "sent_messages": self.sent_messages,
                })

    def _listen_poll(self, dry_run: bool, max_iterations: Optional[int]):
        if not self.security_checker.config.get("allowed_senders"):
            logger.warning("发件人白名单为空，所有邮件将被拒绝处理。请在配置文件中设置 allowed_senders")
        self._init_baseline()
        self._prune_old_sent_messages()
        self._save_state({
            "processed_uids": list(self.processed_uids),
            "sent_messages": self.sent_messages,
        })
        iteration = 0
        backoff = _Backoff()
        while not self._stopped.is_set():
            iteration += 1
            if max_iterations and iteration > max_iterations:
                break

            try:
                emails = self.fetch_unread_emails(dry_run=dry_run)

                for email_entry in emails:
                    if dry_run:
                        continue

                    success, message = self.process_email(email_entry, dry_run=dry_run)
                    status_icon = "OK" if success else "FAIL"
                    logger.info(f"{status_icon} {message}")

                self._prune_old_sent_messages()
                if not dry_run:
                    self._save_state({
                        "processed_uids": list(self.processed_uids),
                        "sent_messages": self.sent_messages,
                    })

                backoff.reset()

            except (ConnectionError, EOFError, socket.timeout, imaplib.IMAP4.abort) as e:
                delay = backoff.next_delay()
                self._log_connection_error(e, attempt=backoff._n, next_delay=delay)
                if self._stopped.wait(timeout=delay):
                    break
                continue
            except imaplib.IMAP4.error as e:
                host = self.imap_config.get("host", "?")
                logger.error(f"IMAP 协议错误, 放弃轮询 [{host}]: {e}")
                break

            if self._stopped.wait(timeout=self.check_interval):
                break

    def _listen_idle(self, dry_run: bool, max_iterations: Optional[int]):
        if not self.security_checker.config.get("allowed_senders"):
            logger.warning("发件人白名单为空，所有邮件将被拒绝处理。请在配置文件中设置 allowed_senders")
        self._init_baseline()
        self._prune_old_sent_messages()
        self._save_state({
            "processed_uids": list(self.processed_uids),
            "sent_messages": self.sent_messages,
        })
        mail = self._connect()
        mail.select("INBOX")

        # 检测服务器是否支持 IMAP IDLE
        capabilities = getattr(mail, 'capabilities', None) or ()
        if 'IDLE' not in capabilities:
            host = self.imap_config.get("host", "?")
            logger.warning(
                f"IMAP 服务器 {host} 不支持 IDLE, 自动切换为轮询模式 "
                f"(check_interval={self.check_interval}s)。"
            )
            logger.warning(
                "如使用 163/126 等不支持 IDLE 的邮箱, 建议把 mailcode_bot.check_interval "
                "调到 60-120s 以避免触发反滥用频率限制。"
            )
            try:
                mail.logout()
            except Exception:
                pass
            self._mail = None
            return self._listen_poll(dry_run, max_iterations)

        iteration = 0
        backoff = _Backoff()
        HEALTH_CHECK_INTERVAL = 60  # 秒: IDLE 健康检查周期
        health_check_every = max(1, HEALTH_CHECK_INTERVAL // self._idle_timeout)

        try:
            while not self._stopped.is_set():
                iteration += 1
                if max_iterations and iteration > max_iterations:
                    break

                if self._stopped.is_set():
                    break

                try:
                    # 标记当前连接在 IDLE 等待中, 给 stop() 的 idle_done() 用
                    self._active_idle_mail = mail
                    got_event = self._wait_for_idle(mail)
                    self._active_idle_mail = None

                    if got_event:
                        if self._stopped.is_set():
                            break
                        mail = self._reconnect()
                        mail.select("INBOX")
                        try:
                            mail.noop()
                        except Exception:
                            pass
                        logger.info("IDLE 收到事件, 已重连")

                    if self._stopped.is_set():
                        break

                    # 健康检查: 每 60s 一次 NOOP 探测死连接
                    # 必须在 got_event 分支之后 (mail 是重连后的新连接),
                    # 避免与 _wait_for_idle_old 后台 IDLE daemon 线程撞协议
                    if iteration % health_check_every == 0:
                        logger.debug(f"IDLE 健康检查 iter={iteration}")
                        try:
                            mail.noop()
                            self._emit("heartbeat")
                        except (ConnectionError, EOFError, socket.timeout, imaplib.IMAP4.abort) as e:
                            logger.warning(f"IDLE 健康检查失败 ({type(e).__name__}), 触发重连")
                            raise

                    # 预判性重连: QQ 邮箱约 2h 静默断连, 提前至 FORCED_RECONNECT_INTERVAL 秒主动重建
                    if not got_event and time.monotonic() - self._last_connect_time >= self.FORCED_RECONNECT_INTERVAL:
                        logger.info("预判性重连: 连接已持续 %d 秒", self.FORCED_RECONNECT_INTERVAL)
                        mail = self._reconnect()
                        mail.select("INBOX")
                        try:
                            mail.noop()
                        except Exception:
                            pass
                        self._last_connect_time = time.monotonic()

                    # 复用现有连接, 避免每轮 fetch 再开一个 (163 反滥用触发点)
                    emails = self.fetch_unread_emails(dry_run=dry_run, mail=mail)

                    # 重连后首轮 fetch 若拉到累积未读, 显式记录
                    if iteration <= health_check_every + 1 and emails:
                        logger.info(f"重连后首轮 fetch 拉到 {len(emails)} 封累积未读")

                    # got_event 但无结果: 可能是非新邮件的 IMAP 通知 (flag 变更/BYE/keepalive)
                    if got_event and not emails:
                        logger.info("IDLE 事件后无新邮件 (可能为非新邮件的 IMAP 通知)")

                    for email_entry in emails:
                        if dry_run:
                            continue

                        success, message = self.process_email(email_entry, dry_run=dry_run)
                        status_icon = "OK" if success else "FAIL"
                        logger.info(f"{status_icon} {message}")

                    if not dry_run:
                        self._prune_old_sent_messages()
                        self._save_state({
                            "processed_uids": list(self.processed_uids),
                            "sent_messages": self.sent_messages,
                        })

                    backoff.reset()  # 一轮成功, 退避归零

                except (ConnectionError, EOFError, socket.timeout, imaplib.IMAP4.abort) as e:
                    # 瞬时错误: 退避后重连
                    self._active_idle_mail = None
                    delay = backoff.next_delay()
                    self._log_connection_error(e, attempt=backoff._n, next_delay=delay)
                    if self._stopped.wait(timeout=delay):
                        break
                    try:
                        mail = self._reconnect()
                        mail.select("INBOX")
                        try:
                            mail.noop()
                        except Exception:
                            pass
                        self._last_connect_time = time.monotonic()
                        logger.info("退避重连成功, 准备拉取累积未读")
                    except imaplib.IMAP4.error as auth_err:
                        # 重连时再认证失败 = 配置问题, 放弃
                        logger.error(f"IMAP 认证失败, 放弃重试: {auth_err}")
                        break
                    except OSError as os_err:
                        # 网络级错误 (DNS 解析失败等), 由外层退避循环继续重试
                        logger.error(
                            f"重连时网络错误 ({type(os_err).__name__}), 继续退避重试"
                        )
                except imaplib.IMAP4.error as e:
                    # 协议级错误 (非瞬时), 不退避直接退出让用户排查
                    self._active_idle_mail = None
                    logger.error(f"IMAP 协议错误, 放弃重试: {e}")
                    break
        finally:
            self._active_idle_mail = None
            if mail is not None:
                try:
                    mail.logout()
                except Exception:
                    pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="IMAP 邮件监听器")
    parser.add_argument("--dry-run", action="store_true", help="仅显示邮件，不注入命令")
    parser.add_argument("--once", action="store_true", help="只执行一次轮询")
    parser.add_argument("--idle", action="store_true", help="使用 IMAP IDLE 长连接模式")
    args = parser.parse_args()

    listener = IMAPListener()

    if args.once:
        emails = listener.fetch_unread_emails(dry_run=args.dry_run)
        logger.info(f"发现 {len(emails)} 封新邮件")
        for email_entry in emails:
            success, message = listener.process_email(email_entry, dry_run=args.dry_run)
            logger.info(f"{'OK' if success else 'FAIL'} {message}")
    else:
        listener.listen(dry_run=args.dry_run, use_idle=args.idle)
