"""StatelessHandler 单元测试 — 覆盖单次回复模式 + cwd 解析 + 错误处理。"""

from unittest.mock import MagicMock, patch

import pytest

from mailcode.relay import stateless_handler as sh_module
from mailcode.relay.stateless_handler import StatelessHandler


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def mock_email_channel():
    """Mock EmailChannel: 提供 email_config/smtp_user + send_reply。"""
    channel = MagicMock()
    channel.email_config = {"from": "bot@mailcode.com", "from_name": "Bot"}
    channel.smtp_user = "bot@mailcode.com"
    channel.send_reply.return_value = (True, "<reply-abc@mailcode>")
    return channel


@pytest.fixture
def handler(mock_email_channel):
    """StatelessHandler 实例。"""
    return StatelessHandler(email_channel=mock_email_channel)


# ------------------------------------------------------------------ #
# StatelessHandler 行为测试
# ------------------------------------------------------------------ #


class TestStatelessHandler:
    """StatelessHandler.handle_email 7 个关键路径。

    Patch 注意点: stateless_handler.py 用 `from ... import call_claude` 模块级导入,
    所以测试需 patch `mailcode.relay.stateless_handler.call_claude` (导入站),
    直接 patch `mailcode.relay.conversation_handler.call_claude` 无效。
    """

    def test_handle_email_success(self, handler, mock_email_channel):
        """claude 返回字符串 → send_reply 调一次, 返回 True。"""
        with patch.object(sh_module, "call_claude", return_value="这是 AI 的回复") as mock_call:
            result = handler.handle_email(
                from_email="u@t.com",
                subject="Hello",
                body="请帮我看下",
            )

        assert result is True
        # call_claude 被调一次
        mock_call.assert_called_once()
        # send_reply 被调一次
        mock_email_channel.send_reply.assert_called_once()
        call_kwargs = mock_email_channel.send_reply.call_args.kwargs
        assert call_kwargs["to_email"] == "u@t.com"
        assert call_kwargs["subject"] == "Re: Hello"
        assert call_kwargs["body"] == "这是 AI 的回复"

    def test_handle_email_cwd_extraction(self, handler, mock_email_channel, tmp_path):
        """邮件正文含 `cwd: /tmp` → cwd 正确解析并传给 call_claude。"""
        d = tmp_path / "project"
        d.mkdir()
        with patch.object(sh_module, "call_claude", return_value="ok") as mock_call:
            result = handler.handle_email(
                from_email="u@t.com",
                subject="T",
                body=f"cwd: {d}\n请在当前项目运行测试",
            )

        assert result is True
        # cwd 传给 call_claude 的第二个参数
        assert mock_call.call_args.kwargs["cwd"] == str(d.resolve())

    def test_handle_email_claude_failure_sends_error(self, handler, mock_email_channel):
        """call_claude 返回 None → send_error_email 兜底, handle_email 返回 False。"""
        with patch.object(sh_module, "call_claude", return_value=None), \
             patch.object(sh_module, "send_error_email", return_value=True) as mock_err:
            result = handler.handle_email(
                from_email="u@t.com", subject="Hi", body="q",
            )

        assert result is False
        # send_error_email 被调一次
        mock_err.assert_called_once()
        # 调用方传入的 email_channel, from_email, subject 都透传
        call_args = mock_err.call_args
        assert call_args.args[0] is mock_email_channel
        assert call_args.args[1] == "u@t.com"
        assert call_args.args[2] == "Hi"
        # 错误文案含"技术问题"
        assert "技术问题" in call_args.args[3]
        # 正常 reply 路径的 send_reply 不应被调
        mock_email_channel.send_reply.assert_not_called()

    def test_handle_email_claude_empty_sends_error(self, handler, mock_email_channel):
        """call_claude 返回 "" → send_error_email 兜底, handle_email 返回 False。"""
        with patch.object(sh_module, "call_claude", return_value=""), \
             patch.object(sh_module, "send_error_email", return_value=True) as mock_err:
            result = handler.handle_email(
                from_email="u@t.com", subject="Hi", body="q",
            )

        assert result is False
        mock_err.assert_called_once()
        # 空 response 的错误文案是"没有回复内容"
        assert "没有回复内容" in mock_err.call_args.args[3]
        mock_email_channel.send_reply.assert_not_called()

    def test_handle_email_smtp_failure_returns_false(self, handler, mock_email_channel):
        """send_reply 返回 (False, None) → handle_email 返回 False, 不抛异常。"""
        mock_email_channel.send_reply.return_value = (False, None)
        with patch.object(sh_module, "call_claude", return_value="r"):
            result = handler.handle_email(
                from_email="u@t.com", subject="Hi", body="q",
            )

        assert result is False
        # 失败时 send_reply 仍被调一次 (没捕获)
        mock_email_channel.send_reply.assert_called_once()

    def test_handle_email_subject_re_prefix(self, handler, mock_email_channel):
        """subject 已是 'Re: x' → 不再加前缀。"""
        mock_email_channel.send_reply.return_value = (True, "<r@m>")
        with patch.object(sh_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="Re: Already", body="q",
            )

        assert mock_email_channel.send_reply.call_args.kwargs["subject"] == "Re: Already"

    def test_handle_email_subject_no_prefix(self, handler, mock_email_channel):
        """subject 无 Re: → 自动加 'Re: ' 前缀。"""
        mock_email_channel.send_reply.return_value = (True, "<r@m>")
        with patch.object(sh_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="Hello", body="q",
            )

        assert mock_email_channel.send_reply.call_args.kwargs["subject"] == "Re: Hello"
