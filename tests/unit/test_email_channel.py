"""EmailChannel 单元测试"""

from unittest.mock import patch, MagicMock

from mailcode.channels.email_channel import EmailChannel

IN_REPLY_TO = "<abc123@mail.test.com>"
REFERENCES = "<prev1@test.com> <prev2@test.com>"


MOCK_SMTP = {
    "host": "smtp.test.com",
    "port": 587,
    "secure": False,
    "user": "test@test.com",
    "pass": "secret",
}

MOCK_EMAIL = {
    "from": "test@test.com",
    "from_name": "Tester",
    "to": "user@test.com",
}


class TestEmailChannelInit:
    def test_default_config_from_getters(self):
        with patch("mailcode.channels.email_channel.get_smtp_config", return_value=MOCK_SMTP):
            with patch("mailcode.channels.email_channel.get_email_config", return_value=MOCK_EMAIL):
                ch = EmailChannel()
                assert ch.smtp_user == "test@test.com"

    def test_custom_config(self):
        ch = EmailChannel(smtp_config=MOCK_SMTP, email_config=MOCK_EMAIL)
        assert ch.smtp_user == "test@test.com"
        assert ch.smtp_pass == "secret"


class TestEmailChannelConnection:
    def test_create_connection_ssl(self):
        cfg = {**MOCK_SMTP, "secure": True, "port": 465}
        ch = EmailChannel(smtp_config=cfg, email_config=MOCK_EMAIL)
        with patch("mailcode.channels.email_channel.smtplib.SMTP_SSL") as mock_ssl:
            mock_server = MagicMock()
            mock_ssl.return_value = mock_server
            assert ch._create_connection() is True
            mock_ssl.assert_called_once_with("smtp.test.com", 465, timeout=15)

    def test_create_connection_starttls(self):
        ch = EmailChannel(smtp_config=MOCK_SMTP, email_config=MOCK_EMAIL)
        with patch("mailcode.channels.email_channel.smtplib.SMTP") as mock_smtp:
            mock_server = MagicMock()
            mock_smtp.return_value = mock_server
            assert ch._create_connection() is True
            mock_smtp.assert_called_once_with("smtp.test.com", 587, timeout=15)
            mock_server.starttls.assert_called_once()

    def test_create_connection_failure(self):
        ch = EmailChannel(smtp_config=MOCK_SMTP, email_config=MOCK_EMAIL)
        with patch("mailcode.channels.email_channel.smtplib.SMTP", side_effect=Exception("conn err")):
            assert ch._create_connection() is False


class TestEmailChannelSend:
    def test_send_no_from(self):
        cfg = {**MOCK_EMAIL, "from": ""}
        ch = EmailChannel(smtp_config=MOCK_SMTP, email_config=cfg)
        import pytest
        with pytest.raises(ValueError, match="SMTP 配置不完整"):
            ch.send(subject="test", body="hello")

    def test_send_creates_mime(self):
        ch = EmailChannel(smtp_config=MOCK_SMTP, email_config=MOCK_EMAIL)
        mock_server = MagicMock()
        ch._server = mock_server
        with patch.object(ch, "_create_connection", return_value=True):
            ok = ch.send(to_email="user@test.com", subject="Hello", body="World", token="ABC123")
            assert ok is True
            args, _ = mock_server.sendmail.call_args
            raw = args[2]
            assert "From: Tester <test@test.com>" in raw
            assert "To: user@test.com" in raw
            assert "Subject:" in raw
            assert "X-MailCode-Remote-Token: ABC123" in raw
            assert "V29ybGQ=" in raw


