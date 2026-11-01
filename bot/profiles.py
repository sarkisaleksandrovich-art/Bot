from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class LocationProfile:
    """
    Расширенный профиль локации.

    Хранит:
    - идентификатор профиля из config.yaml;
    - отображаемое имя;
    - файловые пути его базы знаний;
    - обученный путь до локации из главного меню;
    - обученный способ выбора этажа;
    - путь к progress.json.
    """

    id: str
    name: str
    knowledge_dir: str
    rules_file: str
    screens_dir: str
    common_knowledge_dir: str
    common_rules_file: str
    route_file: str
    floor_selection_file: str
    progress_file: str
    floor_from: int
    floor_to: int
    run_all_floors: bool


@dataclass(frozen=True)
class ProfileKnowledgePaths:
    """Служебный набор путей к знаниям конкретного профиля."""

    knowledge_dir: str
    rules_file: str
    screens_dir: str
    route_file: str
    floor_selection_file: str
    progress_file: str


DEFAULT_PROFILE_IDS: Tuple[str, ...] = (
    "loc1",
    "loc2",
    "loc3",
    "loc4",
    "loc5",
    "loc6",
    "loc7",
)

DEFAULT_PROGRESS: Dict[str, Any] = {
    "version": 1,
    "profile": "",
    "started_at": "",
    "updated_at": "",
    "current_floor": 0,
    "floor_from": 0,
    "floor_to": 0,
    "status": "idle",
    "totals": {
        "floors_completed": 0,
        "rooms_cleared": 0,
        "rooms_skipped": 0,
        "rooms_failed": 0,
    },
    "last_result": {
        "floor_number": 0,
        "status": "",
        "reason": "",
    },
}


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")



def _ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)



def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    target = Path(path)
    _ensure_dir(str(target.parent))
    fd, tmp_path = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass



