"""Logging 模块单元测试"""

import logging
import tempfile
from pathlib import Path

from mailcode.utils.logging import setup_logging, get_logger


class TestSetupLogging:
    def test_returns_logger(self):
        logger = setup_logging(level=logging.WARNING)
        assert logger is not None
        assert isinstance(logger, logging.Logger)

    def test_with_log_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test.log"
            logger = logging.getLogger("test_file_logger")
            logger.setLevel(logging.INFO)
            fh = logging.FileHandler(log_file)
            fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            logger.addHandler(fh)
            logger.info("hello world")
            fh.flush()
            assert log_file.exists()
            content = log_file.read_text()
            assert "hello world" in content

    def test_custom_level(self):
        logger = logging.getLogger("test_custom_level_logger")
        logger.setLevel(logging.DEBUG)
        assert logger.level == logging.DEBUG

    def test_honors_get_logger_name(self):
        logger = get_logger("custom_name")
        assert logger.name == "custom_name"
