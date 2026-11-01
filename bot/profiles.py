from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import BotConfig, get_path


class ProfileError(Exception):
    pass


DEFAULT_PROFILE_IDS: tuple[str, ...] = (
    "loc1",
    "loc2",
    "loc3",
    "loc4",
    "loc5",
    "loc6",
    "loc7",
)

_ALLOWED_FLOOR_SELECT_METHODS: tuple[str, ...] = (
    "next_after_victory",
    "list_select",
)


@dataclass(frozen=True)
class ProfileRunConfig:
    profile: str
    floor_from: int
    floor_to: int
    run_all_floors: bool


class ProfileManager:
    """
    Менеджер профилей данных и настроек локаций.

    Публичные методы:
    - list_profiles() -> list[str]
      Возвращает список идентификаторов профилей из config.yaml.
    - ensure_profile(profile: str) -> None
      Создаёт каталог профиля и обязательные JSON-файлы по умолчанию.
    - profile_dir(profile: str) -> str
      Возвращает путь к каталогу профиля.
    - load_profile(profile: str) -> dict[str, Any]
      Загружает секцию профиля из config.yaml.
    - get_selected_profile(cli_profile: str | None = None) -> str
      Возвращает профиль из CLI или профиль по умолчанию.
    - load_run_config(cli_profile: str | None = None, floor_from: int | None = None, floor_to: int | None = None)
      Собирает диапазон этажей с учётом config.yaml и аргументов CLI.
    - load_progress(profile: str) -> dict[str, Any]
      Загружает progress.json профиля.
    - save_progress(profile: str, data: dict[str, Any]) -> None
      Атомарно сохраняет progress.json профиля.
    - read_last_completed_floor(profile: str) -> int
      Возвращает последний пройденный этаж из progress.json.
    - write_last_completed_floor(profile: str, floor_number: int) -> dict[str, Any]
      Обновляет progress.json, записывая последний пройденный этаж.
    - should_stop_at_floor(profile: str, floor_number: int, floor_to: int | None = None) -> bool
      Проверяет, достигнут ли конечный этаж.
    - knowledge_dir(profile: str) -> str
      Возвращает каталог knowledge/<profile>/.
    - common_knowledge_dir() -> str
      Возвращает каталог knowledge/common/.
    - location_path_file(profile: str) -> str
      Возвращает путь к файлу маршрута до локации.
    - floor_selection_file(profile: str) -> str
      Возвращает путь к файлу способа выбора этажа.
    """

    def __init__(self, config: "BotConfig", logger: "logging.Logger") -> None:
        self.config = config
        self.logger = logger
        self.root_dir_path = self._resolve_profiles_root_dir()
        self.profile_entries = self._parse_profiles_section()

    def list_profiles(self) -> list[str]:
        """
        Возвращает список профилей.

        :return: list[str]
        """
        return [entry["id"] for entry in self.profile_entries]

    def ensure_profile(self, profile: str) -> None:
        """
        Создаёт структуру файлов профиля при отсутствии.

        Обязаны быть подготовлены:
          rules.json
          features.json
          progress.json
          floor_state.json
          templates/

        Дополнительно для контракта локационных профилей создаются:
          knowledge/<profile>/rules.json
          knowledge/<profile>/screens/
          knowledge/<profile>/location_path.json
          knowledge/<profile>/floor_selection.json
          knowledge/common/rules.json
          knowledge/common/screens/

        :raises ProfileError: при ошибке создания
        """
        self._validate_profile_known(profile)
        profile_dir = Path(self.profile_dir(profile))
        knowledge_dir = Path(self.knowledge_dir(profile))
        common_dir = Path(self.common_knowledge_dir())

        try:
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / "templates").mkdir(parents=True, exist_ok=True)
            knowledge_dir.mkdir(parents=True, exist_ok=True)
            (knowledge_dir / "screens").mkdir(parents=True, exist_ok=True)
            common_dir.mkdir(parents=True, exist_ok=True)
            (common_dir / "screens").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProfileError(f"Failed to create directories for profile '{profile}': {exc}") from exc

        self._ensure_json_file(profile_dir / self._rules_file_name(), self._default_rules(profile))
        self._ensure_json_file(profile_dir / self._features_file_name(), self._default_features(profile))
        self._ensure_json_file(profile_dir / self._progress_file_name(), self._default_progress(profile))
        self._ensure_json_file(profile_dir / self._floor_state_file_name(), self._default_floor_state(profile))

        self._ensure_json_file(knowledge_dir / "rules.json", self._default_profile_knowledge_rules(profile))
        self._ensure_json_file(knowledge_dir / "location_path.json", self._default_location_path(profile))
        self._ensure_json_file(knowledge_dir / "floor_selection.json", self._default_floor_selection(profile))
        self._ensure_json_file(common_dir / "rules.json", self._default_common_rules())

    def profile_dir(self, profile: str) -> str:
        """
        :return: абсолютный или нормализованный путь к каталогу профиля
        """
        self._validate_profile_known(profile)
        return str((self.root_dir_path / profile).resolve())

    def load_profile(self, profile: str) -> dict[str, Any]:
        """Возвращает словарь одного профиля из секции profiles config.yaml."""
        for entry in self.profile_entries:
            if entry["id"] == profile:
                return dict(entry)
        raise ProfileError(f"Unknown profile '{profile}'")

    def get_selected_profile(self, cli_profile: str | None = None) -> str:
        """Возвращает профиль из CLI или profiles.default_profile."""
        if cli_profile:
            self._validate_profile_known(cli_profile)
            return cli_profile
        default_profile = self._default_profile_id()
        self._validate_profile_known(default_profile)
        return default_profile

    def load_run_config(
        self,
        cli_profile: str | None = None,
        floor_from: int | None = None,
        floor_to: int | None = None,
    ) -> ProfileRunConfig:
        """
        Собирает рабочий диапазон этажей.

        Приоритет:
        1) CLI-аргументы
        2) запись профиля в config.yaml
        3) значения по умолчанию
        """
        selected_profile = self.get_selected_profile(cli_profile)
        profile_cfg = self.load_profile(selected_profile)

        cfg_floor_from = int(profile_cfg.get("floor_from", 1))
        cfg_floor_to = int(profile_cfg.get("floor_to", self._game_floors_per_level()))
        run_all_floors = bool(profile_cfg.get("run_all_floors", False))

        actual_floor_from = int(floor_from) if floor_from is not None else cfg_floor_from
        actual_floor_to = int(floor_to) if floor_to is not None else cfg_floor_to

        if run_all_floors and floor_from is None and floor_to is None:
            actual_floor_from = 1
            actual_floor_to = self._game_floors_per_level()

        self._validate_floor_range(actual_floor_from, actual_floor_to)

        return ProfileRunConfig(
            profile=selected_profile,
            floor_from=actual_floor_from,
            floor_to=actual_floor_to,
            run_all_floors=run_all_floors,
        )

    def load_progress(self, profile: str) -> dict[str, Any]:
        """
        Читает progress.json профиля.

        :raises FileNotFoundError: если файл отсутствует
        :raises ProfileError: если JSON повреждён
        """
        self.ensure_profile(profile)
        path = Path(self.profile_dir(profile)) / self._progress_file_name()
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            raise
        except json.JSONDecodeError as exc:
            raise ProfileError(f"Invalid JSON in progress file for '{profile}': {exc}") from exc
        if not isinstance(data, dict):
            raise ProfileError(f"Progress file for '{profile}' must contain a JSON object")
        return data

    def save_progress(self, profile: str, data: dict[str, Any]) -> None:
        """
        Атомарно сохраняет progress.json профиля.

        :raises ProfileError: при ошибке записи
        """
        self._validate_profile_known(profile)
        if not isinstance(data, dict):
            raise ProfileError("Progress data must be a dict")
        normalized = dict(data)
        normalized["profile"] = profile
        normalized["updated_at"] = _utc_now()
        path = Path(self.profile_dir(profile)) / self._progress_file_name()
        self._atomic_write_json(path, normalized)

    def read_last_completed_floor(self, profile: str) -> int:
        """Возвращает current_floor из progress.json."""
        progress = self.load_progress(profile)
        value = progress.get("current_floor", 1)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProfileError(f"Invalid current_floor in progress.json for '{profile}'")
        return value

    def write_last_completed_floor(self, profile: str, floor_number: int) -> dict[str, Any]:
        """
        Записывает последний пройденный этаж в progress.json.

        current_floor трактуется как последний завершённый этаж,
        чтобы следующий запуск мог продолжить работу.
        """
        if isinstance(floor_number, bool) or not isinstance(floor_number, int) or floor_number < 1:
            raise ProfileError("floor_number must be int >= 1")

        progress = self.load_progress(profile)
        progress["current_floor"] = floor_number
        totals = progress.get("totals")
        if not isinstance(totals, dict):
            totals = {
                "floors_completed": 0,
                "rooms_cleared": 0,
                "rooms_skipped": 0,
                "rooms_failed": 0,
            }
            progress["totals"] = totals
        if "floors_completed" not in totals or not isinstance(totals.get("floors_completed"), int):
            totals["floors_completed"] = 0
        totals["floors_completed"] = max(int(totals["floors_completed"]), floor_number)
        self.save_progress(profile, progress)
        return progress

    def should_stop_at_floor(self, profile: str, floor_number: int, floor_to: int | None = None) -> bool:
        """Проверяет, достигнут ли конечный этаж включительно."""
        if floor_to is None:
            run_cfg = self.load_run_config(cli_profile=profile)
            target_floor = run_cfg.floor_to
        else:
            target_floor = floor_to
        return floor_number >= target_floor

    def knowledge_dir(self, profile: str) -> str:
        """Возвращает каталог knowledge/<profile>/ для отдельной базы знаний локации."""
        self._validate_profile_known(profile)
        return str((self.root_dir_path / "knowledge" / profile).resolve())

    def common_knowledge_dir(self) -> str:
        """Возвращает каталог knowledge/common/ с общими правилами для всех профилей."""
        return str((self.root_dir_path / "knowledge" / "common").resolve())

    def location_path_file(self, profile: str) -> str:
        """Путь к JSON с обученным путём до локации из главного меню."""
        return str((Path(self.knowledge_dir(profile)) / "location_path.json").resolve())

    def floor_selection_file(self, profile: str) -> str:
        """Путь к JSON с обученным способом выбора этажа."""
        return str((Path(self.knowledge_dir(profile)) / "floor_selection.json").resolve())

    def _resolve_profiles_root_dir(self) -> Path:
        raw = get_path(self.config, "profiles", "root_dir")
        if not isinstance(raw, str) or not raw.strip():
            raise ProfileError("profiles.root_dir must be a non-empty string")
        path = Path(raw)
        base_dir = Path.cwd()
        source_path = self.config.raw.get("__source_path")
        if isinstance(source_path, str) and source_path:
            base_dir = Path(source_path).resolve().parent
        if not path.is_absolute():
            path = base_dir / path
        return path.resolve()

    def _parse_profiles_section(self) -> list[dict[str, Any]]:
        raw_profiles = self.config.raw.get("profiles")
        if not isinstance(raw_profiles, dict):
            raise ProfileError("profiles section must be a mapping")

        entries = raw_profiles.get("items")
        if entries is None:
            result: list[dict[str, Any]] = []
            for profile_id in DEFAULT_PROFILE_IDS:
                result.append(
                    {
                        "id": profile_id,
                        "name": profile_id,
                        "floor_from": 1,
                        "floor_to": self._game_floors_per_level(),
                        "run_all_floors": False,
                    }
                )
            return result

        if not isinstance(entries, list):
            raise ProfileError("profiles.items must be a list")
        if len(entries) != 7:
            raise ProfileError("profiles.items must contain exactly 7 profiles")

        seen: set[str] = set()
        result = []
        for index, item in enumerate(entries, start=1):
            if not isinstance(item, dict):
                raise ProfileError(f"profiles.items[{index - 1}] must be a mapping")
            profile_id = item.get("id", f"loc{index}")
            if not isinstance(profile_id, str) or not profile_id.strip():
                raise ProfileError(f"profiles.items[{index - 1}].id must be a non-empty string")
            profile_id = profile_id.strip()
            if profile_id in seen:
                raise ProfileError(f"Duplicate profile id '{profile_id}' in profiles.items")
            seen.add(profile_id)

            name = item.get("name", profile_id)
            if not isinstance(name, str) or not name.strip():
                raise ProfileError(f"profiles.items[{index - 1}].name must be a non-empty string")

            floor_from = item.get("floor_from", 1)
            floor_to = item.get("floor_to", self._game_floors_per_level())
            run_all_floors = item.get("run_all_floors", False)
            if isinstance(floor_from, bool) or not isinstance(floor_from, int) or floor_from < 1:
                raise ProfileError(f"profiles.items[{index - 1}].floor_from must be int >= 1")
            if isinstance(floor_to, bool) or not isinstance(floor_to, int) or floor_to < 1:
                raise ProfileError(f"profiles.items[{index - 1}].floor_to must be int >= 1")
            if not isinstance(run_all_floors, bool):
                raise ProfileError(f"profiles.items[{index - 1}].run_all_floors must be bool")
            self._validate_floor_range(floor_from, floor_to)

            result.append(
                {
                    "id": profile_id,
                    "name": name.strip(),
                    "floor_from": floor_from,
                    "floor_to": floor_to,
                    "run_all_floors": run_all_floors,
                }
            )
        return result

    def _default_profile_id(self) -> str:
        raw_profiles = self.config.raw.get("profiles", {})
        default_profile = raw_profiles.get("default_profile", DEFAULT_PROFILE_IDS[0])
        if not isinstance(default_profile, str) or not default_profile.strip():
            raise ProfileError("profiles.default_profile must be a non-empty string")
        return default_profile.strip()

    def _validate_profile_known(self, profile: str) -> None:
        if not isinstance(profile, str) or not profile.strip():
            raise ProfileError("profile must be a non-empty string")
        if profile not in self.list_profiles():
            raise ProfileError(f"Unknown profile '{profile}'. Available: {', '.join(self.list_profiles())}")

    def _rules_file_name(self) -> str:
        value = get_path(self.config, "app", "rules_file")
        if not isinstance(value, str) or not value.strip():
            raise ProfileError("app.rules_file must be a non-empty string")
        return value

    def _features_file_name(self) -> str:
        value = get_path(self.config, "app", "features_file")
        if not isinstance(value, str) or not value.strip():
            raise ProfileError("app.features_file must be a non-empty string")
        return value

    def _progress_file_name(self) -> str:
        value = get_path(self.config, "app", "progress_file")
        if not isinstance(value, str) or not value.strip():
            raise ProfileError("app.progress_file must be a non-empty string")
        return value

    def _floor_state_file_name(self) -> str:
        value = get_path(self.config, "app", "floor_state_file")
        if not isinstance(value, str) or not value.strip():
            raise ProfileError("app.floor_state_file must be a non-empty string")
        return value

    def _game_floors_per_level(self) -> int:
        value = get_path(self.config, "game", "floors_per_level")
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ProfileError("game.floors_per_level must be int >= 1")
        return value

    def _categories(self) -> list[str]:
        categories = get_path(self.config, "knowledge", "categories")
        if not isinstance(categories, list) or not categories:
            raise ProfileError("knowledge.categories must be a non-empty list")
        result: list[str] = []
        for item in categories:
            if not isinstance(item, str) or not item.strip():
                raise ProfileError("knowledge.categories must contain non-empty strings")
            result.append(item)
        return result

    def _validate_floor_range(self, floor_from: int, floor_to: int) -> None:
        max_floor = self._game_floors_per_level()
        if floor_from < 1:
            raise ProfileError("floor_from must be >= 1")
        if floor_to < 1:
            raise ProfileError("floor_to must be >= 1")
        if floor_from > floor_to:
            raise ProfileError("floor_from must be <= floor_to")
        if floor_from > max_floor or floor_to > max_floor:
            raise ProfileError(
                f"Floor range must be within 1..{max_floor}, got {floor_from}..{floor_to}"
            )

    def _default_rules(self, profile: str) -> dict[str, Any]:
        return {
            "version": 1,
            "profile": profile,
            "updated_at": "",
            "categories": self._categories(),
            "policy": {
                "block_shop": bool(get_path(self.config, "safety", "block_shop")),
                "block_ads": bool(get_path(self.config, "safety", "block_ads")),
                "block_pvp": bool(get_path(self.config, "safety", "block_pvp")),
                "stop_on_danger": bool(get_path(self.config, "safety", "stop_on_danger")),
                "clear_room_immediately": bool(get_path(self.config, "game", "clear_room_immediately")),
                "skip_events_by_exit": bool(get_path(self.config, "game", "skip_events_by_exit")),
                "go_all_doors_before_next_floor": bool(get_path(self.config, "game", "go_all_doors_before_next_floor")),
                "revisit_open_rooms": bool(get_path(self.config, "game", "revisit_open_rooms")),
            },
            "screen_rules": [],
            "notes": "",
        }

    def _default_features(self, profile: str) -> dict[str, Any]:
        return {
            "version": 1,
            "profile": profile,
            "updated_at": "",
            "categories": self._categories(),
            "templates": [],
        }

    def _default_floor_state(self, profile: str) -> dict[str, Any]:
        return {
            "version": 1,
            "profile": profile,
            "floor_number": 0,
            "updated_at": "",
            "root_node_id": None,
            "current_node_id": None,
            "nodes": [],
            "stats": {
                "rooms_cleared": 0,
                "rooms_skipped": 0,
                "rooms_failed": 0,
            },
        }

    def _default_progress(self, profile: str) -> dict[str, Any]:
        return {
            "version": 1,
            "profile": profile,
            "updated_at": "",
            "current_location": 1,
            "current_level": 1,
            "current_floor": 1,
            "last_run": {
                "started_at": "",
                "finished_at": "",
                "status": "",
                "reason": "",
            },
            "totals": {
                "floors_completed": 0,
                "rooms_cleared": 0,
                "rooms_skipped": 0,
                "rooms_failed": 0,
            },
            "history": [],
        }

    def _default_profile_knowledge_rules(self, profile: str) -> dict[str, Any]:
        return {
            "version": 1,
            "profile": profile,
            "updated_at": "",
            "location_path": {
                "taught": False,
                "steps": [],
                "notes": "Путь до локации обучается пользователем в режиме --teach.",
            },
            "floor_selection": {
                "taught": False,
                "method": None,
                "next_after_victory": None,
                "list_select": {
                    "reference_floor": None,
                    "template_file": None,
                    "selected_rect": None,
                    "search_roi": None,
                    "scroll": {
                        "enabled": False,
                        "swipe_start": None,
                        "swipe_end": None,
                        "max_swipes": 0,
                    },
                },
                "notes": "Способ выбора этажа не выдумывается и обучается пользователем.",
            },
        }

    def _default_location_path(self, profile: str) -> dict[str, Any]:
        return {
            "version": 1,
            "profile": profile,
            "updated_at": "",
            "taught": False,
            "steps": [],
            "notes": "Последовательность шагов до локации из главного меню задаётся пользователем.",
        }

    def _default_floor_selection(self, profile: str) -> dict[str, Any]:
        return {
            "version": 1,
            "profile": profile,
            "updated_at": "",
            "taught": False,
            "method": None,
            "allowed_methods": list(_ALLOWED_FLOOR_SELECT_METHODS),
            "next_after_victory": {
                "button_template": None,
                "selected_rect": None,
                "search_roi": None,
                "notes": "Кнопка 'следующий этаж' обучается пользователем один раз.",
            },
            "list_select": {
                "reference_floor": None,
                "template_file": None,
                "selected_rect": None,
                "search_roi": None,
                "scroll": {
                    "enabled": False,
                    "swipe_start": None,
                    "swipe_end": None,
                    "max_swipes": 0,
                    "notes": "Свайп для прокрутки списка обучается пользователем; OCR не используется.",
                },
            },
            "notes": "Поддерживаются варианты выбора этажа: кнопка после победы и выбор из списка.",
        }

    def _default_common_rules(self) -> dict[str, Any]:
        return {
            "version": 1,
            "profile": "common",
            "updated_at": "",
            "categories": self._categories(),
            "notes": "Общие правила для всех профилей, например закрытие всплывающих окон.",
            "screen_rules": [],
            "shared_actions": [],
        }

    def _ensure_json_file(self, path: Path, payload: dict[str, Any]) -> None:
        if path.exists():
            return
        self._atomic_write_json(path, payload)

    def _atomic_write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        try:
            with temp_path.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_path, path)
        except OSError as exc:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass
            raise ProfileError(f"Failed to write JSON file '{path}': {exc}") from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
