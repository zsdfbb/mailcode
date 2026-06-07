import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_file: Path = None, level: int = None):
    if logging.root.handlers:
        return logging.getLogger(__name__)

    if level is None:
        level = getattr(
            logging,
            os.environ.get("MAILCODE_LOG_LEVEL", "INFO").upper(),
            logging.INFO,
        )

    handlers = []

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file,
                encoding="utf-8",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
            )
        )

    # 错误级别输出到 stderr，让用户看到出问题了
    err_handler = logging.StreamHandler(sys.stderr)
    err_handler.setLevel(logging.ERROR)
    handlers.append(err_handler)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )

    return logging.getLogger(__name__)


def get_logger(name: str = "mailcode"):
    return logging.getLogger(name)