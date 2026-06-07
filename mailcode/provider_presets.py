"""邮件服务商预设 — SMTP/IMAP 默认值及域名检测"""

# 域名 → provider 映射
DOMAIN_PROVIDER_MAP = {
    "qq.com": "qq",
    "126.com": "126",
    "163.com": "163",
    "gmail.com": "gmail",
    "outlook.com": "outlook",
    "hotmail.com": "outlook",
    "live.com": "outlook",
}

# provider → SMTP/IMAP 默认值
PROVIDER_PRESETS = {
    "qq": {
        "smtp": {"host": "smtp.qq.com", "port": 465, "secure": True},
        "imap": {"host": "imap.qq.com", "port": 993, "secure": True},
    },
    "126": {
        "smtp": {"host": "smtp.126.com", "port": 465, "secure": True},
        "imap": {"host": "imap.126.com", "port": 993, "secure": True},
    },
    "163": {
        "smtp": {"host": "smtp.163.com", "port": 465, "secure": True},
        "imap": {"host": "imap.163.com", "port": 993, "secure": True},
    },
    "gmail": {
        "smtp": {"host": "smtp.gmail.com", "port": 587, "secure": True},
        "imap": {"host": "imap.gmail.com", "port": 993, "secure": True},
    },
    "outlook": {
        "smtp": {"host": "smtp-mail.outlook.com", "port": 587, "secure": True},
        "imap": {"host": "outlook.office365.com", "port": 993, "secure": True},
    },
}


def detect_provider(email: str) -> str:
    """根据邮箱域名识别邮件服务商。

    Args:
        email: 邮箱地址。

    Returns:
        provider 名称: "qq", "126", "163", "gmail", "outlook", 或 "custom"。
    """
    if not email or "@" not in email:
        return "custom"
    domain = email.split("@", 1)[1].lower().strip()
    return DOMAIN_PROVIDER_MAP.get(domain, "custom")
