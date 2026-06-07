"""ConversationHandler 单元测试 —— 覆盖 session-per-file 新 API"""

import json
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mailcode.relay import conversation_handler as ch_module
from mailcode.relay.conversation_handler import (
    ConversationHandler,
    extract_cwd,
    strip_cwd,
)


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def conv_dir(tmp_path):
    """为每个测试提供隔离的会话数据目录。"""
    d = tmp_path / "conversations"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def mock_email_channel():
    """Mock EmailChannel: 提供 email_config/smtp_user 属性 + send_reply 方法。"""
    channel = MagicMock()
    channel.email_config = {"from": "bot@mailcode.com", "from_name": "Bot"}
    channel.smtp_user = "bot@mailcode.com"
    channel.send_reply.return_value = (True, "<reply-abc@mailcode>")
    return channel


@pytest.fixture
def handler(mock_email_channel, conv_dir):
    """使用临时 conv 目录的 ConversationHandler。"""
    index_file = conv_dir / "index.json"
    # _INDEX_FILE 是模块级常量 (import 时计算), 必须同步 patch 才能隔离 IO
    with patch.object(ch_module, "_CONV_DIR", conv_dir), \
         patch.object(ch_module, "_INDEX_FILE", index_file):
        h = ConversationHandler(email_channel=mock_email_channel)
        yield h


def _make_handler(channel, conv_dir):
    """工厂: 在指定 conv_dir 上构造 handler。"""
    index_file = conv_dir / "index.json"
    with patch.object(ch_module, "_CONV_DIR", conv_dir), \
         patch.object(ch_module, "_INDEX_FILE", index_file):
        return ConversationHandler(email_channel=channel)


# ------------------------------------------------------------------ #
# _call_claude
# ------------------------------------------------------------------ #


