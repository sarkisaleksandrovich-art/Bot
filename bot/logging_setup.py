from __future__ import annotations

import logging
from pathlib import Path

from bot.config import Config


def _resolve_path(config: Config, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (config.path.parent / path).resolve()
    return path


def setup_logging(config: Config) -> logging.Logger:
    """
    Настраивает корневой логгер приложения.
    Возвращает:
        logging.Logger
    Исключения:
        - ValueError: если log_level неизвестен
        - OSError: если невозможно создать лог-файлы/директории при реализации с файлами
    """
    log_level_name = str(config.get("app.log_level", "INFO")).upper()
    log_level = getattr(logging, log_level_name, None)
    if not isinstance(log_level, int):
        raise ValueError(f"unknown log level: {log_level_name}")

    app_name = str(config.get("app.name", "app"))
    temp_dir = _resolve_path(config, str(config.get("app.temp_dir")))
    temp_dir.mkdir(parents=True, exist_ok=True)
    log_file = temp_dir / f"{app_name}.log"

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    logger = logging.getLogger(app_name)
    logger.setLevel(log_level)
    logger.debug("Logging initialized")
    return logger
