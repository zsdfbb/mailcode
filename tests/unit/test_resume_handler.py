"""ResumeConversationHandler 单元测试 —— 覆盖 claude --session-id/--resume 版 handler"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mailcode.relay import resume_handler as rh_module
from mailcode.relay.resume_handler import ResumeConversationHandler
from mailcode.utils import claude_runner as cr_module


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def mock_email_channel():
    """Mock EmailChannel: 提供 email_config/smtp_user 属性 + send_reply 方法。"""
    channel = MagicMock()
    channel.email_config = {"from": "bot@mailcode.com", "from_name": "Bot"}
    channel.smtp_user = "bot@mailcode.com"
    channel.send_reply.return_value = (True, "<reply-abc@mailcode>")
    return channel


@pytest.fixture
def handler(mock_email_channel, temp_data_dir):
    """使用临时目录的 ResumeConversationHandler。"""
    transcripts_dir = temp_data_dir / "transcripts"
    mapping_file = temp_data_dir / "claude_sessions.json"

    with patch.object(rh_module, "_MAILCODE_HOME", temp_data_dir), \
         patch.object(rh_module, "_TRANSCRIPTS_DIR", transcripts_dir), \
         patch.object(rh_module, "_MAPPING_FILE", mapping_file):
        h = ResumeConversationHandler(email_channel=mock_email_channel)
        yield h


# ------------------------------------------------------------------ #
# Mapping IO
# ------------------------------------------------------------------ #


class TestMappingIO:
    """claude_sessions.json 读写测试。"""

    def test_load_mapping_empty(self, handler):
        """文件不存在 → 返回空文档。"""
        mapping = handler._load_mapping()
        assert mapping == {"version": 1, "threads": {}}

    def test_load_mapping_corrupt(self, handler, temp_data_dir):
        """损坏的 JSON → 返回空文档 + warn。"""
        mapping_file = temp_data_dir / "claude_sessions.json"
        mapping_file.write_text("not json{", encoding="utf-8")

        with patch.object(rh_module.logger, "warning") as mock_warn:
            mapping = handler._load_mapping()

        assert mapping == {"version": 1, "threads": {}}
        mock_warn.assert_called()

    def test_save_and_load_mapping(self, handler):
        """写入后能正确读取。"""
        data = {
            "version": 1,
            "threads": {
                "<msg1@t>": {
                    "claude_session_id": "uuid-1",
                    "user_email": "u@t.com",
                    "subject": "Hello",
                    "cwd": "/home/user",
                    "email_count": 2,
                    "created_at": 1000.0,
                    "last_interaction": 2000.0,
                },
            },
        }
        handler._save_mapping(data)
        loaded = handler._load_mapping()
        assert loaded == data

    def test_save_atomic_uses_tmp(self, handler, temp_data_dir):
        """_save_mapping 走 tmp + replace 原子写。"""
        handler._save_mapping({"version": 1, "threads": {}})
        mapping_file = temp_data_dir / "claude_sessions.json"
        assert mapping_file.exists()
        tmp_files = list(temp_data_dir.glob("*.tmp"))
        assert tmp_files == []

    def test_load_mapping_threads_not_dict(self, handler, temp_data_dir):
        """threads 字段不是 dict → 回退为空 dict。"""
        mapping_file = temp_data_dir / "claude_sessions.json"
        mapping_file.write_text(
            json.dumps({"version": 1, "threads": "not_a_dict"}), encoding="utf-8"
        )
        mapping = handler._load_mapping()
        assert mapping["threads"] == {}


# ------------------------------------------------------------------ #
# Transcript IO
# ------------------------------------------------------------------ #


class TestTranscript:
    """transcripts/<uuid>.json 读写测试。"""

    def test_save_and_load_transcript(self, handler):
        """写入后能正确读取。"""
        data = {
            "claude_session_id": "uuid-1",
            "user_email": "u@t.com",
            "created_at": 1000.0,
            "entries": [
                {"direction": "incoming", "from": "u@t.com", "body": "hi"},
            ],
        }
        handler._save_transcript("uuid-1", data)
        loaded = handler._load_transcript("uuid-1")
        assert loaded == data

    def test_load_missing_returns_none(self, handler):
        """文件不存在 → None。"""
        assert handler._load_transcript("nonexistent") is None

    def test_load_corrupted_returns_none(self, handler, temp_data_dir):
        """损坏的 JSON → None + warn。"""
        transcript_file = temp_data_dir / "transcripts" / "corrupt.json"
        transcript_file.parent.mkdir(parents=True, exist_ok=True)
        transcript_file.write_text("garbage{", encoding="utf-8")

        with patch.object(rh_module.logger, "warning") as mock_warn:
            result = handler._load_transcript("corrupt")

        assert result is None
        mock_warn.assert_called()

    def test_append_transcript(self, handler):
        """追加 entry 后内容正确。"""
        # 先初始化 transcript
        handler._save_transcript("uuid-1", {
            "claude_session_id": "uuid-1",
            "user_email": "u@t.com",
            "created_at": 1000.0,
            "entries": [],
        })

        entry = {
            "direction": "incoming",
            "from": "u@t.com",
            "subject": "Hello",
            "body": "Hi there",
            "date": 1500.0,
            "email_msg_id": "",
        }
        handler._append_to_transcript("uuid-1", entry)

        transcript = handler._load_transcript("uuid-1")
        assert len(transcript["entries"]) == 1
        assert transcript["entries"][0]["body"] == "Hi there"
        assert transcript["entries"][0]["direction"] == "incoming"

    def test_append_multiple_entries(self, handler):
        """连续追加多条 entry。"""
        handler._save_transcript("uuid-multi", {
            "claude_session_id": "uuid-multi",
            "user_email": "u@t.com",
            "created_at": 1000.0,
            "entries": [],
        })

        handler._append_to_transcript("uuid-multi", {
            "direction": "incoming", "body": "first",
        })
        handler._append_to_transcript("uuid-multi", {
            "direction": "outgoing", "body": "reply",
        })

        transcript = handler._load_transcript("uuid-multi")
        assert len(transcript["entries"]) == 2
        assert transcript["entries"][0]["body"] == "first"
        assert transcript["entries"][1]["body"] == "reply"

    def test_transcript_rejects_non_dict_entry(self, handler):
        """非 dict entry → log error, transcript 不变。"""
        handler._save_transcript("uuid-2", {
            "claude_session_id": "uuid-2",
            "user_email": "u@t.com",
            "created_at": 1000.0,
            "entries": [],
        })

        with patch.object(rh_module.logger, "error") as mock_err:
            handler._append_to_transcript("uuid-2", "not a dict")

        mock_err.assert_called_once()
        transcript = handler._load_transcript("uuid-2")
        assert len(transcript["entries"]) == 0

    def test_append_transcript_missing_file(self, handler):
        """transcript 文件不存在 → log error。"""
        with patch.object(rh_module.logger, "error") as mock_err:
            handler._append_to_transcript("no-such-uuid", {
                "direction": "incoming", "body": "hi",
            })
        mock_err.assert_called_once()


# ------------------------------------------------------------------ #
# Thread key lookup
# ------------------------------------------------------------------ #


class TestGetThreadKey:
    """_get_thread_key 测试。"""

    def test_exact_match(self, handler):
        """精确匹配。"""
        handler._save_mapping({
            "version": 1,
            "threads": {
                "<msg@t>": {
                    "claude_session_id": "uuid-1",
                    "user_email": "u@t.com",
                    "subject": "Hi",
                    "cwd": "/tmp",
                    "email_count": 2,
                    "created_at": 1000.0,
                    "last_interaction": 2000.0,
                },
            },
        })
        assert handler._get_thread_key("<msg@t>") == "<msg@t>"

    def test_stripped_brackets_match(self, handler):
        """in_reply_to 不带尖括号, 映射 key 带尖括号 → 匹配。"""
        handler._save_mapping({
            "version": 1,
            "threads": {
                "<msg@t>": {
                    "claude_session_id": "uuid-1",
                    "user_email": "u@t.com",
                    "subject": "Hi",
                    "cwd": "/tmp",
                    "email_count": 2,
                    "created_at": 1000.0,
                    "last_interaction": 2000.0,
                },
            },
        })
        assert handler._get_thread_key("msg@t") == "<msg@t>"

    def test_extra_brackets_match(self, handler):
        """in_reply_to 带尖括号, 映射 key 不带 → 匹配。"""
        handler._save_mapping({
            "version": 1,
            "threads": {
                "msg@t": {
                    "claude_session_id": "uuid-1",
                    "user_email": "u@t.com",
                    "subject": "Hi",
                    "cwd": "/tmp",
                    "email_count": 2,
                    "created_at": 1000.0,
                    "last_interaction": 2000.0,
                },
            },
        })
        assert handler._get_thread_key("<msg@t>") == "msg@t"

    def test_no_match_returns_none(self, handler):
        """不存在时返回 None。"""
        assert handler._get_thread_key("<none@x>") is None

    def test_empty_in_reply_to(self, handler):
        """空 in_reply_to 返回 None。"""
        assert handler._get_thread_key("") is None


# ------------------------------------------------------------------ #
# handle_email 主流程
# ------------------------------------------------------------------ #


class TestHandleEmail:
    """handle_email 主入口测试。"""

    def test_new_conversation_calls_session_id(self, handler, mock_email_channel):
        """第一封邮件 (无 in_reply_to) → call_claude 带 session_id, resume=False。"""
        mock_email_channel.send_reply.return_value = (True, "<sent-1@mailcode>")

        with patch.object(cr_module, "call_claude", return_value="回复内容") as mock_call, \
             patch("uuid.uuid4", return_value="mock-uuid-new"):
            result = handler.handle_email(
                from_email="user@test.com",
                subject="Hello",
                body="你好",
            )

        assert result is True
        mock_call.assert_called_once()
        call_kwargs = mock_call.call_args.kwargs
        assert call_kwargs["session_id"] == "mock-uuid-new"
        assert call_kwargs["resume"] is False

    def test_reply_calls_resume(self, handler, mock_email_channel):
        """有 in_reply_to 命中映射 → call_claude 带 resume=True。"""
        # 预写入映射
        handler._save_mapping({
            "version": 1,
            "threads": {
                "<prev-msg@test.com>": {
                    "claude_session_id": "existing-uuid",
                    "user_email": "user@test.com",
                    "subject": "Original",
                    "cwd": str(Path.home()),
                    "email_count": 2,
                    "created_at": 1000.0,
                    "last_interaction": 2000.0,
                },
            },
        })

        mock_email_channel.send_reply.return_value = (True, "<reply-1@mailcode>")

        with patch.object(cr_module, "call_claude", return_value="后续回复") as mock_call:
            result = handler.handle_email(
                from_email="user@test.com",
                subject="Re: Original",
                body="继续对话",
                in_reply_to="<prev-msg@test.com>",
            )

        assert result is True
        mock_call.assert_called_once()
        call_kwargs = mock_call.call_args.kwargs
        assert call_kwargs["session_id"] == "existing-uuid"
        assert call_kwargs["resume"] is True

    def test_cwd_extracted_and_stripped(self, handler, mock_email_channel, tmp_path):
        """邮件带 cwd → cwd 传给 call_claude, body 中 cwd 行被剥离。"""
        d = tmp_path / "project"
        d.mkdir()
        mock_email_channel.send_reply.return_value = (True, "<cwd-1@mailcode>")

        with patch.object(cr_module, "call_claude", return_value="r") as mock_call:
            handler.handle_email(
                from_email="u@t.com",
                subject="Hi",
                body=f"cwd: {d}\n真实问题",
            )

        # cwd 作为关键字参数
        assert mock_call.call_args.kwargs["cwd"] == str(d.resolve())
        # 剥离后的 body 作为第一个位置参数 (prompt)
        prompt = mock_call.call_args[0][0]
        assert "cwd:" not in prompt
        assert prompt == "真实问题"

    def test_cwd_sticky(self, handler, mock_email_channel, tmp_path):
        """第一封设 cwd, 第二封不带 cwd → 沿用上次 cwd。"""
        d = tmp_path / "sticky"
        d.mkdir()

        # 第一封: 带 cwd
        mock_email_channel.send_reply.return_value = (True, "<first-msg@mc>")
        with patch.object(cr_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="Hi", body=f"cwd: {d}\nq1",
            )

        # 第二封: 不带 cwd, in_reply_to 指向上次 outgoing
        mock_email_channel.send_reply.return_value = (True, "<second-msg@mc>")
        with patch.object(cr_module, "call_claude", return_value="r") as mock_call:
            handler.handle_email(
                from_email="u@t.com",
                subject="Re: Hi",
                body="q2",
                in_reply_to="<first-msg@mc>",
            )

        assert mock_call.call_args.kwargs["cwd"] == str(d.resolve())

    def test_cwd_overwrite(self, handler, mock_email_channel, tmp_path):
        """第二封带新 cwd → 覆盖旧 cwd。"""
        d1 = tmp_path / "first"
        d1.mkdir()
        d2 = tmp_path / "second"
        d2.mkdir()

        mock_email_channel.send_reply.return_value = (True, "<o1@mailcode>")
        with patch.object(cr_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="Hi", body=f"cwd: {d1}\nq",
            )

        mock_email_channel.send_reply.return_value = (True, "<o2@mailcode>")
        with patch.object(cr_module, "call_claude", return_value="r") as mock_call:
            handler.handle_email(
                from_email="u@t.com",
                subject="Re: Hi",
                body=f"cwd: {d2}\nq2",
                in_reply_to="<o1@mailcode>",
            )

        assert mock_call.call_args.kwargs["cwd"] == str(d2.resolve())

    def test_claude_none_sends_error(self, handler, mock_email_channel):
        """call_claude 返回 None → 发送错误邮件。"""
        with patch.object(cr_module, "call_claude", return_value=None):
            result = handler.handle_email(
                from_email="u@t.com", subject="Hi", body="q",
            )

        assert result is False
        assert mock_email_channel.send_reply.call_count == 1
        call_kwargs = mock_email_channel.send_reply.call_args.kwargs
        # The error message should be present (will vary based on shutil.which)
        assert len(call_kwargs["body"]) > 10

    def test_claude_empty_sends_error(self, handler, mock_email_channel):
        """call_claude 返回 "" → 发送错误邮件。"""
        with patch.object(cr_module, "call_claude", return_value=""):
            result = handler.handle_email(
                from_email="u@t.com", subject="Hi", body="q",
            )

        assert result is False
        call_kwargs = mock_email_channel.send_reply.call_args.kwargs
        assert "没有返回任何内容" in call_kwargs["body"]

    def test_mapping_updated_on_first_message(self, handler, mock_email_channel):
        """新消息发送后, 映射文件包含 outgoing msg_id → claude_session_id。"""
        mock_email_channel.send_reply.return_value = (True, "<brand-new@mailcode>")

        with patch.object(cr_module, "call_claude", return_value="回复内容"):
            result = handler.handle_email(
                from_email="user@test.com",
                subject="Hello",
                body="你好",
            )

        assert result is True
        mapping = handler._load_mapping()
        assert "<brand-new@mailcode>" in mapping["threads"]
        thread = mapping["threads"]["<brand-new@mailcode>"]
        assert thread["user_email"] == "user@test.com"
        assert thread["email_count"] == 2  # 1 incoming + 1 outgoing
        assert thread["cwd"] == str(Path.home())

    def test_transcript_created_on_first_message(self, handler, mock_email_channel):
        """新消息创建 transcript 文件, 含 incoming + outgoing 两条 entry。"""
        mock_email_channel.send_reply.return_value = (True, "<trans-1@mailcode>")

        with patch.object(cr_module, "call_claude", return_value="回复") as mock_call, \
             patch("uuid.uuid4", return_value="uuid-for-transcript"):
            result = handler.handle_email(
                from_email="user@test.com",
                subject="Hello",
                body="你好",
            )

        assert result is True
        transcript = handler._load_transcript("uuid-for-transcript")
        assert transcript is not None
        assert transcript["user_email"] == "user@test.com"
        assert len(transcript["entries"]) == 2
        assert transcript["entries"][0]["direction"] == "incoming"
        assert transcript["entries"][0]["body"] == "你好"
        assert transcript["entries"][1]["direction"] == "outgoing"
        assert transcript["entries"][1]["body"] == "回复\n\n──────────────────────────────────────────\n📬 MailCode · 对话 uuid-for-tra（第 1 轮）\n回复此邮件继续 · 发「status」查系统状态"

    def test_subject_re_prefix_added(self, handler, mock_email_channel):
        """无 Re: 时自动加。"""
        mock_email_channel.send_reply.return_value = (True, "<sub-1@mailcode>")
        with patch.object(cr_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="New Topic", body="q",
            )
        assert mock_email_channel.send_reply.call_args.kwargs["subject"] == "Re: New Topic"

    def test_subject_re_prefix_not_duplicated(self, handler, mock_email_channel):
        """已有 Re: 时不再加。"""
        mock_email_channel.send_reply.return_value = (True, "<sub-2@mailcode>")
        with patch.object(cr_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="Re: Topic", body="q",
            )
        assert mock_email_channel.send_reply.call_args.kwargs["subject"] == "Re: Topic"

    def test_in_reply_to_passed_through(self, handler, mock_email_channel):
        """references / in_reply_to 透传给 send_reply。"""
        mock_email_channel.send_reply.return_value = (True, "<z@mailcode>")
        with patch.object(cr_module, "call_claude", return_value="r"):
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

    def test_smtp_failure_returns_false(self, handler, mock_email_channel):
        """SMTP 失败时返回 False。"""
        mock_email_channel.send_reply.return_value = (False, None)
        with patch.object(cr_module, "call_claude", return_value="reply body"):
            result = handler.handle_email(
                from_email="u@t.com", subject="Hi", body="q",
            )
        assert result is False

    def test_session_footer_included(self, handler, mock_email_channel):
        """回复末尾包含会话脚注。"""
        mock_email_channel.send_reply.return_value = (True, "<footer-test@mc>")
        with patch.object(cr_module, "call_claude", return_value="reply"):
            handler.handle_email("u@t.com", "Hi", "hello")
        body = mock_email_channel.send_reply.call_args.kwargs["body"]
        assert "MailCode" in body
        assert "第 1 轮" in body
        assert "status" in body

    def test_send_reply_success_no_msg_id(self, handler, mock_email_channel):
        """SMTP 成功但 msg_id 为 None → handle_email 成功, 映射不更新。"""
        mock_email_channel.send_reply.return_value = (True, None)
        with patch.object(cr_module, "call_claude", return_value="reply"):
            result = handler.handle_email(
                from_email="u@t.com", subject="Hi", body="q",
            )
        assert result is True
        # 映射文件不应包含此 thread (our_msg_id 为 None, 跳过了更新)
        mapping = handler._load_mapping()
        assert len(mapping["threads"]) == 0

    def test_error_email_in_reply_to_passed(self, handler, mock_email_channel):
        """错误邮件的 in_reply_to 正确传递。"""
        with patch.object(cr_module, "call_claude", return_value=None):
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
        assert len(kwargs["body"]) > 10

    def test_no_in_reply_to_creates_new(self, handler, mock_email_channel):
        """in_reply_to 为空时, 两封独立邮件对应两个不同的 claude session。"""
        mock_email_channel.send_reply.return_value = (True, "<a@mailcode>")
        with patch.object(cr_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="A", body="a",
            )

        mock_email_channel.send_reply.return_value = (True, "<b@mailcode>")
        # 第二封也不带 in_reply_to → 新 session
        with patch.object(cr_module, "call_claude", return_value="r"):
            handler.handle_email(
                from_email="u@t.com", subject="B", body="b",
            )

        # 通过 mapping 中两个 msg_id 对应的 claude_session_id 判断
        mapping = handler._load_mapping()
        assert len(mapping["threads"]) == 2
        session_ids = {t["claude_session_id"] for t in mapping["threads"].values()}
        assert len(session_ids) == 2
