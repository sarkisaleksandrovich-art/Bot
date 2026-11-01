from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


class ConfigError(Exception):
    ...


_MISSING = object()


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any]
    path: Path

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """
        Возвращает значение по пути вида 'motion.stable_frames'.
        Если ключ отсутствует и default не передан, возвращает None.
        Исключения:
            - ConfigError: если dotted_key пустой или структура не словарь там, где ожидается.
        """
        if not dotted_key or not dotted_key.strip():
            raise ConfigError("dotted_key must be a non-empty string")

        current: Any = self.raw
        for part in dotted_key.split("."):
            if not isinstance(current, Mapping):
                raise ConfigError(
                    f"expected mapping while traversing '{dotted_key}', got {type(current).__name__}"
                )
            if part not in current:
                return default
            current = current[part]
        return current


_REQUIRED_TOP_LEVEL_SECTIONS: tuple[str, ...] = (
    "app",
    "adb",
    "game",
    "motion",
    "matching",
    "timings",
    "teaching",
    "safety",
    "storage",
    "profiles",
    "debug",
)


_REQUIRED_KEYS: dict[str, type | tuple[type, ...]] = {
    "app.name": str,
    "app.log_level": str,
    "app.data_dir": str,
    "app.profiles_dir": str,
    "app.screenshots_dir": str,
    "app.temp_dir": str,
    "adb.serial": str,
    "adb.adb_path": str,
    "adb.exec_timeout_s": (int, float),
    "adb.connect_on_start": bool,
    "adb.device_resolution_cache": bool,
    "game.name": str,
    "game.team_size": int,
    "game.locations_count": int,
    "game.floors_per_location": int,
    "game.random_subfloors_per_level": int,
    "game.enter_all_doors": bool,
    "game.clear_room_immediately": bool,
    "game.skip_events_by_exit": bool,
    "game.never_return_to_opened_rooms": bool,
    "game.auto_battle_only": bool,
    "game.has_energy_system": bool,
    "game.use_next_floor_template_only": bool,
    "motion.stable_frames": int,
    "motion.stable_diff_threshold": (int, float),
    "motion.stable_poll_ms": int,
    "motion.stable_timeout_s": (int, float),
    "motion.max_stable_retries": int,
    "matching.method": str,
    "matching.default_threshold": (int, float),
    "matching.classify_threshold": (int, float),
    "matching.use_profile_rois": bool,
    "timings.tap_after_screenshot_delay_ms": int,
    "timings.post_tap_delay_ms": int,
    "timings.post_back_delay_ms": int,
    "timings.after_room_enter_delay_ms": int,
    "timings.after_exit_delay_ms": int,
    "timings.next_floor_delay_ms": int,
    "timings.auto_battle_check_interval_ms": int,
    "timings.battle_timeout_s": (int, float),
    "teaching.enabled": bool,
    "teaching.pause_hotkey": str,
    "teaching.allow_teach_on_unstable_timeout": bool,
    "teaching.unstable_screen_label": str,
    "teaching.save_crops_as_templates": bool,
    "teaching.save_fullscreen_debug_copy": bool,
    "safety.stop_on_danger": bool,
    "safety.forbid_purchases": bool,
    "safety.forbid_ads": bool,
    "safety.forbid_pvp": bool,
    "safety.min_action_interval_ms": int,
    "storage.rules_file": str,
    "storage.features_file": str,
    "storage.progress_file": str,
    "storage.floor_state_dir": str,
    "storage.knowledge_dir": str,
    "storage.backups_enabled": bool,
    "storage.atomic_write": bool,
    "profiles.active": str,
    "profiles.available": list,
    "debug.save_raw_screenshots": bool,
    "debug.save_stability_samples": bool,
    "debug.save_match_visualization": bool,
    "debug.dry_run": bool,
}


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"section '{name}' must be a mapping")
    return value


def _require_type(config: Config, key: str, expected_type: type | tuple[type, ...]) -> Any:
    value = config.get(key, _MISSING)
    if value is _MISSING:
        raise ConfigError(f"missing required config key: {key}")
    if not isinstance(value, expected_type):
        if expected_type == (int, float) and isinstance(value, bool):
            raise ConfigError(f"config key '{key}' must be numeric, got bool")
        expected_name = (
            ", ".join(t.__name__ for t in expected_type)
            if isinstance(expected_type, tuple)
            else expected_type.__name__
        )
        raise ConfigError(
            f"config key '{key}' must be of type {expected_name}, got {type(value).__name__}"
        )
    return value


def _validate_non_negative_int(name: str, value: int) -> None:
    if value < 0:
        raise ConfigError(f"config key '{name}' must be >= 0")


def _validate_positive_number(name: str, value: int | float) -> None:
    if value <= 0:
        raise ConfigError(f"config key '{name}' must be > 0")


def _validate_probability_like(name: str, value: int | float) -> None:
    if not 0 <= float(value) <= 1:
        raise ConfigError(f"config key '{name}' must be in range [0, 1]")


