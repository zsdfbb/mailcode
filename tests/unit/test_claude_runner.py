"""claude_runner 单元测试 —— 从 test_conversation_handler.py::TestCallClaude 迁移

测试 ``mailcode.utils.claude_runner.call_claude`` 的契约:
  - 成功: 返回 stdout.strip()
  - 非 0 返回码: 返回 None
  - TimeoutExpired: 返回 None
  - FileNotFoundError (claude 未安装): 返回 None
  - cwd 为空: 回退到 Path.home()
  - 传入 cwd: 传给 subprocess
  - 函数是模块级, 可独立导入
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch


from mailcode.utils import claude_runner as cr_module
from mailcode.utils.claude_runner import call_claude


class TestCallClaude:
    """claude -p 子进程调用测试。"""

    def test_success(self):
        """成功调用返回 stdout.strip()。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "  Hello, world!  \n"
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            result = cr_module.call_claude("test prompt")

        assert result == "Hello, world!"
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert args[0] == [
            "claude",
            "-p",
            "test prompt",
            "--dangerously-skip-permissions",
        ]
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 300
        # 默认 cwd 应该是 Path.home()
        assert kwargs["cwd"] == str(Path.home())

    def test_nonzero_exit(self):
        """返回码非 0 时返回 None。"""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error occurred"
        mock_result.stdout = ""

        with patch.object(subprocess, "run", return_value=mock_result):
            result = cr_module.call_claude("test")

        assert result is None

    def test_timeout(self):
        """TimeoutExpired 时返回 None。"""
        with patch.object(
            subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=300),
        ):
            result = cr_module.call_claude("test")

        assert result is None

    def test_file_not_found(self):
        """claude 命令不存在时返回 None。"""
        with patch.object(subprocess, "run", side_effect=FileNotFoundError()):
            result = cr_module.call_claude("test")

        assert result is None

    def test_cwd_fallback(self):
        """cwd 为空时回退到 Path.home()。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            result = cr_module.call_claude("test", cwd="")

        assert result == "ok"
        _, kwargs = mock_run.call_args
        assert kwargs["cwd"] == str(Path.home())

    def test_cwd_propagated(self):
        """传入 cwd 时传递给 subprocess。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            cr_module.call_claude("test", cwd="/tmp")

        _, kwargs = mock_run.call_args
        assert kwargs["cwd"] == "/tmp"

    def test_module_level_signature(self):
        """call_claude 是模块级函数, 可独立导入, 不依赖 handler 实例。"""
        import inspect
        assert callable(cr_module.call_claude)
        sig = inspect.signature(cr_module.call_claude)
        params = list(sig.parameters.keys())
        assert "prompt" in params
        assert "cwd" in params
        # 同样可从顶层 utils 包导入
        assert callable(call_claude)