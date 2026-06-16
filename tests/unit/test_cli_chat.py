"""cli_chat 单元测试"""


class TestChatCommand:
    def test_module_importable(self):
        """模块可导入。"""
        from mailcode import cli_chat
        assert hasattr(cli_chat, "cmd_chat")
