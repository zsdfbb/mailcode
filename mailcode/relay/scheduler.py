"""MailCode 定时任务调度器 — 周期性调 Claude + 发邮件。

设计意图
========

MailCode 本身是 "dumb pipe" (见 ``docs/design-final/design.md`` §34-56):
只在收到邮件时拉起 AI Agent, 没有主动触发能力。本模块提供 out-of-band
触发, 让用户能配置"每天 9 点查 TODO.md 发邮件给我"等周期性任务。

**为什么把 ``call_claude`` 抽出来**
- Scheduler 调 Claude 的方式 (单轮、非交互、不写 session) 和
  ConversationHandler 几乎相同, 但略有差异 (cwd 显式传、不写 session
  文件、不查 IMAP)。
- 抽到 ``mailcode.utils.claude_runner.call_claude`` 后两边共享同一份
  超时/参数/日志实现, 避免行为漂移。

**错过策略 (skip 不补跑)**
- Scheduler tick 默认 30s, 错过窗口 (例如服务挂了 10 分钟) 不补跑。
- 到期判定只看 ``now >= next_run_at``; 触发后立刻按当前时间算下一次。
- 补跑请用 ``mailcode schedule run-now <name>`` 显式触发, 用户有显式
  控制权, 避免邮件风暴。

**线程模型**
- 单进程多线程, ``ScheduleStore`` 用模块级 ``threading.Lock`` 保护
  JSON 读写, 锁粒度够细 (仅 JSON 文件操作)。
- Scheduler 自己是一个 ``threading.Thread``, 主循环用 ``_stopped``
  ``Event.wait(timeout=tick_seconds)`` 优雅退出 (沿用
  ``email_listener.py`` 同样的模式)。
"""

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from mailcode.utils.claude_runner import call_claude

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# 路径常量 — 模块级便于测试 patch
# ------------------------------------------------------------------ #

_MAILCODE_HOME = Path.home() / ".config" / "mailcode"
_SCHEDULES_PATH = _MAILCODE_HOME / "schedules.json"

# 全局锁保护所有 _SCHEDULES_PATH 读写
# 用 RLock 而非 Lock, 因为 add/update/delete/set_enabled/mark_run 持锁状态下
# 会再调 self.save() 写文件, 非可重入锁会自死锁
_STORE_LOCK = threading.RLock()


# ------------------------------------------------------------------ #
# 调度类型常量
# ------------------------------------------------------------------ #

SCHEDULE_INTERVAL = "interval"
SCHEDULE_DAILY = "daily"
SCHEDULE_WEEKLY = "weekly"
SCHEDULE_MONTHLY = "monthly"

VALID_SCHEDULE_TYPES = {
    SCHEDULE_INTERVAL,
    SCHEDULE_DAILY,
    SCHEDULE_WEEKLY,
    SCHEDULE_MONTHLY,
}

# 状态常量
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_DRY_RUN = "dry_run"

VALID_STATUSES = {STATUS_SUCCESS, STATUS_FAILED, STATUS_DRY_RUN}


# ------------------------------------------------------------------ #
# 数据类
# ------------------------------------------------------------------ #


@dataclass
class ScheduleSpec:
    """单个任务的调度规则。

    Attributes:
        type: 调度类型 (interval / daily / weekly / monthly)
        interval_seconds: interval 类型的周期秒数
        time: daily/weekly/monthly 类型的触发时间 "HH:MM"
        day_of_week: weekly 类型的星期几 (0=今天/周一, 6=周日)
            注释: 任务描述说 "0=今天", 这里我们用 0=今天偏移量, 见
            ``compute_next_run``。
        day_of_month: monthly 类型的日期 (1-31)
    """

    type: str
    interval_seconds: Optional[int] = None
    time: Optional[str] = None
    day_of_week: Optional[int] = None
    day_of_month: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ScheduleSpec":
        return cls(
            type=d.get("type", ""),
            interval_seconds=d.get("interval_seconds"),
            time=d.get("time"),
            day_of_week=d.get("day_of_week"),
            day_of_month=d.get("day_of_month"),
        )


