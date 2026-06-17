"""Health 模块单元测试"""

from unittest.mock import patch, MagicMock

from mailcode.health import run_health, _check


def _make_smtp_cfg(**overrides):
    cfg = {"host": "smtp.qq.com", "port": 465, "secure": True, "user": "", "pass": ""}
    cfg.update(overrides)
    return cfg


def _make_imap_cfg(**overrides):
    cfg = {"host": "imap.qq.com", "port": 993, "secure": True, "user": "", "pass": ""}
    cfg.update(overrides)
    return cfg


def _make_email_cfg(**overrides):
    cfg = {"from": "", "to": ""}
    cfg.update(overrides)
    return cfg


class TestCheck:
    def test_check_ok(self):
        assert _check("测试", True) is True

    def test_check_fail(self):
        assert _check("测试", False) is False


class TestRunHealth:
    @patch("mailcode.health.get_smtp_config")
    @patch("mailcode.health.get_imap_config")
    @patch("mailcode.health.get_email_config")
    def test_incomplete_config_skips_network(self, mock_email, mock_imap, mock_smtp):
        mock_smtp.return_value = _make_smtp_cfg()
        mock_imap.return_value = _make_imap_cfg()
        mock_email.return_value = _make_email_cfg()
        ok = run_health()
        assert ok is False

    @patch("mailcode.health.get_smtp_config")
    @patch("mailcode.health.get_imap_config")
    @patch("mailcode.health.get_email_config")
    def test_full_config(self, mock_email, mock_imap, mock_smtp):
        mock_smtp.return_value = _make_smtp_cfg(user="test@qq.com", **{"pass": "abc"})
        mock_imap.return_value = _make_imap_cfg(user="test@qq.com", **{"pass": "abc"})
        mock_email.return_value = _make_email_cfg(**{"from": "test@qq.com", "to": "test@qq.com"})
        mock_server = MagicMock()
        mock_server.login.side_effect = Exception("login failed")
        with patch("mailcode.health.smtplib.SMTP_SSL", return_value=mock_server):
            ok = run_health()
            assert ok is False

    @patch("mailcode.health.get_smtp_config")
    @patch("mailcode.health.get_imap_config")
    @patch("mailcode.health.get_email_config")
    def test_missing_smtp_user(self, mock_email, mock_imap, mock_smtp):
        mock_smtp.return_value = _make_smtp_cfg()
        mock_imap.return_value = _make_imap_cfg(user="u", **{"pass": "p"})
        mock_email.return_value = _make_email_cfg(**{"from": "a@b.com", "to": "a@b.com"})
        ok = run_health()
        assert ok is False

    @patch("mailcode.health.load_config")
    @patch("mailcode.health.get_smtp_config")
    @patch("mailcode.health.get_imap_config")
    @patch("mailcode.health.get_email_config")
    def test_all_ok_called(self, mock_email, mock_imap, mock_smtp, mock_load_config):
        mock_smtp.return_value = _make_smtp_cfg(user="t", **{"pass": "p"})
        mock_imap.return_value = _make_imap_cfg(user="t", **{"pass": "p"})
        mock_email.return_value = _make_email_cfg(**{"from": "t@t.com", "to": "t@t.com"})
        mock_load_config.return_value = {"security": {"allowed_senders": ["a@b.com"]}}
        with patch("mailcode.health.smtplib.SMTP_SSL") as mock_smtp_lib:
            mock_server = MagicMock()
            mock_smtp_lib.return_value = mock_server
            with patch("mailcode.health.imaplib.IMAP4_SSL") as mock_imap_lib:
                mock_mail = MagicMock()
                mock_imap_lib.return_value = mock_mail
                mock_mail.select.return_value = ("OK", [b"10"])
                ok = run_health()
                assert ok is True
