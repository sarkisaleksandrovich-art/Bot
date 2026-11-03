from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    pass


_REQUIRED_SECTIONS: tuple[str, ...] = (
    "app",
    "adb",
    "capture",
    "motion",
    "vision",
    "teacher",
    "knowledge",
    "profiles",
    "game",
    "safety",
    "timeouts",
    "input",
    "debug",
)

_REQUIRED_MOTION_FIELDS: tuple[str, ...] = (
    "stable_frames",
    "stable_diff_threshold",
    "stable_poll_ms",
    "stable_timeout_s",
    "max_stable_retries",
    "downscale_size",
)


@dataclass(frozen=True)
class BotConfig:
    raw: dict[str, Any]

    @property
    def base_dir(self) -> Path:
        source_path = self.raw.get("__source_path")
        if isinstance(source_path, str) and source_path:
            return Path(source_path).resolve().parent
        return Path.cwd()

    @property
    def data_dir(self) -> Path:
        return self.resolve_path("app", "data_dir")

    @property
    def log_dir(self) -> Path:
        return self.resolve_path("app", "log_dir")

    @property
    def debug_dir(self) -> Path:
        return self.resolve_path("vision", "debug_dir")

    @property
    def templates_dir(self) -> Path:
        return self.resolve_path("knowledge", "templates_dir")

    @property
    def profiles_root_dir(self) -> Path:
        return self.resolve_path("profiles", "root_dir")

    @property
    def rules_file_name(self) -> str:
        return str(get_path(self, "app", "rules_file"))

    @property
    def features_file_name(self) -> str:
        return str(get_path(self, "app", "features_file"))

    @property
    def progress_file_name(self) -> str:
        return str(get_path(self, "app", "progress_file"))

    @property
    def floor_state_file_name(self) -> str:
        return str(get_path(self, "app", "floor_state_file"))

    @property
    def log_level(self) -> str:
        return str(get_path(self, "app", "log_level")).upper()

    @property
    def default_match_threshold(self) -> float:
        return float(get_path(self, "vision", "default_match_threshold"))

    @property
    def min_roi_area(self) -> int:
        return int(get_path(self, "vision", "min_roi_area"))

    @property
    def motion(self) -> dict[str, Any]:
        return dict(get_path(self, "motion"))

    @property
    def stable_frames(self) -> int:
        return int(get_path(self, "motion", "stable_frames"))

    @property
    def stable_diff_threshold(self) -> float:
        return float(get_path(self, "motion", "stable_diff_threshold"))

    @property
    def stable_poll_ms(self) -> int:
        return int(get_path(self, "motion", "stable_poll_ms"))

    @property
    def stable_timeout_s(self) -> float:
        return float(get_path(self, "motion", "stable_timeout_s"))

    @property
    def max_stable_retries(self) -> int:
        return int(get_path(self, "motion", "max_stable_retries"))

    @property
    def downscale_size(self) -> tuple[int, int]:
        value = get_path(self, "motion", "downscale_size")
        width, height = _validate_int_pair(value, "motion.downscale_size")
        return width, height

    @property
    def min_template_size(self) -> tuple[int, int]:
        value = get_path(self, "knowledge", "min_template_size")
        width, height = _validate_int_pair(value, "knowledge.min_template_size")
        return width, height

    @property
    def max_template_size(self) -> tuple[int, int]:
        value = get_path(self, "knowledge", "max_template_size")
        width, height = _validate_int_pair(value, "knowledge.max_template_size")
        return width, height

    @property
    def categories(self) -> list[str]:
        categories = get_path(self, "knowledge", "categories")
        if not isinstance(categories, list):
            raise ConfigError("knowledge.categories must be a list[str]")
        return [str(item) for item in categories]

    def resolve_path(self, *keys: str) -> Path:
        value = get_path(self, *keys)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{'.'.join(keys)} must be a non-empty path string")
        path = Path(value)
        if not path.is_absolute():
            path = self.base_dir / path
        return path.resolve()


