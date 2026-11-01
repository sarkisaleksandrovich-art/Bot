from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.config import Config, ConfigError
from bot.vision import Rect


class ProfileError(Exception):
    ...


class ProfileNotFoundError(ProfileError):
    ...


@dataclass(frozen=True)
class Profile:
    name: str
    screen_width: int | None
    screen_height: int | None
    rois: dict[str, Rect]
    thresholds: dict[str, float]


@dataclass(frozen=True)
class ProfileRunConfig:
    profile: str
    floor_from: int
    floor_to: int
    run_all_floors: bool


@dataclass(frozen=True)
class LocationPathStep:
    kind: str
    x: int
    y: int
    note: str = ""


@dataclass(frozen=True)
class NextFloorAction:
    x: int
    y: int
    note: str = ""


@dataclass(frozen=True)
class FloorTemplateEntry:
    floor: int
    image_path: str
    roi: Rect
    threshold: float
    swipe_attempts_from_top: int = 0


@dataclass(frozen=True)
class FloorSelectionMethod:
    mode: str
    next_floor_action: NextFloorAction | None
    floor_templates: dict[int, FloorTemplateEntry]
    list_roi: Rect | None
    swipe_start_x: int | None
    swipe_start_y: int | None
    swipe_end_x: int | None
    swipe_end_y: int | None
    swipe_duration_ms: int
    swipe_step_limit: int


@dataclass(frozen=True)
class LocationProfile:
    profile: Profile
    profile_id: str
    display_name: str
    knowledge_dir: Path
    rules_path: Path
    screens_dir: Path
    progress_path: Path
    path_to_location: list[LocationPathStep]
    floor_selection: FloorSelectionMethod
    run: ProfileRunConfig


DEFAULT_PROFILE_IDS: tuple[str, ...] = (
    "loc1",
    "loc2",
    "loc3",
    "loc4",
    "loc5",
    "loc6",
    "loc7",
)


_PROGRESS_VERSION = 1


def _config_dir(config: Config) -> Path:
    return config.path.parent.resolve()



def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path



def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        finally:
            pass
        raise



def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileError(f"{name} must be a mapping")
    return value



def _require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProfileError(f"{name} must be a list")
    return value



