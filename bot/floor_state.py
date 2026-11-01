from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import BotConfig, get_path


class FloorStateError(Exception):
    pass


class NoAvailableDoorError(FloorStateError):
    pass


class DoorNotFoundError(FloorStateError):
    pass


_ALLOWED_STATUSES: tuple[str, ...] = (
    "unvisited",
    "passage",
    "in_progress",
    "cleared",
    "skipped",
    "failed",
)

_TERMINAL_STATUSES: set[str] = {"cleared", "skipped", "failed"}
_NON_SELECTABLE_STATUSES: set[str] = {"in_progress", "cleared", "skipped", "failed"}
_DEFAULT_NODE_KIND = "map_position"
_DEFAULT_COORDINATE_TOLERANCE_PX = 24.0
_DEFAULT_MAX_ATTEMPTS = 3
_OPEN_ROOM_NOTE_TOKEN = "room_open_big_icon"


@dataclass(frozen=True)
class DoorRecord:
    door_id: str
    marker_rect: tuple[int, int, int, int]
    status: str
    target_node_id: str | None
    discovered_at: str
    updated_at: str
    notes: str


@dataclass(frozen=True)
class NodeRecord:
    node_id: str
    kind: str
    visited: bool
    doors: tuple[DoorRecord, ...]


class FloorStateStore:
    def __init__(self, config: "BotConfig", profile: str, logger: "logging.Logger") -> None:
        self.config = config
        self.profile = profile
        self.logger = logger
        self._version = 1
        self._coordinate_tolerance_px = self._read_coordinate_tolerance()
        self._max_attempts = self._read_max_attempts()
        self._path = self._resolve_state_path()

    def load(self) -> dict:
        """
        Загружает floor_state.json.

        :return: dict
        :raises FileNotFoundError: если файл отсутствует
        :raises FloorStateError: если JSON повреждён
        """
        if not self._path.exists():
            raise FileNotFoundError(str(self._path))

        candidates = [self._path, self._tmp_path()]
        last_error: Exception | None = None

        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                with candidate.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self._validate_state_dict(data)
                if candidate != self._path:
                    self.logger.warning(
                        "Recovered floor state from temporary file: %s", candidate
                    )
                return data
            except (OSError, json.JSONDecodeError, FloorStateError) as exc:
                last_error = exc
                self.logger.warning("Failed to load floor state candidate %s: %s", candidate, exc)

        raise FloorStateError(f"Failed to load floor state: {last_error}")

    def save(self, state: dict) -> None:
        """
        Атомарно сохраняет floor_state.json.

        :param state: полное состояние этажа
        :raises FloorStateError: при ошибке записи
        """
        self._validate_state_dict(state)
        state["updated_at"] = _utc_now()
        self._ensure_parent_dir()

        tmp_path = self._tmp_path()
        payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=False)

        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self._path.parent),
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
                named_tmp_path = Path(fh.name)

            try:
                os.replace(named_tmp_path, tmp_path)
                self._fsync_dir(self._path.parent)
                os.replace(tmp_path, self._path)
                self._fsync_dir(self._path.parent)
            finally:
                if named_tmp_path.exists():
                    try:
                        named_tmp_path.unlink()
                    except OSError:
                        pass
                if tmp_path.exists() and tmp_path != self._path:
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
        except OSError as exc:
            raise FloorStateError(f"Failed to save floor state atomically: {exc}") from exc

    def create_empty(self, floor_number: int) -> dict:
        """
        Создаёт пустое состояние этажа.

        :param floor_number: номер этажа
        :return: dict состояния
        """
        now = _utc_now()
        return {
            "version": self._version,
            "profile": self.profile,
            "floor_number": int(floor_number),
            "updated_at": now,
            "root_node_id": None,
            "current_node_id": None,
            "phase": "passage",
            "progress": {
                "attempts_limit": self._max_attempts,
                "discovered_doors": 0,
                "remaining_unvisited": 0,
                "has_in_progress_at_start": False,
            },
            "nodes": [],
            "stats": {
                "rooms_cleared": 0,
                "rooms_skipped": 0,
                "rooms_failed": 0,
            },
        }

    def reset_for_floor(self, floor_number: int) -> dict:
        """
        Сбрасывает состояние и сохраняет пустой граф для нового этажа.

        :param floor_number: номер этажа
        :return: новое состояние
        :raises FloorStateError: при ошибке записи
        """
        state = self.create_empty(floor_number)
        self.save(state)
        return state

    def upsert_visible_doors(
        self,
        state: dict,
        door_rects: list[tuple[int, int, int, int]],
        current_node_id: str | None = None,
    ) -> dict:
        """
        Добавляет в граф новые видимые двери, если таких ещё нет.
        Сопоставление "та же дверь или новая" делается по близости marker_rect;
        точный алгоритм остаётся реализацией, но контракт данных не меняется.

        Все новые двери получают статус 'unvisited'.

        :param state: текущее состояние
        :param door_rects: список прямоугольников door_marker
        :param current_node_id: узел, к которому относятся двери; если None, использовать current_node_id из state
        :return: обновлённый state
        :raises FloorStateError: если state невалиден
        """
        self._validate_state_dict(state)
        node_id = current_node_id or state.get("current_node_id")
        state = self._ensure_node(state, node_id=node_id, kind=_DEFAULT_NODE_KIND)
        resolved_node_id = state["current_node_id"]
        node = self._find_node_dict(state, resolved_node_id)
        if node is None:
            raise FloorStateError(f"Current node not found: {resolved_node_id}")

        now = _utc_now()
        changed = False

        for raw_rect in door_rects:
            rect = _normalize_rect(raw_rect)
            existing = self._find_matching_door(node.get("doors", []), rect)
            if existing is not None:
                if tuple(existing["marker_rect"]) != rect:
                    existing["marker_rect"] = list(rect)
                    existing["updated_at"] = now
                    changed = True
                continue

            door = {
                "door_id": self._next_door_id(state),
                "marker_rect": list(rect),
                "status": "unvisited",
                "target_node_id": None,
                "discovered_at": now,
                "updated_at": now,
                "notes": "",
                "attempts": 0,
            }
            node.setdefault("doors", []).append(door)
            changed = True

        node["visited"] = True
        if state.get("root_node_id") is None:
            state["root_node_id"] = resolved_node_id
        if changed:
            self._touch_progress(state)
            self._touch_state(state)
        return state

    def mark_door_status(
        self,
        state: dict,
        door_id: str,
        status: str,
        note: str = "",
    ) -> dict:
        """
        Обновляет статус двери.

        Допустимые статусы:
          unvisited, passage, in_progress, cleared, skipped, failed

        :param state: текущее состояние
        :param door_id: идентификатор двери
        :param status: новый статус
        :param note: примечание
        :return: обновлённый state
        :raises DoorNotFoundError: если дверь не найдена
        :raises FloorStateError: если статус невалиден
        """
        self._validate_state_dict(state)
        self._ensure_status(status)
        door = self._find_door_dict(state, door_id)
        if door is None:
            raise DoorNotFoundError(door_id)

        current_status = str(door.get("status", ""))
        self._ensure_transition(current_status, status)

        now = _utc_now()
        door["status"] = status
        door["updated_at"] = now
        if note:
            door["notes"] = self._merge_note(str(door.get("notes", "")), note)

        if status == "cleared":
            self.increment_stats(state, cleared=1)
        elif status == "skipped":
            self.increment_stats(state, skipped=1)
        elif status == "failed":
            self.increment_stats(state, failed=1)

        self._touch_progress(state)
        self._touch_state(state)
        return state

    def mark_in_progress_and_flush(
        self,
        state: dict,
        door_id: str,
        note: str = "",
    ) -> dict:
        """
        Помечает дверь как in_progress и НЕМЕДЛЕННО атомарно пишет state на диск.

        Это обязательный вызов сразу после подтверждённого входа в дверь.

        :param state: текущее состояние
        :param door_id: идентификатор двери
        :param note: примечание
        :return: обновлённый state
        :raises DoorNotFoundError: если дверь не найдена
        :raises FloorStateError: при ошибке записи
        """
        state = self.mark_door_status(state, door_id, "in_progress", note=note)
        self.save(state)
        return state

    def choose_next_target(self, state: dict) -> DoorRecord:
        """
        Выбирает следующую цель для исследования.

        Никогда не выбирает двери со статусами:
          in_progress, cleared, skipped, failed

        Также не должен возвращать дверь, если она соответствует уже открытой комнате
        (логика связи с room_open_big_icon задаётся вызывающим кодом через предварительное
        обновление state/notes либо фильтрацию кандидатов до вызова).

        Приоритет:
          1) unvisited
          2) passage
        Если доступных дверей нет, выбрасывает исключение.

        :param state: текущее состояние
        :return: DoorRecord
        :raises NoAvailableDoorError: если целей нет
        :raises FloorStateError: если state невалиден
        """
        self._validate_state_dict(state)
        party_point = self._party_marker_point(state)
        current_node_id = state.get("current_node_id")
        candidates_unvisited: list[tuple[float, dict[str, Any]]] = []
        candidates_passage: list[tuple[float, dict[str, Any]]] = []

        for node in state.get("nodes", []):
            if current_node_id is not None and node.get("node_id") != current_node_id:
                continue
            for door in node.get("doors", []):
                status = str(door.get("status", ""))
                if status in _NON_SELECTABLE_STATUSES:
                    continue
                if self._is_marked_open_room(door):
                    continue
                center = _rect_center(_normalize_rect(door["marker_rect"]))
                distance = _distance(center, party_point)
                if status == "unvisited":
                    candidates_unvisited.append((distance, door))
                elif status == "passage":
                    candidates_passage.append((distance, door))

        pool = candidates_unvisited or candidates_passage
        if not pool:
            raise NoAvailableDoorError("No selectable doors in current node")

        pool.sort(key=lambda item: item[0])
        return self._door_record_from_dict(pool[0][1])

    def set_current_node(self, state: dict, node_id: str) -> dict:
        """
        Обновляет current_node_id.

        :raises FloorStateError: если node_id отсутствует
        """
        self._validate_state_dict(state)
        if self._find_node_dict(state, node_id) is None:
            raise FloorStateError(f"Unknown node_id: {node_id}")
        state["current_node_id"] = node_id
        self._touch_state(state)
        return state

    def increment_stats(
        self,
        state: dict,
        cleared: int = 0,
        skipped: int = 0,
        failed: int = 0,
    ) -> dict:
        """
        Увеличивает счётчики stats.

        :return: обновлённый state
        """
        self._validate_state_dict(state)
        stats = state.setdefault(
            "stats",
            {"rooms_cleared": 0, "rooms_skipped": 0, "rooms_failed": 0},
        )
        stats["rooms_cleared"] = int(stats.get("rooms_cleared", 0)) + int(cleared)
        stats["rooms_skipped"] = int(stats.get("rooms_skipped", 0)) + int(skipped)
        stats["rooms_failed"] = int(stats.get("rooms_failed", 0)) + int(failed)
        self._touch_state(state)
        return state

    def add_or_update_door(
        self,
        state: dict,
        marker_rect: tuple[int, int, int, int],
        current_node_id: str | None = None,
        tolerance_px: float | None = None,
    ) -> tuple[dict, DoorRecord]:
        self._validate_state_dict(state)
        tolerance = self._coordinate_tolerance_px if tolerance_px is None else float(tolerance_px)
        node_id = current_node_id or state.get("current_node_id")
        state = self._ensure_node(state, node_id=node_id, kind=_DEFAULT_NODE_KIND)
        node = self._find_node_dict(state, state["current_node_id"])
        if node is None:
            raise FloorStateError("Current node does not exist after ensure_node")

        rect = _normalize_rect(marker_rect)
        existing = self._find_matching_door(node.get("doors", []), rect, tolerance_px=tolerance)
        now = _utc_now()

        if existing is None:
            existing = {
                "door_id": self._next_door_id(state),
                "marker_rect": list(rect),
                "status": "unvisited",
                "target_node_id": None,
                "discovered_at": now,
                "updated_at": now,
                "notes": "",
                "attempts": 0,
            }
            node.setdefault("doors", []).append(existing)
        else:
            existing["marker_rect"] = list(rect)
            existing["updated_at"] = now

        node["visited"] = True
        if state.get("root_node_id") is None:
            state["root_node_id"] = state["current_node_id"]
        self._touch_progress(state)
        self._touch_state(state)
        return state, self._door_record_from_dict(existing)

    def set_status(self, state: dict, door_id: str, status: str, note: str = "") -> dict:
        state = self.mark_door_status(state, door_id, status, note=note)
        if status == "in_progress":
            self.save(state)
        return state

    def next_target(self, state: dict) -> DoorRecord:
        return self.choose_next_target(state)

    def has_unvisited(self, state: dict) -> bool:
        self._validate_state_dict(state)
        current_node_id = state.get("current_node_id")
        for node in state.get("nodes", []):
            if current_node_id is not None and node.get("node_id") != current_node_id:
                continue
            for door in node.get("doors", []):
                if door.get("status") == "unvisited" and not self._is_marked_open_room(door):
                    return True
        return False

    def increment_attempts(self, state: dict, door_id: str) -> dict:
        self._validate_state_dict(state)
        door = self._find_door_dict(state, door_id)
        if door is None:
            raise DoorNotFoundError(door_id)

        attempts = int(door.get("attempts", 0)) + 1
        door["attempts"] = attempts
        door["updated_at"] = _utc_now()
        self._touch_progress(state)

        if attempts >= self._max_attempts and str(door.get("status", "")) not in _TERMINAL_STATUSES:
            self.mark_door_status(
                state,
                door_id,
                "failed",
                note=f"attempt_limit_reached:{attempts}/{self._max_attempts}",
            )
        else:
            self._touch_state(state)
        return state

    def get_attempts(self, state: dict, door_id: str) -> int:
        self._validate_state_dict(state)
        door = self._find_door_dict(state, door_id)
        if door is None:
            raise DoorNotFoundError(door_id)
        return int(door.get("attempts", 0))

    def notify_in_progress_found_at_start(self, state: dict) -> list[DoorRecord]:
        self._validate_state_dict(state)
        result: list[DoorRecord] = []
        for node in state.get("nodes", []):
            for door in node.get("doors", []):
                if door.get("status") == "in_progress":
                    result.append(self._door_record_from_dict(door))
        state.setdefault("progress", {})["has_in_progress_at_start"] = bool(result)
        return result

    def mark_open_room_icon(self, state: dict, door_id: str, present: bool = True) -> dict:
        self._validate_state_dict(state)
        door = self._find_door_dict(state, door_id)
        if door is None:
            raise DoorNotFoundError(door_id)
        notes = str(door.get("notes", ""))
        if present and _OPEN_ROOM_NOTE_TOKEN not in notes:
            door["notes"] = self._merge_note(notes, _OPEN_ROOM_NOTE_TOKEN)
        elif not present and notes:
            parts = [p for p in notes.split(";") if p and p != _OPEN_ROOM_NOTE_TOKEN]
            door["notes"] = ";".join(parts)
        door["updated_at"] = _utc_now()
        self._touch_state(state)
        return state

    def set_phase(self, state: dict, phase: str) -> dict:
        self._validate_state_dict(state)
        state["phase"] = str(phase)
        self._touch_state(state)
        return state

    def set_progress_value(self, state: dict, key: str, value: Any) -> dict:
        self._validate_state_dict(state)
        progress = state.setdefault("progress", {})
        progress[str(key)] = value
        self._touch_state(state)
        return state

    def _resolve_state_path(self) -> Path:
        root_dir = Path(str(get_path(self.config, "profiles", "root_dir")))
        if not root_dir.is_absolute():
            root_dir = self.config.base_dir / root_dir
        file_name = str(get_path(self.config, "app", "floor_state_file"))
        return (root_dir / self.profile / file_name).resolve()

    def _read_coordinate_tolerance(self) -> float:
        floor_cfg = self.config.raw.get("floor_state", {})
        value = floor_cfg.get("coordinate_tolerance_px", _DEFAULT_COORDINATE_TOLERANCE_PX)
        try:
            tolerance = float(value)
        except (TypeError, ValueError):
            tolerance = _DEFAULT_COORDINATE_TOLERANCE_PX
        return max(0.0, tolerance)

    def _read_max_attempts(self) -> int:
        floor_cfg = self.config.raw.get("floor_state", {})
        value = floor_cfg.get("max_attempts", _DEFAULT_MAX_ATTEMPTS)
        try:
            max_attempts = int(value)
        except (TypeError, ValueError):
            max_attempts = _DEFAULT_MAX_ATTEMPTS
        return max(1, max_attempts)

    def _validate_state_dict(self, state: Any) -> None:
        if not isinstance(state, dict):
            raise FloorStateError("State must be a dict")
        required = (
            "version",
            "profile",
            "floor_number",
            "updated_at",
            "root_node_id",
            "current_node_id",
            "nodes",
            "stats",
        )
        for key in required:
            if key not in state:
                raise FloorStateError(f"Missing required state key: {key}")
        if not isinstance(state["nodes"], list):
            raise FloorStateError("nodes must be a list")
        if not isinstance(state["stats"], dict):
            raise FloorStateError("stats must be a dict")
        state.setdefault("phase", "passage")
        state.setdefault("progress", {})

        for node in state["nodes"]:
            if not isinstance(node, dict):
                raise FloorStateError("Each node must be a dict")
            for key in ("node_id", "kind", "visited", "doors"):
                if key not in node:
                    raise FloorStateError(f"Node missing key: {key}")
            if not isinstance(node["doors"], list):
                raise FloorStateError("node.doors must be a list")
            for door in node["doors"]:
                self._validate_door_dict(door)

    def _validate_door_dict(self, door: Any) -> None:
        if not isinstance(door, dict):
            raise FloorStateError("Door must be a dict")
        for key in (
            "door_id",
            "marker_rect",
            "status",
            "target_node_id",
            "discovered_at",
            "updated_at",
            "notes",
        ):
            if key not in door:
                raise FloorStateError(f"Door missing key: {key}")
        self._ensure_status(str(door["status"]))
        _normalize_rect(door["marker_rect"])
        if "attempts" not in door:
            door["attempts"] = 0

    def _ensure_status(self, status: str) -> None:
        if status not in _ALLOWED_STATUSES:
            raise FloorStateError(f"Invalid door status: {status}")

    def _ensure_transition(self, current_status: str, new_status: str) -> None:
        if current_status == new_status:
            return
        if current_status in _TERMINAL_STATUSES and new_status != current_status:
            raise FloorStateError(
                f"Invalid status transition from terminal status {current_status} to {new_status}"
            )
        if new_status in {"cleared", "skipped", "failed"} and current_status != "in_progress":
            raise FloorStateError(
                f"Status {new_status} requires current status in_progress, got {current_status}"
            )
        if current_status == "passage" and new_status == "in_progress":
            return
        if current_status == "unvisited" and new_status in {"passage", "in_progress"}:
            return
        if current_status == "in_progress" and new_status in {"cleared", "skipped", "failed", "passage", "unvisited"}:
            return
        if current_status == "passage" and new_status == "failed":
            raise FloorStateError("passage cannot become failed without in_progress")
        if current_status not in {"unvisited", "passage", "in_progress"}:
            raise FloorStateError(f"Unsupported current status: {current_status}")
        if current_status == "passage" and new_status == "unvisited":
            return
        raise FloorStateError(f"Invalid status transition: {current_status} -> {new_status}")

    def _ensure_node(self, state: dict, node_id: str | None, kind: str) -> dict:
        if node_id is None:
            node_id = self._next_node_id(state)
        node = self._find_node_dict(state, node_id)
        if node is None:
            node = {
                "node_id": node_id,
                "kind": kind,
                "visited": False,
                "doors": [],
            }
            state.setdefault("nodes", []).append(node)
        state["current_node_id"] = node_id
        if state.get("root_node_id") is None:
            state["root_node_id"] = node_id
        return state

    def _find_node_dict(self, state: dict, node_id: str | None) -> dict[str, Any] | None:
        if node_id is None:
            return None
        for node in state.get("nodes", []):
            if node.get("node_id") == node_id:
                return node
        return None

    def _find_door_dict(self, state: dict, door_id: str) -> dict[str, Any] | None:
        for node in state.get("nodes", []):
            for door in node.get("doors", []):
                if door.get("door_id") == door_id:
                    return door
        return None

    def _find_matching_door(
        self,
        doors: list[dict[str, Any]],
        rect: tuple[int, int, int, int],
        tolerance_px: float | None = None,
    ) -> dict[str, Any] | None:
        tolerance = self._coordinate_tolerance_px if tolerance_px is None else float(tolerance_px)
        rect_center = _rect_center(rect)
        best: tuple[float, dict[str, Any]] | None = None
        for door in doors:
            existing_rect = _normalize_rect(door["marker_rect"])
            existing_center = _rect_center(existing_rect)
            distance = _distance(rect_center, existing_center)
            if distance <= tolerance:
                if best is None or distance < best[0]:
                    best = (distance, door)
        return best[1] if best is not None else None

    def _next_node_id(self, state: dict) -> str:
        max_index = 0
        for node in state.get("nodes", []):
            max_index = max(max_index, _extract_numeric_suffix(str(node.get("node_id", "")), "node_"))
        return f"node_{max_index + 1:04d}"

    def _next_door_id(self, state: dict) -> str:
        max_index = 0
        for node in state.get("nodes", []):
            for door in node.get("doors", []):
                max_index = max(
                    max_index,
                    _extract_numeric_suffix(str(door.get("door_id", "")), "door_"),
                )
        return f"door_{max_index + 1:04d}"

    def _door_record_from_dict(self, door: dict[str, Any]) -> DoorRecord:
        return DoorRecord(
            door_id=str(door["door_id"]),
            marker_rect=_normalize_rect(door["marker_rect"]),
            status=str(door["status"]),
            target_node_id=str(door["target_node_id"]) if door.get("target_node_id") is not None else None,
            discovered_at=str(door["discovered_at"]),
            updated_at=str(door["updated_at"]),
            notes=str(door.get("notes", "")),
        )

    def _party_marker_point(self, state: dict) -> tuple[float, float]:
        progress = state.get("progress", {})
        marker = progress.get("party_marker")
        if isinstance(marker, (list, tuple)) and len(marker) == 4:
            return _rect_center(_normalize_rect(marker))
        if isinstance(marker, dict):
            rect = marker.get("rect")
            if isinstance(rect, (list, tuple)) and len(rect) == 4:
                return _rect_center(_normalize_rect(rect))
            x = marker.get("x")
            y = marker.get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                return float(x), float(y)
        return 0.0, 0.0

    def _is_marked_open_room(self, door: dict[str, Any]) -> bool:
        notes = str(door.get("notes", ""))
        return _OPEN_ROOM_NOTE_TOKEN in notes

    def _merge_note(self, existing: str, note: str) -> str:
        if not existing:
            return note
        if note in existing.split(";"):
            return existing
        return f"{existing};{note}"

    def _touch_progress(self, state: dict) -> None:
        progress = state.setdefault("progress", {})
        doors = [door for node in state.get("nodes", []) for door in node.get("doors", [])]
        progress["attempts_limit"] = self._max_attempts
        progress["discovered_doors"] = len(doors)
        progress["remaining_unvisited"] = sum(
            1 for door in doors if door.get("status") == "unvisited" and not self._is_marked_open_room(door)
        )

    def _touch_state(self, state: dict) -> None:
        state["updated_at"] = _utc_now()

    def _ensure_parent_dir(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _tmp_path(self) -> Path:
        return self._path.with_name(f"{self._path.name}.tmp")

    def _fsync_dir(self, directory: Path) -> None:
        try:
            fd = os.open(str(directory), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)


FloorState = FloorStateStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_rect(rect: Any) -> tuple[int, int, int, int]:
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        raise FloorStateError("marker_rect must contain four coordinates")
    try:
        x1, y1, x2, y2 = (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
    except (TypeError, ValueError) as exc:
        raise FloorStateError("marker_rect coordinates must be integers") from exc
    if x2 <= x1 or y2 <= y1:
        raise FloorStateError("marker_rect must satisfy x2>x1 and y2>y1")
    return x1, y1, x2, y2


def _rect_center(rect: tuple[int, int, int, int]) -> tuple[float, float]:
    x1, y1, x2, y2 = rect
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _extract_numeric_suffix(value: str, prefix: str) -> int:
    if not value.startswith(prefix):
        return 0
    suffix = value[len(prefix) :]
    return int(suffix) if suffix.isdigit() else 0
