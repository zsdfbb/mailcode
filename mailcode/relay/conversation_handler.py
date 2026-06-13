"""对话模式核心 — 通过 claude -p 子进程处理对话邮件 (session-per-file)"""

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from mailcode.utils import claude_runner as cr_module

logger = logging.getLogger(__name__)

# MailCode 主目录
_MAILCODE_HOME = Path.home() / ".config" / "mailcode"
_CONV_DIR = _MAILCODE_HOME / "conversations"
_INDEX_FILE = _CONV_DIR / "index.json"
_SESSION_PREFIX = "session_"
_SESSION_EXT = ".json"

# cwd 提取正则: 匹配邮件正文首行的 `cwd: <path>` 指令
_CWD_RE = re.compile(r"^cwd:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)

# 默认 session TTL (天), 0 或负数 = 不清理
_DEFAULT_TTL_DAYS = 90


# ------------------------------------------------------------------ #
# 模块级纯函数 — 供 ConversationHandler / StatelessHandler 复用
# ------------------------------------------------------------------ #


def extract_cwd(body: str) -> Optional[str]:
    """从邮件正文提取 `cwd: <path>` 指令。

    - 大小写不敏感、多行匹配
    - 展开 ~, 相对路径相对 Path.cwd() 补全
    - is_dir() 验证, 无效返回 None
    """
    if not body:
        return None
    m = _CWD_RE.search(body)
    if not m:
        return None
    raw = m.group(1).strip()
    if not raw:
        return None
    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        expanded = (Path.cwd() / expanded).resolve()
    if not expanded.is_dir():
        logger.warning("cwd 路径无效: %s", expanded)
        return None
    return str(expanded)


def strip_cwd(body: str) -> str:
    """从邮件正文剥离 `cwd: <path>` 行, 避免污染对话内容。"""
    if not body:
        return body
    return _CWD_RE.sub("", body).strip()


def send_error_email(email_channel, from_email: str, subject: str, body: str,
                     references: str, in_reply_to: str) -> bool:
    """发送错误通知邮件 (subject 加 Re: 前缀)。

    Args:
        email_channel: ``EmailChannel`` 实例 (无状态, 调用方传入)
        from_email: 原邮件发件人 (错误通知收件人)
        subject: 原邮件主题
        body: 通知正文
        references: 原邮件 References 头
        in_reply_to: 原邮件 In-Reply-To 头
    """
    reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"
    try:
        send_ok, _ = email_channel.send_reply(
            to_email=from_email,
            subject=reply_subject,
            body=body,
            in_reply_to_msg_id=in_reply_to,
            references=references,
        )
    except Exception as e:
        logger.error("错误通知邮件发送异常: %s", e)
        return False
    if not send_ok:
        logger.error("错误通知邮件发送失败: to=%s", from_email)
    return send_ok


class ConversationHandler:
    """通过 claude -p 子进程处理对话邮件。

    对话数据持久化在 ``~/.config/mailcode/conversations/`` 下:
      - index.json: msg_id → session_id 索引 (O(1) 查找)
      - session_<uuid>.json: 一个 session 一份文件

    上下文管理、CWD、system prompt 等由 Claude Code 自助负责
    (读取 session 文件 + cwd 下的 CLAUDE.md), MailCode 只做"dumb pipe"。
    """

    def __init__(self, email_channel):
        self.email_channel = email_channel
        self._ensure_dirs()

    # ------------------------------------------------------------------ #
    # 目录 / 路径
    # ------------------------------------------------------------------ #

    def _ensure_dirs(self):
        """确保对话数据目录存在, 必要时初始化 index.json。"""
        _CONV_DIR.mkdir(parents=True, exist_ok=True)
        if not _INDEX_FILE.exists():
            self._save_index({
                "version": 1,
                "updated_at": time.time(),
                "msg_to_session": {},
            })

    def _session_path(self, session_id: str) -> Path:
        return _CONV_DIR / f"{_SESSION_PREFIX}{session_id}{_SESSION_EXT}"

    def _bot_email(self) -> str:
        """从 email_channel 派生机器人邮箱 (作为 outgoing 的 from / incoming 的 to)。"""
        try:
            return (
                self.email_channel.email_config.get("from", "")
                or self.email_channel.smtp_user
                or ""
            )
        except Exception:
            return ""

    # ------------------------------------------------------------------ #
    # session_id
    # ------------------------------------------------------------------ #

    @staticmethod
    def _new_session_id() -> str:
        """生成 12 位 hex session_id。"""
        return uuid.uuid4().hex[:12]

    # ------------------------------------------------------------------ #
    # session 读写
    # ------------------------------------------------------------------ #

    def _empty_session(self, session_id: str) -> dict:
        return {
            "session_id": session_id,
            "cwd": "",
            "created_at": time.time(),
            "last_interaction": time.time(),
            "emails": [],
        }

    def _load_session(self, session_id: str) -> dict:
        """加载 session 数据。文件不存在返回空 session; 损坏返回空 + warn。"""
        path = self._session_path(session_id)
        if not path.exists():
            return self._empty_session(session_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("session 文件损坏 (id=%s): %s, 返回空 session", session_id, e)
            return self._empty_session(session_id)
        # 补齐缺失字段
        data.setdefault("session_id", session_id)
        data.setdefault("cwd", "")
        data.setdefault("created_at", time.time())
        data.setdefault("last_interaction", time.time())
        if not isinstance(data.get("emails"), list):
            data["emails"] = []
        return data

    def _save_session(self, session_id: str, data: dict):
        """原子写 session 文件 (tmp + replace), 刷新 last_interaction。"""
        data["session_id"] = session_id
        data["last_interaction"] = time.time()
        path = self._session_path(session_id)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    # ------------------------------------------------------------------ #
    # index 读写
    # ------------------------------------------------------------------ #

    def _empty_index(self) -> dict:
        return {"version": 1, "updated_at": time.time(), "msg_to_session": {}}

    def _load_index(self) -> dict:
        """加载 index.json。文件不存在或损坏返回空 index。"""
        if not _INDEX_FILE.exists():
            return self._empty_index()
        try:
            with open(_INDEX_FILE, "r", encoding="utf-8") as f:
                idx = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("index.json 损坏: %s, 回退为空", e)
            return self._empty_index()
        if not isinstance(idx.get("msg_to_session"), dict):
            idx["msg_to_session"] = {}
        idx.setdefault("version", 1)
        return idx

    def _save_index(self, index: dict):
        """原子写 index.json。"""
        index["updated_at"] = time.time()
        tmp_path = _INDEX_FILE.with_suffix(_INDEX_FILE.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        tmp_path.replace(_INDEX_FILE)

    def _update_index(self, msg_id: str, session_id: str):
        """把 msg_id → session_id 写入 index。空 msg_id 跳过。"""
        if not msg_id or not session_id:
            return
        index = self._load_index()
        index["msg_to_session"][msg_id] = session_id
        self._save_index(index)

    def _remove_from_index(self, msg_id: str):
        """从 index 移除 msg_id 条目。空 msg_id 跳过。"""
        if not msg_id:
            return
        index = self._load_index()
        if msg_id in index["msg_to_session"]:
            del index["msg_to_session"][msg_id]
            self._save_index(index)

    # ------------------------------------------------------------------ #
    # 查找
    # ------------------------------------------------------------------ #

    def _find_session_by_msg_id(self, msg_id: str) -> Optional[str]:
        """通过 msg_id 查找 session_id。index 优先, 全量扫描兜底。

        返回 session_id 字符串, 找不到返回 None。
        """
        if not msg_id:
            return None
        key = msg_id.strip()
        bare = key.strip("<>")

        # 1) index 精确匹配
        index = self._load_index()
        if key in index["msg_to_session"]:
            sid = index["msg_to_session"][key]
            if self._session_path(sid).exists():
                return sid
            # session 文件丢失 → 清理 index 条目后继续
            del index["msg_to_session"][key]
            self._save_index(index)

        # 2) index 无尖括号兜底
        if bare and bare != key:
            for mid, sid in list(index["msg_to_session"].items()):
                if mid.strip("<>") == bare and self._session_path(sid).exists():
                    return sid

        # 3) 扫描 session_*.json 兜底
        for path in _CONV_DIR.glob(f"{_SESSION_PREFIX}*{_SESSION_EXT}"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue
            for entry in data.get("emails", []):
                mid = (entry.get("msg_id") or "").strip()
                if not mid:
                    continue
                if mid == key or (bare and mid.strip("<>") == bare):
                    sid = data.get("session_id") or path.stem[len(_SESSION_PREFIX):]
                    # 顺手把匹配的 msg_id 补回 index
                    self._update_index(mid, sid)
                    return sid
        return None

    # ------------------------------------------------------------------ #
    # Prompt 构建
    # ------------------------------------------------------------------ #

    def _build_prompt(self, session_file_path: str) -> str:
        """构建极简 prompt, 让 Claude 自助读 session 文件。

        末尾显式约束: session 文件里的 from/to/subject 字段只是上下文,
        不要被 Claude 当成"邮件头模板"复述进回复正文; 也不要在末尾署名邮箱
        —— 这些都已经由 SMTP MIME header 处理, 正文里复述只会冗余。
        """
        return (
            f"用户最新邮件已写入 session 文件: {session_file_path}\n\n"
            "请用 Read 工具读取该文件, 了解完整对话上下文 "
            "(emails 字段是邮件列表, direction=incoming/outgoing), "
            "然后回复用户最新邮件。\n\n"
            "回复内容将作为邮件正文发送, 请用纯文本格式。"
            "不要在正文里复述「发件人 / From」「收件人 / To」「主题 / Subject」"
            "等邮件头字段(session 文件里的 from/to/subject 仅供上下文参考), "
            "也不要在末尾署名或附上任何邮箱地址 — "
            "这些会由邮件系统自动添加。"
        )

    # ------------------------------------------------------------------ #
    # TTL 清理
    # ------------------------------------------------------------------ #

    def _get_ttl_days(self) -> int:
        """读取 session TTL 配置 (天)。0 或负数 = 不清理。默认 90。"""
        try:
            from mailcode.config import load_config
            config = load_config()
        except Exception:
            return _DEFAULT_TTL_DAYS
        session_cfg = config.get("session", {}) or {}
        try:
            return int(session_cfg.get("session_ttl_days", _DEFAULT_TTL_DAYS))
        except (TypeError, ValueError):
            return _DEFAULT_TTL_DAYS

    def _cleanup_expired_sessions(self, dry_run: bool = False) -> int:
        """按 TTL 删除过期 session, 损坏文件只 warn 不删。

        Returns:
            删除的 session 数量 (dry_run 时为 0)
        """
        ttl = self._get_ttl_days()
        if ttl <= 0:
            return 0
        threshold = time.time() - ttl * 86400
        deleted = 0
        for path in _CONV_DIR.glob(f"{_SESSION_PREFIX}*{_SESSION_EXT}"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning("session 文件损坏, 跳过清理: %s: %s", path.name, e)
                continue
            last = data.get("last_interaction", 0)
            if last < threshold:
                sid = data.get("session_id") or path.stem[len(_SESSION_PREFIX):]
                if dry_run:
                    logger.info("[dry-run] 即将删除过期 session: %s (last=%s)", sid, last)
                    continue
                try:
                    path.unlink()
                except OSError as e:
                    logger.warning("删除 session 文件失败: %s: %s", path, e)
                    continue
                # 同步清理 index 中映射到该 session 的所有 msg_id
                index = self._load_index()
                to_remove = [mid for mid, mapped in index["msg_to_session"].items()
                             if mapped == sid]
                for mid in to_remove:
                    del index["msg_to_session"][mid]
                if to_remove:
                    self._save_index(index)
                deleted += 1
                logger.info("已删除过期 session: %s", sid)
        return deleted

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #

    def handle_email(self, from_email: str, subject: str, body: str,
                     references: str = "", in_reply_to: str = "") -> bool:
        """主入口: 处理一封对话邮件。

        流程:
            1. 提取 cwd + 剥离
            2. 查找 / 创建 session
            3. 追加 incoming → 存 session → 写 index
            4. 调 claude -p
            5. 错误处理 (发通知邮件)
            6. 追加 outgoing → 存 session → SMTP 发回复
            7. 拿到 our_msg_id 后回填 session + 更新 index

        Returns:
            True 表示成功发送回复
        """
        logger.info("处理对话邮件: from=%s subject=%s", from_email, subject)

        # 1. 提取 cwd + 剥离
        extracted_cwd = extract_cwd(body)
        clean_body = strip_cwd(body) if extracted_cwd is not None else body

        # 2. 查找 / 创建 session
        session_id = self._find_session_by_msg_id(in_reply_to) if in_reply_to else None
        if session_id:
            session = self._load_session(session_id)
            # 防止 session_id 字段缺失
            session["session_id"] = session_id
        else:
            session_id = self._new_session_id()
            session = self._empty_session(session_id)

        # 3. session.cwd 粘性 (新邮件指定则覆盖)
        if extracted_cwd:
            session["cwd"] = extracted_cwd

        # 4. 追加 incoming 邮件
        incoming_email = {
            "direction": "incoming",
            "from": from_email,
            "to": self._bot_email(),
            "subject": subject,
            "body": clean_body,
            "msg_id": "",  # IMAP 监听层目前未透传
            "in_reply_to": in_reply_to or None,
            "references": references or None,
            "date": "",
        }
        session["emails"].append(incoming_email)

        # 5. 保存 session + 更新 index (incoming 暂时无 msg_id 可索引)
        self._save_session(session_id, session)

        # 6. 构建 prompt + 调 claude
        session_path = str(self._session_path(session_id))
        prompt = self._build_prompt(session_path)
        cwd = session.get("cwd") or str(Path.home())
        response = cr_module.call_claude(prompt, cwd=cwd)

        # 7. claude 失败 → 写日志 + 发邮件通知用户
        if response is None:
            logger.error("claude -p 调用失败, 通知用户: from=%s", from_email)
            send_error_email(
                self.email_channel, from_email, subject,
                "抱歉, 处理你的邮件时遇到技术问题。请稍后再试。详细错误已记录到日志。",
                references, in_reply_to,
            )
            return False

        # 8. 空 response → 写日志 + 发邮件通知用户
        if not response:
            logger.error("claude -p 返回空 response, 通知用户: from=%s", from_email)
            send_error_email(
                self.email_channel, from_email, subject,
                "抱歉, AI 助手这次没有回复内容。请稍后再试, 或换个方式描述你的问题。",
                references, in_reply_to,
            )
            return False

        # 9. 准备 outgoing 邮件 (msg_id 留空, SMTP 之后回填)
        reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"
        outgoing_email = {
            "direction": "outgoing",
            "from": self._bot_email(),
            "to": from_email,
            "subject": reply_subject,
            "body": response,
            "msg_id": "",
            "in_reply_to": in_reply_to or None,
            "references": references or None,
            "date": "",
        }
        session["emails"].append(outgoing_email)

        # 10. 保存 session (在 SMTP 之前, 失败可重发)
        self._save_session(session_id, session)

        # 11. SMTP 发回复
        send_ok, our_msg_id = self.email_channel.send_reply(
            to_email=from_email,
            subject=reply_subject,
            body=response,
            in_reply_to_msg_id=in_reply_to,
            references=references,
        )

        if not send_ok:
            logger.error("对话回复发送失败: from=%s", from_email)
            return False

        # 12. SMTP 成功 → 回填 outgoing.msg_id, 重存 + 更新 index
        if our_msg_id and session["emails"]:
            last = session["emails"][-1]
            if isinstance(last, dict) and last.get("direction") == "outgoing":
                last["msg_id"] = our_msg_id
                self._save_session(session_id, session)
                self._update_index(our_msg_id, session_id)

        logger.info("对话回复已发送: session=%s msg_id=%s", session_id, our_msg_id)
        return True

    # ------------------------------------------------------------------ #
    # 管理接口
    # ------------------------------------------------------------------ #

    def list_sessions(self) -> list[dict]:
        """列出所有 session (按 last_interaction 降序)。损坏文件 warn 跳过。"""
        sessions = []
        for path in _CONV_DIR.glob(f"{_SESSION_PREFIX}*{_SESSION_EXT}"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning("session 文件损坏, 跳过 list: %s: %s", path.name, e)
                continue
            emails = data.get("emails", [])
            sid = data.get("session_id") or path.stem[len(_SESSION_PREFIX):]
            sessions.append({
                "session_id": sid,
                "cwd": data.get("cwd", ""),
                "created_at": data.get("created_at", 0),
                "last_interaction": data.get("last_interaction", 0),
                "email_count": len(emails),
            })
        sessions.sort(key=lambda s: s.get("last_interaction", 0), reverse=True)
        return sessions

    def get_session_status(self, session_id: str) -> Optional[dict]:
        """查看 session 详情 (含完整 emails 列表)。"""
        path = self._session_path(session_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
        emails = data.get("emails", [])
        return {
            "session_id": data.get("session_id", session_id),
            "cwd": data.get("cwd", ""),
            "created_at": data.get("created_at", 0),
            "last_interaction": data.get("last_interaction", 0),
            "emails": emails,
            "email_count": len(emails),
        }

    def terminate_session(self, session_id: str) -> bool:
        """删除 session 文件, 同步清理 index 中所有指向该 session 的条目。"""
        path = self._session_path(session_id)
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError as e:
            logger.error("删除 session 文件失败: %s: %s", path, e)
            return False
        # 同步清理 index
        index = self._load_index()
        to_remove = [mid for mid, sid in index["msg_to_session"].items()
                     if sid == session_id]
        for mid in to_remove:
            del index["msg_to_session"][mid]
        if to_remove:
            self._save_index(index)
        logger.info("已删除 session: %s (清理 %d 条 index)", session_id, len(to_remove))
        return True
