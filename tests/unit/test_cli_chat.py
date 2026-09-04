"""cli_chat 单元测试"""

from unittest.mock import MagicMock, patch


class TestChatCommand:
    def test_module_importable(self):
        """模块可导入。"""
        from mailcode import cli_chat
        assert hasattr(cli_chat, "cmd_chat")

    def test_chat_uses_runner(self):
        """cmd_chat 使用 get_runner 获取 runner 并调用 call。"""
        from mailcode.cli_chat import cmd_chat

        class FakeArgs:
            session_id = None
            cwd = "/tmp"
            agent = "claude"

        with patch("mailcode.cli_chat.get_runner") as mock_get:
            mock_runner = MagicMock()
            mock_runner.call.return_value = "hello"
            mock_get.return_value = mock_runner

            with patch("builtins.input", side_effect=["/exit"]):
                cmd_chat(FakeArgs())

            mock_get.assert_called_once_with("claude")

    def test_chat_passes_agent_from_args(self):
        """cmd_chat 传递 --agent 到 get_runner。"""
        from mailcode.cli_chat import cmd_chat

        class FakeArgs:
            session_id = None
            cwd = ""
            agent = "pi"

        with patch("mailcode.cli_chat.get_runner") as mock_get:
            mock_runner = MagicMock()
            mock_runner.call.return_value = "response"
            mock_get.return_value = mock_runner

            with patch("builtins.input", side_effect=["/exit"]):
                cmd_chat(FakeArgs())

            mock_get.assert_called_once_with("pi")
