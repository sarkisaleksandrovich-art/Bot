from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

import yaml


@dataclass(frozen=True)
class ROI:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class MotionConfig:
    stable_frames: int
    stable_diff_threshold: float
    stable_poll_ms: int
    stable_timeout_s: float
    max_stable_retries: int
    downscale_size: Tuple[int, int]


@dataclass(frozen=True)
class ProfileConfig:
    name: str
    rois: Dict[str, ROI]
    actions: Dict[str, Point]
    timings: Dict[str, float]


@dataclass(frozen=True)
class AppConfig:
    raw: Dict[str, Any]
    path: str

    data_dir: str
    captures_dir: str
    temp_dir: str

    adb_device_serial: str
    adb_command_timeout_s: float
    adb_connect_timeout_s: float
    adb_path: str

    post_tap_delay_ms: int
    post_swipe_delay_ms: int
    post_keyevent_delay_ms: int

    expected_width: int
    expected_height: int
    capture_format: str
    rotation: int

    motion: MotionConfig

    matching_default_threshold: float
    matching_classify_threshold: float
    matching_method: str
    matching_grayscale: bool

    teacher_pause_hotkey: str
    knowledge_dir: str
    unknown_dir: str
    save_unknown_frames: bool

    block_purchases: bool
    block_ads: bool
    block_pvp: bool
    stop_on_danger: bool
    enforce_safe_actions: bool

    active_profile_name: str
    profiles: Dict[str, ProfileConfig]

    progress_file: str
    rules_file: str
    features_file: str

    state_dir: str
    floor_state_file_pattern: str

    default_floor_from: int
    default_floor_to: int

    enter_all_doors: bool
    skip_events_by_exit: bool
    revisit_open_rooms: bool
    max_attempts_per_door: int

    log_level: str
    save_every_capture: bool
    save_matched_frames: bool
    annotate_matches: bool


def _require_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return value


def _require_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a bool")
    return value


