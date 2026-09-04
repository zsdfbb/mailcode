"""PiRunner 单元测试。

用 parametrize 覆盖 _build_args 的所有参数组合,
以及 hint_for_failure、_command 返回值。
"""

import pytest

from mailcode.utils.pi_runner import PiRunner


class TestPiRunnerBuildArgs:
    """PiRunner._build_args() 全组合测试。"""

    @pytest.mark.parametrize(
        "session_id, resume, no_session, expected",
        [
            # 基本: 无参数
            (None, False, False, ["pi", "-p"]),
            # 只有 session_id
            ("abc-123", False, False, ["pi", "-p", "--session-id", "abc-123"]),
            # 只有 resume
            (None, True, False, ["pi", "-p", "-c"]),
            # 只有 no_session
            (None, False, True, ["pi", "-p", "--no-session"]),
            # session_id + resume
            ("uuid-456", True, False, ["pi", "-p", "--session-id", "uuid-456", "-c"]),
            # session_id + no_session
            ("uuid-789", False, True, ["pi", "-p", "--session-id", "uuid-789", "--no-session"]),
            # resume + no_session
            (None, True, True, ["pi", "-p", "-c", "--no-session"]),
            # 全组合
            ("full-combo", True, True, ["pi", "-p", "--session-id", "full-combo", "-c", "--no-session"]),
        ],
        ids=[
            "default",
            "session_id_only",
            "resume_only",
            "no_session_only",
            "session_id_resume",
            "session_id_no_session",
            "resume_no_session",
            "all_combined",
        ],
    )
    def test_build_args(self, session_id, resume, no_session, expected):
        runner = PiRunner()
        assert runner._build_args(
            session_id=session_id, resume=resume, no_session=no_session
        ) == expected


class TestPiRunnerMetadata:
    """PiRunner 元数据和提示信息测试。"""

    def test_name(self):
        runner = PiRunner()
        assert runner.name == "pi"

    def test_command(self):
        runner = PiRunner()
        assert runner._command() == "pi"

    def test_hint_for_failure_contains_npm(self):
        runner = PiRunner()
        hint = runner.hint_for_failure()
        assert "npm i -g" in hint

    def test_hint_for_failure_contains_package_name(self):
        runner = PiRunner()
        hint = runner.hint_for_failure()
        assert "@mariozechner/pi-coding-agent" in hint

    def test_hint_for_failure_contains_login(self):
        runner = PiRunner()
        hint = runner.hint_for_failure()
        assert "pi /login" in hint
