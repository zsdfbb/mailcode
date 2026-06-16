"""对话模式核心 — 通过 claude --session-id/--resume 处理对话邮件 (claude 原生 session)

与 ConversationHandler (session-per-file) 的差异:
  - 不自维护 session_*.json 文件, 由 Claude 自身管理 session 状态
  - 使用 claude --session-id <uuid> / --resume 参数续接
  - 只保存:
      1) claude_sessions.json — email_msg_id → claude_session_id 映射
      2) transcripts/<uuid>.json — 对话归档 (审计/调试用途)
"""

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

from mailcode.relay.conversation_handler import extract_cwd, strip_cwd, send_error_email
from mailcode.utils import claude_runner as cr_module

logger = logging.getLogger(__name__)

# MailCode 主目录
_MAILCODE_HOME = Path.home() / ".config" / "mailcode"
_TRANSCRIPTS_DIR = _MAILCODE_HOME / "transcripts"
_MAPPING_FILE = _MAILCODE_HOME / "claude_sessions.json"


class ResumeConversationHandler:
    """通过 claude --session-id/--resume 处理对话邮件。

    使用 Claude 原生的 session 管理机制, MailCode 只负责:
    - 维护 email_msg_id → claude_session_id 映射
    - 记录 transcripts 用于审计和调试
    """

    def __init__(self, email_channel):
        self.email_channel = email_channel
        self._ensure_dirs()

    # ------------------------------------------------------------------ #
    # 目录 / 路径
    # ------------------------------------------------------------------ #

    def _ensure_dirs(self):
        """确保数据目录存在。"""
        _MAILCODE_HOME.mkdir(parents=True, exist_ok=True)
        _TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Mapping IO — claude_sessions.json
    # ------------------------------------------------------------------ #

    @staticmethod
    def _empty_mapping() -> dict:
        return {"version": 1, "threads": {}}

    def _load_mapping(self) -> dict:
        """加载 claude_sessions.json。文件不存在或损坏返回空文档。"""
        if not _MAPPING_FILE.exists():
            return self._empty_mapping()
        try:
            with open(_MAPPING_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("claude_sessions.json 损坏: %s, 回退为空", e)
            return self._empty_mapping()
        if not isinstance(data.get("threads"), dict):
            data["threads"] = {}
        data.setdefault("version", 1)
        return data

    def _save_mapping(self, data: dict):
        """原子写 claude_sessions.json (tmp + replace)。"""
        data["version"] = 1
        tmp_path = _MAPPING_FILE.with_suffix(_MAPPING_FILE.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(_MAPPING_FILE)

    # ------------------------------------------------------------------ #
    # Transcript IO — transcripts/<uuid>.json
    # ------------------------------------------------------------------ #

    def _transcript_path(self, session_id: str) -> Path:
        return _TRANSCRIPTS_DIR / f"{session_id}.json"

    @staticmethod
    def _empty_transcript(session_id: str, user_email: str) -> dict:
        return {
            "claude_session_id": session_id,
            "user_email": user_email,
            "created_at": time.time(),
            "entries": [],
        }

    def _load_transcript(self, session_id: str) -> Optional[dict]:
        """加载 transcript 文件。不存在或损坏返回 None。"""
        path = self._transcript_path(session_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("transcript 文件损坏 (id=%s): %s", session_id, e)
            return None

    def _save_transcript(self, session_id: str, data: dict):
        """原子写 transcript 文件 (tmp + replace)。"""
        path = self._transcript_path(session_id)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    def _append_to_transcript(self, session_id: str, entry: dict):
        """向 transcript 追加一条 entry (incoming/outgoing)。

        entry 必须是 dict, 否则静默忽略并 log error。
        transcript 文件必须已存在 (由 handle_email 保证)。
        """
        if not isinstance(entry, dict):
            logger.error("transcript entry 必须是 dict, 跳过: %s", type(entry))
            return
        path = self._transcript_path(session_id)
        if not path.exists():
            logger.error("transcript 不存在, 无法追加: %s", session_id)
            return
        transcript = self._load_transcript(session_id)
        if transcript is None:
            logger.error("transcript 损坏, 无法追加: %s", session_id)
            return
        transcript.setdefault("entries", [])
        transcript["entries"].append(entry)
        self._save_transcript(session_id, transcript)

    # ------------------------------------------------------------------ #
    # Footer 构建
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_footer(claude_session_id: str, round_num: int) -> str:
        """构建回复邮件末尾的会话脚注。"""
        short_id = claude_session_id[:12]  # 取前 12 位方便阅读
        return (
            f"\n\n──────────────────────────────────────────\n"
            f"📬 MailCode · 对话 {short_id}（第 {round_num} 轮）\n"
            f"回复此邮件继续 · 发「status」查系统状态"
        )

    # ------------------------------------------------------------------ #
    # Thread 查找
    # ------------------------------------------------------------------ #

    def _get_thread_key(self, in_reply_to: str) -> Optional[str]:
        """根据 in_reply_to msg_id 查找映射中的 thread key。

        先尝试精确匹配, 再尝试去掉尖括号后匹配 (双向兼容)。
        返回 mapping["threads"] 中的 key, 找不到返回 None。
        """
        if not in_reply_to:
            return None
        mapping = self._load_mapping()
        threads = mapping.get("threads", {})

        key = in_reply_to.strip()

        # 1) 精确匹配
        if key in threads:
            return key

        bare = key.strip("<>")
        if not bare:
            return None

        # 2) 去掉尖括号后兜底匹配 (in_reply_to 可能带/不带 <>)
        for tid in threads:
            if tid.strip("<>") == bare:
                return tid

        return None

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #

    def handle_email(self, from_email: str, subject: str, body: str,
                     references: str = "", in_reply_to: str = "") -> bool:
        """主入口: 处理一封对话邮件 (claude 原生 session 版本)。

        流程:
            1. 提取 cwd + 剥离
            2. 查找 / 创建 claude session
            3. 调 claude --session-id / --resume
            4. 错误处理 (发通知邮件)
            5. SMTP 发送回复
            6. 追加 incoming + outgoing 到 transcript
            7. 更新映射文件 (our_msg_id → claude_session_id)

        Returns:
            True 表示成功发送回复
        """
        logger.info("处理对话邮件 (resume): from=%s subject=%s", from_email, subject)

        # ------ 1. 提取 cwd + 剥离 ------ #
        extracted_cwd = extract_cwd(body)
        clean_body = strip_cwd(body) if extracted_cwd is not None else body

        # ------ 2. 查找 / 创建 claude session ------ #
        thread_key = self._get_thread_key(in_reply_to) if in_reply_to else None

        if thread_key:
            mapping = self._load_mapping()
            thread_info = mapping["threads"][thread_key]
            claude_session_id = thread_info["claude_session_id"]
            cwd = thread_info.get("cwd", "") or str(Path.home())
            resume = True
            if extracted_cwd:
                cwd = extracted_cwd
            email_count = thread_info.get("email_count", 0)
            created_at = thread_info.get("created_at", time.time())
            user_email = thread_info.get("user_email", from_email)
        else:
            claude_session_id = str(uuid.uuid4())
            cwd = extracted_cwd or str(Path.home())
            resume = False
            email_count = 0
            created_at = time.time()
            user_email = from_email
            # 新会话: 初始化 transcript (空 entries)
            transcript = self._empty_transcript(claude_session_id, user_email)
            self._save_transcript(claude_session_id, transcript)

        # ------ 3. 调 claude ------ #
        response = cr_module.call_claude(
            clean_body,
            cwd=cwd,
            session_id=claude_session_id,
            resume=resume,
        )

        # ------ 4a. claude 失败 ------ #
        if response is None:
            logger.error("claude 调用失败, 通知用户: from=%s", from_email)

            # 检查 claude 是否可用
            import shutil
            if shutil.which("claude") is None:
                hint = "Claude Code 未安装或不在 PATH 中。请先安装 Claude Code，或检查 PATH 配置。"
            else:
                hint = "AI 处理失败，可能由于超时或进程异常。请简化问题后重试，或稍后再试。"

            send_error_email(
                self.email_channel, from_email, subject,
                hint,
                references, in_reply_to,
            )
            return False

        # ------ 4b. 空 response ------ #
        if not response:
            logger.error("claude 返回空 response, 通知用户: from=%s", from_email)
            send_error_email(
                self.email_channel, from_email, subject,
                "AI 助手没有返回任何内容。请换个方式描述你的问题，或将大问题拆成小步骤。",
                references, in_reply_to,
            )
            return False

        # ------ 5. SMTP 发送回复 ------ #
        reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"
        round_num = (email_count // 2) + 1
        body_with_footer = response + self._build_footer(claude_session_id, round_num)
        send_ok, our_msg_id = self.email_channel.send_reply(
            to_email=from_email,
            subject=reply_subject,
            body=body_with_footer,
            in_reply_to_msg_id=in_reply_to,
            references=references,
        )

        if not send_ok:
            logger.error("对话回复发送失败: from=%s", from_email)
            return False

        # ------ 6. 追加 incoming + outgoing 到 transcript ------ #
        incoming_entry = {
            "direction": "incoming",
            "from": from_email,
            "subject": subject,
            "body": clean_body,
            "date": time.time(),
            "email_msg_id": "",
        }
        outgoing_entry = {
            "direction": "outgoing",
            "to": from_email,
            "subject": reply_subject,
            "body": body_with_footer,
            "date": time.time(),
            "email_msg_id": our_msg_id or "",
        }
        self._append_to_transcript(claude_session_id, incoming_entry)
        self._append_to_transcript(claude_session_id, outgoing_entry)

        # ------ 7. 更新映射文件 ------ #
        mapping = self._load_mapping()
        thread_entry = {
            "claude_session_id": claude_session_id,
            "user_email": user_email,
            "subject": subject,
            "cwd": cwd,
            "email_count": email_count + 2,  # +1 incoming, +1 outgoing
            "created_at": created_at,
            "last_interaction": time.time(),
        }
        if our_msg_id:
            mapping.setdefault("threads", {})[our_msg_id] = thread_entry
        self._save_mapping(mapping)

        logger.info("对话回复已发送 (resume): session=%s msg_id=%s",
                     claude_session_id, our_msg_id)
        return True
