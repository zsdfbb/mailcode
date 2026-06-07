import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from typing import Optional
import email.utils

from mailcode.config import get_smtp_config, get_email_config

logger = logging.getLogger(__name__)


class EmailChannel:
    def __init__(self, smtp_config=None, email_config=None):
        self.smtp_config = smtp_config or get_smtp_config()
        self.email_config = email_config or get_email_config()
        self.smtp_user = self.smtp_config.get("user", "")
        self.smtp_pass = self.smtp_config.get("pass", "")

    def _create_connection(self) -> bool:
        host = self.smtp_config.get("host", "smtp.gmail.com")
        port = self.smtp_config.get("port", 587)
        secure = self.smtp_config.get("secure", False)

        try:
            if secure:
                self._server = smtplib.SMTP_SSL(host, port, timeout=15)
            else:
                self._server = smtplib.SMTP(host, port, timeout=15)
                self._server.starttls()
            return True
        except Exception as e:
            logger.error("SMTP 连接失败: %s", e)
            return False

    def send(
        self,
        to_email: Optional[str] = None,
        subject: str = "",
        body: str = "",
        token: Optional[str] = None
    ) -> bool:
        from_email = self.email_config.get("from", self.smtp_user)
        from_name = self.email_config.get("from_name", "Mailcode Remote")
        to_email = to_email or ""

        if not from_email or not self.smtp_user:
            raise ValueError("SMTP 配置不完整，请检查配置文件中的 mailcode_bot.email")

        msg = MIMEMultipart()
        msg["From"] = f"{from_name} <{from_email}>"
        msg["To"] = to_email
        msg["Subject"] = Header(subject, "utf-8")

        if token:
            msg["X-MailCode-Remote-Token"] = token

        msg.attach(MIMEText(body, "plain", "utf-8"))

        try:
            if not self._create_connection():
                return False
            self._server.login(self.smtp_user, self.smtp_pass)
            self._server.sendmail(from_email, [to_email], msg.as_string())
            return True
        except Exception:
            logger.exception("邮件发送失败")
            return False
        finally:
            try:
                if getattr(self, "_server", None):
                    self._server.quit()
            except Exception:
                pass

    def send_reply(
        self,
        to_email: str,
        subject: str,
        body: str,
        in_reply_to_msg_id: Optional[str] = None,
        references: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        """发送带线程追踪的回复邮件。

        在 send() 基础上添加 In-Reply-To 和 References 邮件头，
        实现邮件线程的层级追踪。

        Args:
            to_email: 收件人地址
            subject: 邮件主题
            body: 邮件正文
            in_reply_to_msg_id: 被回复邮件的 Message-ID
            references: 被回复邮件的 References 头内容（可选）

        Returns:
            (success: bool, message_id: Optional[str])
        """
        from_email = self.email_config.get("from", self.smtp_user)
        from_name = self.email_config.get("from_name", "Mailcode Remote")
        to_email = to_email or ""

        if not from_email or not self.smtp_user:
            raise ValueError("SMTP 配置不完整，请检查配置文件中的 mailcode_bot.email")

        msg = MIMEMultipart()
        msg["From"] = f"{from_name} <{from_email}>"
        msg["To"] = to_email
        msg["Subject"] = Header(subject, "utf-8")

        # 生成当前邮件的 Message-ID
        domain = from_email.split("@")[-1] if "@" in from_email else "mailcode"
        message_id = email.utils.make_msgid(domain=domain)
        msg["Message-ID"] = message_id
        msg["Date"] = email.utils.formatdate(localtime=True)

        # 设置线程追踪头
        if in_reply_to_msg_id:
            msg["In-Reply-To"] = in_reply_to_msg_id
            ref_parts: list[str] = []
            if references:
                ref_parts.append(references)
            ref_parts.append(in_reply_to_msg_id)
            msg["References"] = " ".join(ref_parts)

        msg.attach(MIMEText(body, "plain", "utf-8"))

        try:
            if not self._create_connection():
                return False, None
            self._server.login(self.smtp_user, self.smtp_pass)
            self._server.sendmail(from_email, [to_email], msg.as_string())
            return True, message_id
        except Exception:
            logger.exception("邮件发送失败")
            return False, None
        finally:
            try:
                if getattr(self, "_server", None):
                    self._server.quit()
            except Exception:
                pass

