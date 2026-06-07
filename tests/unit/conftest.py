"""单元测试 fixtures —— mock/patch 依赖，无真实外部服务"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def temp_data_dir(tmp_path):
    """临时数据目录，替代 ~/.config/mailcode/"""
    data_dir = tmp_path / "mailcode_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def mock_config_full():
    """返回完整内存配置字典（SMTP/IMAP/email/security/notification）"""
    return {
        "smtp": {
            "host": "smtp.test.com",
            "port": 465,
            "secure": True,
            "user": "test@test.com",
            "pass": "testpass123"
        },
        "imap": {
            "host": "imap.test.com",
            "port": 993,
            "secure": True,
            "user": "test@test.com",
            "pass": "testpass123"
        },
        "email": {
            "from": "test@test.com",
            "from_name": "Mailcode Remote",
            "to": "test@test.com",
            "check_interval": 5
        },
        "security": {
            "allowed_senders": ["allowed@test.com"],
            "auth_policy": "warn"
        },
        "notification": {
            "desktop": True,
            "desktop_sound": ""
        }
    }


@pytest.fixture
def mock_config_patch(mock_config_full):
    """patch mailcode.config.load_config 返回内存配置"""
    import mailcode.config
    mailcode.config._config_cache = None

    def mock_load_config(force_reload=False):
        return mock_config_full

    with patch.object(mailcode.config, 'load_config', side_effect=mock_load_config):
        yield mock_config_full


@pytest.fixture
def mock_config_new():
    """新格式配置（mailcode_bot + security，无 email 段）"""
    return {
        "mailcode_bot": {
            "email": "bot@qq.com",
            "password": "secret",
            "from_name": "My Bot",
            "check_interval": 15,
        },
        "security": {
            "allowed_senders": ["admin@qq.com"],
            "auth_policy": "warn",
        },
    }


@pytest.fixture
def mock_config_new_patch(mock_config_new):
    """patch load_config 返回新格式配置"""
    import mailcode.config
    mailcode.config._config_cache = None

    def mock_load(force_reload=False):
        return mock_config_new

    from unittest.mock import patch
    with patch.object(mailcode.config, 'load_config', side_effect=mock_load):
        yield mock_config_new


@pytest.fixture
def mock_tmux():
    """Mock subprocess.run 的 tmux 调用，返回模拟输出"""
    def tmux_mock(*args, **kwargs):
        cmd = args[0] if args else kwargs.get('args', [])
        if isinstance(cmd, list):
            cmd_str = ' '.join(cmd)
        else:
            cmd_str = str(cmd)

        mock_result = MagicMock()
        mock_result.returncode = 0

        if 'has-session' in cmd_str:
            mock_result.stdout = b"Session: mailcode-abc123"
        elif 'capture-pane' in cmd_str:
            mock_result.stdout = b"$ opencode\n> hello world\n"
        elif 'send-keys' in cmd_str:
            mock_result.stdout = b""
        elif 'display-message' in cmd_str:
            mock_result.stdout = b"mailcode-abc123"
        else:
            mock_result.stdout = b""

        return mock_result

    return tmux_mock


@pytest.fixture
def mock_opencode_available():
    """Mock shutil.which 使 opencode 被视为可用"""
    with patch('shutil.which') as mock_which:
        mock_which.return_value = "/usr/local/bin/opencode"
        yield mock_which


@pytest.fixture
def mock_opencode_missing():
    """Mock shutil.which 使 opencode 被视为不可用"""
    with patch('shutil.which') as mock_which:
        mock_which.return_value = None
        yield mock_which


@pytest.fixture
def sample_email():
    """工厂函数，快速构建标准测试邮件 dict"""
    def _make_email(
        from_addr="allowed@test.com",
        subject="Test Subject",
        body="Test body content",
        message_id="<test123@example.com>",
        extra_headers=None
    ):
        headers = {
            "From": from_addr,
            "Subject": subject,
            "Message-ID": message_id,
            "Date": "Wed, 01 Jan 2025 12:00:00 +0000",
        }
        if extra_headers:
            headers.update(extra_headers)

        return {
            "from": from_addr,
            "subject": subject,
            "body": body,
            "message_id": message_id,
            "headers": headers
        }
    return _make_email


@pytest.fixture
def requires_tmux():
    """条件跳过：检查 tmux 是否可用"""
    import shutil
    if not shutil.which("tmux"):
        pytest.skip("tmux 未安装，跳过需要 tmux 的测试")
