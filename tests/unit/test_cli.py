"""CLI 统一入口测试 — pytest 风格"""

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from mailcode.cli import cmd_config, _mask_sensitive, build_parser, main


def _make_config_args(subcommand: str, **extra):
    class Args:
        pass
    args = Args()
    args.config_command = subcommand
    for k, v in extra.items():
        setattr(args, k, v)
    return args


# ============================================================
# _mask_sensitive 测试
# ============================================================


class TestMaskSensitive:
    def test_mask_password_when_set(self):
        config = {"smtp": {"host": "smtp.qq.com", "pass": "mysecret"}}
        masked = _mask_sensitive(config)
        assert masked["smtp"]["pass"] == "***"

    def test_preserve_empty_password(self):
        config = {"smtp": {"host": "smtp.qq.com", "pass": ""}}
        masked = _mask_sensitive(config)
        assert masked["smtp"]["pass"] == ""

    def test_imap_password_masked(self):
        config = {"imap": {"pass": "imapsecret"}}
        masked = _mask_sensitive(config)
        assert masked["imap"]["pass"] == "***"

    def test_non_password_fields_preserved(self):
        config = {"smtp": {"host": "smtp.qq.com", "user": "me@qq.com"}}
        masked = _mask_sensitive(config)
        assert masked["smtp"]["host"] == "smtp.qq.com"
        assert masked["smtp"]["user"] == "me@qq.com"

    def test_empty_config_unchanged(self):
        config = {}
        masked = _mask_sensitive(config)
        assert masked == {}

    def test_passwords_masked(self, mock_config_full):
        masked = _mask_sensitive(mock_config_full)
        assert masked["smtp"]["pass"] == "***"
        assert masked["imap"]["pass"] == "***"


# ============================================================
# cmd_config 测试
# ============================================================


class TestConfigInit:
    def test_init_creates_default_config(self, tmp_path):
        config_path = tmp_path / "config.json"
        with patch("mailcode.config.USER_CONFIG_PATH", config_path):
            assert not config_path.exists()
            cmd_config(_make_config_args("init"))
            assert config_path.exists()
            data = json.loads(config_path.read_text())
            assert "mailcode_bot" in data
            assert "security" in data

    def test_init_overwrites_existing_config(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('{"smtp": {"host": "custom.smtp.com"}}')

        with patch("mailcode.config.USER_CONFIG_PATH", config_path):
            cmd_config(_make_config_args("init", force=True))

        data = json.loads(config_path.read_text())
        assert data["mailcode_bot"]["email"] == ""

    def test_init_backup_restore_cycle(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        original = {
            "smtp": {"host": "custom.smtp.com", "pass": "secret123"},
            "imap": {"host": "custom.imap.com", "pass": "secret456"},
            "email": {"from": "me@example.com"}
        }
        config_path.write_text(json.dumps(original))

        with patch("mailcode.config.USER_CONFIG_PATH", config_path):
            backup_path = config_path.with_suffix(".json.bak")
            config_path.rename(backup_path)
            assert not config_path.exists()

            cmd_config(_make_config_args("init"))
            assert config_path.exists()

            backup_path.rename(config_path)
            restored = json.loads(config_path.read_text())
            assert restored["smtp"]["host"] == "custom.smtp.com"
            assert restored["smtp"]["pass"] == "secret123"
            assert restored["email"]["from"] == "me@example.com"

    def test_ensure_user_config_preserves_existing(self, tmp_path, mock_config_patch):
        """_ensure_user_config 在文件已存在时不覆盖"""
        config_path = tmp_path / "config.json"
        config_path.write_text('{"smtp": {"user": "old@test.com"}}')

        with patch("mailcode.config.USER_CONFIG_PATH", config_path):
            from mailcode.config import _ensure_user_config
            _ensure_user_config()

        data = json.loads(config_path.read_text())
        assert "old@test.com" in data.get("smtp", {}).get("user", "")


class TestConfigShow:
    def test_show_masks_passwords(self, tmp_path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "smtp": {"host": "smtp.qq.com", "pass": "mysecret"},
            "imap": {"pass": "imapsecret"}
        }))

        with patch("mailcode.config.USER_CONFIG_PATH", config_path), \
             patch("mailcode.config._config_cache", None):
            captured = StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                cmd_config(_make_config_args("show"))
            finally:
                sys.stdout = old_stdout
            output = json.loads(captured.getvalue())
            assert output["smtp"]["pass"] == "***"
            assert output["imap"]["pass"] == "***"

    def test_path_prints_config_location(self, tmp_path):
        config_path = tmp_path / "config.json"
        with patch("mailcode.config.USER_CONFIG_PATH", config_path):
            captured = StringIO()
            old = sys.stdout
            try:
                sys.stdout = captured
                cmd_config(_make_config_args("path"))
            finally:
                sys.stdout = old
            output = captured.getvalue().strip()
            assert output == str(config_path)


# ============================================================
# build_parser / main 测试
# ============================================================