def _as_int(value: Any, name: str, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileError(f"{name} must be an integer")
    return value



def _as_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(f"{name} must be a number")
    return float(value)



def _as_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{name} must be a non-empty string")
    return value



def _as_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ProfileError(f"{name} must be a boolean")
    return value



def _parse_rect(data: Any, name: str) -> Rect:
    mapping = _require_mapping(data, name)
    x = _as_int(mapping.get("x"), f"{name}.x")
    y = _as_int(mapping.get("y"), f"{name}.y")
    w = _as_int(mapping.get("w"), f"{name}.w")
    h = _as_int(mapping.get("h"), f"{name}.h")
    assert x is not None and y is not None and w is not None and h is not None
    if w <= 0 or h <= 0:
        raise ProfileError(f"{name}.w and {name}.h must be > 0")
    return Rect(x=x, y=y, w=w, h=h)



def _default_profile_ids(config: Config) -> list[str]:
    game_locations_count = config.get("game.locations_count", 7)
    if isinstance(game_locations_count, int) and game_locations_count == 7:
        return list(DEFAULT_PROFILE_IDS)
    return [f"loc{i}" for i in range(1, 8)]



def _profiles_section(config: Config) -> dict[str, Any]:
    section = config.get("profiles")
    if not isinstance(section, dict):
        raise ProfileError("config section 'profiles' must be a mapping")
    return section



def _profiles_list(config: Config) -> list[dict[str, Any]]:
    section = _profiles_section(config)
    raw_profiles = section.get("list")
    if raw_profiles is None:
        default_ids = _default_profile_ids(config)
        generated: list[dict[str, Any]] = []
        for pid in default_ids:
            generated.append({"id": pid, "name": pid})
        return generated

    profiles = _require_list(raw_profiles, "profiles.list")
    if len(profiles) != 7:
        raise ProfileError("profiles.list must contain exactly 7 profiles")
    parsed: list[dict[str, Any]] = []
    for index, item in enumerate(profiles, start=1):
        mapping = _require_mapping(item, f"profiles.list[{index}]")
        pid = _as_str(mapping.get("id", f"loc{index}"), f"profiles.list[{index}].id")
        name = _as_str(mapping.get("name", pid), f"profiles.list[{index}].name")
        parsed.append({"id": pid, "name": name})
    return parsed



def list_profiles(config: Config) -> list[str]:
    """
    Возвращает список доступных профилей.
    Исключения:
        - ProfileError
    """
    return [item["id"] for item in _profiles_list(config)]



def _find_profile_entry(config: Config, profile_name: str) -> dict[str, Any]:
    for item in _profiles_list(config):
        if item["id"] == profile_name:
            return item
    raise ProfileNotFoundError(f"profile not found: {profile_name}")



def _parse_rois(raw: Any) -> dict[str, Rect]:
    if raw is None:
        return {}
    mapping = _require_mapping(raw, "rois")
    result: dict[str, Rect] = {}
    for category, rect_data in mapping.items():
        if not isinstance(category, str) or not category:
            raise ProfileError("rois keys must be non-empty strings")
        result[category] = _parse_rect(rect_data, f"rois.{category}")
    return result



def _parse_thresholds(raw: Any) -> dict[str, float]:
    if raw is None:
        return {}
    mapping = _require_mapping(raw, "thresholds")
    result: dict[str, float] = {}
    for category, value in mapping.items():
        if not isinstance(category, str) or not category:
            raise ProfileError("thresholds keys must be non-empty strings")
        result[category] = _as_float(value, f"thresholds.{category}")
    return result



def _profile_dirs(config: Config, profile_id: str) -> tuple[Path, Path, Path, Path, Path]:
    base_dir = _config_dir(config)
    knowledge_base = _resolve_path(base_dir, str(config.get("storage.knowledge_dir", "./data/knowledge")))
    profile_dir = knowledge_base / profile_id
    rules_path = profile_dir / "rules.json"
    screens_dir = profile_dir / "screens"
    progress_path = profile_dir / "progress.json"
    common_dir = knowledge_base / "common"
    return profile_dir, rules_path, screens_dir, progress_path, common_dir



def _parse_path_steps(raw: Any) -> list[LocationPathStep]:
    if raw is None:
        return []
    items = _require_list(raw, "path_to_location")
    steps: list[LocationPathStep] = []
    for index, item in enumerate(items, start=1):
        mapping = _require_mapping(item, f"path_to_location[{index}]")
        kind = _as_str(mapping.get("kind", "tap"), f"path_to_location[{index}].kind")
        x = _as_int(mapping.get("x"), f"path_to_location[{index}].x")
        y = _as_int(mapping.get("y"), f"path_to_location[{index}].y")
        note = str(mapping.get("note", ""))
        assert x is not None and y is not None
        steps.append(LocationPathStep(kind=kind, x=x, y=y, note=note))
    return steps



def _parse_next_floor_action(raw: Any) -> NextFloorAction | None:
    if raw is None:
        return None
    mapping = _require_mapping(raw, "floor_selection.next_floor_action")
    x = _as_int(mapping.get("x"), "floor_selection.next_floor_action.x")
    y = _as_int(mapping.get("y"), "floor_selection.next_floor_action.y")
    note = str(mapping.get("note", ""))
    assert x is not None and y is not None
    return NextFloorAction(x=x, y=y, note=note)



def _parse_floor_templates(raw: Any) -> dict[int, FloorTemplateEntry]:
    if raw is None:
        return {}
    items = _require_list(raw, "floor_selection.floor_templates")
    result: dict[int, FloorTemplateEntry] = {}
    for index, item in enumerate(items, start=1):
        mapping = _require_mapping(item, f"floor_selection.floor_templates[{index}]")
        floor = _as_int(mapping.get("floor"), f"floor_selection.floor_templates[{index}].floor")
        image_path = _as_str(
            mapping.get("image_path"), f"floor_selection.floor_templates[{index}].image_path"
        )
        roi = _parse_rect(mapping.get("roi"), f"floor_selection.floor_templates[{index}].roi")
        threshold = _as_float(
            mapping.get("threshold", 0.88), f"floor_selection.floor_templates[{index}].threshold"
        )
        swipe_attempts_from_top = _as_int(
            mapping.get("swipe_attempts_from_top", 0),
            f"floor_selection.floor_templates[{index}].swipe_attempts_from_top",
        )
        assert floor is not None and swipe_attempts_from_top is not None
        result[floor] = FloorTemplateEntry(
            floor=floor,
            image_path=image_path,
            roi=roi,
            threshold=threshold,
            swipe_attempts_from_top=swipe_attempts_from_top,
        )
    return result



def _parse_floor_selection(raw: Any) -> FloorSelectionMethod:
    if raw is None:
        return FloorSelectionMethod(
            mode="untrained",
            next_floor_action=None,
            floor_templates={},
            list_roi=None,
            swipe_start_x=None,
            swipe_start_y=None,
            swipe_end_x=None,
            swipe_end_y=None,
            swipe_duration_ms=300,
            swipe_step_limit=0,
        )

    mapping = _require_mapping(raw, "floor_selection")
    mode = _as_str(mapping.get("mode", "untrained"), "floor_selection.mode")
    next_floor_action = _parse_next_floor_action(mapping.get("next_floor_action"))
    floor_templates = _parse_floor_templates(mapping.get("floor_templates"))
    list_roi = None
    if mapping.get("list_roi") is not None:
        list_roi = _parse_rect(mapping.get("list_roi"), "floor_selection.list_roi")

    swipe_start_x = _as_int(mapping.get("swipe_start_x"), "floor_selection.swipe_start_x", allow_none=True)
    swipe_start_y = _as_int(mapping.get("swipe_start_y"), "floor_selection.swipe_start_y", allow_none=True)
    swipe_end_x = _as_int(mapping.get("swipe_end_x"), "floor_selection.swipe_end_x", allow_none=True)
    swipe_end_y = _as_int(mapping.get("swipe_end_y"), "floor_selection.swipe_end_y", allow_none=True)
    swipe_duration_ms = _as_int(mapping.get("swipe_duration_ms", 300), "floor_selection.swipe_duration_ms")
    swipe_step_limit = _as_int(mapping.get("swipe_step_limit", 0), "floor_selection.swipe_step_limit")
    assert swipe_duration_ms is not None and swipe_step_limit is not None

    if mode not in {"untrained", "next_floor_button", "floor_list"}:
        raise ProfileError(
            "floor_selection.mode must be one of: untrained, next_floor_button, floor_list"
        )

    return FloorSelectionMethod(
        mode=mode,
        next_floor_action=next_floor_action,
        floor_templates=floor_templates,
        list_roi=list_roi,
        swipe_start_x=swipe_start_x,
        swipe_start_y=swipe_start_y,
        swipe_end_x=swipe_end_x,
        swipe_end_y=swipe_end_y,
        swipe_duration_ms=swipe_duration_ms,
        swipe_step_limit=swipe_step_limit,
    )



def _parse_run_config(config: Config, profile_id: str) -> ProfileRunConfig:
    profiles_section = _profiles_section(config)
    raw_ranges = profiles_section.get("ranges", {})
    ranges = _require_mapping(raw_ranges, "profiles.ranges") if raw_ranges is not None else {}
    raw_entry = ranges.get(profile_id, {})
    entry = _require_mapping(raw_entry, f"profiles.ranges.{profile_id}") if raw_entry is not None else {}

    floor_from = _as_int(entry.get("floor_from", 1), f"profiles.ranges.{profile_id}.floor_from")
    floor_to = _as_int(entry.get("floor_to", floor_from), f"profiles.ranges.{profile_id}.floor_to")
    run_all_floors = _as_bool(
        entry.get("run_all_floors", False), f"profiles.ranges.{profile_id}.run_all_floors"
    )
    assert floor_from is not None and floor_to is not None
    if floor_from <= 0 or floor_to <= 0:
        raise ProfileError(f"profiles.ranges.{profile_id} floors must be > 0")
    if floor_from > floor_to:
        raise ProfileError(f"profiles.ranges.{profile_id}.floor_from must be <= floor_to")

    return ProfileRunConfig(
        profile=profile_id,
        floor_from=floor_from,
        floor_to=floor_to,
        run_all_floors=run_all_floors,
    )



def load_profile(config: Config, profile_name: str) -> Profile:
    """
    Загружает профиль.
    Источник допустим либо из config, либо из файлов в profiles_dir,
    но сигнатура должна остаться этой.

    Возвращает:
        Profile

    Исключения:
        - ProfileNotFoundError
        - ProfileError
    """
    if not profile_name or not profile_name.strip():
        raise ProfileNotFoundError("profile name is empty")

    entry = _find_profile_entry(config, profile_name)
    profiles_section = _profiles_section(config)
    details = profiles_section.get("details", {})
    details_map = _require_mapping(details, "profiles.details") if details is not None else {}
    detail_entry = details_map.get(profile_name, {})
    detail = _require_mapping(detail_entry, f"profiles.details.{profile_name}") if detail_entry is not None else {}

    screen_width = _as_int(detail.get("screen_width"), f"profiles.details.{profile_name}.screen_width", allow_none=True)
    screen_height = _as_int(
        detail.get("screen_height"), f"profiles.details.{profile_name}.screen_height", allow_none=True
    )
    rois = _parse_rois(detail.get("rois"))
    thresholds = _parse_thresholds(detail.get("thresholds"))

    return Profile(
        name=entry["id"],
        screen_width=screen_width,
        screen_height=screen_height,
        rois=rois,
        thresholds=thresholds,
    )



def roi_for(profile: Profile, category: str) -> Rect | None:
    """
    Возвращает ROI для категории, если определён.
    """
    return profile.rois.get(category)



def load_location_profile(config: Config, profile_name: str) -> LocationProfile:
    """
    Загружает расширенный профиль локации.

    Публичный метод на русском:
    - читает базовый Profile через load_profile(...)
    - привязывает к нему отдельную папку знаний knowledge/<profile_id>/
    - подключает общую папку knowledge/common/
    - загружает обученный путь до локации и способ выбора этажа
    - загружает диапазон этажей из config.yaml

    Исключения:
        - ProfileNotFoundError
        - ProfileError
    """
    base_profile = load_profile(config, profile_name)
    profile_dir, rules_path, screens_dir, progress_path, _common_dir = _profile_dirs(config, profile_name)
    profiles_section = _profiles_section(config)
    details = profiles_section.get("details", {})
    details_map = _require_mapping(details, "profiles.details") if details is not None else {}
    detail_entry = details_map.get(profile_name, {})
    detail = _require_mapping(detail_entry, f"profiles.details.{profile_name}") if detail_entry is not None else {}

    path_to_location = _parse_path_steps(detail.get("path_to_location"))
    floor_selection = _parse_floor_selection(detail.get("floor_selection"))
    run = _parse_run_config(config, profile_name)

    profile_dir.mkdir(parents=True, exist_ok=True)
    screens_dir.mkdir(parents=True, exist_ok=True)

    return LocationProfile(
        profile=base_profile,
        profile_id=profile_name,
        display_name=detail.get("display_name", profile_name),
        knowledge_dir=profile_dir,
        rules_path=rules_path,
        screens_dir=screens_dir,
        progress_path=progress_path,
        path_to_location=path_to_location,
        floor_selection=floor_selection,
        run=run,
    )



def profile_rules_path(config: Config, profile_name: str) -> Path:
    _, rules_path, _, _, _ = _profile_dirs(config, profile_name)
    return rules_path



def profile_progress_path(config: Config, profile_name: str) -> Path:
    _, _, _, progress_path, _ = _profile_dirs(config, profile_name)
    return progress_path



def common_knowledge_dir(config: Config) -> Path:
    _, _, _, _, common_dir = _profile_dirs(config, "loc1")
    common_dir.mkdir(parents=True, exist_ok=True)
    return common_dir



def get_active_profile_name(config: Config, cli_profile: str | None = None) -> str:
    """
    Возвращает имя активного профиля.

    Публичный метод на русском:
    - если передан --profile, он имеет приоритет
    - иначе используется profiles.active из config.yaml
    - имя проверяется по списку из 7 профилей

    Исключения:
        - ProfileNotFoundError
        - ProfileError
        - ConfigError
    """
    candidate = cli_profile.strip() if isinstance(cli_profile, str) and cli_profile.strip() else None
    if candidate is None:
        configured = config.get("profiles.active")
        if not isinstance(configured, str) or not configured.strip():
            raise ConfigError("profiles.active must be a non-empty string")
        candidate = configured.strip()
    _find_profile_entry(config, candidate)
    return candidate



def progress_json_template(profile_name: str, floor_from: int, floor_to: int) -> dict[str, Any]:
    return {
        "version": _PROGRESS_VERSION,
        "profile": profile_name,
        "updated_at": "",
        "run_state": {
            "active": False,
            "mode": "teach",
            "started_at": "",
            "last_error": "",
        },
        "game_progress": {
            "current_location": None,
            "current_floor": None,
            "target_floor_from": floor_from,
            "target_floor_to": floor_to,
            "last_completed_floor": None,
        },
        "stats": {
            "floors_completed": 0,
            "rooms_cleared": 0,
            "rooms_skipped": 0,
            "rooms_failed": 0,
        },
    }



def read_progress(config: Config, profile_name: str) -> dict[str, Any]:
    """
    Читает progress.json профиля.

    Публичный метод на русском:
    - если файла ещё нет, создаёт структуру по умолчанию на основе диапазона из конфига
    - возвращает словарь прогресса
    - прогресс хранится отдельно для каждого профиля в knowledge/<profile_id>/progress.json

    Исключения:
        - ProfileError
        - OSError
        - json.JSONDecodeError
    """
    run = _parse_run_config(config, profile_name)
    path = profile_progress_path(config, profile_name)
    if not path.exists():
        return progress_json_template(profile_name, run.floor_from, run.floor_to)
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ProfileError(f"invalid progress.json for profile {profile_name}: root must be object")
    return data



def write_progress(config: Config, profile_name: str, progress: dict[str, Any]) -> None:
    """
    Записывает progress.json профиля атомарно.

    Публичный метод на русском:
    - сохраняет последний пройденный этаж и агрегированный прогресс
    - файл лежит в папке конкретного профиля
    - нужен для продолжения запуска после остановки

    Исключения:
        - ProfileError
        - OSError
    """
    _find_profile_entry(config, profile_name)
    if not isinstance(progress, dict):
        raise ProfileError("progress must be a dict")
    path = profile_progress_path(config, profile_name)
    _atomic_write_json(path, progress)



def get_last_completed_floor(config: Config, profile_name: str) -> int | None:
    """
    Возвращает последний пройденный этаж из progress.json.

    Публичный метод на русском:
    - читает game_progress.last_completed_floor
    - если значения нет, возвращает None
    """
    progress = read_progress(config, profile_name)
    game_progress = progress.get("game_progress", {})
    if not isinstance(game_progress, dict):
        return None
    value = game_progress.get("last_completed_floor")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileError("game_progress.last_completed_floor must be integer or null")
    return value



def set_last_completed_floor(config: Config, profile_name: str, floor_number: int) -> None:
    """
    Обновляет последний пройденный этаж в progress.json.

    Публичный метод на русском:
    - сохраняет номер последнего успешно завершённого этажа
    - используется для возобновления прохода
    - не позволяет записать номер этажа меньше 1

    Исключения:
        - ProfileError
        - OSError
    """
    if isinstance(floor_number, bool) or not isinstance(floor_number, int) or floor_number <= 0:
        raise ProfileError("floor_number must be an integer > 0")
    progress = read_progress(config, profile_name)
    game_progress = progress.setdefault("game_progress", {})
    if not isinstance(game_progress, dict):
        raise ProfileError("progress.game_progress must be a dict")
    game_progress["last_completed_floor"] = floor_number
    write_progress(config, profile_name, progress)



def resolve_floor_range(config: Config, profile_name: str) -> tuple[int, int, bool]:
    """
    Возвращает диапазон этажей профиля из config.yaml.

    Публичный метод на русском:
    - читает profile, floor_from, floor_to, run_all_floors из секции profiles.ranges
    - используется для остановки на floor_to
    - не выдумывает значения вне конфига
    """
    run = _parse_run_config(config, profile_name)
    return run.floor_from, run.floor_to, run.run_all_floors



def next_floor_to_run(config: Config, profile_name: str) -> int:
    """
    Вычисляет следующий этаж для запуска.

    Публичный метод на русском:
    - если прогресса ещё нет, возвращает floor_from
    - если есть last_completed_floor, возвращает следующий этаж
    - если достигнут floor_to, дальнейший запуск должен быть остановлен вызывающим кодом

    Исключения:
        - ProfileError
    """
    floor_from, floor_to, _run_all = resolve_floor_range(config, profile_name)
    last_completed = get_last_completed_floor(config, profile_name)
    if last_completed is None or last_completed < floor_from:
        return floor_from
    candidate = last_completed + 1
    if candidate > floor_to:
        return floor_to + 1
    return candidate



def should_stop_after_floor(config: Config, profile_name: str, floor_number: int) -> bool:
    """
    Проверяет, нужно ли остановиться по достижении floor_to.

    Публичный метод на русском:
    - возвращает True, если floor_number >= floor_to
    - используется внешним циклом прохода
    """
    _floor_from, floor_to, _run_all = resolve_floor_range(config, profile_name)
    return floor_number >= floor_to
