"""scheduler 单元测试 —— 覆盖 parse / compute_next_run / store / scheduler"""

import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from mailcode.relay import scheduler as sched_module
from mailcode.relay.scheduler import (
    SCHEDULE_DAILY,
    SCHEDULE_INTERVAL,
    SCHEDULE_MONTHLY,
    SCHEDULE_WEEKLY,
    STATUS_DRY_RUN,
    STATUS_FAILED,
    STATUS_SUCCESS,
    ScheduleSpec,
    ScheduleStore,
    Scheduler,
    Task,
    compute_next_run,
    parse_schedule,
)


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def mock_email_channel():
    """Mock EmailChannel: 提供 send_reply 默认返回成功。"""
    channel = MagicMock()
    channel.send_reply.return_value = (True, "<reply-abc@mailcode>")
    return channel


@pytest.fixture
def store(schedules_path):
    """使用临时 schedules.json 的 ScheduleStore。"""
    with patch.object(sched_module, "_SCHEDULES_PATH", schedules_path):
        s = ScheduleStore(schedules_path)
        yield s


@pytest.fixture
def sample_task():
    """工厂: 构造一个基础 Task。"""
    def _make(
        name: str = "demo",
        enabled: bool = True,
        schedule: ScheduleSpec = None,
        prompt: str = "test prompt",
        cwd: str = "/tmp",
        to_email: str = "user@test.com",
        next_run_at: str = None,
    ) -> Task:
        if schedule is None:
            schedule = ScheduleSpec(type=SCHEDULE_INTERVAL, interval_seconds=3600)
        return Task(
            id=sched_module._new_task_id(),
            name=name,
            enabled=enabled,
            schedule=schedule,
            prompt=prompt,
            cwd=cwd,
            to_email=to_email,
            next_run_at=next_run_at,
        )
    return _make


# ------------------------------------------------------------------ #
# TestParseSchedule
# ------------------------------------------------------------------ #


class TestParseSchedule:
    """parse_schedule(dict) -> ScheduleSpec 校验。"""

    def test_interval_合法(self):
        spec = parse_schedule({"type": "interval", "interval_seconds": 60})
        assert spec.type == "interval"
        assert spec.interval_seconds == 60

    def test_daily_合法(self):
        spec = parse_schedule({"type": "daily", "time": "09:00"})
        assert spec.type == "daily"
        assert spec.time == "09:00"

    def test_weekly_合法(self):
        spec = parse_schedule(
            {"type": "weekly", "time": "08:30", "day_of_week": 0}
        )
        assert spec.type == "weekly"
        assert spec.day_of_week == 0
        assert spec.time == "08:30"

    def test_monthly_合法(self):
        spec = parse_schedule(
            {"type": "monthly", "time": "12:00", "day_of_month": 15}
        )
        assert spec.type == "monthly"
        assert spec.day_of_month == 15

    def test_daily_缺_time_抛_ValueError(self):
        with pytest.raises(ValueError, match="time"):
            parse_schedule({"type": "daily"})

    def test_interval_负数_抛_ValueError(self):
        with pytest.raises(ValueError, match="interval_seconds"):
            parse_schedule({"type": "interval", "interval_seconds": -1})

    def test_interval_非整数_抛_ValueError(self):
        with pytest.raises(ValueError, match="interval_seconds"):
            parse_schedule({"type": "interval", "interval_seconds": "fast"})

    def test_未知_type_抛_ValueError(self):
        with pytest.raises(ValueError, match="type"):
            parse_schedule({"type": "yearly", "time": "09:00"})

    def test_weekly_day_of_week_越界_抛_ValueError(self):
        with pytest.raises(ValueError, match="day_of_week"):
            parse_schedule(
                {"type": "weekly", "time": "09:00", "day_of_week": 7}
            )
        with pytest.raises(ValueError, match="day_of_week"):
            parse_schedule(
                {"type": "weekly", "time": "09:00", "day_of_week": -1}
            )

    def test_monthly_day_of_month_越界_抛_ValueError(self):
        with pytest.raises(ValueError, match="day_of_month"):
            parse_schedule(
                {"type": "monthly", "time": "09:00", "day_of_month": 0}
            )
        with pytest.raises(ValueError, match="day_of_month"):
            parse_schedule(
                {"type": "monthly", "time": "09:00", "day_of_month": 32}
            )


# ------------------------------------------------------------------ #
# TestComputeNextRun
# ------------------------------------------------------------------ #


class TestComputeNextRun:
    """compute_next_run(spec, after_dt) 跨天/跨月/跨年逻辑。"""

    def test_daily_09_00_当前_08_00_今天_09_00(self):
        spec = ScheduleSpec(type=SCHEDULE_DAILY, time="09:00")
        after = datetime(2026, 6, 13, 8, 0, 0)
        result = compute_next_run(spec, after)
        assert result == datetime(2026, 6, 13, 9, 0, 0)

    def test_daily_09_00_当前_10_00_明天_09_00(self):
        spec = ScheduleSpec(type=SCHEDULE_DAILY, time="09:00")
        after = datetime(2026, 6, 13, 10, 0, 0)
        result = compute_next_run(spec, after)
        assert result == datetime(2026, 6, 14, 9, 0, 0)

    def test_weekly_偏移_0_周日_10_00_到_mon_09_00(self):
        """weekly day_of_week=0 表示 "今天偏移 0 天" (周一偏移语义)。"""
        # 2026-06-14 是周日
        spec = ScheduleSpec(
            type=SCHEDULE_WEEKLY, time="09:00", day_of_week=0
        )
        after = datetime(2026, 6, 14, 10, 0, 0)  # 周日 10:00
        # offset=0 即"今天", 但 09:00 已过 → 推到下个周期 +7 天
        result = compute_next_run(spec, after)
        assert result == datetime(2026, 6, 21, 9, 0, 0)

    def test_weekly_偏移_0_周一_10_00_下周一_09_00(self):
        """weekly day_of_week=0 在周一 10:00 时, 推到下周一 09:00。"""
        # 2026-06-15 是周一
        spec = ScheduleSpec(
            type=SCHEDULE_WEEKLY, time="09:00", day_of_week=0
        )
        after = datetime(2026, 6, 15, 10, 0, 0)
        result = compute_next_run(spec, after)
        assert result == datetime(2026, 6, 22, 9, 0, 0)

    def test_weekly_偏移_1_周一_10_00_周二_09_00(self):
        """weekly day_of_week=1, 当前 Mon 10:00 → 明天 (Tue) 09:00。"""
        spec = ScheduleSpec(
            type=SCHEDULE_WEEKLY, time="09:00", day_of_week=1
        )
        after = datetime(2026, 6, 15, 10, 0, 0)  # 周一 10:00
        result = compute_next_run(spec, after)
        assert result == datetime(2026, 6, 16, 9, 0, 0)

    def test_monthly_day_1_5_号_10_00_下个月_1_号(self):
        spec = ScheduleSpec(
            type=SCHEDULE_MONTHLY, time="09:00", day_of_month=1
        )
        after = datetime(2026, 6, 5, 10, 0, 0)
        result = compute_next_run(spec, after)
        assert result == datetime(2026, 7, 1, 9, 0, 0)

    def test_monthly_day_31_2_月_调用_跳到_3_月(self):
        """2 月没有 31 日, 自动跳到 3 月 31 日。"""
        spec = ScheduleSpec(
            type=SCHEDULE_MONTHLY, time="09:00", day_of_month=31
        )
        after = datetime(2026, 2, 1, 10, 0, 0)
        result = compute_next_run(spec, after)
        assert result == datetime(2026, 3, 31, 9, 0, 0)

    def test_monthly_day_30_2_月_调用_跳到_3_月(self):
        """2 月没有 30 日, 自动跳到 3 月 30 日。"""
        spec = ScheduleSpec(
            type=SCHEDULE_MONTHLY, time="09:00", day_of_month=30
        )
        after = datetime(2026, 2, 15, 10, 0, 0)
        result = compute_next_run(spec, after)
        assert result == datetime(2026, 3, 30, 9, 0, 0)

    def test_跨年_12_31_23_59_daily_00_01(self):
        spec = ScheduleSpec(type=SCHEDULE_DAILY, time="00:01")
        after = datetime(2026, 12, 31, 23, 59, 0)
        result = compute_next_run(spec, after)
        assert result == datetime(2027, 1, 1, 0, 1, 0)

    def test_interval_3600s(self):
        spec = ScheduleSpec(type=SCHEDULE_INTERVAL, interval_seconds=3600)
        after = datetime(2026, 6, 13, 10, 0, 0)
        result = compute_next_run(spec, after)
        assert result == after + timedelta(seconds=3600)


# ------------------------------------------------------------------ #
# TestStore
# ------------------------------------------------------------------ #


class TestStore:
    """ScheduleStore 持久化 + 线程安全。"""

    def test_读不存在文件_返回空_doc(self, store):
        doc = store.load()
        assert doc.get("version") == 1
        assert doc.get("tasks") == []
        assert isinstance(doc.get("updated_at"), (int, float))
        assert doc["updated_at"] > 0

    def test_写后读回一致_round_trip(self, store, sample_task):
        task = sample_task(name="alpha")
        store.add(task)

        tasks = store.list_tasks()
        assert len(tasks) == 1
        loaded = tasks[0]
        assert loaded.name == "alpha"
        assert loaded.schedule.type == SCHEDULE_INTERVAL
        assert loaded.schedule.interval_seconds == 3600

        # round-trip JSON 序列化
        d = loaded.to_dict()
        assert d["name"] == "alpha"
        assert d["schedule"]["interval_seconds"] == 3600

    def test_损坏_JSON_load_返回空_doc_不抛(self, schedules_path):
        with patch.object(sched_module, "_SCHEDULES_PATH", schedules_path):
            schedules_path.write_text("{not valid json", encoding="utf-8")
            s = ScheduleStore(schedules_path)
            doc = s.load()
            assert doc.get("tasks") == []
            assert doc.get("version") == 1

    def test_name_唯一性校验_不区分大小写(self, store, sample_task):
        store.add(sample_task(name="DailyReport"))
        with pytest.raises(ValueError, match="已存在"):
            store.add(sample_task(name="dailyreport"))
        with pytest.raises(ValueError, match="已存在"):
            store.add(sample_task(name="  DailyReport  "))

    def test_多线程并发_add_10_线程(self, store, sample_task):
        results: list = []
        errors: list = []

        def worker(i: int):
            try:
                t = sample_task(name=f"task-{i:02d}")
                store.add(t)
                results.append(t.id)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(results) == 10
        # 全部持久化
        tasks = store.list_tasks()
        assert len(tasks) == 10
        names = {t.name for t in tasks}
        assert names == {f"task-{i:02d}" for i in range(10)}

    def test_set_enabled_返回更新后_task(self, store, sample_task):
        store.add(sample_task(name="foo"))
        updated = store.set_enabled("foo", False)
        assert updated is not None
        assert updated.enabled is False
        assert store.get("foo").enabled is False

    def test_mark_run_注入_now_计算_next_run(self, store, sample_task):
        store.add(sample_task(name="bar"))
        t = store.get("bar")
        # 用 _now_local 拿到本机时区的"现在", 避免 aware/naive 比较
        fixed_now = sched_module._now_local()
        result = store.mark_run(
            t.id, STATUS_SUCCESS, error=None, now=fixed_now
        )
        assert result is not None
        assert result.last_status == STATUS_SUCCESS
        # next_run_at = fixed_now + 3600s
        next_dt = sched_module._parse_dt(result.next_run_at)
        # 比较时去掉 tzinfo, 避免本地时区引入误差
        assert next_dt.replace(tzinfo=None) == (
            fixed_now + timedelta(seconds=3600)
        ).replace(tzinfo=None)


# ------------------------------------------------------------------ #
# TestSchedulerLifecycle
# ------------------------------------------------------------------ #


class TestSchedulerLifecycle:
    """Scheduler 线程生命周期: start / stop / join。"""

    def test_start_后_is_alive_True(self, store, mock_email_channel):
        sched = Scheduler(mock_email_channel, store, tick_seconds=60)
        sched.start()
        try:
            assert sched.is_alive()
            assert not sched._stopped.is_set()
        finally:
            sched.stop()
            sched.join(timeout=5)

    def test_stop_后_join_退出(self, store, mock_email_channel):
        sched = Scheduler(mock_email_channel, store, tick_seconds=60)
        sched.start()
        sched.stop()
        sched.join(timeout=10)
        assert sched._stopped.is_set()
        assert not sched.is_alive()

    def test_trigger_now_同步等结果(self, store, mock_email_channel, sample_task):
        with patch.object(sched_module, "call_claude", return_value="hello"):
            store.add(sample_task(name="trigger-me"))
            sched = Scheduler(mock_email_channel, store, tick_seconds=60)
            # 不需要 start() — trigger_now 是同步方法
            updated = sched.trigger_now("trigger-me")
            assert updated is not None
            assert updated.last_status == STATUS_SUCCESS
            # Claude 被调一次, 邮件发一次
            assert mock_email_channel.send_reply.call_count == 1
            assert sched_module.call_claude.call_count == 1

    def test_trigger_now_找不到任务_返回_None(
        self, store, mock_email_channel
    ):
        sched = Scheduler(mock_email_channel, store, tick_seconds=60)
        assert sched.trigger_now("nonexistent") is None


# ------------------------------------------------------------------ #
# TestTrigger
# ------------------------------------------------------------------ #


class TestTrigger:
    """Scheduler._tick / _run_task 触发条件 + 副作用。"""

    def _now_aware(self) -> datetime:
        """返回带本机时区的 aware datetime, 模拟 _now_local 的实际行为。"""
        return sched_module._now_local()

    def test_到点_enabled_调_call_claude_和_send_reply(
        self, store, mock_email_channel, sample_task
    ):
        task = sample_task(name="tick-me")
        # 设置 next_run_at 为过去时间, 模拟已到期
        past = self._now_aware() - timedelta(hours=1)
        task.next_run_at = sched_module._format_dt(past)
        store.add(task)

        with patch.object(
            sched_module, "call_claude", return_value="claude output"
        ) as mock_claude:
            sched = Scheduler(mock_email_channel, store, tick_seconds=60)
            fixed_now = self._now_aware()
            with patch.object(sched_module, "_now_local") as mock_now:
                mock_now.return_value = fixed_now
                sched._tick()

        assert mock_claude.call_count == 1
        assert mock_email_channel.send_reply.call_count == 1
        # 验证 task 状态被更新
        t = store.get("tick-me")
        assert t.last_status == STATUS_SUCCESS
        # next_run_at 被重新计算
        assert t.next_run_at is not None

    def test_enabled_False_不触发(
        self, store, mock_email_channel, sample_task
    ):
        task = sample_task(name="disabled-task", enabled=False)
        past = self._now_aware() - timedelta(hours=1)
        task.next_run_at = sched_module._format_dt(past)
        store.add(task)

        with patch.object(sched_module, "call_claude", return_value="x") as mock_claude:
            sched = Scheduler(mock_email_channel, store, tick_seconds=60)
            with patch.object(sched_module, "_now_local") as mock_now:
                mock_now.return_value = self._now_aware()
                sched._tick()

        mock_claude.assert_not_called()
        mock_email_channel.send_reply.assert_not_called()

    def test_call_claude_返回_None_last_status_failed(
        self, store, mock_email_channel, sample_task
    ):
        current = self._now_aware()
        task = sample_task(name="claude-fail")
        past = current - timedelta(hours=1)
        task.next_run_at = sched_module._format_dt(past)
        store.add(task)

        with patch.object(sched_module, "call_claude", return_value=None):
            sched = Scheduler(mock_email_channel, store, tick_seconds=60)
            with patch.object(sched_module, "_now_local") as mock_now:
                mock_now.return_value = current
                sched._tick()

        t = store.get("claude-fail")
        assert t.last_status == STATUS_FAILED
        assert t.last_error  # 非空
        assert "None" in t.last_error

    def test_dry_run_True_调_call_claude_不发邮件(
        self, store, mock_email_channel, sample_task
    ):
        current = self._now_aware()
        task = sample_task(name="dry-task")
        past = current - timedelta(hours=1)
        task.next_run_at = sched_module._format_dt(past)
        store.add(task)

        # dry_run=True 时, _run_task 早 return, 不调 call_claude
        sched = Scheduler(
            mock_email_channel, store, dry_run=True, tick_seconds=60
        )
        with patch.object(sched_module, "call_claude") as mock_claude:
            with patch.object(sched_module, "_now_local") as mock_now:
                mock_now.return_value = current
                sched._tick()

        # dry_run 模式: 既不调 Claude 也不发邮件
        mock_claude.assert_not_called()
        mock_email_channel.send_reply.assert_not_called()
        t = store.get("dry-task")
        assert t.last_status == STATUS_DRY_RUN

    def test_send_reply_返回_False_last_status_failed(
        self, store, sample_task
    ):
        channel = MagicMock()
        channel.send_reply.return_value = (False, None)
        current = self._now_aware()

        task = sample_task(name="smtp-fail")
        past = current - timedelta(hours=1)
        task.next_run_at = sched_module._format_dt(past)
        store.add(task)

        with patch.object(sched_module, "call_claude", return_value="out"):
            sched = Scheduler(channel, store, tick_seconds=60)
            with patch.object(sched_module, "_now_local") as mock_now:
                mock_now.return_value = current
                sched._tick()

        t = store.get("smtp-fail")
        assert t.last_status == STATUS_FAILED
        assert t.last_error is not None