def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def load_config(path: str | Path) -> Config:
    """
    Загружает YAML-конфиг.
    Возвращает:
        Config
    Исключения:
        - FileNotFoundError: файл не найден
        - ConfigError: YAML некорректен или отсутствуют обязательные секции
    """
    config_path = Path(path).expanduser()
    if not config_path.exists():
        raise FileNotFoundError(config_path)

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in config '{config_path}': {exc}") from exc

    if data is None:
        raise ConfigError("config file is empty")
    if not isinstance(data, dict):
        raise ConfigError("top-level YAML structure must be a mapping")

    config = Config(raw=data, path=config_path.resolve())
    validate_config(config)
    return config


def validate_config(config: Config) -> None:
    """
    Проверяет обязательные секции и типы ключевых параметров.
    Возвращает:
        None
    Исключения:
        - ConfigError: при неверных типах/значениях
    """
    if not isinstance(config.raw, dict):
        raise ConfigError("config.raw must be a dict")

    for section in _REQUIRED_TOP_LEVEL_SECTIONS:
        if section not in config.raw:
            raise ConfigError(f"missing required config section: {section}")
        _require_mapping(config.raw[section], section)

    ldplayer = config.raw.get("ldplayer")
    if ldplayer is not None:
        _require_mapping(ldplayer, "ldplayer")

    for key, expected_type in _REQUIRED_KEYS.items():
        _require_type(config, key, expected_type)

    available = config.get("profiles.available")
    if not all(isinstance(item, str) and item for item in available):
        raise ConfigError("config key 'profiles.available' must be a non-empty list of strings")
    if config.get("profiles.active") not in available:
        raise ConfigError("config key 'profiles.active' must be present in 'profiles.available'")

    for key in (
        "adb.exec_timeout_s",
        "motion.stable_timeout_s",
        "timings.battle_timeout_s",
    ):
        _validate_positive_number(key, _require_type(config, key, (int, float)))

    for key in (
        "game.team_size",
        "game.locations_count",
        "game.floors_per_location",
        "game.random_subfloors_per_level",
        "motion.stable_frames",
        "motion.stable_poll_ms",
        "motion.max_stable_retries",
        "timings.tap_after_screenshot_delay_ms",
        "timings.post_tap_delay_ms",
        "timings.post_back_delay_ms",
        "timings.after_room_enter_delay_ms",
        "timings.after_exit_delay_ms",
        "timings.next_floor_delay_ms",
        "timings.auto_battle_check_interval_ms",
        "safety.min_action_interval_ms",
    ):
        _validate_non_negative_int(key, _require_type(config, key, int))

    if config.get("game.team_size") <= 0:
        raise ConfigError("config key 'game.team_size' must be > 0")
    if config.get("game.locations_count") <= 0:
        raise ConfigError("config key 'game.locations_count' must be > 0")
    if config.get("game.floors_per_location") <= 0:
        raise ConfigError("config key 'game.floors_per_location' must be > 0")
    if config.get("game.random_subfloors_per_level") <= 0:
        raise ConfigError("config key 'game.random_subfloors_per_level' must be > 0")
    if config.get("motion.stable_frames") <= 0:
        raise ConfigError("config key 'motion.stable_frames' must be > 0")

    _validate_probability_like(
        "motion.stable_diff_threshold", _require_type(config, "motion.stable_diff_threshold", (int, float))
    )
    _validate_probability_like(
        "matching.default_threshold", _require_type(config, "matching.default_threshold", (int, float))
    )
    _validate_probability_like(
        "matching.classify_threshold", _require_type(config, "matching.classify_threshold", (int, float))
    )

    downscale_size = config.get("motion.downscale_size", _MISSING)
    if downscale_size is _MISSING:
        raise ConfigError("missing required config key: motion.downscale_size")
    if not isinstance(downscale_size, (list, tuple)) or len(downscale_size) != 2:
        raise ConfigError("config key 'motion.downscale_size' must be a list/tuple of two integers")
    if not all(isinstance(item, int) and item > 0 for item in downscale_size):
        raise ConfigError("config key 'motion.downscale_size' must contain positive integers")

    log_level = config.get("app.log_level")
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError("config key 'app.log_level' must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")

    for key in (
        "app.data_dir",
        "app.profiles_dir",
        "app.screenshots_dir",
        "app.temp_dir",
        "storage.rules_file",
        "storage.features_file",
        "storage.progress_file",
        "storage.floor_state_dir",
        "storage.knowledge_dir",
    ):
        value = _require_type(config, key, str)
        if not value.strip():
            raise ConfigError(f"config key '{key}' must be a non-empty path string")


def ensure_directories(config: Config) -> None:
    """
    Создаёт необходимые директории из конфига.
    Возвращает:
        None
    Исключения:
        - OSError: ошибки файловой системы
        - ConfigError: если путь невалиден
    """
    base_dir = config.path.parent

    dir_keys = (
        "app.data_dir",
        "app.profiles_dir",
        "app.screenshots_dir",
        "app.temp_dir",
        "storage.floor_state_dir",
        "storage.knowledge_dir",
    )
    file_keys = (
        "storage.rules_file",
        "storage.features_file",
        "storage.progress_file",
    )

    for key in dir_keys:
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"config key '{key}' must be a non-empty path string")
        target = _resolve_path(base_dir, value)
        target.mkdir(parents=True, exist_ok=True)

    for key in file_keys:
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"config key '{key}' must be a non-empty path string")
        target = _resolve_path(base_dir, value)
        target.parent.mkdir(parents=True, exist_ok=True)