@dataclass
class Task:
    """一个调度任务的完整定义。

    Attributes:
        id: 全局唯一 ID (创建时生成, sched_<8hex>)
        name: 人类可读名称, 必须唯一 (不区分大小写 trim)
        enabled: 是否启用 (False 时 Scheduler 跳过)
        schedule: 调度规则
        prompt: 调 Claude 时使用的 prompt
        cwd: 调 Claude 时的工作目录
        to_email: 结果邮件的收件人
        subject_prefix: 结果邮件 subject 前缀 (默认 "Schedule:")
        last_run_at: 上次触发时间 (ISO8601 字符串)
        last_status: 上次执行状态 (success / failed / dry_run)
        last_error: 上次失败的错误信息
        next_run_at: 下次计划触发时间 (ISO8601 字符串)
        created_at: 创建时间 (ISO8601 字符串)
        updated_at: 更新时间 (ISO8601 字符串)
    """

    id: str
    name: str
    enabled: bool
    schedule: ScheduleSpec
    prompt: str
    cwd: str
    to_email: str
    subject_prefix: str = "Schedule:"
    last_run_at: Optional[str] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    next_run_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # schedule 嵌套对象展开为 dict
        d["schedule"] = self.schedule.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        schedule_data = d.get("schedule") or {}
        schedule = ScheduleSpec.from_dict(schedule_data)
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            enabled=bool(d.get("enabled", True)),
            schedule=schedule,
            prompt=d.get("prompt", ""),
            cwd=d.get("cwd", "") or "",
            to_email=d.get("to_email", "") or "",
            subject_prefix=d.get("subject_prefix", "Schedule:") or "Schedule:",
            last_run_at=d.get("last_run_at"),
            last_status=d.get("last_status"),
            last_error=d.get("last_error"),
            next_run_at=d.get("next_run_at"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )


# ------------------------------------------------------------------ #
# 纯函数 — 解析 + 计算下次触发时间
# ------------------------------------------------------------------ #


def parse_schedule(spec: dict) -> ScheduleSpec:
    """从 dict 解析 + 校验 ScheduleSpec。失败抛 ``ValueError``。

    校验规则:
      - type 必须 ∈ {interval, daily, weekly, monthly}
      - interval: interval_seconds 为正整数
      - daily: time 为 "HH:MM" (00-23:00-59)
      - weekly: time 合法 + day_of_week ∈ [0, 6]
      - monthly: time 合法 + day_of_month ∈ [1, 31]
    """
    if not isinstance(spec, dict):
        raise ValueError(f"schedule 必须是 dict, 得到 {type(spec).__name__}")

    t = spec.get("type")
    if t not in VALID_SCHEDULE_TYPES:
        raise ValueError(
            f"schedule.type 必须是 {sorted(VALID_SCHEDULE_TYPES)} 之一, 得到 {t!r}"
        )

    if t == SCHEDULE_INTERVAL:
        iv = spec.get("interval_seconds")
        if not isinstance(iv, int) or iv <= 0:
            raise ValueError(f"interval 类型需要正整数 interval_seconds, 得到 {iv!r}")
        return ScheduleSpec(type=t, interval_seconds=iv)

    # daily / weekly / monthly 都需要 time
    time_str = spec.get("time")
    hh, mm = _parse_hhmm(time_str)

    if t == SCHEDULE_DAILY:
        return ScheduleSpec(type=t, time=f"{hh:02d}:{mm:02d}")

    if t == SCHEDULE_WEEKLY:
        dow = spec.get("day_of_week")
        if not isinstance(dow, int) or not 0 <= dow <= 6:
            raise ValueError(f"weekly 需要 day_of_week ∈ [0,6], 得到 {dow!r}")
        return ScheduleSpec(type=t, time=f"{hh:02d}:{mm:02d}", day_of_week=dow)

    if t == SCHEDULE_MONTHLY:
        dom = spec.get("day_of_month")
        if not isinstance(dom, int) or not 1 <= dom <= 31:
            raise ValueError(f"monthly 需要 day_of_month ∈ [1,31], 得到 {dom!r}")
        return ScheduleSpec(type=t, time=f"{hh:02d}:{mm:02d}", day_of_month=dom)

    # 不可达
    raise ValueError(f"未知的 schedule type: {t!r}")


