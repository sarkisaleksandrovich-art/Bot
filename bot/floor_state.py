from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

DoorStatus = Literal[
    "unvisited",
    "passage",
    "in_progress",
    "cleared",
    "skipped",
    "failed",
]

_ALLOWED_STATUSES = {
    "unvisited",
    "passage",
    "in_progress",
    "cleared",
    "skipped",
    "failed",
}

_TERMINAL_STATUSES = {"cleared", "skipped", "failed"}
_NON_TARGET_STATUSES = {"in_progress", "cleared", "skipped", "failed", "passage"}


class FloorStateError(RuntimeError):
    pass


@dataclass
class DoorRecord:
    id: str
    from_node_id: str
    to_node_id: Optional[str]
    bbox: Tuple[int, int, int, int]
    tap_point: Tuple[int, int]
    status: str
    attempts: int = 0
    last_error: Optional[str] = None
    updated_at: str = ""


@dataclass
class NodeRecord:
    id: str
    kind: str
    screen_ref: Optional[str] = None
    discovered_at: str = ""


class FloorState:
    VERSION = 1
    DOOR_MATCH_DISTANCE_PX = 48.0
    BBOX_MATCH_DISTANCE_PX = 64.0

    def __init__(
        self,
        path: str,
        floor_number: int,
        profile: str,
    ) -> None:
        self.path = str(path)
        self.floor_number = int(floor_number)
        self.profile = str(profile)

        self.version: int = self.VERSION
        self.created_at: str = ""
        self.updated_at: str = ""
        self.current_node_id: Optional[str] = None
        self.entry_node_id: Optional[str] = None
        self.phase: str = "idle"
        self.progress: Dict[str, int] = {
            "rooms_cleared": 0,
            "rooms_skipped": 0,
            "rooms_failed": 0,
        }
        self.meta: Dict[str, object] = {
            "rooms_cleared": 0,
            "rooms_skipped": 0,
            "rooms_failed": 0,
            "notes": "",
        }
        self.nodes: List[NodeRecord] = []
        self.doors: List[DoorRecord] = []

        self._node_seq: int = 0
        self._door_seq: int = 0

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _ensure_status(status: str) -> str:
        if status not in _ALLOWED_STATUSES:
            raise FloorStateError(f"unsupported door status: {status}")
        return status

    @staticmethod
    def _ensure_bbox(value: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        if not isinstance(value, tuple) or len(value) != 4:
            raise FloorStateError("bbox must be a tuple of 4 integers")
        x, y, w, h = value
        if not all(isinstance(v, int) for v in value):
            raise FloorStateError("bbox must contain integers")
        if w <= 0 or h <= 0:
            raise FloorStateError("bbox width and height must be positive")
        return x, y, w, h

    @staticmethod
    def _ensure_point(value: Tuple[int, int], name: str = "tap_point") -> Tuple[int, int]:
        if not isinstance(value, tuple) or len(value) != 2:
            raise FloorStateError(f"{name} must be a tuple of 2 integers")
        x, y = value
        if not all(isinstance(v, int) for v in value):
            raise FloorStateError(f"{name} must contain integers")
        return x, y

    @staticmethod
    def _distance_points(a: Tuple[int, int], b: Tuple[int, int]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    @staticmethod
    def _bbox_center(bbox: Tuple[int, int, int, int]) -> Tuple[int, int]:
        x, y, w, h = bbox
        return (x + w // 2, y + h // 2)

    @classmethod
    def _bbox_distance(cls, a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
        return cls._distance_points(cls._bbox_center(a), cls._bbox_center(b))

    def _touch(self) -> None:
        self.updated_at = self._now_iso()

    def _reindex_sequences(self) -> None:
        max_node = 0
        max_door = 0
        for node in self.nodes:
            if node.id.startswith("node_"):
                try:
                    max_node = max(max_node, int(node.id.split("_", 1)[1]))
                except ValueError:
                    continue
        for door in self.doors:
            if door.id.startswith("door_"):
                try:
                    max_door = max(max_door, int(door.id.split("_", 1)[1]))
                except ValueError:
                    continue
        self._node_seq = max_node
        self._door_seq = max_door

    def _next_node_id(self) -> str:
        self._node_seq += 1
        return f"node_{self._node_seq:04d}"

    def _next_door_id(self) -> str:
        self._door_seq += 1
        return f"door_{self._door_seq:04d}"

    def _get_node_index(self, node_id: str) -> int:
        for index, node in enumerate(self.nodes):
            if node.id == node_id:
                return index
        raise FloorStateError(f"node not found: {node_id}")

    def _get_door_index(self, door_id: str) -> int:
        for index, door in enumerate(self.doors):
            if door.id == door_id:
                return index
        raise FloorStateError(f"door not found: {door_id}")

    def _set_status_internal(
        self,
        door_id: str,
        status: DoorStatus,
        *,
        to_node_id: Optional[str] = None,
        error: Optional[str] = None,
        increment_attempt: bool = False,
        save_immediately: bool = False,
    ) -> None:
        status = self._ensure_status(status)
        door_index = self._get_door_index(door_id)
        door = self.doors[door_index]

        if to_node_id is not None:
            self._get_node_index(to_node_id)
            door.to_node_id = to_node_id

        if increment_attempt:
            door.attempts += 1

        if error is not None:
            door.last_error = str(error)

        door.status = status
        door.updated_at = self._now_iso()

        self._recount_progress()
        self.phase = status
        self._touch()

        if save_immediately:
            self.save()

    def _recount_progress(self) -> None:
        rooms_cleared = sum(1 for door in self.doors if door.status == "cleared")
        rooms_skipped = sum(1 for door in self.doors if door.status == "skipped")
        rooms_failed = sum(1 for door in self.doors if door.status == "failed")

        self.progress = {
            "rooms_cleared": rooms_cleared,
            "rooms_skipped": rooms_skipped,
            "rooms_failed": rooms_failed,
        }
        notes = self.meta.get("notes", "") if isinstance(self.meta, dict) else ""
        self.meta = {
            "rooms_cleared": rooms_cleared,
            "rooms_skipped": rooms_skipped,
            "rooms_failed": rooms_failed,
            "notes": notes,
        }

    def _serialize(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "floor_number": self.floor_number,
            "profile": self.profile,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_node_id": self.current_node_id,
            "entry_node_id": self.entry_node_id,
            "phase": self.phase,
            "progress": dict(self.progress),
            "nodes": [asdict(node) for node in self.nodes],
            "doors": [asdict(door) for door in self.doors],
            "meta": dict(self.meta),
        }

    def _load_from_data(self, data: Dict[str, object]) -> None:
        try:
            self.version = int(data.get("version", self.VERSION))
            file_floor = int(data.get("floor_number", self.floor_number))
            file_profile = str(data.get("profile", self.profile))
        except Exception as exc:
            raise FloorStateError(f"invalid floor state header: {exc}") from exc

        if file_floor != self.floor_number:
            raise FloorStateError(
                f"floor state floor_number mismatch: expected {self.floor_number}, got {file_floor}"
            )
        if file_profile != self.profile:
            raise FloorStateError(
                f"floor state profile mismatch: expected {self.profile}, got {file_profile}"
            )

        self.created_at = str(data.get("created_at") or self._now_iso())
        self.updated_at = str(data.get("updated_at") or self.created_at)
        self.current_node_id = data.get("current_node_id") if data.get("current_node_id") is None else str(data.get("current_node_id"))
        self.entry_node_id = data.get("entry_node_id") if data.get("entry_node_id") is None else str(data.get("entry_node_id"))
        self.phase = str(data.get("phase") or "loaded")

        raw_nodes = data.get("nodes", [])
        raw_doors = data.get("doors", [])
        raw_progress = data.get("progress", {})
        raw_meta = data.get("meta", {})

        if not isinstance(raw_nodes, list):
            raise FloorStateError("nodes must be a list")
        if not isinstance(raw_doors, list):
            raise FloorStateError("doors must be a list")
        if raw_progress is not None and not isinstance(raw_progress, dict):
            raise FloorStateError("progress must be an object")
        if raw_meta is not None and not isinstance(raw_meta, dict):
            raise FloorStateError("meta must be an object")

        self.nodes = []
        for item in raw_nodes:
            if not isinstance(item, dict):
                raise FloorStateError("node item must be an object")
            node = NodeRecord(
                id=str(item["id"]),
                kind=str(item["kind"]),
                screen_ref=None if item.get("screen_ref") is None else str(item.get("screen_ref")),
                discovered_at=str(item.get("discovered_at") or self._now_iso()),
            )
            self.nodes.append(node)

        self.doors = []
        for item in raw_doors:
            if not isinstance(item, dict):
                raise FloorStateError("door item must be an object")
            bbox_raw = item.get("bbox")
            tap_raw = item.get("tap_point")
            if not isinstance(bbox_raw, list) or len(bbox_raw) != 4:
                raise FloorStateError("door.bbox must be a list of 4 integers")
            if not isinstance(tap_raw, list) or len(tap_raw) != 2:
                raise FloorStateError("door.tap_point must be a list of 2 integers")
            bbox = self._ensure_bbox(tuple(int(v) for v in bbox_raw))
            tap_point = self._ensure_point(tuple(int(v) for v in tap_raw))
            status = self._ensure_status(str(item.get("status")))
            door = DoorRecord(
                id=str(item["id"]),
                from_node_id=str(item["from_node_id"]),
                to_node_id=None if item.get("to_node_id") is None else str(item.get("to_node_id")),
                bbox=bbox,
                tap_point=tap_point,
                status=status,
                attempts=int(item.get("attempts", 0)),
                last_error=None if item.get("last_error") is None else str(item.get("last_error")),
                updated_at=str(item.get("updated_at") or self._now_iso()),
            )
            self.doors.append(door)

        self.progress = {
            "rooms_cleared": int(raw_progress.get("rooms_cleared", 0)) if isinstance(raw_progress, dict) else 0,
            "rooms_skipped": int(raw_progress.get("rooms_skipped", 0)) if isinstance(raw_progress, dict) else 0,
            "rooms_failed": int(raw_progress.get("rooms_failed", 0)) if isinstance(raw_progress, dict) else 0,
        }
        self.meta = {
            "rooms_cleared": int(raw_meta.get("rooms_cleared", self.progress["rooms_cleared"])) if isinstance(raw_meta, dict) else 0,
            "rooms_skipped": int(raw_meta.get("rooms_skipped", self.progress["rooms_skipped"])) if isinstance(raw_meta, dict) else 0,
            "rooms_failed": int(raw_meta.get("rooms_failed", self.progress["rooms_failed"])) if isinstance(raw_meta, dict) else 0,
            "notes": str(raw_meta.get("notes", "")) if isinstance(raw_meta, dict) else "",
        }

        self._reindex_sequences()
        self._recount_progress()

    def load(self) -> None:
        target = Path(self.path)
        if not target.exists():
            self.create_empty()
            return

        try:
            with target.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise FloorStateError("floor state root must be an object")
            self._load_from_data(data)
        except FloorStateError:
            raise
        except Exception as exc:
            backup_path = target.with_suffix(target.suffix + ".corrupt")
            try:
                if backup_path.exists():
                    backup_path.unlink()
                os.replace(str(target), str(backup_path))
            except Exception:
                pass
            self.create_empty()

    def create_empty(self) -> None:
        now = self._now_iso()
        self.version = self.VERSION
        self.created_at = now
        self.updated_at = now
        self.current_node_id = None
        self.entry_node_id = None
        self.phase = "new_floor"
        self.progress = {
            "rooms_cleared": 0,
            "rooms_skipped": 0,
            "rooms_failed": 0,
        }
        self.meta = {
            "rooms_cleared": 0,
            "rooms_skipped": 0,
            "rooms_failed": 0,
            "notes": "",
        }
        self.nodes = []
        self.doors = []
        self._node_seq = 0
        self._door_seq = 0

    def save(self) -> None:
        try:
            target = Path(self.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._touch()
            data = self._serialize()
            payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)

            fd, temp_path = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                    tmp.write(payload)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                os.replace(temp_path, target)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
        except Exception as exc:
            raise FloorStateError(f"failed to save floor state: {exc}") from exc

    def ensure_entry_node(self) -> str:
        if self.entry_node_id is not None:
            self._get_node_index(self.entry_node_id)
            if self.current_node_id is None:
                self.current_node_id = self.entry_node_id
                self.phase = "entry_ready"
                self._touch()
            return self.entry_node_id

        node_id = self.add_node(kind="start", screen_ref=None)
        self.entry_node_id = node_id
        self.current_node_id = node_id
        self.phase = "entry_ready"
        self._touch()
        return node_id

    def set_current_node(self, node_id: str) -> None:
        self._get_node_index(node_id)
        self.current_node_id = node_id
        self.phase = "current_node_set"
        self._touch()

    def add_node(self, kind: str, screen_ref: Optional[str] = None) -> str:
        if not isinstance(kind, str) or not kind.strip():
            raise FloorStateError("kind must be a non-empty string")
        node_id = self._next_node_id()
        node = NodeRecord(
            id=node_id,
            kind=kind,
            screen_ref=screen_ref,
            discovered_at=self._now_iso(),
        )
        self.nodes.append(node)
        self._touch()
        return node_id

    def upsert_door(
        self,
        from_node_id: str,
        bbox: Tuple[int, int, int, int],
        tap_point: Tuple[int, int],
    ) -> str:
        self._get_node_index(from_node_id)
        bbox = self._ensure_bbox(bbox)
        tap_point = self._ensure_point(tap_point)

        best_index: Optional[int] = None
        best_score: Optional[float] = None

        for index, door in enumerate(self.doors):
            if door.from_node_id != from_node_id:
                continue
            tap_distance = self._distance_points(door.tap_point, tap_point)
            bbox_distance = self._bbox_distance(door.bbox, bbox)
            if tap_distance <= self.DOOR_MATCH_DISTANCE_PX or bbox_distance <= self.BBOX_MATCH_DISTANCE_PX:
                score = min(tap_distance, bbox_distance)
                if best_score is None or score < best_score:
                    best_score = score
                    best_index = index

        if best_index is not None:
            door = self.doors[best_index]
            door.bbox = bbox
            door.tap_point = tap_point
            door.updated_at = self._now_iso()
            self._touch()
            return door.id

        door_id = self._next_door_id()
        door = DoorRecord(
            id=door_id,
            from_node_id=from_node_id,
            to_node_id=None,
            bbox=bbox,
            tap_point=tap_point,
            status="unvisited",
            attempts=0,
            last_error=None,
            updated_at=self._now_iso(),
        )
        self.doors.append(door)
        self._touch()
        return door_id

    def mark_passage(self, door_id: str, to_node_id: Optional[str] = None) -> None:
        self._set_status_internal(door_id, "passage", to_node_id=to_node_id)

    def mark_in_progress(self, door_id: str) -> None:
        self._set_status_internal(door_id, "in_progress", save_immediately=True)

    def mark_cleared(self, door_id: str, to_node_id: Optional[str] = None) -> None:
        self._set_status_internal(door_id, "cleared", to_node_id=to_node_id)

    def mark_skipped(self, door_id: str, to_node_id: Optional[str] = None) -> None:
        self._set_status_internal(door_id, "skipped", to_node_id=to_node_id)

    def mark_failed(self, door_id: str, error: str) -> None:
        self._set_status_internal(door_id, "failed", error=error, increment_attempt=True)

    def increment_attempt(self, door_id: str) -> None:
        door_index = self._get_door_index(door_id)
        self.doors[door_index].attempts += 1
        self.doors[door_index].updated_at = self._now_iso()
        self._touch()

    def increment_attempts(self, door_id: str, max_attempts: int, error: Optional[str] = None) -> int:
        if max_attempts < 1:
            raise FloorStateError("max_attempts must be >= 1")
        door_index = self._get_door_index(door_id)
        door = self.doors[door_index]
        door.attempts += 1
        if error is not None:
            door.last_error = str(error)
        door.updated_at = self._now_iso()
        if door.attempts > max_attempts:
            door.status = "failed"
        self._recount_progress()
        self._touch()
        return door.attempts

    def set_status(
        self,
        door_id: str,
        status: DoorStatus,
        *,
        to_node_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        save_immediately = status == "in_progress"
        increment_attempt = status == "failed"
        self._set_status_internal(
            door_id,
            status,
            to_node_id=to_node_id,
            error=error,
            increment_attempt=increment_attempt,
            save_immediately=save_immediately,
        )

    def get_next_target_door_id(self, exclude_open_room_icon_ids: Optional[list[str]] = None) -> Optional[str]:
        exclude = set(exclude_open_room_icon_ids or [])
        marker = self.get_current_marker_position()

        candidates: List[DoorRecord] = []
        for door in self.doors:
            if door.id in exclude:
                continue
            if door.status != "unvisited":
                continue
            candidates.append(door)

        if not candidates:
            return None

        if marker is None:
            candidates.sort(key=lambda d: d.id)
            return candidates[0].id

        candidates.sort(key=lambda d: (self._distance_points(d.tap_point, marker), d.id))
        return candidates[0].id

    def next_target(
        self,
        squad_marker: Optional[Tuple[int, int]],
        exclude_open_room_icon_ids: Optional[list[str]] = None,
    ) -> Optional[DoorRecord]:
        exclude = set(exclude_open_room_icon_ids or [])
        candidates = [
            door for door in self.doors
            if door.status == "unvisited" and door.id not in exclude
        ]
        if not candidates:
            return None
        if squad_marker is None:
            candidates.sort(key=lambda d: d.id)
            return candidates[0]
        marker = self._ensure_point(squad_marker, "squad_marker")
        candidates.sort(key=lambda d: (self._distance_points(d.tap_point, marker), d.id))
        return candidates[0]

    def has_unvisited(self, exclude_open_room_icon_ids: Optional[list[str]] = None) -> bool:
        exclude = set(exclude_open_room_icon_ids or [])
        return any(door.status == "unvisited" and door.id not in exclude for door in self.doors)

    def has_in_progress_at_start(self) -> bool:
        return any(door.status == "in_progress" for door in self.doors)

    def get_in_progress_door_ids(self) -> List[str]:
        return [door.id for door in self.doors if door.status == "in_progress"]

    def get_found_in_progress(self) -> List[DoorRecord]:
        return [door for door in self.doors if door.status == "in_progress"]

    def get_current_marker_position(self) -> Optional[Tuple[int, int]]:
        if self.current_node_id is None:
            return None
        door_points = [door.tap_point for door in self.doors if door.from_node_id == self.current_node_id]
        if not door_points:
            return None
        avg_x = sum(point[0] for point in door_points) // len(door_points)
        avg_y = sum(point[1] for point in door_points) // len(door_points)
        return avg_x, avg_y

    def get_door(self, door_id: str) -> DoorRecord:
        for door in self.doors:
            if door.id == door_id:
                return door
        raise KeyError(door_id)

    def list_doors(self) -> List[DoorRecord]:
        return list(self.doors)

    def set_meta_counts(
        self,
        rooms_cleared: int,
        rooms_skipped: int,
        rooms_failed: int,
    ) -> None:
        notes = self.meta.get("notes", "") if isinstance(self.meta, dict) else ""
        self.progress = {
            "rooms_cleared": int(rooms_cleared),
            "rooms_skipped": int(rooms_skipped),
            "rooms_failed": int(rooms_failed),
        }
        self.meta = {
            "rooms_cleared": int(rooms_cleared),
            "rooms_skipped": int(rooms_skipped),
            "rooms_failed": int(rooms_failed),
            "notes": notes,
        }
        self._touch()