class TestParser:
    def test_serve_help_lists_options(self):
        parser = build_parser()
        parser.parse_args(["serve"])

    def test_top_level_help(self):
        parser = build_parser()
        parser.parse_args([])

    def test_unknown_subcommand_exits_nonzero(self):
        with patch("sys.argv", ["mailcode", "unknown_command"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code != 0


class TestServe:
    def test_serve_dry_run_once(self, mock_config_patch, temp_data_dir):
        from mailcode.server import run_serve

        class FakeArgs:
            dry_run = True
            once = True
            idle = False

        with patch("mailcode.relay.email_listener.IMAPListener") as mock_listener:
            instance = MagicMock()
            instance.fetch_unread_emails.return_value = []
            mock_listener.return_value = instance
            run_serve(FakeArgs())

    def test_serve_with_session_flag(self):
        """serve --session 应解析为 flag 参数 True"""
        parser = build_parser()
        args = parser.parse_args(["serve", "--session"])
        assert args.session is True

    def test_serve_with_session_short(self):
        """serve -S 短参数可用"""
        parser = build_parser()
        args = parser.parse_args(["serve", "-S"])
        assert args.session is True

    def test_serve_without_session(self):
        """serve 不带 --session 时 session 默认为 False"""
        parser = build_parser()
        args = parser.parse_args(["serve", "--once"])
        assert args.session is False

    def test_cmd_serve_exits_when_config_invalid(self, capsys):
        """cmd_serve 在 validate_serve_config 返回错误时 exit 1。"""
        from mailcode.cli import cmd_serve

        class FakeArgs:
            dry_run = False
            once = False
            idle = False
            session = False

        # cmd_serve 内部 `from mailcode.config import validate_serve_config`,
        # 因此 patch 源模块 `mailcode.config.validate_serve_config` 才能拦截
        with patch("mailcode.config.validate_serve_config", return_value=["mailcode_bot.email 未设置"]):
            with pytest.raises(SystemExit) as exc:
                cmd_serve(FakeArgs())

        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "MailCode 中继启动失败" in out
        assert "mailcode_bot.email 未设置" in out

    def test_cmd_serve_proceeds_when_config_valid(self, mock_config_patch, temp_data_dir):
        """cmd_serve 在配置通过后进入原 serve 流程 (调用 run_serve)。"""
        from mailcode.cli import cmd_serve

        class FakeArgs:
            dry_run = True
            once = True
            idle = False
            session = False

        with patch("mailcode.config.validate_serve_config", return_value=[]), \
             patch("mailcode.utils.logging.setup_logging"), \
             patch("mailcode.server.run_serve") as mock_run:
            cmd_serve(FakeArgs())
            mock_run.assert_called_once()

    def test_cmd_serve_validates_before_logging_setup(self, mock_config_patch):
        """预检失败时, setup_logging 和 run_serve 都不应被调 (即 IMAPListener 也未被构造)。"""
        from mailcode.cli import cmd_serve

        class FakeArgs:
            dry_run = False
            once = False
            idle = False
            session = False

        with patch("mailcode.config.validate_serve_config", return_value=["err1", "err2"]), \
             patch("mailcode.utils.logging.setup_logging") as mock_log, \
             patch("mailcode.relay.email_listener.IMAPListener") as mock_listener, \
             patch("mailcode.server.run_serve") as mock_run:
            with pytest.raises(SystemExit):
                cmd_serve(FakeArgs())

        mock_log.assert_not_called()
        mock_listener.assert_not_called()
        mock_run.assert_not_called()


class TestSession:
    def test_session_subcommand_registered(self):
        """session 应注册为顶级子命令"""
        parser = build_parser()
        args = parser.parse_args(["session", "list"])
        assert args.command == "session"
        assert args.session_command == "list"

    def test_session_list_parsed(self):
        """session list 应正确解析"""
        parser = build_parser()
        args = parser.parse_args(["session", "list"])
        assert args.session_command == "list"

    def test_session_show_parsed(self):
        """session show <session_id> 应正确解析"""
        parser = build_parser()
        args = parser.parse_args(["session", "show", "abc123"])
        assert args.session_command == "show"
        assert args.session_id == "abc123"

    def test_session_delete_parsed(self):
        """session delete <session_id> 应正确解析"""
        parser = build_parser()
        args = parser.parse_args(["session", "delete", "xyz789"])
        assert args.session_command == "delete"
        assert args.session_id == "xyz789"

    def test_session_cleanup_parsed(self):
        """session cleanup --dry-run 应正确解析"""
        parser = build_parser()
        args = parser.parse_args(["session", "cleanup", "--dry-run"])
        assert args.session_command == "cleanup"
        assert args.dry_run is True

    def test_session_list_invokes_handler(self, capsys, tmp_path):
        """session list 调用 handler.list_sessions"""
        from mailcode.cli import cmd_session

        with patch("mailcode.cli._build_session_handler") as mock_builder:
            mock_handler = MagicMock()
            mock_handler.list_sessions.return_value = []
            mock_builder.return_value = mock_handler

            class FakeArgs:
                session_command = "list"

            cmd_session(FakeArgs())
            mock_handler.list_sessions.assert_called_once()
            out = capsys.readouterr().out
            assert "暂无 session" in out

    def test_session_show_invokes_handler(self, capsys, tmp_path):
        """session show <id> 调用 handler.get_session_status"""
        from mailcode.cli import cmd_session

        with patch("mailcode.cli._build_session_handler") as mock_builder:
            mock_handler = MagicMock()
            mock_handler.get_session_status.return_value = None
            mock_builder.return_value = mock_handler

            class FakeArgs:
                session_command = "show"
                session_id = "deadbeef"

            with pytest.raises(SystemExit) as exc:
                cmd_session(FakeArgs())
            assert exc.value.code == 1
            mock_handler.get_session_status.assert_called_once_with("deadbeef")
            err = capsys.readouterr().err
            assert "未找到" in err

    def test_session_delete_invokes_handler(self, capsys, tmp_path):
        """session delete <id> 调用 handler.terminate_session"""
        from mailcode.cli import cmd_session

        with patch("mailcode.cli._build_session_handler") as mock_builder:
            mock_handler = MagicMock()
            mock_handler.get_session_status.return_value = {
                "session_id": "deadbeef",
                "subject": "test",
                "from_email": "u@t.com",
            }
            mock_handler.terminate_session.return_value = True
            mock_builder.return_value = mock_handler

            class FakeArgs:
                session_command = "delete"
                session_id = "deadbeef"
                yes = True

            cmd_session(FakeArgs())
            mock_handler.terminate_session.assert_called_once_with("deadbeef")

    def test_session_cleanup_invokes_handler(self, capsys, tmp_path):
        """session cleanup 调用 handler._cleanup_expired_sessions"""
        from mailcode.cli import cmd_session

        with patch("mailcode.cli._build_session_handler") as mock_builder:
            mock_handler = MagicMock()
            mock_handler._cleanup_expired_sessions.return_value = 3
            mock_builder.return_value = mock_handler

            class FakeArgs:
                session_command = "cleanup"
                dry_run = True

            cmd_session(FakeArgs())
            mock_handler._cleanup_expired_sessions.assert_called_once_with(dry_run=True)
            out = capsys.readouterr().out
            assert "3" in out
            assert "dry-run" in out


# ============================================================
# --config 参数 / MAILCODE_CONFIG 环境变量 测试
# ============================================================

class TestCustomConfigPath:

    @pytest.fixture(autouse=True)
    def _reset_config_path(self):
        """每个测试后恢复默认配置路径，防止测试间泄漏"""
        yield
        from mailcode.config import set_config_path
        set_config_path(str(Path.home() / ".config" / "mailcode" / "config.json"))

    def test_set_config_path_updates_global(self):
        """set_config_path 更新 USER_CONFIG_PATH 并清除缓存"""
        from mailcode.config import set_config_path, get_config_path
        custom = "/tmp/test-mailcode-config.json"
        set_config_path(custom)
        assert str(get_config_path()) == custom

    def test_config_flag_in_parser(self):
        """--config 参数在 parser 中可用"""
        parser = build_parser()
        args = parser.parse_args(["--config", "/tmp/my-config.json", "serve", "--once"])
        assert args.config == "/tmp/my-config.json"
        assert args.command == "serve"
        assert args.once is True

    def test_config_flag_short_form(self):
        """-c 短参数可用"""
        parser = build_parser()
        args = parser.parse_args(["-c", "/tmp/my-config.json", "config", "show"])
        assert args.config == "/tmp/my-config.json"
        assert args.command == "config"
        assert args.config_command == "show"

    def test_set_config_path_then_config_operations(self, tmp_path):
        """指定自定义路径后 config show/path/init 操作该路径"""
        from mailcode.config import set_config_path

        config_path = tmp_path / "custom-config.json"
        config_path.write_text(json.dumps({"smtp": {"host": "smtp.custom.com"}}))

        set_config_path(str(config_path))

        with patch("mailcode.config.USER_CONFIG_PATH", config_path):
            captured = StringIO()
            old = sys.stdout
            try:
                sys.stdout = captured
                cmd_config(_make_config_args("path"))
            finally:
                sys.stdout = old
            assert captured.getvalue().strip() == str(config_path)

    def test_default_config_path_restored(self):
        """set_config_path 可恢复默认路径"""
        from mailcode.config import set_config_path, get_config_path

        # 先设置自定义路径
        set_config_path("/tmp/some-config.json")
        assert str(get_config_path()) == "/tmp/some-config.json"

        # 恢复默认路径
        set_config_path(str(Path.home() / ".config" / "mailcode" / "config.json"))
        default = get_config_path()
        expected = Path.home() / ".config" / "mailcode" / "config.json"
        assert default == expected