def _parse_hhmm(s) -> tuple[int, int]:
    """解析 "HH:MM" 字符串, 返回 (hh, mm)。失败抛 ``ValueError``。"""
    if not isinstance(s, str) or ":" not in s:
        raise ValueError(f"time 必须是 'HH:MM' 字符串, 得到 {s!r}")
    parts = s.split(":")
    if len(parts) != 2:
        raise ValueError(f"time 必须是 'HH:MM', 得到 {s!r}")
    try:
        hh = int(parts[0])
        mm = int(parts[1])
    except ValueError:
        raise ValueError(f"time 必须是 'HH:MM', 得到 {s!r}")
    if not 0 <= hh <= 23 or not 0 <= mm <= 59:
        raise ValueError(f"time 越界, 得到 {s!r}")
    return hh, mm


def compute_next_run(spec: ScheduleSpec, after: datetime) -> datetime:
    """按 ``spec`` 计算 ``after`` 之后的下一个触发时间。

    - interval: ``after + interval_seconds`` (秒级精度的整数加法)
    - daily: 找 after 之后第一个 time=HH:MM (跨天)
    - weekly: 同 daily, 但按 day_of_week 跳转 (0=今天偏移, 1=明天, ...)
    - monthly: 试 (year, month, day_of_month), 该月不存在此日则跳过到下月,
      最多找 12 个月 (跨年自动 wrap)

    Returns:
        下次触发的本地时间 ``datetime``

    Raises:
        ValueError: spec 字段不合法 (实际不会发生, 因为 parse_schedule 已校验)
    """
    if spec.type == SCHEDULE_INTERVAL:
        if not spec.interval_seconds or spec.interval_seconds <= 0:
            raise ValueError("interval 类型需要正整数 interval_seconds")
        return after + timedelta(seconds=spec.interval_seconds)

    if not spec.time:
        raise ValueError(f"{spec.type} 类型需要 time 字段")
    hh, mm = _parse_hhmm(spec.time)

    if spec.type == SCHEDULE_DAILY:
        candidate = after.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= after:
            # 今天的 HH:MM 已经过了, 推到明天
            candidate = candidate + timedelta(days=1)
        return candidate

    if spec.type == SCHEDULE_WEEKLY:
        # day_of_week 解释为相对今天的偏移 (0=今天, 1=明天, ..., 6=6 天后)
        # — 任务描述明确说 "0=今天"
        offset = spec.day_of_week
        if offset is None or not 0 <= offset <= 6:
            raise ValueError(f"weekly 需要 day_of_week ∈ [0,6], 得到 {spec.day_of_week!r}")
        candidate = after.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if offset == 0:
            # 今天 — 如果 HH:MM 已过则推到下个周期 (即 +7 天)
            if candidate <= after:
                candidate = candidate + timedelta(days=7)
            return candidate
        # offset > 0: 找 after 之后第一个 (today + offset) 天的 HH:MM
        target_date = (after + timedelta(days=offset)).date()
        candidate = datetime.combine(target_date, datetime.min.time()).replace(
            hour=hh, minute=mm
        )
        if candidate <= after:
            # 已过 → 推到下个周期 (+7 天)
            candidate = candidate + timedelta(days=7)
        return candidate

    if spec.type == SCHEDULE_MONTHLY:
        dom = spec.day_of_month
        if dom is None or not 1 <= dom <= 31:
            raise ValueError(f"monthly 需要 day_of_month ∈ [1,31], 得到 {dom!r}")
        # 最多找 12 个月, 跳过该月不存在此日的情况
        year, month = after.year, after.month
        for _ in range(12):
            try:
                candidate = datetime(year, month, dom, hh, mm)
            except ValueError:
                # 该月没有这个日 (2/30, 4/31 等) → 跳到下个月
                month += 1
                if month > 12:
                    month = 1
                    year += 1
                continue
            if candidate > after:
                return candidate
            # 本月日期已过, 跳到下个月
            month += 1
            if month > 12:
                month = 1
                year += 1
        raise ValueError(
            f"monthly 调度在 12 个月内找不到下次触发时间 (day_of_month={dom})"
        )

    raise ValueError(f"未知的 schedule type: {spec.type!r}")


