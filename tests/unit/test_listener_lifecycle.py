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

        mock_mail = MagicMock(spec=["capabilities", "select"])
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

        mock_mail = MagicMock(spec=["capabilities", "select"])
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

        mock_mail = MagicMock(spec=["capabilities", "select"])
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
