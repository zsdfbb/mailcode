"""Integration tests 配置。

注册 pytest markers, 避免 UnknownMarkWarning。
"""


def pytest_configure(config):
    """注册自定义 markers。"""
    config.addinivalue_line(
        "markers",
        "real_agent: tests that call real agent CLIs (require MAILCODE_TEST_REAL_AGENT=1)",
    )
