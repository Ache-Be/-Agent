# _*_ coding : UTF-8 _*_
"""
统一日志配置：参考项目使用 loguru，这里使用标准 logging + 轮转，避免新依赖
"""
import logging
import logging.handlers
from .config import config

_LOGGER_NAME = "teaching-warning"
_INITIALIZED = False


def get_logger() -> logging.Logger:
    global _INITIALIZED
    logger = logging.getLogger(_LOGGER_NAME)
    if _INITIALIZED:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    # 控制台
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # 文件轮转
    try:
        log_file = config.log_dir / "teaching-warning.log"
        fh = logging.handlers.RotatingFileHandler(
            str(log_file),
            maxBytes=int(config.get("logging.max_bytes_mb", 10)) * 1024 * 1024,
            backupCount=int(config.get("logging.backup_count", 3)),
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass

    _INITIALIZED = True
    return logger


logger = get_logger()