def load_config(path: str) -> BotConfig:
    """
    Загружает YAML-конфиг, валидирует обязательные секции и возвращает BotConfig.

    :param path: путь к config.yaml
    :return: BotConfig
    :raises FileNotFoundError: если файл не найден
    :raises ConfigError: если YAML повреждён или отсутствуют обязательные ключи
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(path)

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in config '{path}': {exc}") from exc

    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ConfigError("Root YAML object must be a mapping")

    data = dict(loaded)
    data["__source_path"] = str(config_path.resolve())
    validate_config(data)
    return BotConfig(raw=data)


def validate_config(data: dict[str, Any]) -> None:
    """
    Проверяет структуру конфига.

    Обязательные секции:
      app, adb, capture, motion, vision, teacher, knowledge, profiles, game, safety, timeouts, input, debug

    Обязательные поля motion:
      stable_frames, stable_diff_threshold, stable_poll_ms, stable_timeout_s, max_stable_retries, downscale_size

    :param data: словарь конфига
    :raises ConfigError: если структура невалидна
    """
    if not isinstance(data, dict):
        raise ConfigError("Config must be a dict")

    for section in _REQUIRED_SECTIONS:
        if section not in data:
            raise ConfigError(f"Missing required section: {section}")
        if not isinstance(data[section], dict):
            raise ConfigError(f"Section '{section}' must be a mapping")

    motion = data["motion"]
    for field in _REQUIRED_MOTION_FIELDS:
        if field not in motion:
            raise ConfigError(f"Missing required motion field: {field}")

    _require_non_empty_string(data, "app", "data_dir")
    _require_non_empty_string(data, "app", "log_dir")
    _require_non_empty_string(data, "app", "log_level")
    _require_non_empty_string(data, "app", "rules_file")
    _require_non_empty_string(data, "app", "features_file")
    _require_non_empty_string(data, "app", "progress_file")
    _require_non_empty_string(data, "app", "floor_state_file")

    _require_non_empty_string(data, "adb", "device_serial")
    _require_number(data, "adb", "command_timeout_s", minimum=0.0, allow_zero=False)
    _require_int(data, "adb", "post_input_delay_ms", minimum=0)
    _require_bool(data, "adb", "auto_connect")

    _require_non_empty_string(data, "capture", "method")
    _require_non_empty_string(data, "capture", "color_format")
    _require_int(data, "capture", "retry_count", minimum=0)
    _require_int(data, "capture", "retry_delay_ms", minimum=0)

    _require_int(data, "motion", "stable_frames", minimum=1)
    _require_number(data, "motion", "stable_diff_threshold", minimum=0.0)
    _require_int(data, "motion", "stable_poll_ms", minimum=1)
    _require_number(data, "motion", "stable_timeout_s", minimum=0.0, allow_zero=False)
    _require_int(data, "motion", "max_stable_retries", minimum=0)
    downscale = motion["downscale_size"]
    width, height = _validate_int_pair(downscale, "motion.downscale_size")
    if width <= 0 or height <= 0:
        raise ConfigError("motion.downscale_size values must be > 0")

    _require_number(data, "vision", "default_match_threshold", minimum=0.0, maximum=1.0)
    _require_non_empty_string(data, "vision", "template_method")
    _require_bool(data, "vision", "save_debug_images")
    _require_non_empty_string(data, "vision", "debug_dir")
    _require_int(data, "vision", "min_roi_area", minimum=1)

    _require_non_empty_string(data, "teacher", "pause_hotkey")
    _require_non_empty_string(data, "teacher", "window_name")
    _require_number(data, "teacher", "preview_scale", minimum=0.0, allow_zero=False)
    _require_bool(data, "teacher", "allow_cancel")
    _require_non_empty_string(data, "teacher", "unknown_stable_prompt")
    _require_non_empty_string(data, "teacher", "unknown_unstable_prompt")

    _require_non_empty_string(data, "knowledge", "templates_dir")
    _require_non_empty_string(data, "knowledge", "profiles_dir")
    _require_bool(data, "knowledge", "store_template_metadata")
    min_tpl = _validate_int_pair(get_path(BotConfig(data), "knowledge", "min_template_size"), "knowledge.min_template_size")
    max_tpl = _validate_int_pair(get_path(BotConfig(data), "knowledge", "max_template_size"), "knowledge.max_template_size")
    if min_tpl[0] <= 0 or min_tpl[1] <= 0:
        raise ConfigError("knowledge.min_template_size values must be > 0")
    if max_tpl[0] <= 0 or max_tpl[1] <= 0:
        raise ConfigError("knowledge.max_template_size values must be > 0")
    if min_tpl[0] > max_tpl[0] or min_tpl[1] > max_tpl[1]:
        raise ConfigError("knowledge.min_template_size must be <= knowledge.max_template_size")
    categories = data["knowledge"].get("categories")
    if not isinstance(categories, list) or not categories:
        raise ConfigError("knowledge.categories must be a non-empty list[str]")
    normalized_categories = []
    for item in categories:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError("knowledge.categories must contain non-empty strings")
        normalized_categories.append(item)
    if len(set(normalized_categories)) != len(normalized_categories):
        raise ConfigError("knowledge.categories must not contain duplicates")

    _require_non_empty_string(data, "profiles", "default_profile")
    _require_non_empty_string(data, "profiles", "root_dir")
    _validate_profiles_items(data["profiles"], data["game"]["floors_per_level"])

    _require_int(data, "game", "party_size", minimum=1)
    _require_int(data, "game", "locations_count", minimum=1)
    _require_int(data, "game", "levels_per_location", minimum=1)
    _require_int(data, "game", "floors_per_level", minimum=1)
    _require_bool(data, "game", "go_all_doors_before_next_floor")
    _require_bool(data, "game", "skip_events_by_exit")
    _require_bool(data, "game", "clear_room_immediately")
    _require_bool(data, "game", "revisit_open_rooms")

    _require_bool(data, "safety", "block_shop")
    _require_bool(data, "safety", "block_ads")
    _require_bool(data, "safety", "block_pvp")
    _require_bool(data, "safety", "stop_on_danger")
    _require_int(data, "safety", "max_unknown_stable_screens", minimum=0)
    _require_int(data, "safety", "max_io_errors", minimum=0)

    _require_number(data, "timeouts", "battle_start_timeout_s", minimum=0.0, allow_zero=False)
    _require_number(data, "timeouts", "battle_finish_timeout_s", minimum=0.0, allow_zero=False)
    _require_number(data, "timeouts", "room_enter_timeout_s", minimum=0.0, allow_zero=False)
    _require_number(data, "timeouts", "room_exit_timeout_s", minimum=0.0, allow_zero=False)
    _require_number(data, "timeouts", "next_floor_timeout_s", minimum=0.0, allow_zero=False)

    _require_int(data, "input", "swipe_duration_ms", minimum=0)
    _require_int(data, "input", "tap_jitter_px", minimum=0)
    _require_bool(data, "input", "clamp_to_screen")

    _require_bool(data, "debug", "save_last_frame_on_error")
    _require_bool(data, "debug", "verbose_vision")


def _validate_profiles_items(profiles: dict[str, Any], max_floor: Any) -> None:
    entries = profiles.get("items")
    if entries is None:
        return
    if not isinstance(entries, list):
        raise ConfigError("profiles.items must be a list")
    if len(entries) != 7:
        raise ConfigError("profiles.items must contain exactly 7 profiles")

    if isinstance(max_floor, bool) or not isinstance(max_floor, int) or max_floor < 1:
        raise ConfigError("game.floors_per_level must be int >= 1")

    seen: set[str] = set()
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ConfigError(f"profiles.items[{index}] must be a mapping")

        profile_id = item.get("id", f"loc{index + 1}")
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ConfigError(f"profiles.items[{index}].id must be a non-empty string")
        profile_id = profile_id.strip()
        if profile_id in seen:
            raise ConfigError(f"Duplicate profile id '{profile_id}' in profiles.items")
        seen.add(profile_id)

        name = item.get("name", profile_id)
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"profiles.items[{index}].name must be a non-empty string")

        floor_from = item.get("floor_from", 1)
        floor_to = item.get("floor_to", max_floor)
        run_all_floors = item.get("run_all_floors", False)
        if isinstance(floor_from, bool) or not isinstance(floor_from, int) or floor_from < 1:
            raise ConfigError(f"profiles.items[{index}].floor_from must be int >= 1")
        if isinstance(floor_to, bool) or not isinstance(floor_to, int) or floor_to < 1:
            raise ConfigError(f"profiles.items[{index}].floor_to must be int >= 1")
        if not isinstance(run_all_floors, bool):
            raise ConfigError(f"profiles.items[{index}].run_all_floors must be bool")
        if floor_from > floor_to or floor_from > max_floor or floor_to > max_floor:
            raise ConfigError(
                f"Floor range must be within 1..{max_floor}, got {floor_from}..{floor_to}"
            )


def get_path(config: BotConfig, *keys: str) -> Any:
    """
    Достаёт вложенное значение из config.raw по цепочке ключей.

    :param config: BotConfig
    :param keys: последовательность ключей
    :return: значение
    :raises KeyError: если путь отсутствует
    """
    current: Any = config.raw
    walked: list[str] = []
    for key in keys:
        walked.append(key)
        if not isinstance(current, dict) or key not in current:
            raise KeyError(".".join(walked))
        current = current[key]
    return current


def _require_non_empty_string(data: dict[str, Any], section: str, key: str) -> None:
    value = data[section].get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{section}.{key} must be a non-empty string")


def _require_bool(data: dict[str, Any], section: str, key: str) -> None:
    value = data[section].get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"{section}.{key} must be a bool")


def _require_int(data: dict[str, Any], section: str, key: str, minimum: int | None = None) -> None:
    value = data[section].get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{section}.{key} must be an int")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{section}.{key} must be >= {minimum}")


def _require_number(
    data: dict[str, Any],
    section: str,
    key: str,
    minimum: float | None = None,
    maximum: float | None = None,
    allow_zero: bool = True,
) -> None:
    value = data[section].get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{section}.{key} must be a number")
    number = float(value)
    if not allow_zero and number == 0.0:
        raise ConfigError(f"{section}.{key} must be > 0")
    if minimum is not None and number < minimum:
        raise ConfigError(f"{section}.{key} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise ConfigError(f"{section}.{key} must be <= {maximum}")


def _validate_int_pair(value: Any, field_name: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ConfigError(f"{field_name} must be a list/tuple of two integers")
    first, second = value
    if isinstance(first, bool) or not isinstance(first, int):
        raise ConfigError(f"{field_name}[0] must be an int")
    if isinstance(second, bool) or not isinstance(second, int):
        raise ConfigError(f"{field_name}[1] must be an int")
    return first, second
