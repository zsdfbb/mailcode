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

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch


from mailcode.utils import claude_runner as cr_module
from mailcode.utils.claude_runner import call_claude


class TestCallClaude:
    """claude -p 子进程调用测试。"""

    def test_success(self):
        """成功调用返回 stdout.strip(), 使用 stdin 而非 -p。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "  Hello, world!  \n"
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            result = cr_module.call_claude("test prompt")

        assert result == "Hello, world!"
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        # 应通过 stdin 传 prompt, 不再用 -p
        assert "-p" not in args[0], f"-p flag should not be in args: {args[0]}"
        assert kwargs["input"] == "test prompt"
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 86400
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
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=86400),
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

    def test_stdin_instead_of_dash_p(self):
        """prompt 通过 stdin (input=) 传入, 不经过 -p 参数。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            cr_module.call_claude("hello")

        args = mock_run.call_args[0][0]
        assert "-p" not in args
        kwargs = mock_run.call_args[1]
        assert kwargs["input"] == "hello"

    def test_multiline_prompt_with_yaml_frontmatter(self):
        """含 --- YAML frontmatter 的多行 prompt 不应报 unknown option。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "response"
        mock_result.stderr = ""

        prompt = "---\ntitle: 投资基础系列-定时任务\n---\n\n# 正文"
        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            result = cr_module.call_claude(prompt)

        assert result == "response"
        args = mock_run.call_args[0][0]
        assert "-p" not in args
        kwargs = mock_run.call_args[1]
        assert kwargs["input"] == prompt

    #
    # --- session_id / resume 参数测试 ---
    #

    def test_default_args_backward_compatible(self):
        """无参数时 args 保持 ``["claude", "--dangerously-skip-permissions"]``。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            cr_module.call_claude("hello")

        args = mock_run.call_args[0][0]
        assert args == ["claude", "--dangerously-skip-permissions"]

    def test_session_id_only(self):
        """仅传 session_id → args 包含 ``--session-id <id>``。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            cr_module.call_claude("hello", session_id="sid-123")

        args = mock_run.call_args[0][0]
        assert args == [
            "claude", "--dangerously-skip-permissions",
            "--session-id", "sid-123",
        ]

    def test_session_id_and_resume(self):
        """传 session_id + resume → args 包含 ``--session-id <id> --resume``。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            cr_module.call_claude("hello", session_id="sid-456", resume=True)

        args = mock_run.call_args[0][0]
        assert args == [
            "claude", "--dangerously-skip-permissions",
            "--session-id", "sid-456",
            "--resume",
        ]

    def test_session_id_reuse_errors(self):
        """新参数下错误处理依然正常工作。"""
        # nonzero exit
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "err"
        mock_result.stdout = ""
        with patch.object(subprocess, "run", return_value=mock_result):
            assert cr_module.call_claude("hello", session_id="x") is None

        # timeout
        with patch.object(
            subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=86400),
        ):
            assert cr_module.call_claude("hello", session_id="x", resume=True) is None

        # file not found
        with patch.object(subprocess, "run", side_effect=FileNotFoundError()):
            assert cr_module.call_claude("hello", resume=False) is None

    def test_module_level_signature(self):
        """call_claude 是模块级函数, 可独立导入, 不依赖 handler 实例。"""
        import inspect
        assert callable(cr_module.call_claude)
        sig = inspect.signature(cr_module.call_claude)
        params = list(sig.parameters.keys())
        assert "prompt" in params
        assert "cwd" in params
        assert "session_id" in params
        assert "resume" in params
        assert "timeout" in params  # 修复 C: per-task timeout 参数
        # 同样可从顶层 utils 包导入
        assert callable(call_claude)

    #
    # --- 错误日志精度 (修复 A) ---
    #

    def test_nonzero_exit_empty_stderr_logs_returncode(self, caplog):
        """non-zero + 空 stderr 模式 (本次 Bug 模式): 返回 None 且日志含 returncode=-15."""
        mock_result = MagicMock()
        mock_result.returncode = -15  # SIGTERM
        mock_result.stderr = ""
        mock_result.stdout = ""

        with caplog.at_level(logging.ERROR, logger="mailcode.utils.claude_runner"):
            with patch.object(subprocess, "run", return_value=mock_result):
                result = cr_module.call_claude("test prompt")

        assert result is None
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("returncode=-15" in r.getMessage() for r in error_records), \
            f"expected returncode=-15 in ERROR log, got: {[r.getMessage() for r in error_records]}"
        assert any("stderr[:500]=''" in r.getMessage() for r in error_records), \
            f"expected empty stderr repr in ERROR log, got: {[r.getMessage() for r in error_records]}"
        assert any("args=" in r.getMessage() for r in error_records)

    def test_signal_kill_logged_as_negative_returncode(self, caplog):
        """SIGKILL (returncode=-9) 应被显式区分于普通错误 (returncode=+N)."""
        mock_result = MagicMock()
        mock_result.returncode = -9
        mock_result.stderr = "Killed"
        mock_result.stdout = ""

        with caplog.at_level(logging.ERROR, logger="mailcode.utils.claude_runner"):
            with patch.object(subprocess, "run", return_value=mock_result):
                result = cr_module.call_claude("test")

        assert result is None
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("returncode=-9" in r.getMessage() for r in error_records)

    #
    # --- OSError 兜底 (修复 B) ---
    #

    def test_broken_pipe_error_returns_none(self, caplog):
        """BrokenPipeError 应被捕获并返回 None, 不冒泡到调用方."""
        with caplog.at_level(logging.ERROR, logger="mailcode.utils.claude_runner"):
            with patch.object(
                subprocess, "run",
                side_effect=BrokenPipeError(32, "Broken pipe"),
            ):
                result = cr_module.call_claude("test")

        assert result is None
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("errno=32" in r.getMessage() for r in error_records)
        assert any("OS 错误" in r.getMessage() for r in error_records)

    def test_permission_error_returns_none(self, caplog):
        """PermissionError 应被捕获并返回 None."""
        with caplog.at_level(logging.ERROR, logger="mailcode.utils.claude_runner"):
            with patch.object(
                subprocess, "run",
                side_effect=PermissionError(13, "Permission denied"),
            ):
                result = cr_module.call_claude("test")

        assert result is None
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("errno=13" in r.getMessage() for r in error_records)

    #
    # --- Per-task timeout (修复 C) ---
    #

    def test_custom_timeout_passed_to_subprocess(self):
        """timeout 参数应传给 subprocess.run 而非默认 86400."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            cr_module.call_claude("test", timeout=120)

        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 120

    def test_default_timeout_unchanged_without_param(self):
        """不传 timeout 时仍用 CLAUDE_TIMEOUT_SECONDS (向后兼容)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            cr_module.call_claude("test")

        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 86400

    #
    # --- Subprocess lifecycle 日志 (修复 D) ---
    #

    def test_lifecycle_logs_start_and_end(self, caplog):
        """INFO 日志应包含启动 + 结束 (elapsed)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with caplog.at_level(logging.INFO, logger="mailcode.utils.claude_runner"):
            with patch.object(subprocess, "run", return_value=mock_result):
                cr_module.call_claude("hello world")

        info_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert any("claude 子进程启动" in m for m in info_messages), \
            f"expected startup INFO log, got: {info_messages}"
        assert any("claude 子进程成功" in m for m in info_messages), \
            f"expected success INFO log, got: {info_messages}"
        assert any("prompt_len=" in m for m in info_messages)