def _read_json(path: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    file_path = Path(path)
    if not file_path.is_file():
        return dict(default or {})
    with file_path.open("r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    if not isinstance(loaded, dict):
        raise ValueError(f"JSON object expected in file: {path}")
    return loaded



def _profile_root(config: Any, profile_id: str) -> str:
    knowledge_root = str(getattr(config, "knowledge_dir"))
    return str((Path(knowledge_root) / profile_id).resolve())



def _common_root(config: Any) -> str:
    knowledge_root = str(getattr(config, "knowledge_dir"))
    return str((Path(knowledge_root) / "common").resolve())



def _build_profile_paths(config: Any, profile_id: str) -> ProfileKnowledgePaths:
    root = Path(_profile_root(config, profile_id))
    return ProfileKnowledgePaths(
        knowledge_dir=str(root),
        rules_file=str((root / "rules.json").resolve()),
        screens_dir=str((root / "screens").resolve()),
        route_file=str((root / "route.json").resolve()),
        floor_selection_file=str((root / "floor_selection.json").resolve()),
        progress_file=str((root / "progress.json").resolve()),
    )



def _normalize_profile_items(raw_items: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(raw_items, dict):
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    for key, value in raw_items.items():
        if not isinstance(key, str) or not key:
            continue
        result[key] = value if isinstance(value, dict) else {}
    return result



def _extract_profiles_section(config: Any) -> Tuple[Optional[str], Dict[str, Dict[str, Any]]]:
    raw = getattr(config, "raw", {})
    if not isinstance(raw, dict):
        return None, {}
    profiles_section = raw.get("profiles")
    if not isinstance(profiles_section, dict):
        return None, {}

    active = profiles_section.get("active")
    if active is not None and not isinstance(active, str):
        active = None

    items = profiles_section.get("items")
    normalized = _normalize_profile_items(items)

    if normalized:
        return active, normalized

    auto_items: Dict[str, Dict[str, Any]] = {}
    for profile_id in DEFAULT_PROFILE_IDS:
        entry = profiles_section.get(profile_id)
        if isinstance(entry, dict):
            auto_items[profile_id] = entry

    return active, auto_items



def _get_profile_range_settings(config: Any, profile_id: str) -> Tuple[int, int, bool]:
    raw = getattr(config, "raw", {})
    if not isinstance(raw, dict):
        floor_from = int(getattr(config, "default_floor_from", 1))
        floor_to = int(getattr(config, "default_floor_to", floor_from))
        return floor_from, floor_to, True

    exploration = raw.get("exploration")
    default_floor_from = int(getattr(config, "default_floor_from", 1))
    default_floor_to = int(getattr(config, "default_floor_to", default_floor_from))
    run_all_default = True

    if isinstance(exploration, dict):
        if isinstance(exploration.get("default_floor_from"), int):
            default_floor_from = int(exploration["default_floor_from"])
        if isinstance(exploration.get("default_floor_to"), int):
            default_floor_to = int(exploration["default_floor_to"])

    profiles_run = raw.get("run")
    if isinstance(profiles_run, dict):
        per_profile = profiles_run.get("profiles")
        if isinstance(per_profile, dict):
            entry = per_profile.get(profile_id)
            if isinstance(entry, dict):
                floor_from = int(entry.get("floor_from", default_floor_from))
                floor_to = int(entry.get("floor_to", default_floor_to))
                run_all_floors = bool(entry.get("run_all_floors", run_all_default))
                return floor_from, floor_to, run_all_floors

    profile_entry = None
    _, items = _extract_profiles_section(config)
    if profile_id in items:
        profile_entry = items[profile_id]
    if isinstance(profile_entry, dict):
        floor_from = int(profile_entry.get("floor_from", default_floor_from))
        floor_to = int(profile_entry.get("floor_to", default_floor_to))
        run_all_floors = bool(profile_entry.get("run_all_floors", run_all_default))
        return floor_from, floor_to, run_all_floors

    return default_floor_from, default_floor_to, run_all_default



def _make_location_profile(config: Any, profile_id: str, profile_data: Dict[str, Any]) -> LocationProfile:
    paths = _build_profile_paths(config, profile_id)
    common_root = Path(_common_root(config))
    floor_from, floor_to, run_all_floors = _get_profile_range_settings(config, profile_id)

    _ensure_dir(paths.knowledge_dir)
    _ensure_dir(paths.screens_dir)
    _ensure_dir(str(common_root))
    common_screens_dir = common_root / "screens"
    _ensure_dir(str(common_screens_dir))

    return LocationProfile(
        id=profile_id,
        name=str(profile_data.get("name", profile_id)),
        knowledge_dir=paths.knowledge_dir,
        rules_file=paths.rules_file,
        screens_dir=paths.screens_dir,
        common_knowledge_dir=str(common_root.resolve()),
        common_rules_file=str((common_root / "rules.json").resolve()),
        route_file=paths.route_file,
        floor_selection_file=paths.floor_selection_file,
        progress_file=paths.progress_file,
        floor_from=floor_from,
        floor_to=floor_to,
        run_all_floors=run_all_floors,
    )



def list_profiles(config: Any) -> List[str]:
    """
    Возвращает список имён профилей.

    Публичный метод по контракту модуля.
    Если в config.yaml профили не заданы явно, создаёт стандартный список
    из 7 идентификаторов: loc1..loc7.
    """

    _, items = _extract_profiles_section(config)
    if items:
        names = list(items.keys())
        for default_id in DEFAULT_PROFILE_IDS:
            if default_id not in items:
                names.append(default_id)
        return names
    return list(DEFAULT_PROFILE_IDS)



def resolve_profile(config: Any, profile_name: Optional[str]) -> Any:
    """
    Возвращает профиль или активный профиль.

    Публичный метод по контракту модуля.
    Поддерживает два режима:
    - если конфиг уже содержит объект профиля в config.profiles, возвращает его;
    - если выбран один из 7 профилей локаций, возвращает LocationProfile.

    Исключения:
    - KeyError, если профиль не найден.
    """

    requested = profile_name
    if not requested:
        requested = getattr(config, "active_profile_name", None)

    active_from_raw, raw_items = _extract_profiles_section(config)
    if not requested:
        requested = active_from_raw or DEFAULT_PROFILE_IDS[0]

    config_profiles = getattr(config, "profiles", None)
    if isinstance(config_profiles, dict) and requested in config_profiles:
        return config_profiles[requested]

    allowed = set(list_profiles(config))
    if requested not in allowed:
        raise KeyError(f"profile not found: {requested}")

    profile_data = raw_items.get(requested, {"name": requested})
    return _make_location_profile(config, requested, profile_data)



def load_profile(config: Any, profile_name: Optional[str] = None) -> LocationProfile:
    """
    Загружает расширенный профиль локации.

    Публичный метод:
    - выбирает профиль по имени или активный;
    - создаёт каталоги knowledge/<profile_id>/ и knowledge/common/ при необходимости;
    - возвращает объект LocationProfile с путями к знаниям, route.json,
      floor_selection.json и progress.json.

    Исключения:
    - KeyError, если профиль не найден.
    """

    requested = profile_name
    if not requested:
        requested = getattr(config, "active_profile_name", None)
    if not requested:
        active_from_raw, _ = _extract_profiles_section(config)
        requested = active_from_raw or DEFAULT_PROFILE_IDS[0]

    _, raw_items = _extract_profiles_section(config)
    if requested not in set(list_profiles(config)):
        raise KeyError(f"profile not found: {requested}")

    profile_data = raw_items.get(requested, {"name": requested})
    return _make_location_profile(config, requested, profile_data)



def load_profile_by_cli(config: Any, cli_args: Any) -> LocationProfile:
    """
    Выбирает профиль из аргументов командной строки.

    Ожидается поле `cli_args.profile`, совместимое с `--profile loc3`.
    Если аргумент отсутствует, используется активный профиль.
    """

    profile_name = getattr(cli_args, "profile", None)
    return load_profile(config, profile_name)



def create_default_progress(profile: LocationProfile) -> Dict[str, Any]:
    """
    Создаёт структуру progress.json для профиля.

    Поля соответствуют архитектурному контракту общего прогресса,
    а файл хранится отдельно для каждого профиля: knowledge/<profile_id>/progress.json.
    """

    progress = json.loads(json.dumps(DEFAULT_PROGRESS))
    now = _utc_now_iso()
    progress["profile"] = profile.id
    progress["started_at"] = now
    progress["updated_at"] = now
    progress["current_floor"] = max(0, profile.floor_from - 1)
    progress["floor_from"] = profile.floor_from
    progress["floor_to"] = profile.floor_to
    progress["status"] = "idle"
    return progress



def read_progress(profile: LocationProfile) -> Dict[str, Any]:
    """
    Читает progress.json профиля.

    Если файл ещё не существует, создаёт структуру по умолчанию,
    сохраняет её и возвращает.
    """

    if not Path(profile.progress_file).is_file():
        progress = create_default_progress(profile)
        write_progress(profile, progress)
        return progress

    progress = _read_json(profile.progress_file, default=create_default_progress(profile))
    progress.setdefault("version", 1)
    progress.setdefault("profile", profile.id)
    progress.setdefault("started_at", "")
    progress.setdefault("updated_at", _utc_now_iso())
    progress.setdefault("current_floor", max(0, profile.floor_from - 1))
    progress.setdefault("floor_from", profile.floor_from)
    progress.setdefault("floor_to", profile.floor_to)
    progress.setdefault("status", "idle")
    progress.setdefault(
        "totals",
        {
            "floors_completed": 0,
            "rooms_cleared": 0,
            "rooms_skipped": 0,
            "rooms_failed": 0,
        },
    )
    progress.setdefault(
        "last_result",
        {
            "floor_number": 0,
            "status": "",
            "reason": "",
        },
    )
    return progress



def write_progress(profile: LocationProfile, progress: Dict[str, Any]) -> None:
    """
    Записывает progress.json профиля атомарно.

    Перед сохранением дополняет обязательные поля profile/floor_from/floor_to/updated_at.
    """

    progress["version"] = int(progress.get("version", 1))
    progress["profile"] = profile.id
    progress["floor_from"] = int(progress.get("floor_from", profile.floor_from))
    progress["floor_to"] = int(progress.get("floor_to", profile.floor_to))
    progress["updated_at"] = _utc_now_iso()
    progress.setdefault("started_at", progress["updated_at"])
    progress.setdefault("status", "idle")
    progress.setdefault(
        "totals",
        {
            "floors_completed": 0,
            "rooms_cleared": 0,
            "rooms_skipped": 0,
            "rooms_failed": 0,
        },
    )
    progress.setdefault(
        "last_result",
        {
            "floor_number": 0,
            "status": "",
            "reason": "",
        },
    )
    _atomic_write_json(profile.progress_file, progress)



def get_last_completed_floor(profile: LocationProfile) -> int:
    """
    Возвращает последний пройденный этаж из progress.json.

    Используется для продолжения после остановки.
    """

    progress = read_progress(profile)
    return int(progress.get("current_floor", max(0, profile.floor_from - 1)))



def set_last_completed_floor(
    profile: LocationProfile,
    floor_number: int,
    status: str = "running",
    reason: str = "",
) -> Dict[str, Any]:
    """
    Обновляет последний пройденный этаж в progress.json.

    Параметры:
    - floor_number: последний завершённый этаж;
    - status: текущее состояние прогресса;
    - reason: причина последнего результата.

    Возвращает обновлённую структуру progress.
    """

    progress = read_progress(profile)
    progress["current_floor"] = int(floor_number)
    progress["status"] = status
    progress["last_result"] = {
        "floor_number": int(floor_number),
        "status": status,
        "reason": reason,
    }
    completed = max(0, int(floor_number) - int(progress.get("floor_from", profile.floor_from)) + 1)
    progress.setdefault("totals", {})
    progress["totals"]["floors_completed"] = completed
    write_progress(profile, progress)
    return progress



def get_next_floor_to_run(profile: LocationProfile) -> Optional[int]:
    """
    Возвращает следующий этаж для запуска с учётом progress.json.

    Логика:
    - если прогресса нет, стартует с floor_from;
    - если run_all_floors=true, продолжает с последнего + 1;
    - если достигнут floor_to, возвращает None.
    """

    progress = read_progress(profile)
    floor_from = int(progress.get("floor_from", profile.floor_from))
    floor_to = int(progress.get("floor_to", profile.floor_to))
    current_floor = int(progress.get("current_floor", floor_from - 1))

    if profile.run_all_floors:
        next_floor = max(floor_from, current_floor + 1)
    else:
        next_floor = floor_from if current_floor < floor_from else current_floor + 1

    if next_floor > floor_to:
        return None
    return next_floor



def should_stop_after_floor(profile: LocationProfile, floor_number: int) -> bool:
    """
    Проверяет, нужно ли остановиться по достижении floor_to.

    Возвращает True, если floor_number >= floor_to.
    """

    return int(floor_number) >= int(profile.floor_to)



def mark_progress_completed(profile: LocationProfile, floor_number: int, reason: str = "floor_to_reached") -> Dict[str, Any]:
    """
    Помечает progress.json как completed.

    Обычно вызывается, когда бот достиг floor_to и должен остановиться.
    """

    progress = read_progress(profile)
    progress["current_floor"] = int(floor_number)
    progress["status"] = "completed"
    progress["last_result"] = {
        "floor_number": int(floor_number),
        "status": "completed",
        "reason": reason,
    }
    progress.setdefault("totals", {})
    progress["totals"]["floors_completed"] = max(
        0,
        int(floor_number) - int(progress.get("floor_from", profile.floor_from)) + 1,
    )
    write_progress(profile, progress)
    return progress



def save_location_route(profile: LocationProfile, steps: List[Dict[str, Any]]) -> None:
    """
    Сохраняет обученный путь до локации из главного меню.

    Формат route.json:
    {
      "profile": "loc1",
      "steps": [ ... ]
    }

    Содержимое шагов не выдумывается модулем: его должен передать режим обучения.
    """

    data = {
        "profile": profile.id,
        "steps": steps,
        "updated_at": _utc_now_iso(),
    }
    _atomic_write_json(profile.route_file, data)



def load_location_route(profile: LocationProfile) -> Dict[str, Any]:
    """
    Загружает обученный путь до локации из route.json.

    Если файла нет, возвращает пустую структуру со списком шагов.
    """

    return _read_json(
        profile.route_file,
        default={
            "profile": profile.id,
            "steps": [],
        },
    )



def save_floor_selection(profile: LocationProfile, selection_data: Dict[str, Any]) -> None:
    """
    Сохраняет обученный способ выбора этажа.

    Поддерживаемые сценарии задаются только пользователем в --teach:
    - вариант (а): кнопка "следующий этаж" после победы;
    - вариант (б): выбор этажа из списка с эталонами номеров и прокруткой свайпом.

    Модуль ничего не угадывает, а лишь сохраняет структуру как есть.
    """

    data = dict(selection_data)
    data["profile"] = profile.id
    data["updated_at"] = _utc_now_iso()
    _atomic_write_json(profile.floor_selection_file, data)



def load_floor_selection(profile: LocationProfile) -> Dict[str, Any]:
    """
    Загружает обученный способ выбора этажа.

    Если способ ещё не обучен, возвращает пустую структуру:
    {
      "profile": "loc1",
      "mode": "",
      "updated_at": ""
    }
    """

    return _read_json(
        profile.floor_selection_file,
        default={
            "profile": profile.id,
            "mode": "",
            "updated_at": "",
        },
    )


__all__ = [
    "DEFAULT_PROFILE_IDS",
    "LocationProfile",
    "ProfileKnowledgePaths",
    "create_default_progress",
    "get_last_completed_floor",
    "get_next_floor_to_run",
    "list_profiles",
    "load_floor_selection",
    "load_location_route",
    "load_profile",
    "load_profile_by_cli",
    "mark_progress_completed",
    "read_progress",
    "resolve_profile",
    "save_floor_selection",
    "save_location_route",
    "set_last_completed_floor",
    "should_stop_after_floor",
    "write_progress",
]