# ------------------------------------------------------------------ #
# 辅助函数
# ------------------------------------------------------------------ #


def _new_task_id() -> str:
    """生成 ``sched_<8hex>`` 形式的 task id。"""
    return f"sched_{uuid.uuid4().hex[:8]}"


def _now_local() -> datetime:
    """当前本地时间 (带 UTC offset)。

    使用 ``astimezone()`` 确保返回 offset-aware datetime,
    与 ``_parse_dt``／``_format_dt`` 的 aware 行为一致,
    避免 ``_tick`` 中比较 ``now >= next_dt`` 时出现
    "can't compare offset-naive and offset-aware datetimes"。
    """
    return datetime.now().astimezone()


def _format_dt(dt: Optional[datetime]) -> Optional[str]:
    """格式化为 ISO8601 带本地 UTC offset。None 透传。"""
    if dt is None:
        return None
    return dt.astimezone().isoformat()


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    """解析 ISO8601 字符串为 ``datetime``。None/空 透传 None。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError) as e:
        logger.warning("解析 ISO8601 失败: %s (%s)", s, e)
        return None


# ------------------------------------------------------------------ #
# ScheduleStore — schedules.json 持久化
# ------------------------------------------------------------------ #


class ScheduleStore:
    """``schedules.json`` 持久化层。

    文件格式::

        {
            "version": 1,
            "updated_at": <unix_ts>,
            "tasks": [Task.to_dict(), ...]
        }

    线程安全: 全部方法走模块级 ``_STORE_LOCK``。
    """

    def __init__(self, path: Optional[Path] = None):
        # 允许测试时注入自定义路径
        self._path: Path = path if path is not None else _SCHEDULES_PATH

    # ---- I/O ----

    def load(self) -> dict:
        """加载 schedules.json。文件不存在或损坏返回空 doc。"""
        with _STORE_LOCK:
            return self._load_locked()

    def _load_locked(self) -> dict:
        if not self._path.exists():
            return self._empty_doc()
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("schedules.json 损坏: %s, 回退为空", e)
            return self._empty_doc()
        if not isinstance(data, dict):
            logger.warning("schedules.json 顶层不是 dict, 回退为空")
            return self._empty_doc()
        data.setdefault("version", 1)
        if not isinstance(data.get("tasks"), list):
            data["tasks"] = []
        return data

    def save(self, doc: dict) -> None:
        """原子写 schedules.json (tmp + os.replace)。"""
        with _STORE_LOCK:
            doc = dict(doc)
            doc["updated_at"] = time.time()
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)

    @staticmethod
    def _empty_doc() -> dict:
        return {"version": 1, "updated_at": time.time(), "tasks": []}

    # ---- CRUD ----

    def list_tasks(self) -> list[Task]:
        doc = self.load()
        out: list[Task] = []
        for raw in doc.get("tasks", []):
            if isinstance(raw, dict):
                try:
                    out.append(Task.from_dict(raw))
                except Exception as e:
                    logger.warning("跳过非法 task: %s (%s)", raw, e)
        return out

    def get(self, name_or_id: str) -> Optional[Task]:
        if not name_or_id:
            return None
        key = name_or_id.strip().lower()
        for t in self.list_tasks():
            if t.id == name_or_id or t.name.strip().lower() == key:
                return t
        return None

    def add(self, task: Task) -> None:
        """添加任务。name 必须唯一 (不区分大小写 trim)。"""
        with _STORE_LOCK:
            doc = self._load_locked()
            new_name = task.name.strip().lower()
            if not new_name:
                raise ValueError("task.name 不能为空")
            for existing in doc.get("tasks", []):
                if not isinstance(existing, dict):
                    continue
                existing_name = (existing.get("name") or "").strip().lower()
                if existing_name == new_name:
                    raise ValueError(f"task.name 已存在: {task.name!r}")
            # 补齐 created_at / updated_at
            now_iso = _format_dt(_now_local())
            task.created_at = task.created_at or now_iso
            task.updated_at = now_iso
            doc.setdefault("tasks", []).append(task.to_dict())
            self.save(doc)

    def update(self, task: Task) -> None:
        """按 id 更新已存在的任务。"""
        with _STORE_LOCK:
            doc = self._load_locked()
            tasks = doc.get("tasks", [])
            for i, raw in enumerate(tasks):
                if isinstance(raw, dict) and raw.get("id") == task.id:
                    task.updated_at = _format_dt(_now_local())
                    tasks[i] = task.to_dict()
                    self.save(doc)
                    return
            raise ValueError(f"找不到 id={task.id!r} 的任务")

    def delete(self, name_or_id: str) -> bool:
        """按 name 或 id 删除。返回是否实际删除。"""
        with _STORE_LOCK:
            doc = self._load_locked()
            tasks = doc.get("tasks", [])
            key = (name_or_id or "").strip().lower()
            for i, raw in enumerate(tasks):
                if not isinstance(raw, dict):
                    continue
                if raw.get("id") == name_or_id or (raw.get("name") or "").strip().lower() == key:
                    del tasks[i]
                    self.save(doc)
                    return True
            return False

    def set_enabled(self, name_or_id: str, enabled: bool) -> Optional[Task]:
        """切换 enabled 标志。返回更新后的 task, 找不到返回 None。"""
        with _STORE_LOCK:
            doc = self._load_locked()
            tasks = doc.get("tasks", [])
            key = (name_or_id or "").strip().lower()
            for i, raw in enumerate(tasks):
                if not isinstance(raw, dict):
                    continue
                if raw.get("id") == name_or_id or (raw.get("name") or "").strip().lower() == key:
                    raw["enabled"] = bool(enabled)
                    raw["updated_at"] = _format_dt(_now_local())
                    tasks[i] = raw
                    self.save(doc)
                    return Task.from_dict(raw)
            return None

    def mark_run(
        self,
        task_id: str,
        status: str,
        error: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Optional[Task]:
        """更新 last_run_at / last_status / last_error / next_run_at / updated_at。

        Args:
            task_id: 任务 id
            status: STATUS_SUCCESS / STATUS_FAILED / STATUS_DRY_RUN
            error: 错误信息 (status=failed 时填)
            now: 用于计算 next_run_at 的"现在"时间 (测试可注入)

        Returns:
            更新后的 task, 找不到返回 None
        """
        if status not in VALID_STATUSES:
            raise ValueError(f"status 必须是 {sorted(VALID_STATUSES)} 之一, 得到 {status!r}")

        with _STORE_LOCK:
            doc = self._load_locked()
            tasks = doc.get("tasks", [])
            for i, raw in enumerate(tasks):
                if not isinstance(raw, dict) or raw.get("id") != task_id:
                    continue
                task = Task.from_dict(raw)
                now_dt = now or _now_local()
                task.last_run_at = _format_dt(now_dt)
                task.last_status = status
                task.last_error = error
                # 计算 next_run_at: 严格从 now 开始
                try:
                    next_dt = compute_next_run(task.schedule, now_dt)
                except Exception as e:
                    logger.warning(
                        "计算 next_run_at 失败 (id=%s): %s, 留空", task_id, e
                    )
                    next_dt = None
                task.next_run_at = _format_dt(next_dt) if next_dt else None
                task.updated_at = _format_dt(now_dt)
                tasks[i] = task.to_dict()
                self.save(doc)
                return task
            return None


# ------------------------------------------------------------------ #
# Scheduler — 线程 + tick 循环
# ------------------------------------------------------------------ #


class Scheduler(threading.Thread):
    """调度线程, 按 ``tick_seconds`` 间隔扫描 schedules.json。

    使用方式::

        store = ScheduleStore()
        scheduler = Scheduler(email_channel, store, dry_run=False, tick_seconds=30)
        scheduler.start()
        ...
        scheduler.stop()
        scheduler.join(timeout=5)

    Args:
        email_channel: ``EmailChannel`` 实例 (用于发结果邮件)
        store: ``ScheduleStore`` 实例
        dry_run: True 时不实际调 Claude / 不发邮件, 只更新 last_status=dry_run
        tick_seconds: tick 间隔秒数 (默认 30)
    """

    def __init__(
        self,
        email_channel,
        store: ScheduleStore,
        *,
        dry_run: bool = False,
        tick_seconds: int = 30,
    ):
        super().__init__(daemon=True, name="mailcode-scheduler")
        self.email_channel = email_channel
        self.store = store
        self.dry_run = dry_run
        self.tick_seconds = max(1, int(tick_seconds))
        self._stopped = threading.Event()
        # 保护"任务正在执行中"的标记
        self._running_task_ids: set[str] = set()
        self._running_lock = threading.Lock()

    # ---- lifecycle ----

    def stop(self) -> None:
        """发出停止信号。``run()`` 最多等一个 tick 周期后退出。"""
        self._stopped.set()

    def run(self) -> None:
        """主循环: 周期性 tick, 收到 stop 信号后干净退出。"""
        logger.info(
            "Scheduler started (tick=%ss, dry_run=%s)", self.tick_seconds, self.dry_run
        )
        try:
            while not self._stopped.is_set():
                # wait(timeout=...) 是关键 — 既能周期触发, 又能被 stop 立刻打断
                if self._stopped.wait(timeout=self.tick_seconds):
                    break
                try:
                    self._tick()
                except Exception as e:
                    logger.exception("Scheduler tick 异常: %s", e)
        finally:
            logger.info("Scheduler stopped")

    # ---- tick ----

    def _tick(self) -> None:
        """一轮扫描: 重新加载任务列表, 对每个 enabled 任务检查是否到期。"""
        try:
            tasks = self.store.list_tasks()
        except Exception as e:
            logger.warning("Scheduler 加载 tasks 失败: %s", e)
            return
        now = _now_local()
        for task in tasks:
            if not task.enabled:
                continue
            if not task.next_run_at:
                # 任务从未计算过 next_run_at, 跳过 (CLI add 后第一次
                # 计算通常由 ScheduleStore.add / show 时设置; 如果没设,
                # 这里也可以算一次)
                try:
                    next_dt = compute_next_run(task.schedule, now)
                    # 写回 next_run_at 持久化
                    self.store.update(
                        _with_next_run(task, _format_dt(next_dt))
                    )
                    continue
                except Exception as e:
                    logger.warning(
                        "Scheduler 无法为 id=%s 计算 next_run_at: %s", task.id, e
                    )
                    continue
            try:
                next_dt = _parse_dt(task.next_run_at)
            except Exception:
                next_dt = None
            if next_dt is None:
                continue
            if now >= next_dt:
                self._dispatch(task, now)

    def _dispatch(self, task: Task, now: datetime) -> None:
        """决定是否触发: 如果该 task_id 正在执行, 跳过 (running flag 保护)。"""
        with self._running_lock:
            if task.id in self._running_task_ids:
                logger.info(
                    "Scheduler 跳过 id=%s: 上一轮仍在执行", task.id
                )
                return
            self._running_task_ids.add(task.id)
        try:
            self._run_task(task, now)
        finally:
            with self._running_lock:
                self._running_task_ids.discard(task.id)

    def _run_task(self, task: Task, now: datetime) -> tuple[bool, Optional[str]]:
        """实际执行一个任务: 调 Claude + 发邮件 + 写回 last_* 字段。

        Returns:
            (success, error_message)
        """
        logger.info("Scheduler 触发任务: id=%s name=%r", task.id, task.name)

        if self.dry_run:
            body = (
                f"[dry-run] task={task.name}\n"
                f"prompt={task.prompt[:200]}\n"
                f"cwd={task.cwd}\n"
                f"to={task.to_email}\n"
            )
            subject = f"{task.subject_prefix} {task.name} [dry-run]"
            self.store.mark_run(task.id, STATUS_DRY_RUN, error=None, now=now)
            # dry-run 也不发邮件, 只写 last_status
            logger.info("Scheduler dry-run: 不调 Claude, 不发邮件, id=%s", task.id)
            return True, None

        # 调 Claude
        try:
            claude_output = call_claude(task.prompt, task.cwd)
        except Exception as e:
            err = f"call_claude 异常: {e}"
            logger.error("Scheduler call_claude 失败 id=%s: %s", task.id, e)
            self.store.mark_run(task.id, STATUS_FAILED, error=err, now=now)
            self._send_error_email(task, err)
            return False, err

        if claude_output is None:
            err = "call_claude 返回 None (失败/超时/未找到)"
            logger.error("Scheduler call_claude 返回 None id=%s", task.id)
            self.store.mark_run(task.id, STATUS_FAILED, error=err, now=now)
            self._send_error_email(task, err)
            return False, err

        # 拼邮件
        body = claude_output
        subject = f"{task.subject_prefix} {task.name}"
        try:
            ok, _msg_id = self.email_channel.send_reply(
                to_email=task.to_email,
                subject=subject,
                body=body,
            )
        except Exception as e:
            err = f"send_reply 异常: {e}"
            logger.error("Scheduler 发邮件失败 id=%s: %s", task.id, e)
            self.store.mark_run(task.id, STATUS_FAILED, error=err, now=now)
            return False, err

        if not ok:
            err = "send_reply 返回 False"
            logger.error("Scheduler 发邮件失败 id=%s", task.id)
            self.store.mark_run(task.id, STATUS_FAILED, error=err, now=now)
            return False, err

        self.store.mark_run(task.id, STATUS_SUCCESS, error=None, now=now)
        logger.info("Scheduler 任务完成 id=%s name=%r", task.id, task.name)
        return True, None

    def _send_error_email(self, task: Task, err: str) -> None:
        """任务执行失败时给 to_email 发一封错误通知 (best-effort)。"""
        try:
            subject = f"{task.subject_prefix} {task.name} [FAILED]"
            body = (
                f"MailCode Scheduler 执行失败\n\n"
                f"task: {task.name} (id={task.id})\n"
                f"error: {err}\n"
            )
            self.email_channel.send_reply(
                to_email=task.to_email,
                subject=subject,
                body=body,
            )
        except Exception as e:
            logger.warning("Scheduler 发送错误通知邮件失败 id=%s: %s", task.id, e)

    # ---- 外部触发 (供 CLI run-now 等用) ----

    def trigger_now(self, name_or_id: str) -> Optional[Task]:
        """立即触发一个任务 (不依赖调度), 同步等待结果。

        Returns:
            更新后的 task, 找不到返回 None
        """
        task = self.store.get(name_or_id)
        if task is None:
            return None
        with self._running_lock:
            if task.id in self._running_task_ids:
                logger.warning(
                    "trigger_now 跳过 id=%s: 上一轮仍在执行", task.id
                )
                return task
            self._running_task_ids.add(task.id)
        try:
            self._run_task(task, _now_local())
        finally:
            with self._running_lock:
                self._running_task_ids.discard(task.id)
        return self.store.get(name_or_id)


def _with_next_run(task: Task, next_run_iso: str) -> Task:
    """复制 task 并设置 next_run_at, 用于 ``update``。"""
    return Task(
        id=task.id,
        name=task.name,
        enabled=task.enabled,
        schedule=task.schedule,
        prompt=task.prompt,
        cwd=task.cwd,
        to_email=task.to_email,
        subject_prefix=task.subject_prefix,
        last_run_at=task.last_run_at,
        last_status=task.last_status,
        last_error=task.last_error,
        next_run_at=next_run_iso,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )
