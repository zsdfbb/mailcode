"""IMAPListener 生命周期测试 —— stop()、IDLE 回退、循环退出"""
from unittest.mock import MagicMock, patch


from mailcode.relay.email_listener import IMAPListener


class TestListenerLifecycle:
    """验证 IMAPListener 的启动/停止/IDLE 回退行为"""

    def test_stop_sets_stopped(self, mock_config_patch):
        """stop() 设置 _stopped 事件且幂等"""
        listener = IMAPListener()
        assert not listener._stopped.is_set()

        listener.stop()
        assert listener._stopped.is_set()

        listener.stop()  # 第二次调用应安全
        assert listener._stopped.is_set()

    def test_listen_poll_exits_on_stopped(self, mock_config_patch):
        """预置 _stopped 后轮询循环立即退出"""
        listener = IMAPListener()
        listener._stopped.set()

        with patch.object(listener, "_init_baseline"):
            with patch.object(listener, "_save_state"):
                with patch.object(listener, "fetch_unread_emails") as mock_fetch:
                    listener._listen_poll(dry_run=False, max_iterations=None)
                    mock_fetch.assert_not_called()

    def test_stop_does_not_crash_in_poll_mode(self, mock_config_patch):
        """未连接时 stop() 安全"""
        listener = IMAPListener()
        listener.stop()  # _idle_mail is None
        assert listener._stopped.is_set()

    def test_listen_idle_falls_back_when_no_idle_capability(
        self, mock_config_patch
    ):
        """无 IDLE 能力时回退到 _listen_poll"""
        listener = IMAPListener()

        mock_mail = MagicMock(spec=["capabilities", "select", "logout"])
        mock_mail.capabilities = ("IMAP4rev1", "LITERAL+")  # 没有 IDLE
        mock_mail.select.return_value = ("OK", [b"1"])

        with patch.object(listener, "_connect", return_value=mock_mail):
            with patch.object(listener, "_listen_poll") as mock_poll:
                listener._listen_idle(dry_run=False, max_iterations=None)
                mock_poll.assert_called_once_with(False, None)

    def test_listen_idle_proceeds_when_idle_supported(
        self, mock_config_patch
    ):
        """有 IDLE 能力时正常使用 IDLE，不调用回退"""
        listener = IMAPListener()

        mock_mail = MagicMock(spec=["capabilities", "select", "logout", "idle_done"])
        mock_mail.capabilities = ("IMAP4rev1", "IDLE", "LITERAL+")
        mock_mail.select.return_value = ("OK", [b"1"])

        with patch.object(listener, "_connect", return_value=mock_mail):
            with patch.object(listener, "_wait_for_idle", return_value=True):
                with patch.object(listener, "_reconnect", return_value=mock_mail):
                    with patch.object(listener, "fetch_unread_emails", return_value=[]):
                        with patch.object(listener, "_listen_poll") as mock_poll:
                            listener._stopped.set()  # 让循环立即退出
                            listener._listen_idle(dry_run=False, max_iterations=None)
                            mock_poll.assert_not_called()

    def test_idle_fallback_logs_warning(self, mock_config_patch, caplog):
        """回退时打印 warning 日志"""
        import logging
        caplog.set_level(logging.WARNING)

        listener = IMAPListener()

        mock_mail = MagicMock(spec=["capabilities", "select", "logout"])
        mock_mail.capabilities = ("IMAP4rev1",)
        mock_mail.select.return_value = ("OK", [b"1"])

        with patch.object(listener, "_connect", return_value=mock_mail):
            with patch.object(listener, "_listen_poll"):
                listener._listen_idle(dry_run=False, max_iterations=None)

        assert any("不支持 IDLE" in msg for msg in caplog.messages)

    def test_listen_cleanup_on_stop(self, mock_config_patch):
        """listen() 的 finally 块执行 UID 保存"""
        listener = IMAPListener()

        with patch.object(listener, "_listen_poll", wraps=lambda *a, **kw: (
            listener._stopped.set()  # 运行后立即停止
        )):
            with patch.object(listener, "_save_state") as mock_save:
                listener.listen(dry_run=False, use_idle=False)

        mock_save.assert_called_once()

    def test_reconnect_backoff(self, mock_config_patch):
        """_Backoff 序列 1, 2, 4, 8, 16, 32, 60, 60 ±20% jitter + reset"""
        from mailcode.relay.email_listener import _Backoff

        b = _Backoff()
        samples = [b.next_delay() for _ in range(8)]
        bases = [1, 2, 4, 8, 16, 32, 60, 60]
        for actual, expected in zip(samples, bases):
            assert expected * 0.8 <= actual <= expected * 1.2, (
                f"delay {actual:.2f} not in [{expected*0.8:.2f}, {expected*1.2:.2f}]"
            )

        b.reset()
        assert b.next_delay() <= 1.2  # reset 后回到 1 ±20%
        assert 0.8 <= b.next_delay() <= 2.4  # 1 -> 2 (上一步已 next 一次)

    def test_stop_calls_idle_done(self, mock_config_patch):
        """stop() 在 IDLE-wait 状态立即打破阻塞; 旧 API 调 idle_done, 新 API 不调."""
        from mailcode.relay.email_listener import _NEW_IDLE_API
        listener = IMAPListener()
        mock_mail = MagicMock()
        listener._active_idle_mail = mock_mail

        listener.stop()
        if not _NEW_IDLE_API:
            mock_mail.idle_done.assert_called_once()
        else:
            # 新 API: signal handler 不主动打断 IDLE, 由 duration 超时自然返回.
            mock_mail.idle_done.assert_not_called()
        assert listener._active_idle_mail is None
        assert listener._stopped.is_set()

    def test_stop_does_not_crash_when_idle_done_fails(self, mock_config_patch):
        """stop() 容忍 idle_done() 抛异常 (连接可能已经断了)"""
        from mailcode.relay.email_listener import _NEW_IDLE_API
        listener = IMAPListener()
        mock_mail = MagicMock()
        if not _NEW_IDLE_API:
            mock_mail.idle_done.side_effect = OSError("socket closed")
        listener._active_idle_mail = mock_mail

        listener.stop()  # 不应抛
        assert listener._active_idle_mail is None
        assert listener._stopped.is_set()

    def test_active_idle_mail_cleared_after_idle_returns(self, mock_config_patch):
        """_listen_idle 在 _wait_for_idle 返回后清掉 _active_idle_mail (给 stop() 用)"""
        listener = IMAPListener()

        mock_mail = MagicMock(spec=["capabilities", "select", "logout", "idle_done"])
        mock_mail.capabilities = ("IMAP4rev1", "IDLE")
        mock_mail.select.return_value = ("OK", [b"1"])

        with patch.object(listener, "_connect", return_value=mock_mail), \
             patch.object(listener, "_wait_for_idle", return_value=True), \
             patch.object(listener, "_reconnect", return_value=mock_mail), \
             patch.object(listener, "fetch_unread_emails", return_value=[]), \
             patch.object(listener, "_save_state"):
            listener._stopped.set()  # 立刻退出
            listener._listen_idle(dry_run=False, max_iterations=None)

        # 循环退出后, _active_idle_mail 必须被清空
        assert listener._active_idle_mail is None

    def test_idle_health_check_triggers_reconnect_on_noop_abort(self, mock_config_patch, caplog):
        """R1: 60s NOOP 健康检查抛 IMAP4.abort 时, 应触发退避重连路径"""
        import logging
        from imaplib import IMAP4
        caplog.set_level(logging.DEBUG)

        listener = IMAPListener()

        # 主连接: 有 IDLE 能力, mock noop()
        mock_mail = MagicMock(spec=["capabilities", "select", "logout", "noop", "idle_done"])
        mock_mail.capabilities = ("IMAP4rev1", "IDLE")
        mock_mail.select.return_value = ("OK", [b"1"])

        # noop: iter 12 触发 NOOP 时抛 abort; 后续成功
        noop_call_count = [0]
        def fake_noop():
            noop_call_count[0] += 1
            if noop_call_count[0] == 1:
                raise IMAP4.abort("socket closed by server")
            return ("OK", [None])
        mock_mail.noop.side_effect = fake_noop

        # 让循环跑到 iter 12 (NOOP 触发) → abort → 重连 → 第二轮后停止
        iter_count = [0]
        def stop_after_noop_reconnect(*args, **kw):
            iter_count[0] += 1
            if iter_count[0] >= 13:  # iter 12 NOOP abort, iter 13 退出
                listener._stopped.set()
            return False  # 不收事件, 让 NOOP 触发

        with patch.object(listener, "_init_baseline"), \
             patch.object(listener, "_save_state"), \
             patch.object(listener, "_connect", return_value=mock_mail), \
             patch.object(listener, "_wait_for_idle", side_effect=stop_after_noop_reconnect), \
             patch.object(listener, "_reconnect", return_value=mock_mail) as mock_reconnect, \
             patch.object(listener, "fetch_unread_emails", return_value=[]):
            listener._listen_idle(dry_run=False, max_iterations=None)

        # 验证: NOOP 失败触发了重连
        assert mock_reconnect.called
        # 验证: warning 日志出现
        assert any("IDLE 健康检查失败" in msg for msg in caplog.messages), \
            f"Expected NOOP failure log. caplog: {caplog.messages}"

    def test_fetch_unread_uses_body_peek_not_rfc822(self, mock_config_patch):
        """R3: fetch_unread_emails 用 BODY.PEEK[] 而非 RFC822, 避免打 \\Seen 标志"""
        listener = IMAPListener()

        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b"1"])
        mock_mail.search.return_value = ("OK", [b"100"])
        # 最小合法邮件
        raw = b"From: u@t.com\r\nSubject: test\r\n\r\nhello"
        mock_mail.fetch.return_value = ("OK", [(b"100 (BODY.PEEK[] {5}", raw)])

        with patch.object(listener, "_is_own_message", return_value=False), \
             patch.object(listener, "_is_duplicate", return_value=False), \
             patch("mailcode.relay.email_listener.get_auth_policy", return_value="off"), \
             patch.object(listener.security_checker, "is_sender_allowed", return_value=True):
            results = listener.fetch_unread_emails(dry_run=True, mail=mock_mail)
        assert isinstance(results, list)  # consume unused var

        # 至少一次 fetch 调用
        assert len(mock_mail.fetch.call_args_list) >= 1
        for call in mock_mail.fetch.call_args_list:
            args = call.args if hasattr(call, "args") else call[0]
            assert "BODY.PEEK" in args[1], f"Expected BODY.PEEK, got {args[1]}"
            assert "RFC822" not in args[1], f"RFC822 should not be present, got {args[1]}"

    def test_post_reconnect_fetch_logs_accumulated_count(self, mock_config_patch, caplog):
        """R2: got_event=True 触发重连后, 首轮 fetch 拉到 N 封累积未读, 日志显式记录"""
        import logging
        caplog.set_level(logging.INFO)

        listener = IMAPListener()

        mock_mail = MagicMock(spec=["capabilities", "select", "logout", "idle_done"])
        mock_mail.capabilities = ("IMAP4rev1", "IDLE")
        mock_mail.select.return_value = ("OK", [b"1"])

        # 第一次 fetch 返回 3 封累积未读, 之后空
        fetch_results = [
            [{"uid": "1", "body": ""}, {"uid": "2", "body": ""}, {"uid": "3", "body": ""}],
            [],
        ]
        fetch_calls = [0]
        def fake_fetch(*args, **kwargs):
            idx = min(fetch_calls[0], len(fetch_results) - 1)
            r = fetch_results[idx]
            fetch_calls[0] += 1
            return r

        # 第一轮: got_event=True → 重连 → fetch 返回 3 封
        # 第二轮: fetch 返回空, 停止
        idle_calls = [0]
        def fake_wait_for_idle(mail):
            idle_calls[0] += 1
            if idle_calls[0] >= 2:
                listener._stopped.set()
            return True  # got_event 走重连分支

        with patch.object(listener, "_init_baseline"), \
             patch.object(listener, "_save_state"), \
             patch.object(listener, "_connect", return_value=mock_mail), \
             patch.object(listener, "_wait_for_idle", side_effect=fake_wait_for_idle), \
             patch.object(listener, "_reconnect", return_value=mock_mail), \
             patch.object(listener, "fetch_unread_emails", side_effect=fake_fetch), \
             patch.object(listener, "process_email", return_value=(True, "ok")):
            listener._listen_idle(dry_run=False, max_iterations=None)

        assert any("重连后首轮 fetch 拉到 3 封累积未读" in msg for msg in caplog.messages), \
            f"Expected log not found. caplog: {caplog.messages}"

    def test_backoff_reconnect_success_log(self, mock_config_patch, caplog):
        """R4: 外层 except 退避后 _reconnect 成功, 打 '退避重连成功' 日志"""
        import logging
        from imaplib import IMAP4
        caplog.set_level(logging.INFO)

        listener = IMAPListener()

        mock_mail = MagicMock(spec=["capabilities", "select", "logout", "noop", "idle_done"])
        mock_mail.capabilities = ("IMAP4rev1", "IDLE")
        mock_mail.select.return_value = ("OK", [b"1"])

        noop_call_count = [0]
        def fake_noop():
            noop_call_count[0] += 1
            if noop_call_count[0] == 1:
                raise IMAP4.abort("socket closed")
            return ("OK", [None])
        mock_mail.noop.side_effect = fake_noop

        iter_count = [0]
        def fake_wait_for_idle(mail):
            iter_count[0] += 1
            if iter_count[0] >= 13:  # 让 iter 12 触发 NOOP
                listener._stopped.set()
            return False

        with patch.object(listener, "_init_baseline"), \
             patch.object(listener, "_save_state"), \
             patch.object(listener, "_connect", return_value=mock_mail), \
             patch.object(listener, "_wait_for_idle", side_effect=fake_wait_for_idle), \
             patch.object(listener, "_reconnect", return_value=mock_mail) as mock_reconnect, \
             patch.object(listener, "fetch_unread_emails", return_value=[]):
            listener._listen_idle(dry_run=False, max_iterations=None)

        assert mock_reconnect.called
        assert any("退避重连成功, 准备拉取累积未读" in msg for msg in caplog.messages), \
            f"Expected log not found. caplog: {caplog.messages}"

    def test_wait_for_idle_old_cleans_up_on_timeout(self, mock_config_patch):
        """Fix 2: _wait_for_idle_old 超时后正确清理 idle_thread。"""
        import threading

        listener = IMAPListener()
        listener._idle_timeout = 0.1  # 100ms 超时, 足够让 idle_thread 进入 idle_response

        mail = MagicMock(spec=["idle", "idle_response", "idle_done", "select", "logout", "capabilities"])

        # idle_response 阻塞直到 idle_done 解除
        response_unblock = threading.Event()
        mail.idle_response.side_effect = lambda: (
            response_unblock.wait(timeout=10) or ("OK", [None])
        )
        mail.idle_done.side_effect = lambda: response_unblock.set()

        result = listener._wait_for_idle_old(mail)

        assert result is False, "应返回 False (超时)"
        mail.idle_done.assert_called_once()
        assert not listener._idle_ready.is_set(), "_idle_ready 应被清除"
        if listener._idle_thread:
            listener._idle_thread.join(timeout=2)
            assert not listener._idle_thread.is_alive(), "idle_thread 应已退出"

    def test_forced_reconnect_triggers_after_interval(self, mock_config_patch, caplog):
        """Fix 3: 超过 90min 后触发预判性重连。"""
        import logging
        caplog.set_level(logging.INFO)

        listener = IMAPListener()
        listener.FORCED_RECONNECT_INTERVAL = 0  # 立即触发
        listener._last_connect_time = 0

        mock_mail = MagicMock(spec=["capabilities", "select", "logout", "noop", "idle_done"])
        mock_mail.capabilities = ("IMAP4rev1", "IDLE")
        mock_mail.select.return_value = ("OK", [b"1"])

        with patch.object(listener, "_init_baseline"), \
             patch.object(listener, "_save_state"), \
             patch.object(listener, "_connect", return_value=mock_mail), \
             patch.object(listener, "_wait_for_idle", return_value=False), \
             patch.object(listener, "_reconnect", return_value=mock_mail) as mock_reconnect, \
             patch.object(listener, "fetch_unread_emails", return_value=[]):
            listener._listen_idle(dry_run=False, max_iterations=1)

        assert mock_reconnect.called, "预判重连应被触发"
        assert any("预判性重连" in msg for msg in caplog.messages), \
            f"Expected proactive reconnect log. caplog: {caplog.messages}"

    def test_forced_reconnect_skipped_when_got_event(self, mock_config_patch):
        """Fix 3: got_event=True 时预判重连不额外触发。"""
        listener = IMAPListener()
        listener.FORCED_RECONNECT_INTERVAL = 0  # 区间为 0 也应跳过
        listener._last_connect_time = 0

        mock_mail = MagicMock(spec=["capabilities", "select", "logout", "noop", "idle_done"])
        mock_mail.capabilities = ("IMAP4rev1", "IDLE")
        mock_mail.select.return_value = ("OK", [b"1"])

        reconnect_calls = [0]
        def fake_reconnect():
            reconnect_calls[0] += 1
            return mock_mail

        with patch.object(listener, "_init_baseline"), \
             patch.object(listener, "_save_state"), \
             patch.object(listener, "_connect", return_value=mock_mail), \
             patch.object(listener, "_wait_for_idle", return_value=True), \
             patch.object(listener, "_reconnect", side_effect=fake_reconnect), \
             patch.object(listener, "fetch_unread_emails", return_value=[]):
            listener._listen_idle(dry_run=False, max_iterations=2)

        # 2 次 got_event=True → 2 次 _reconnect (不带预判额外)
        assert reconnect_calls[0] == 2

    def test_noop_after_select_in_fetch(self, mock_config_patch):
        """Fix 4: fetch_unread_emails 在 SELECT 后立即 NOOP。"""
        listener = IMAPListener()

        mock_mail = MagicMock()
        mock_mail.select.return_value = ("OK", [b"1"])
        mock_mail.search.return_value = ("OK", [b""])

        results = listener.fetch_unread_emails(mail=mock_mail)

        assert isinstance(results, list)
        call_names = [c[0] for c in mock_mail.method_calls]
        select_idx = next(i for i, n in enumerate(call_names) if n == "select")
        noop_idx = next(i for i, n in enumerate(call_names) if n == "noop")
        search_idx = next(i for i, n in enumerate(call_names) if n == "search")
        assert select_idx < noop_idx, "NOOP 应在 SELECT 之后"
        assert noop_idx < search_idx, "NOOP 应在 SEARCH 之前"

    def test_noop_after_reconnect_select(self, mock_config_patch):
        """Fix 4: _listen_idle 的 got_event 重连后执行 NOOP。"""
        listener = IMAPListener()

        mock_mail = MagicMock(spec=["capabilities", "select", "logout", "noop", "idle_done"])
        mock_mail.capabilities = ("IMAP4rev1", "IDLE")
        mock_mail.select.return_value = ("OK", [b"1"])

        with patch.object(listener, "_init_baseline"), \
             patch.object(listener, "_save_state"), \
             patch.object(listener, "_connect", return_value=mock_mail), \
             patch.object(listener, "_wait_for_idle", return_value=True), \
             patch.object(listener, "_reconnect", return_value=mock_mail), \
             patch.object(listener, "fetch_unread_emails", return_value=[]):
            listener._listen_idle(dry_run=False, max_iterations=1)

        call_names = [c[0] for c in mock_mail.method_calls]
        noop_calls = [i for i, n in enumerate(call_names) if n == "noop"]
        assert len(noop_calls) >= 1, "NOOP 应至少被调一次"


# ============================================================
# process_email 路由表
# ============================================================


class TestProcessEmailRouting:
    """process_email 根据 force_session / is_session_enabled 路由到正确 handler。"""

    def _make_entry(self, subject="Hi", body="q", from_email="u@t.com"):
        return {
            "uid": "1",
            "message_id": "<m@t>",
            "from": from_email,
            "sender_email": from_email,
            "subject": subject,
            "body": body,
            "references": "",
            "in_reply_to": "",
        }

    def test_routes_to_correct_handler(self, mock_config_patch):
        """4 种 force_session × is_session_enabled 组合均路由到预期 handler。

        | force_session | is_session_enabled() | 期望 handler        | mode            |
        |---------------|----------------------|--------------------|-----------------|
        | True          | (任意)               | _handle_via_conversation | conversation   |
        | False         | (任意)               | _handle_via_stateless    | stateless      |
        | None          | True                 | _handle_via_conversation | conversation   |
        | None          | False                | _handle_via_stateless    | stateless      |
        """
        listener = IMAPListener()
        entry = self._make_entry()

        # 1) force_session=True → conversation
        with patch("mailcode.relay.email_listener.is_session_enabled", return_value=False), \
             patch.object(listener, "_handle_via_conversation",
                          return_value=(True, "conversation")) as mock_conv, \
             patch.object(listener, "_handle_via_stateless") as mock_stateless:
            success, mode = listener.process_email(entry, dry_run=False, force_session=True)
            assert success is True
            assert mode == "conversation"
            mock_conv.assert_called_once()
            mock_stateless.assert_not_called()

        # 2) force_session=False → stateless
        with patch("mailcode.relay.email_listener.is_session_enabled", return_value=True), \
             patch.object(listener, "_handle_via_conversation") as mock_conv, \
             patch.object(listener, "_handle_via_stateless",
                          return_value=(True, "stateless")) as mock_stateless:
            success, mode = listener.process_email(entry, dry_run=False, force_session=False)
            assert success is True
            assert mode == "stateless"
            mock_stateless.assert_called_once()
            mock_conv.assert_not_called()

        # 3) force_session=None + is_session_enabled()=True → conversation
        with patch("mailcode.relay.email_listener.is_session_enabled", return_value=True), \
             patch.object(listener, "_handle_via_conversation",
                          return_value=(True, "conversation")) as mock_conv, \
             patch.object(listener, "_handle_via_stateless") as mock_stateless:
            success, mode = listener.process_email(entry, dry_run=False, force_session=None)
            assert mode == "conversation"
            mock_conv.assert_called_once()
            mock_stateless.assert_not_called()

        # 4) force_session=None + is_session_enabled()=False → stateless
        with patch("mailcode.relay.email_listener.is_session_enabled", return_value=False), \
             patch.object(listener, "_handle_via_conversation") as mock_conv, \
             patch.object(listener, "_handle_via_stateless",
                          return_value=(True, "stateless")) as mock_stateless:
            success, mode = listener.process_email(entry, dry_run=False, force_session=None)
            assert mode == "stateless"
            mock_stateless.assert_called_once()
            mock_conv.assert_not_called()

    def test_dry_run_does_not_call_handlers(self, mock_config_patch):
        """dry_run=True 走 dry_run 路径, 不调任何 handler。"""
        listener = IMAPListener()

        with patch.object(listener, "_handle_via_conversation") as mock_conv, \
             patch.object(listener, "_handle_via_stateless") as mock_stateless:
            success, mode = listener.process_email(
                self._make_entry(), dry_run=True, force_session=None,
            )

        assert success is True
        assert mode == "dry_run"
        mock_conv.assert_not_called()
        mock_stateless.assert_not_called()

    def test_lazy_init_reuses_handler_instance(self, mock_config_patch):
        """二次调用复用同一 handler 实例 (lazy init 幂等)。

        对 conversation 和 stateless 两条路径分别验证。
        """
        listener = IMAPListener()
        entry = self._make_entry()

        # conversation 路径
        with patch("mailcode.relay.email_listener.is_session_enabled", return_value=True), \
             patch("mailcode.relay.conversation_handler.ConversationHandler") as MockCH:
            mock_h = MagicMock()
            mock_h.handle_email.return_value = True
            MockCH.return_value = mock_h

            listener.process_email(entry, dry_run=False, force_session=None)
            first_id = id(listener._conv_handler)
            listener.process_email(entry, dry_run=False, force_session=None)
            second_id = id(listener._conv_handler)

            assert first_id == second_id
            assert MockCH.call_count == 1
            assert mock_h.handle_email.call_count == 2

        # stateless 路径
        with patch("mailcode.relay.email_listener.is_session_enabled", return_value=False), \
             patch("mailcode.relay.stateless_handler.StatelessHandler") as MockSH:
            mock_h = MagicMock()
            mock_h.handle_email.return_value = True
            MockSH.return_value = mock_h

            listener.process_email(entry, dry_run=False, force_session=None)
            first_id = id(listener._stateless_handler)
            listener.process_email(entry, dry_run=False, force_session=None)
            second_id = id(listener._stateless_handler)

            assert first_id == second_id
            assert MockSH.call_count == 1
            assert mock_h.handle_email.call_count == 2
