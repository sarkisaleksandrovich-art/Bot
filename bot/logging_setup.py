from __future__ import annotations

import logging
from pathlib import Path

from .config import BotConfig, get_path


def setup_logging(config: "BotConfig") -> logging.Logger:
    """
    Настраивает root logger и возвращает логгер приложения.

    :param config: BotConfig
    :return: logging.Logger
    :raises OSError: если нельзя создать каталог/файл лога
    """
    log_dir = config.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "bot.log"
    level_name = config.log_level
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    logger_name = str(get_path(config, "app", "name")) if "name" in config.raw.get("app", {}) else "bot"
    app_logger = logging.getLogger(logger_name)
    app_logger.setLevel(level)
    app_logger.propagate = True
    app_logger.debug("Logging initialized: level=%s, file=%s", level_name, Path(log_file))
    return app_logger
