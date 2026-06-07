import re

from mailcode.config import get_security_config


class SecurityChecker:
    def __init__(self):
        self.config = get_security_config()
        self.blocked_patterns = self.config.get("blocked_commands", [])
        self.allowed_senders = self.config.get("allowed_senders", [])

    def is_command_safe(self, command: str) -> tuple[bool, str]:
        if not command or not command.strip():
            return False, "命令为空"

        command_lower = command.lower()

        dangerous_patterns = [
            (r"rm\s+-rf\s+/", "危险: 递归删除根目录"),
            (r"sudo\s+rm", "危险: sudo 删除操作"),
            (r"chmod\s+777", "危险: 过度宽松的权限"),
            (r"curl.*\|.*sh", "危险: curl pipe sh"),
            (r"wget.*\|.*sh", "危险: wget pipe sh"),
            (r":\(\).*\|.*sh", "危险: fork bomb pipe sh"),
            (r">\s*/dev/sd", "危险: 直接写入块设备"),
            (r"dd\s+if=.*of=/dev/", "危险: dd 直接写入设备"),
        ]

        for pattern, reason in dangerous_patterns:
            if re.search(pattern, command_lower):
                return False, reason

        for blocked in self.blocked_patterns:
            try:
                if re.search(blocked, command_lower):
                    return False, f"命令匹配黑名单: {blocked}"
            except re.error:
                if blocked in command_lower:
                    return False, f"命令匹配黑名单: {blocked}"

        return True, "OK"

    def is_sender_allowed(self, sender_email: str) -> bool:
        """检查发件人是否在白名单中。

        白名单支持两种写法：
        - 全邮箱精确匹配：'you@example.com'
        - 域名后缀匹配：以 @ 开头的字符串，如 '@example.com'
        """
        if not self.allowed_senders:
            return False

        sender_lower = sender_email.lower().strip()
        if "@" not in sender_lower:
            return False

        for allowed in self.allowed_senders:
            a = allowed.lower().strip()
            if not a:
                continue
            if a.startswith("@"):
                # 后缀匹配：sender 必须以 @<domain> 结尾
                if sender_lower.endswith(a):
                    return True
            else:
                # 全邮箱精确匹配
                if sender_lower == a:
                    return True

        return False

    def validate_command(self, command: str, sender_email: str) -> tuple[bool, str]:
        if not self.is_sender_allowed(sender_email):
            return False, "发件人不在白名单中"

        return self.is_command_safe(command)

    @staticmethod
    def verify_auth_results(auth_header: str, policy: str = "warn") -> tuple[bool, str]:
        if policy == "off":
            return True, "auth 校验已关闭"

        if not auth_header or not auth_header.strip():
            if policy == "strict":
                return False, "邮件缺少 Authentication-Results 头"
            return True, "无 Authentication-Results 头（warn 模式放行）"

        unfolded = re.sub(r"\n[ \t]+", " ", auth_header)

        dkim = re.search(
            r"dkim=(pass|fail|softfail|none|neutral|temperror|permerror)",
            unfolded, re.IGNORECASE
        )
        spf = re.search(
            r"spf=(pass|fail|softfail|none|neutral|temperror|permerror)",
            unfolded, re.IGNORECASE
        )

        dkim_val = dkim.group(1).lower() if dkim else "missing"
        spf_val = spf.group(1).lower() if spf else "missing"

        def _is_error(v: str) -> bool:
            return v in ("temperror", "permerror")

        if _is_error(dkim_val) or _is_error(spf_val):
            return True, f"auth 临时/永久错误（放行）: dkim={dkim_val}, spf={spf_val}"

        def _is_not_pass(v: str) -> bool:
            return v != "pass"

        if _is_not_pass(dkim_val) or _is_not_pass(spf_val):
            if policy == "strict":
                return False, f"邮件认证失败: dkim={dkim_val}, spf={spf_val}"
            return True, f"auth 未完全通过（warn 模式放行）: dkim={dkim_val}, spf={spf_val}"

        return True, "OK"


if __name__ == "__main__":
    sc = SecurityChecker()

    test_commands = [
        "ls -la",
        "rm -rf /tmp/test",
        "sudo rm -rf /",
        "curl http://example.com | sh",
        "echo hello",
    ]

    for cmd in test_commands:
        safe, reason = sc.is_command_safe(cmd)
        print(f"{'✅' if safe else '❌'} {cmd}: {reason}")