def _require_int(value: Any, field_name: str, *, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an int")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return value


def _require_float(value: Any, field_name: str, *, minimum: Optional[float] = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return result


def _require_list_tuple(value: Any, field_name: str, expected_len: int) -> Tuple[Any, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != expected_len:
        raise ValueError(f"{field_name} must be a sequence of length {expected_len}")
    return tuple(value)


def _parse_roi(value: Any, field_name: str) -> ROI:
    raw = _require_list_tuple(value, field_name, 4)
    x = _require_int(raw[0], f"{field_name}[0]")
    y = _require_int(raw[1], f"{field_name}[1]")
    w = _require_int(raw[2], f"{field_name}[2]", minimum=1)
    h = _require_int(raw[3], f"{field_name}[3]", minimum=1)
    return ROI(x=x, y=y, w=w, h=h)


def _parse_point(value: Any, field_name: str) -> Point:
    raw = _require_list_tuple(value, field_name, 2)
    x = _require_int(raw[0], f"{field_name}[0]")
    y = _require_int(raw[1], f"{field_name}[1]")
    return Point(x=x, y=y)


def _resolve_path(base_dir: Path, value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve())


def _parse_motion(section: Mapping[str, Any]) -> MotionConfig:
    downscale_raw = _require_list_tuple(section.get("downscale_size"), "motion.downscale_size", 2)
    downscale_size = (
        _require_int(downscale_raw[0], "motion.downscale_size[0]", minimum=1),
        _require_int(downscale_raw[1], "motion.downscale_size[1]", minimum=1),
    )
    return MotionConfig(
        stable_frames=_require_int(section.get("stable_frames"), "motion.stable_frames", minimum=1),
        stable_diff_threshold=_require_float(
            section.get("stable_diff_threshold"),
            "motion.stable_diff_threshold",
            minimum=0.0,
        ),
        stable_poll_ms=_require_int(section.get("stable_poll_ms"), "motion.stable_poll_ms", minimum=1),
        stable_timeout_s=_require_float(section.get("stable_timeout_s"), "motion.stable_timeout_s", minimum=0.0),
        max_stable_retries=_require_int(
            section.get("max_stable_retries"),
            "motion.max_stable_retries",
            minimum=0,
        ),
        downscale_size=downscale_size,
    )


def _parse_profiles(section: Mapping[str, Any]) -> Tuple[str, Dict[str, ProfileConfig]]:
    active = _require_str(section.get("active"), "profiles.active")
    items = _require_mapping(section.get("items"), "profiles.items")
    profiles: Dict[str, ProfileConfig] = {}

    for profile_name, profile_value in items.items():
        if not isinstance(profile_name, str) or not profile_name:
            raise ValueError("profiles.items keys must be non-empty strings")
        profile_map = _require_mapping(profile_value, f"profiles.items.{profile_name}")
        rois_map = _require_mapping(profile_map.get("rois"), f"profiles.items.{profile_name}.rois")
        actions_map = _require_mapping(profile_map.get("actions"), f"profiles.items.{profile_name}.actions")
        timings_map = _require_mapping(profile_map.get("timings"), f"profiles.items.{profile_name}.timings")

        rois: Dict[str, ROI] = {
            key: _parse_roi(value, f"profiles.items.{profile_name}.rois.{key}")
            for key, value in rois_map.items()
        }
        actions: Dict[str, Point] = {
            key: _parse_point(value, f"profiles.items.{profile_name}.actions.{key}")
            for key, value in actions_map.items()
        }
        timings: Dict[str, float] = {
            str(key): _require_float(value, f"profiles.items.{profile_name}.timings.{key}", minimum=0.0)
            for key, value in timings_map.items()
        }

        profiles[profile_name] = ProfileConfig(
            name=_require_str(profile_map.get("name"), f"profiles.items.{profile_name}.name"),
            rois=rois,
            actions=actions,
            timings=timings,
        )

    if active not in profiles:
        raise ValueError(f"profiles.active references unknown profile: {active}")

    return active, profiles


def load_config(path: str) -> AppConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(path)

    with config_path.open("r", encoding="utf-8") as fh:
        raw_loaded = yaml.safe_load(fh)

    if raw_loaded is None:
        raise ValueError("config file is empty")

    raw = dict(_require_mapping(raw_loaded, "config"))
    base_dir = config_path.resolve().parent

    app = _require_mapping(raw.get("app"), "app")
    adb = _require_mapping(raw.get("adb"), "adb")
    screen = _require_mapping(raw.get("screen"), "screen")
    motion = _require_mapping(raw.get("motion"), "motion")
    matching = _require_mapping(raw.get("matching"), "matching")
    teacher = _require_mapping(raw.get("teacher"), "teacher")
    safety = _require_mapping(raw.get("safety"), "safety")
    profiles_section = _require_mapping(raw.get("profiles"), "profiles")
    progress = _require_mapping(raw.get("progress"), "progress")
    knowledge = _require_mapping(raw.get("knowledge"), "knowledge")
    state = _require_mapping(raw.get("state"), "state")
    exploration = _require_mapping(raw.get("exploration"), "exploration")
    debug = _require_mapping(raw.get("debug"), "debug")

    active_profile_name, profiles = _parse_profiles(profiles_section)

    capture_format = _require_str(screen.get("capture_format"), "screen.capture_format").lower()
    if capture_format not in {"png"}:
        raise ValueError("screen.capture_format must be 'png'")

    rotation = _require_int(screen.get("rotation"), "screen.rotation")
    if rotation not in {0, 90, 180, 270}:
        raise ValueError("screen.rotation must be one of 0, 90, 180, 270")

    log_level = _require_str(app.get("log_level"), "app.log_level").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("app.log_level must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")

    return AppConfig(
        raw=raw,
        path=str(config_path.resolve()),
        data_dir=_resolve_path(base_dir, _require_str(app.get("data_dir"), "app.data_dir")),
        captures_dir=_resolve_path(base_dir, _require_str(app.get("captures_dir"), "app.captures_dir")),
        temp_dir=_resolve_path(base_dir, _require_str(app.get("temp_dir"), "app.temp_dir")),
        adb_device_serial=_require_str(adb.get("device_serial"), "adb.device_serial"),
        adb_command_timeout_s=_require_float(adb.get("command_timeout_s"), "adb.command_timeout_s", minimum=0.0),
        adb_connect_timeout_s=_require_float(adb.get("connect_timeout_s"), "adb.connect_timeout_s", minimum=0.0),
        adb_path=str(adb.get("adb_path", "")) if adb.get("adb_path", "") is not None else "",
        post_tap_delay_ms=_require_int(adb.get("post_tap_delay_ms"), "adb.post_tap_delay_ms", minimum=0),
        post_swipe_delay_ms=_require_int(adb.get("post_swipe_delay_ms"), "adb.post_swipe_delay_ms", minimum=0),
        post_keyevent_delay_ms=_require_int(adb.get("post_keyevent_delay_ms"), "adb.post_keyevent_delay_ms", minimum=0),
        expected_width=_require_int(screen.get("expected_width"), "screen.expected_width", minimum=1),
        expected_height=_require_int(screen.get("expected_height"), "screen.expected_height", minimum=1),
        capture_format=capture_format,
        rotation=rotation,
        motion=_parse_motion(motion),
        matching_default_threshold=_require_float(
            matching.get("default_threshold"),
            "matching.default_threshold",
            minimum=0.0,
        ),
        matching_classify_threshold=_require_float(
            matching.get("classify_threshold"),
            "matching.classify_threshold",
            minimum=0.0,
        ),
        matching_method=_require_str(matching.get("method"), "matching.method"),
        matching_grayscale=_require_bool(matching.get("grayscale"), "matching.grayscale"),
        teacher_pause_hotkey=_require_str(teacher.get("pause_hotkey"), "teacher.pause_hotkey"),
        knowledge_dir=_resolve_path(base_dir, _require_str(teacher.get("knowledge_dir"), "teacher.knowledge_dir")),
        unknown_dir=_resolve_path(base_dir, _require_str(teacher.get("unknown_dir"), "teacher.unknown_dir")),
        save_unknown_frames=_require_bool(teacher.get("save_unknown_frames"), "teacher.save_unknown_frames"),
        block_purchases=_require_bool(safety.get("block_purchases"), "safety.block_purchases"),
        block_ads=_require_bool(safety.get("block_ads"), "safety.block_ads"),
        block_pvp=_require_bool(safety.get("block_pvp"), "safety.block_pvp"),
        stop_on_danger=_require_bool(safety.get("stop_on_danger"), "safety.stop_on_danger"),
        enforce_safe_actions=_require_bool(safety.get("enforce_safe_actions"), "safety.enforce_safe_actions"),
        active_profile_name=active_profile_name,
        profiles=profiles,
        progress_file=_resolve_path(base_dir, _require_str(progress.get("file"), "progress.file")),
        rules_file=_resolve_path(base_dir, _require_str(knowledge.get("rules_file"), "knowledge.rules_file")),
        features_file=_resolve_path(base_dir, _require_str(knowledge.get("features_file"), "knowledge.features_file")),
        state_dir=_resolve_path(base_dir, _require_str(state.get("dir"), "state.dir")),
        floor_state_file_pattern=_require_str(state.get("floor_state_file_pattern"), "state.floor_state_file_pattern"),
        default_floor_from=_require_int(exploration.get("default_floor_from"), "exploration.default_floor_from", minimum=1),
        default_floor_to=_require_int(exploration.get("default_floor_to"), "exploration.default_floor_to", minimum=1),
        enter_all_doors=_require_bool(exploration.get("enter_all_doors"), "exploration.enter_all_doors"),
        skip_events_by_exit=_require_bool(exploration.get("skip_events_by_exit"), "exploration.skip_events_by_exit"),
        revisit_open_rooms=_require_bool(exploration.get("revisit_open_rooms"), "exploration.revisit_open_rooms"),
        max_attempts_per_door=_require_int(
            exploration.get("max_attempts_per_door"),
            "exploration.max_attempts_per_door",
            minimum=1,
        ),
        log_level=log_level,
        save_every_capture=_require_bool(debug.get("save_every_capture"), "debug.save_every_capture"),
        save_matched_frames=_require_bool(debug.get("save_matched_frames"), "debug.save_matched_frames"),
        annotate_matches=_require_bool(debug.get("annotate_matches"), "debug.annotate_matches"),
    )


def get_profile(config: AppConfig, profile_name: Optional[str] = None) -> ProfileConfig:
    name = profile_name or config.active_profile_name
    try:
        return config.profiles[name]
    except KeyError as exc:
        raise KeyError(f"profile not found: {name}") from exc
