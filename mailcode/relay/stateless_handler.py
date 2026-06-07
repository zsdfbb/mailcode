"""单次回复模式核心 — 每封邮件独立调一次 claude -p, 不维护 session。"""

import logging
from pathlib import Path

from mailcode.relay.conversation_handler import (
    call_claude,
    extract_cwd,
    send_error_email,
    strip_cwd,
)

logger = logging.getLogger(__name__)


class StatelessHandler:
    """单次回复处理器: 一封邮件 → 一次 ``claude -p`` → 一封回信。

    与 ``ConversationHandler`` 的区别:
        - 不写 session 文件, 不读 index
        - 无状态, 可安全 lazy init 后复用同一实例
        - cwd 解析复用 ``extract_cwd`` / ``strip_cwd`` (行为对齐)
    """

    def __init__(self, email_channel):
        self.email_channel = email_channel

    def handle_email(self, from_email: str, subject: str, body: str,
                     references: str = "", in_reply_to: str = "") -> bool:
        """主入口: 处理一封单次回复邮件。

        流程:
            1. 提取 cwd + 剥离
            2. 构建 prompt
            3. 调 ``claude -p``
            4. 错误处理 (发通知邮件)
            5. SMTP 发回复

        Returns:
            True 表示成功发送回复
        """
        logger.info("处理单次回复邮件: from=%s subject=%s", from_email, subject)

        # 1. 提取 cwd + 剥离
        extracted_cwd = extract_cwd(body)
        clean_body = strip_cwd(body) if extracted_cwd is not None else body

        # 2. 构建 prompt (单次回复: 无 session, 直接给邮件正文)
        prompt = (
            f"用户最新邮件:\n\n"
            f"主题: {subject}\n\n"
            f"{clean_body}\n\n"
            f"请直接回复这封邮件, 内容将作为邮件正文发送, 用纯文本格式。"
        )

        # 3. 调 claude
        cwd = extracted_cwd or str(Path.home())
        response = call_claude(prompt, cwd=cwd)

        # 4. claude 失败 → 写日志 + 发邮件通知用户
        if response is None:
            logger.error("claude -p 调用失败, 通知用户: from=%s", from_email)
            send_error_email(
                self.email_channel, from_email, subject,
                "抱歉, 处理你的邮件时遇到技术问题。请稍后再试。详细错误已记录到日志。",
                references, in_reply_to,
            )
            return False

        # 5. 空 response → 写日志 + 发邮件通知用户
        if not response:
            logger.error("claude -p 返回空 response, 通知用户: from=%s", from_email)
            send_error_email(
                self.email_channel, from_email, subject,
                "抱歉, AI 助手这次没有回复内容。请稍后再试, 或换个方式描述你的问题。",
                references, in_reply_to,
            )
            return False

        # 6. 准备 outgoing 邮件
        reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"

        # 7. SMTP 发回复
        try:
            send_ok, _ = self.email_channel.send_reply(
                to_email=from_email,
                subject=reply_subject,
                body=response,
                in_reply_to_msg_id=in_reply_to,
                references=references,
            )
        except Exception as e:
            logger.error("单次回复发送异常: %s", e)
            return False

        if not send_ok:
            logger.error("单次回复发送失败: from=%s", from_email)
            return False

        logger.info("单次回复已发送: from=%s", from_email)
        return True