class TestCallClaude:
    """claude -p 子进程调用测试。"""

    def test_success(self, handler):
        """成功调用返回 stdout.strip()。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "  Hello, world!  \n"
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            result = ch_module.call_claude("test prompt")

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

    def test_nonzero_exit(self, handler):
        """返回码非 0 时返回 None。"""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error occurred"
        mock_result.stdout = ""

        with patch.object(subprocess, "run", return_value=mock_result):
            result = ch_module.call_claude("test")

        assert result is None

    def test_timeout(self, handler):
        """TimeoutExpired 时返回 None。"""
        with patch.object(
            subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=300),
        ):
            result = ch_module.call_claude("test")

        assert result is None

    def test_file_not_found(self, handler):
        """claude 命令不存在时返回 None。"""
        with patch.object(subprocess, "run", side_effect=FileNotFoundError()):
            result = ch_module.call_claude("test")

        assert result is None

    def test_cwd_fallback(self, handler):
        """cwd 为空时回退到 Path.home()。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            result = ch_module.call_claude("test", cwd="")

        assert result == "ok"
        _, kwargs = mock_run.call_args
        assert kwargs["cwd"] == str(Path.home())

    def test_cwd_propagated(self, handler):
        """传入 cwd 时传递给 subprocess。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "ok"
        mock_result.stderr = ""

        with patch.object(subprocess, "run", return_value=mock_result) as mock_run:
            ch_module.call_claude("test", cwd="/tmp")

        _, kwargs = mock_run.call_args
        assert kwargs["cwd"] == "/tmp"

    def test_module_level_call_claude_signature(self):
        """call_claude 是模块级函数, 可独立导入, 不依赖 handler 实例。"""
        import inspect
        # 函数定义在 conversation_handler 模块级
        assert callable(ch_module.call_claude)
        sig = inspect.signature(ch_module.call_claude)
        # 必须有 prompt 与 cwd 两个参数
        params = list(sig.parameters.keys())
        assert "prompt" in params
        assert "cwd" in params
        # 旧的方法不存在于 ConversationHandler 类上
        assert not hasattr(ConversationHandler, "_call_claude")


# ------------------------------------------------------------------ #
# Session IO
# ------------------------------------------------------------------ #


class TestSessionIO:
    """session_<id>.json 读写测试。"""

    def test_new_session_id_is_12_hex(self):
        """_new_session_id 返回 12 位 hex。"""
        sid = ConversationHandler._new_session_id()
        assert len(sid) == 12
        assert all(c in "0123456789abcdef" for c in sid)

    def test_new_session_id_unique(self):
        """多次生成应不同。"""
        ids = {ConversationHandler._new_session_id() for _ in range(50)}
        assert len(ids) == 50

    def test_save_load_roundtrip(self, handler):
        """写入后能正确读取。"""
        data = {
            "session_id": "abc123",
            "cwd": "/tmp",
            "created_at": 1000.0,
            "last_interaction": 2000.0,
            "emails": [
                {"direction": "incoming", "from": "u@t.com", "body": "hi"},
            ],
        }
        handler._save_session("abc123", data)
        loaded = handler._load_session("abc123")
        # last_interaction 会被刷新, 用 helper 字段比较
        assert loaded["session_id"] == "abc123"
        assert loaded["cwd"] == "/tmp"
        assert loaded["created_at"] == 1000.0
        assert len(loaded["emails"]) == 1
        assert loaded["emails"][0]["body"] == "hi"

    def test_load_missing_returns_empty(self, handler):
        """文件不存在时返回空 session 模板。"""
        loaded = handler._load_session("nonexistent")
        assert loaded["session_id"] == "nonexistent"
        assert loaded["cwd"] == ""
        assert loaded["emails"] == []

    def test_load_corrupted_returns_empty(self, handler, conv_dir):
        """损坏的 JSON 文件返回空 session + warn。"""
        bad = conv_dir / "session_broken.json"
        bad.write_text("not json{", encoding="utf-8")

        with patch.object(ch_module.logger, "warning") as mock_warn:
            loaded = handler._load_session("broken")

        assert loaded["session_id"] == "broken"
        assert loaded["emails"] == []
        mock_warn.assert_called()

    def test_save_atomic_uses_tmp(self, handler, conv_dir):
        """_save_session 走 tmp + replace 原子写。"""
        handler._save_session("xyz", {"cwd": "/x", "emails": []})
        path = conv_dir / "session_xyz.json"
        assert path.exists()
        # 不应残留 .tmp 文件
        tmp_files = list(conv_dir.glob("*.tmp"))
        assert tmp_files == []

    def test_load_fills_missing_fields(self, handler, conv_dir):
        """_load_session 补齐缺失字段。"""
        (conv_dir / "session_partial.json").write_text(
            json.dumps({"emails": []}), encoding="utf-8"
        )
        loaded = handler._load_session("partial")
        assert loaded["session_id"] == "partial"
        assert loaded["cwd"] == ""
        assert loaded["created_at"] > 0
        assert loaded["last_interaction"] > 0

    def test_ensure_dirs_creates_index(self, tmp_path, mock_email_channel):
        """_ensure_dirs 在新目录自动创建 index.json。"""
        d = tmp_path / "new_dir"
        # 目录还不存在
        assert not d.exists()
        with patch.object(ch_module, "_CONV_DIR", d), \
             patch.object(ch_module, "_INDEX_FILE", d / "index.json"):
            ConversationHandler(email_channel=mock_email_channel)
        assert (d / "index.json").exists()


# ------------------------------------------------------------------ #
# Index
# ------------------------------------------------------------------ #


class TestIndex:
    """index.json 增删改查测试。"""

    def test_load_save_roundtrip(self, handler):
        """写入后能正确读取。"""
        idx = {"version": 1, "msg_to_session": {"<a@t>": "sess1"}}
        handler._save_index(idx)
        loaded = handler._load_index()
        assert loaded["msg_to_session"]["<a@t>"] == "sess1"

    def test_load_corrupted_returns_empty(self, handler, conv_dir):
        """损坏的 index.json 回退到空 + warn。"""
        (conv_dir / "index.json").write_text("garbage{", encoding="utf-8")
        with patch.object(ch_module.logger, "warning") as mock_warn:
            loaded = handler._load_index()
        assert loaded["msg_to_session"] == {}
        mock_warn.assert_called()

    def test_load_missing_returns_empty(self, handler, conv_dir):
        """index.json 不存在时返回空。"""
        if (conv_dir / "index.json").exists():
            (conv_dir / "index.json").unlink()
        loaded = handler._load_index()
        assert loaded["msg_to_session"] == {}

    def test_update_index_adds_entry(self, handler):
        """_update_index 写入新条目。"""
        handler._update_index("<msg@x>", "sess123")
        assert handler._load_index()["msg_to_session"]["<msg@x>"] == "sess123"

    def test_update_index_skips_empty(self, handler):
        """空 msg_id / session_id 跳过。"""
        before = handler._load_index()
        handler._update_index("", "sess1")
        handler._update_index("<m@t>", "")
        after = handler._load_index()
        assert before == after

    def test_remove_from_index(self, handler):
        """_remove_from_index 删除条目。"""
        handler._update_index("<a@t>", "s1")
        handler._update_index("<b@t>", "s2")
        handler._remove_from_index("<a@t>")
        idx = handler._load_index()["msg_to_session"]
        assert "<a@t>" not in idx
        assert idx["<b@t>"] == "s2"

    def test_remove_from_index_skips_empty(self, handler):
        """空 msg_id 跳过 remove。"""
        handler._update_index("<a@t>", "s1")
        handler._remove_from_index("")
        assert handler._load_index()["msg_to_session"]["<a@t>"] == "s1"


# ------------------------------------------------------------------ #
# Find session by msg_id
# ------------------------------------------------------------------ #


class TestFindSession:
    """_find_session_by_msg_id 测试。"""

    def test_index_hit(self, handler):
        """index 命中直接返回 session_id。"""
        handler._update_index("<incoming@x>", "sessA")
        # 还要有对应的 session 文件, 否则 _find_session_by_msg_id 会清理条目
        handler._save_session("sessA", {"cwd": "", "emails": []})
        assert handler._find_session_by_msg_id("<incoming@x>") == "sessA"

    def test_index_hit_stripped_brackets(self, handler):
        """带尖括号的 msg_id 能在无尖括号条目中命中 (反之亦然)。"""
        # 场景: index 条目无括号, 查询时带括号
        handler._update_index("incoming@x", "sessA")
        handler._save_session("sessA", {"cwd": "", "emails": []})
        assert handler._find_session_by_msg_id("<incoming@x>") == "sessA"

    def test_scan_fallback(self, handler):
        """index 无, 扫描 session 文件兜底。"""
        # 写入 session 文件含 emails, 但 index 故意不更新
        handler._save_session("sessB", {
            "cwd": "",
            "emails": [
                {"direction": "incoming", "msg_id": "<orphan@user>", "body": "hi"},
            ],
        })
        assert handler._find_session_by_msg_id("<orphan@user>") == "sessB"
        # 命中后应回填 index
        assert handler._load_index()["msg_to_session"].get("<orphan@user>") == "sessB"

    def test_no_match_returns_none(self, handler):
        """不存在时返回 None。"""
        assert handler._find_session_by_msg_id("<none@x>") is None

    def test_empty_msg_id(self, handler):
        """空 msg_id 返回 None。"""
        assert handler._find_session_by_msg_id("") is None

    def test_index_hit_missing_file_cleans_up(self, handler):
        """index 命中但 session 文件丢失 → 清理 index 条目 + 继续扫描。"""
        handler._update_index("<ghost@x>", "sessGhost")
        # session 文件不存在
        assert handler._find_session_by_msg_id("<ghost@x>") is None
        # 索引条目已被清理
        assert "<ghost@x>" not in handler._load_index()["msg_to_session"]


# ------------------------------------------------------------------ #
# Cwd extraction / strip
# ------------------------------------------------------------------ #


class TestCwdExtraction:
    """extract_cwd / strip_cwd 测试 (模块级函数)。"""

    def test_with_absolute_path(self, handler, tmp_path):
        """绝对路径 → 正常返回。"""
        d = tmp_path / "subdir"
        d.mkdir()
        assert extract_cwd(f"cwd: {d}\n实际内容") == str(d.resolve())

    def test_without_cwd_returns_none(self, handler):
        """无 cwd 行 → None。"""
        assert extract_cwd("普通邮件正文") is None

    def test_empty_body_returns_none(self, handler):
        """空 body → None。"""
        assert extract_cwd("") is None

    def test_tilde_expansion(self, handler):
        """cwd: ~ 展开为 home。"""
        result = extract_cwd("cwd: ~")
        assert result == str(Path.home())

    def test_relative_path(self, handler, tmp_path, monkeypatch):
        """相对路径相对 Path.cwd() 补全。"""
        sub = tmp_path / "relsub"
        sub.mkdir()
        monkeypatch.chdir(tmp_path)
        result = extract_cwd("cwd: relsub")
        # 补全为 tmp_path/relsub (绝对路径)
        assert result is not None
        assert Path(result).is_absolute()
        assert Path(result).resolve() == sub.resolve()

    def test_invalid_path_returns_none(self, handler):
        """不存在路径 → None + warn。"""
        with patch.object(ch_module.logger, "warning") as mock_warn:
            assert extract_cwd("cwd: /nonexistent_path_xyz_123") is None
        mock_warn.assert_called()

    def test_case_insensitive(self, handler, tmp_path):
        """cwd 大小写不敏感。"""
        d = tmp_path / "a"
        d.mkdir()
        assert extract_cwd(f"CWD: {d}") == str(d.resolve())
        assert extract_cwd(f"Cwd: {d}") == str(d.resolve())

    def test_strip_cwd_removes_line(self, handler, tmp_path):
        """strip_cwd 移除 cwd 行。"""
        d = tmp_path / "p"
        d.mkdir()
        body = f"cwd: {d}\n你好, 这是问题"
        stripped = strip_cwd(body)
        assert "cwd:" not in stripped
        assert "你好" in stripped

    def test_strip_cwd_no_match(self, handler):
        """无 cwd 行时原样返回。"""
        assert strip_cwd("hello world") == "hello world"

    def test_strip_cwd_empty(self, handler):
        """空 body 安全处理。"""
        assert strip_cwd("") == ""


# ------------------------------------------------------------------ #
# Build prompt
# ------------------------------------------------------------------ #


class TestBuildPrompt:
    """_build_prompt 极简版测试。"""

    def test_contains_session_path(self, handler):
        """prompt 含 session 文件路径。"""
        prompt = handler._build_prompt("/tmp/session_abc.json")
        assert "/tmp/session_abc.json" in prompt

    def test_mentions_read_tool(self, handler):
        """prompt 提示 Claude 用 Read 工具。"""
        prompt = handler._build_prompt("/tmp/x.json")
        assert "Read" in prompt

    def test_mentions_emails_field(self, handler):
        """prompt 提到 emails 字段结构。"""
        prompt = handler._build_prompt("/tmp/x.json")
        assert "emails" in prompt

    def test_plain_text_format_hint(self, handler):
        """prompt 要求纯文本格式。"""
        prompt = handler._build_prompt("/tmp/x.json")
        assert "纯文本" in prompt

    def test_no_history_inline(self, handler):
        """极简版不应内联历史 (历史在 session 文件里)。"""
        prompt = handler._build_prompt("/tmp/x.json")
        # 不应有"对话历史"之类短语
        assert "对话历史" not in prompt


# ------------------------------------------------------------------ #
# handle_email 主流程
# ------------------------------------------------------------------ #


class TestHandleEmail:
    """handle_email 主入口测试。"""

    def test_new_conversation_creates_session_and_saves(self, handler, mock_email_channel):
        """第一封邮件 → 新 session, 存盘, index 更新。"""
        mock_email_channel.send_reply.return_value = (True, "<sent-1@mailcode>")

        with patch.object(ch_module, "call_claude", return_value="回复内容"):
            result = handler.handle_email(
                from_email="user@test.com",
                subject="Hello",
                body="你好",
            )

        assert result is True
        # 找到新 session 文件
        sessions = handler.list_sessions()
        assert len(sessions) == 1
        sid = sessions[0]["session_id"]
        # session 包含 2 封邮件
        status = handler.get_session_status(sid)
        assert status is not None
        assert len(status["emails"]) == 2
        assert status["emails"][0]["direction"] == "incoming"
        assert status["emails"][0]["body"] == "你好"
        assert status["emails"][1]["direction"] == "outgoing"
        assert status["emails"][1]["body"] == "回复内容"
        # index 应有 outgoing msg_id
        idx = handler._load_index()["msg_to_session"]
        assert idx.get("<sent-1@mailcode>") == sid

    def test_continue_via_in_reply_to(self, handler, mock_email_channel):
        """In-Reply-To 命中, 续接同一 session。"""
        # 先建一个 session + outgoing msg_id
        mock_email_channel.send_reply.return_value = (True, "<prev@mailcode>")
        with patch.object(ch_module, "call_claude", return_value="first reply"):
            handler.handle_email(
                from_email="user@test.com",
                subject="Hi",
                body="first",
            )
        sid = handler.list_sessions()[0]["session_id"]

        # 第二封邮件 In-Reply-To 指向上次 outgoing
        mock_email_channel.send_reply.return_value = (True, "<next@mailcode>")
        with patch.object(ch_module, "call_claude", return_value="second reply"):
            result = handler.handle_email(
                from_email="user@test.com",
                subject="Re: Hi",
                body="second",
                in_reply_to="<prev@mailcode>",
            )

        assert result is True
        # 仍是同一个 session
        assert handler.list_sessions()[0]["session_id"] == sid
        status = handler.get_session_status(sid)
        assert len(status["emails"]) == 4  # 2 incoming + 2 outgoing

    def test_continue_via_scan_fallback(self, handler, mock_email_channel):
        """index 无但扫描能找到 → 续接。"""
        # 写一个 session 含 incoming msg_id, 但故意不更新 index
        handler._save_session("sessScan", {
            "cwd": "",
            "emails": [
                {"direction": "incoming", "msg_id": "<scanned@user>", "body": "first"},
            ],
        })

        with patch.object(ch_module, "call_claude", return_value="ok"):
            result = handler.handle_email(
                from_email="user@test.com",
                subject="Re: X",
                body="next",
                in_reply_to="<scanned@user>",
            )
        assert result is True
        # 找到的 session 应是 sessScan
        status = handler.get_session_status("sessScan")
        assert status is not None
        assert len(status["emails"]) >= 2

    def test_cwd_extracted_and_persisted(self, handler, mock_email_channel, tmp_path):
        """邮件带 cwd → session.cwd 更新, body 中 cwd 行被剥离。"""
        d = tmp_path / "project"
        d.mkdir()
        mock_email_channel.send_reply.return_value = (True, "<c@mailcode>")
        with patch.object(ch_module, "call_claude", return_value="r") as mc:
            handler.handle_email(
                from_email="u@t.com",
                subject="Hi",
                body=f"cwd: {d}\n真实问题",
            )
        # cwd 传给 claude
        assert mc.call_args.kwargs["cwd"] == str(d.resolve())
        # session.cwd 持久化
        sid = handler.list_sessions()[0]["session_id"]
        status = handler.get_session_status(sid)
        assert status["cwd"] == str(d.resolve())
        # body 中 cwd 行被剥离
        assert status["emails"][0]["body"] == "真实问题"
        assert "cwd:" not in status["emails"][0]["body"]

    def test_cwd_sticky_across_emails(self, handler, mock_email_channel, tmp_path):
        """第一封设 cwd, 第二封不带 → 沿用上次 cwd。"""
        d = tmp_path / "sticky"
        d.mkdir()
        # 第一封
        mock_email_channel.send_reply.return_value = (True, "<s1@mailcode>")
        with patch.object(ch_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="Hi", body=f"cwd: {d}\nq1",
            )
        sid = handler.list_sessions()[0]["session_id"]
        # 第二封 (无 cwd)
        mock_email_channel.send_reply.return_value = (True, "<s2@mailcode>")
        with patch.object(ch_module, "call_claude", return_value="r") as mc:
            handler.handle_email(
                from_email="u@t.com",
                subject="Re: Hi",
                body="q2",
                in_reply_to="<s1@mailcode>",
            )
        # 仍使用上次 cwd
        assert mc.call_args.kwargs["cwd"] == str(d.resolve())
        assert handler.get_session_status(sid)["cwd"] == str(d.resolve())

    def test_cwd_overwrite(self, handler, mock_email_channel, tmp_path):
        """第二封邮件带新 cwd → 覆盖。"""
        d1 = tmp_path / "first"
        d1.mkdir()
        d2 = tmp_path / "second"
        d2.mkdir()
        mock_email_channel.send_reply.return_value = (True, "<o1@mailcode>")
        with patch.object(ch_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="Hi", body=f"cwd: {d1}\nq",
            )
        mock_email_channel.send_reply.return_value = (True, "<o2@mailcode>")
        with patch.object(ch_module, "call_claude", return_value="r") as mc:
            handler.handle_email(
                from_email="u@t.com",
                subject="Re: Hi",
                body=f"cwd: {d2}\nq2",
                in_reply_to="<o1@mailcode>",
            )
        assert mc.call_args.kwargs["cwd"] == str(d2.resolve())

    def test_claude_failure_sends_error_email(self, handler, mock_email_channel):
        """claude 返回 None → 发送"技术问题"错误邮件。"""
        with patch.object(ch_module, "call_claude", return_value=None):
            result = handler.handle_email(
                from_email="u@t.com", subject="Hi", body="q",
            )
        assert result is False
        # send_reply 被调用一次 (错误邮件)
        assert mock_email_channel.send_reply.call_count == 1
        call_kwargs = mock_email_channel.send_reply.call_args.kwargs
        assert "技术问题" in call_kwargs["body"]
        assert "抱歉" in call_kwargs["body"]
        # 错误邮件 subject 加 Re: 前缀
        assert call_kwargs["subject"] == "Re: Hi"

    def test_claude_failure_does_not_save_outgoing(self, handler, mock_email_channel):
        """claude 失败时 session 不应包含 outgoing 邮件。"""
        with patch.object(ch_module, "call_claude", return_value=None):
            handler.handle_email(
                from_email="u@t.com", subject="Hi", body="q",
            )
        sid = handler.list_sessions()[0]["session_id"]
        status = handler.get_session_status(sid)
        # 只有 incoming
        assert len(status["emails"]) == 1
        assert status["emails"][0]["direction"] == "incoming"

    def test_empty_response_sends_error_email(self, handler, mock_email_channel):
        """claude 返回 "" → 发送"没有回复内容"错误邮件。"""
        with patch.object(ch_module, "call_claude", return_value=""):
            result = handler.handle_email(
                from_email="u@t.com", subject="Hi", body="q",
            )
        assert result is False
        assert mock_email_channel.send_reply.call_count == 1
        call_kwargs = mock_email_channel.send_reply.call_args.kwargs
        assert "没有回复内容" in call_kwargs["body"]
        assert call_kwargs["subject"] == "Re: Hi"

    def test_whitespace_only_response_passes_through(self, handler, mock_email_channel):
        """claude 返回 "  \\n  " (非空字符串) → 视为有内容, 不发错误邮件。

        当前实现用 `not response` 判断空, 因此只对 ""/None 触发错误邮件,
        对纯空白字符串会原样发出 (Claude 端负责处理)。
        """
        mock_email_channel.send_reply.return_value = (True, "<w@mailcode>")
        with patch.object(ch_module, "call_claude", return_value="   \n  "):
            result = handler.handle_email(
                from_email="u@t.com", subject="Hi", body="q",
            )
        assert result is True
        # 实际发送了回复, body 是空白字符串
        body = mock_email_channel.send_reply.call_args.kwargs["body"]
        assert body == "   \n  "

    def test_smtp_failure_still_saves_session(self, handler, mock_email_channel):
        """SMTP 失败时 session.emails 仍包含 outgoing, 返回 False。"""
        mock_email_channel.send_reply.return_value = (False, None)
        with patch.object(ch_module, "call_claude", return_value="reply body"):
            result = handler.handle_email(
                from_email="u@t.com", subject="Hi", body="q",
            )
        assert result is False
        sid = handler.list_sessions()[0]["session_id"]
        status = handler.get_session_status(sid)
        # outgoing 邮件已写盘 (虽然发送失败)
        assert len(status["emails"]) == 2
        assert status["emails"][1]["direction"] == "outgoing"
        assert status["emails"][1]["body"] == "reply body"

    def test_smtp_failure_msg_id_empty(self, handler, mock_email_channel):
        """SMTP 失败时 outgoing.msg_id 留空。"""
        mock_email_channel.send_reply.return_value = (False, None)
        with patch.object(ch_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="Hi", body="q",
            )
        sid = handler.list_sessions()[0]["session_id"]
        status = handler.get_session_status(sid)
        assert status["emails"][1]["msg_id"] == ""

    def test_outgoing_msg_id_from_send_reply(self, handler, mock_email_channel):
        """outgoing.msg_id = send_reply 返回的 our_msg_id。"""
        mock_email_channel.send_reply.return_value = (True, "<custom-id@mailcode>")
        with patch.object(ch_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="Hi", body="q",
            )
        sid = handler.list_sessions()[0]["session_id"]
        status = handler.get_session_status(sid)
        assert status["emails"][1]["msg_id"] == "<custom-id@mailcode>"
        # index 同步
        assert handler._load_index()["msg_to_session"]["<custom-id@mailcode>"] == sid

    def test_subject_re_prefix_added(self, handler, mock_email_channel):
        """无 Re: 时自动加。"""
        mock_email_channel.send_reply.return_value = (True, "<x@mailcode>")
        with patch.object(ch_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="New Topic", body="q",
            )
        assert mock_email_channel.send_reply.call_args.kwargs["subject"] == "Re: New Topic"

    def test_subject_re_prefix_not_duplicated(self, handler, mock_email_channel):
        """已有 Re: 时不再加。"""
        mock_email_channel.send_reply.return_value = (True, "<y@mailcode>")
        with patch.object(ch_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="Re: Topic", body="q",
            )
        assert mock_email_channel.send_reply.call_args.kwargs["subject"] == "Re: Topic"

    def test_in_reply_to_passed_through(self, handler, mock_email_channel):
        """references / in_reply_to 透传给 send_reply。"""
        mock_email_channel.send_reply.return_value = (True, "<z@mailcode>")
        with patch.object(ch_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com",
                subject="Re: T",
                body="q",
                references="<r1@t> <r2@t>",
                in_reply_to="<prev@t>",
            )
        kwargs = mock_email_channel.send_reply.call_args.kwargs
        assert kwargs["references"] == "<r1@t> <r2@t>"
        assert kwargs["in_reply_to_msg_id"] == "<prev@t>"

    def test_error_email_in_reply_to_passed(self, handler, mock_email_channel):
        """错误邮件的 in_reply_to = 用户的 in_reply_to 参数。"""
        with patch.object(ch_module, "call_claude", return_value=None):
            handler.handle_email(
                from_email="u@t.com",
                subject="Hi",
                body="q",
                references="<ref@t>",
                in_reply_to="<prev@t>",
            )
        kwargs = mock_email_channel.send_reply.call_args.kwargs
        assert kwargs["in_reply_to_msg_id"] == "<prev@t>"
        assert kwargs["references"] == "<ref@t>"

    def test_no_in_reply_to_creates_new(self, handler, mock_email_channel):
        """in_reply_to 为空时新建 session。"""
        mock_email_channel.send_reply.return_value = (True, "<n1@mailcode>")
        with patch.object(ch_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="A", body="a",
            )
        mock_email_channel.send_reply.return_value = (True, "<n2@mailcode>")
        with patch.object(ch_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="B", body="b",
            )
        # 两个独立 session
        sessions = handler.list_sessions()
        assert len(sessions) == 2


# ------------------------------------------------------------------ #
# list_sessions
# ------------------------------------------------------------------ #


class TestListSessions:
    """list_sessions 测试。"""

    def test_empty(self, handler):
        """无 session 时返回空列表。"""
        assert handler.list_sessions() == []

    def test_returns_all_sessions(self, handler):
        """返回所有 session。"""
        handler._save_session("a1", {"cwd": "", "emails": [{"x": 1}], "created_at": 1.0})
        handler._save_session("a2", {"cwd": "", "emails": [{"x": 1}, {"x": 2}], "created_at": 2.0})
        sessions = handler.list_sessions()
        assert len(sessions) == 2

    def test_sorted_by_last_interaction_desc(self, handler):
        """按 last_interaction 降序。"""
        handler._save_session("old", {"cwd": "", "emails": []})
        time.sleep(0.01)
        handler._save_session("new", {"cwd": "", "emails": []})
        sessions = handler.list_sessions()
        assert sessions[0]["session_id"] == "new"
        assert sessions[1]["session_id"] == "old"

    def test_fields(self, handler):
        """字段完整 (session_id, cwd, created_at, last_interaction, email_count)。"""
        handler._save_session("a1", {
            "cwd": "/tmp", "emails": [{"x": 1}, {"x": 2}], "created_at": 100.0,
        })
        sessions = handler.list_sessions()
        s = sessions[0]
        assert s["session_id"] == "a1"
        assert s["cwd"] == "/tmp"
        assert s["created_at"] == 100.0
        assert s["email_count"] == 2
        assert s["last_interaction"] > 0

    def test_skips_corrupted(self, handler, conv_dir):
        """损坏文件 warn 跳过。"""
        handler._save_session("good", {"cwd": "", "emails": []})
        (conv_dir / "session_bad.json").write_text("not json{", encoding="utf-8")
        with patch.object(ch_module.logger, "warning") as mock_warn:
            sessions = handler.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "good"
        mock_warn.assert_called()


# ------------------------------------------------------------------ #
# get_session_status
# ------------------------------------------------------------------ #


class TestGetSessionStatus:
    """get_session_status 测试。"""

    def test_found(self, handler):
        """找到时返回完整详情。"""
        handler._save_session("s1", {
            "cwd": "/tmp",
            "emails": [{"direction": "incoming", "body": "hi"}],
            "created_at": 100.0,
        })
        status = handler.get_session_status("s1")
        assert status is not None
        assert status["session_id"] == "s1"
        assert status["cwd"] == "/tmp"
        assert status["created_at"] == 100.0
        assert status["email_count"] == 1
        assert len(status["emails"]) == 1

    def test_not_found(self, handler):
        """找不到时返回 None。"""
        assert handler.get_session_status("ghost") is None

    def test_corrupted_returns_none(self, handler, conv_dir):
        """损坏文件返回 None。"""
        (conv_dir / "session_corrupt.json").write_text("garbage", encoding="utf-8")
        assert handler.get_session_status("corrupt") is None


# ------------------------------------------------------------------ #
# terminate_session
# ------------------------------------------------------------------ #


class TestTerminateSession:
    """terminate_session 测试。"""

    def test_delete_existing(self, handler):
        """删除存在 session 返回 True, 文件消失。"""
        handler._save_session("s1", {"cwd": "", "emails": []})
        assert handler.terminate_session("s1") is True
        assert handler.get_session_status("s1") is None

    def test_delete_nonexistent(self, handler):
        """删除不存在 session 返回 False。"""
        assert handler.terminate_session("ghost") is False

    def test_terminate_removes_index_entries(self, handler, mock_email_channel):
        """终止 session 时, index 中所有该 session 的 msg_id 都清掉。"""
        # 建一个 session, 触发 outgoing 后 index 有 msg_id
        mock_email_channel.send_reply.return_value = (True, "<out1@mailcode>")
        with patch.object(ch_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="Hi", body="q",
            )
        # index 应有 <out1@mailcode>
        assert "<out1@mailcode>" in handler._load_index()["msg_to_session"]
        sid = handler.list_sessions()[0]["session_id"]
        # 终止
        assert handler.terminate_session(sid) is True
        # index 全部清空
        assert handler._load_index()["msg_to_session"] == {}

    def test_terminate_preserves_other_sessions(self, handler):
        """终止一个 session 不影响其他 session。"""
        handler._save_session("s1", {"cwd": "", "emails": []})
        handler._save_session("s2", {"cwd": "", "emails": []})
        handler.terminate_session("s1")
        assert handler.get_session_status("s1") is None
        assert handler.get_session_status("s2") is not None

    def test_terminate_cleans_multiple_index_entries(self, handler):
        """terminate 清掉 index 中所有指向该 session 的 msg_id。"""
        handler._update_index("<a@t>", "s1")
        handler._update_index("<b@t>", "s1")
        handler._update_index("<c@t>", "s2")
        handler._save_session("s1", {"cwd": "", "emails": []})
        handler._save_session("s2", {"cwd": "", "emails": []})
        handler.terminate_session("s1")
        idx = handler._load_index()["msg_to_session"]
        assert "<a@t>" not in idx
        assert "<b@t>" not in idx
        assert idx["<c@t>"] == "s2"


# ------------------------------------------------------------------ #
# Cleanup expired sessions
# ------------------------------------------------------------------ #


class TestCleanupExpired:
    """_cleanup_expired_sessions 测试。"""

    def test_deletes_expired(self, handler, mock_email_channel, conv_dir):
        """过期 session 被删。"""
        # 直接写文件, 绕过 _save_session (它会刷新 last_interaction)
        old_time = time.time() - 100 * 86400
        data = {
            "session_id": "old",
            "cwd": "",
            "emails": [],
            "created_at": old_time,
            "last_interaction": old_time,
        }
        (conv_dir / "session_old.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        # TTL 默认 90 天 → 应被删
        deleted = handler._cleanup_expired_sessions()
        assert deleted == 1
        assert handler.get_session_status("old") is None

    def test_keeps_recent(self, handler, conv_dir):
        """未过期 session 保留。"""
        recent = time.time() - 10 * 86400  # 10 天前
        data = {
            "session_id": "recent",
            "cwd": "",
            "emails": [],
            "created_at": recent,
            "last_interaction": recent,
        }
        (conv_dir / "session_recent.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        deleted = handler._cleanup_expired_sessions()
        assert deleted == 0
        assert handler.get_session_status("recent") is not None

    def test_warns_on_corrupted(self, handler, conv_dir):
        """损坏文件只 warn 不删。"""
        bad = conv_dir / "session_corrupt.json"
        bad.write_text("not json{", encoding="utf-8")
        with patch.object(ch_module.logger, "warning") as mock_warn:
            deleted = handler._cleanup_expired_sessions()
        assert deleted == 0
        # 文件仍在
        assert bad.exists()
        mock_warn.assert_called()

    def test_ttl_zero_disables_cleanup(self, handler, conv_dir):
        """TTL=0 时禁用清理。"""
        data = {
            "session_id": "ancient",
            "cwd": "",
            "emails": [],
            "created_at": 0,
            "last_interaction": 0,
        }
        (conv_dir / "session_ancient.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        with patch.object(handler, "_get_ttl_days", return_value=0):
            deleted = handler._cleanup_expired_sessions()
        assert deleted == 0
        assert handler.get_session_status("ancient") is not None

    def test_ttl_negative_disables_cleanup(self, handler, conv_dir):
        """TTL=-1 时禁用清理。"""
        data = {
            "session_id": "ancient2",
            "cwd": "",
            "emails": [],
            "created_at": 0,
            "last_interaction": 0,
        }
        (conv_dir / "session_ancient2.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        with patch.object(handler, "_get_ttl_days", return_value=-1):
            deleted = handler._cleanup_expired_sessions()
        assert deleted == 0

    def test_dry_run_does_not_delete(self, handler, conv_dir):
        """dry-run 模式不实际删除, 返回 0。"""
        old_time = time.time() - 200 * 86400
        data = {
            "session_id": "would_die",
            "cwd": "",
            "emails": [],
            "created_at": old_time,
            "last_interaction": old_time,
        }
        (conv_dir / "session_would_die.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        deleted = handler._cleanup_expired_sessions(dry_run=True)
        assert deleted == 0
        # 文件仍在
        assert handler.get_session_status("would_die") is not None

    def test_cleanup_clears_index(self, handler, mock_email_channel, conv_dir):
        """清理过期 session 时同步清理 index 条目。"""
        old_time = time.time() - 200 * 86400
        data = {
            "session_id": "expire_me",
            "cwd": "",
            "emails": [],
            "created_at": old_time,
            "last_interaction": old_time,
        }
        (conv_dir / "session_expire_me.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        handler._update_index("<m1@t>", "expire_me")
        handler._update_index("<m2@t>", "expire_me")
        handler._cleanup_expired_sessions()
        idx = handler._load_index()["msg_to_session"]
        assert "<m1@t>" not in idx
        assert "<m2@t>" not in idx