class TestEmailChannelSendReply:
    def test_reply_sets_in_reply_to(self):
        ch = EmailChannel(smtp_config=MOCK_SMTP, email_config=MOCK_EMAIL)
        mock_server = MagicMock()
        ch._server = mock_server
        with patch.object(ch, "_create_connection", return_value=True):
            ok, msg_id = ch.send_reply(
                to_email="user@test.com",
                subject="Re: Hello",
                body="Reply body",
                in_reply_to_msg_id=IN_REPLY_TO,
            )
            assert ok is True
            assert msg_id is not None
            args, _ = mock_server.sendmail.call_args
            raw = args[2]
            assert f"In-Reply-To: {IN_REPLY_TO}" in raw

    def test_reply_sets_references(self):
        ch = EmailChannel(smtp_config=MOCK_SMTP, email_config=MOCK_EMAIL)
        mock_server = MagicMock()
        ch._server = mock_server
        with patch.object(ch, "_create_connection", return_value=True):
            ok, _ = ch.send_reply(
                to_email="user@test.com",
                subject="Re: Hello",
                body="Reply body",
                in_reply_to_msg_id=IN_REPLY_TO,
                references=REFERENCES,
            )
            assert ok is True
            args, _ = mock_server.sendmail.call_args
            raw = args[2]
            # References 应包含旧 references + in_reply_to
            expected_refs = f"{REFERENCES} {IN_REPLY_TO}"
            assert f"References: {expected_refs}" in raw

    def test_reply_no_thread_headers_when_no_in_reply_to(self):
        ch = EmailChannel(smtp_config=MOCK_SMTP, email_config=MOCK_EMAIL)
        mock_server = MagicMock()
        ch._server = mock_server
        with patch.object(ch, "_create_connection", return_value=True):
            ok, msg_id = ch.send_reply(
                to_email="user@test.com",
                subject="New thread",
                body="Body",
            )
            assert ok is True
            assert msg_id is not None
            args, _ = mock_server.sendmail.call_args
            raw = args[2]
            assert "In-Reply-To:" not in raw
            assert "References:" not in raw
            # 应仍包含 Message-ID
            assert "Message-ID:" in raw

    def test_reply_returns_message_id(self):
        """验证返回的 message_id 是 make_msgid 生成的格式"""
        import email.utils
        ch = EmailChannel(smtp_config=MOCK_SMTP, email_config=MOCK_EMAIL)
        mock_server = MagicMock()
        ch._server = mock_server
        with patch.object(ch, "_create_connection", return_value=True):
            with patch.object(email.utils, "make_msgid", return_value="<generated@test>"):
                ok, msg_id = ch.send_reply(
                    to_email="user@test.com",
                    subject="Re: Hello",
                    body="Reply body",
                    in_reply_to_msg_id=IN_REPLY_TO,
                )
                assert ok is True
                assert msg_id == "<generated@test>"

    def test_reply_incomplete_config(self):
        cfg = {**MOCK_EMAIL, "from": ""}
        ch = EmailChannel(smtp_config=MOCK_SMTP, email_config=cfg)
        import pytest
        with pytest.raises(ValueError, match="SMTP 配置不完整"):
            ch.send_reply(to_email="u@t.com", subject="s", body="b")

    def test_reply_connection_fail(self):
        ch = EmailChannel(smtp_config=MOCK_SMTP, email_config=MOCK_EMAIL)
        with patch.object(ch, "_create_connection", return_value=False):
            ok, msg_id = ch.send_reply(
                to_email="user@test.com",
                subject="Re: Hello",
                body="Body",
                in_reply_to_msg_id=IN_REPLY_TO,
            )
            assert ok is False
            assert msg_id is None

    def test_reply_send_exception(self):
        ch = EmailChannel(smtp_config=MOCK_SMTP, email_config=MOCK_EMAIL)
        ch._server = MagicMock()
        ch._server.login.side_effect = Exception("login failed")
        with patch.object(ch, "_create_connection", return_value=True):
            ok, msg_id = ch.send_reply(
                to_email="user@test.com",
                subject="Re: Hello",
                body="Body",
                in_reply_to_msg_id=IN_REPLY_TO,
            )
            assert ok is False
            assert msg_id is None

    def test_reply_compatible_with_existing_methods(self):
        """验证 send_reply 与 send 共存且不冲突"""
        ch = EmailChannel(smtp_config=MOCK_SMTP, email_config=MOCK_EMAIL)
        assert hasattr(ch, "send")
        assert hasattr(ch, "send_reply")
        # send_reply 返回值是 tuple
        from inspect import signature
        sig = signature(ch.send_reply)
        assert "-> tuple" in str(sig)
        assert "tuple" in str(sig)
