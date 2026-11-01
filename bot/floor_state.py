from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from bot.config import Config


class FloorStateError(Exception):
    ...


class DoorNotFoundError(FloorStateError):
    ...


class InvalidDoorStatusError(FloorStateError):
    ...


DoorStatus = Literal["unvisited", "passage", "in_progress", "cleared", "skipped", "failed"]
_ALLOWED_STATUSES: tuple[DoorStatus, ...] = (
    "unvisited",
    "passage",
    "in_progress",
    "cleared",
    "skipped",
    "failed",
)


@dataclass
class DoorNode:
    door_id: str
    status: DoorStatus
    map_x: int
    map_y: int
    bbox_x: int
    bbox_y: int
    bbox_w: int
    bbox_h: int
    is_room_open_big_icon: bool = False
    entered_count: int = 0
    notes: str = ""


@dataclass
class FloorGraph:
    floor_number: int
    nodes: dict[str, DoorNode] = field(default_factory=dict)
    edges: list[tuple[str, str, str]] = field(default_factory=list)
    current_target_door_id: str | None = None
    last_entered_door_id: str | None = None


class FloorStateStore:
    def __init__(self, config: "Config", profile: str, floor_number: int) -> None:
        if not isinstance(profile, str) or not profile.strip():
            raise FloorStateError("profile must be a non-empty string")
        if isinstance(floor_number, bool) or not isinstance(floor_number, int):
            raise FloorStateError("floor_number must be int")
        self._config = config
        self._profile = profile
        self._floor_number = floor_number
        self._path = self._resolve_path()

    def load(self) -> FloorGraph:
        if not self._path.exists():
            return FloorGraph(floor_number=self._floor_number)
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise FloorStateError(f"failed to load floor state: {exc}") from exc
        return self._graph_from_dict(data)

    def save(self, graph: FloorGraph) -> None:
        payload = self._graph_to_dict(graph)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=self._path.name + ".", suffix=".tmp", dir=str(self._path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def upsert_door(
        self,
        graph: FloorGraph,
        door_id: str,
        map_x: int,
        map_y: int,
        bbox_x: int,
        bbox_y: int,
        bbox_w: int,
        bbox_h: int,
        is_room_open_big_icon: bool = False,
    ) -> DoorNode:
        if not isinstance(door_id, str) or not door_id.strip():
            raise FloorStateError("door_id must be a non-empty string")
        node = graph.nodes.get(door_id)
        if node is None:
            node = DoorNode(
                door_id=door_id,
                status="unvisited",
                map_x=map_x,
                map_y=map_y,
                bbox_x=bbox_x,
                bbox_y=bbox_y,
                bbox_w=bbox_w,
                bbox_h=bbox_h,
                is_room_open_big_icon=bool(is_room_open_big_icon),
            )
            graph.nodes[door_id] = node
            return node
        node.map_x = map_x
        node.map_y = map_y
        node.bbox_x = bbox_x
        node.bbox_y = bbox_y
        node.bbox_w = bbox_w
        node.bbox_h = bbox_h
        node.is_room_open_big_icon = bool(is_room_open_big_icon)
        return node

    def add_edge(self, graph: FloorGraph, from_door_id: str, to_door_id: str, kind: str) -> None:
        if from_door_id not in graph.nodes:
            raise DoorNotFoundError(from_door_id)
        if to_door_id not in graph.nodes:
            raise DoorNotFoundError(to_door_id)
        graph.edges.append((from_door_id, to_door_id, kind))

    def set_status(self, graph: FloorGraph, door_id: str, status: DoorStatus, notes: str = "") -> None:
        if status not in _ALLOWED_STATUSES:
            raise InvalidDoorStatusError(status)
        node = graph.nodes.get(door_id)
        if node is None:
            raise DoorNotFoundError(door_id)
        node.status = status
        if notes:
            node.notes = notes
        if status != "in_progress" and graph.current_target_door_id == door_id:
            graph.current_target_door_id = None

    def mark_in_progress_and_save(self, graph: FloorGraph, door_id: str, notes: str = "") -> None:
        node = graph.nodes.get(door_id)
        if node is None:
            raise DoorNotFoundError(door_id)
        node.entered_count += 1
        graph.last_entered_door_id = door_id
        graph.current_target_door_id = door_id
        self.set_status(graph, door_id, "in_progress", notes=notes)
        self.save(graph)

    def next_target(self, graph: FloorGraph) -> DoorNode | None:
        candidates = [
            node
            for door_id, node in sorted(graph.nodes.items(), key=lambda item: item[0])
            if node.status in {"unvisited", "passage"} and not node.is_room_open_big_icon
        ]
        return candidates[0] if candidates else None

    def has_pending_targets(self, graph: FloorGraph) -> bool:
        return self.next_target(graph) is not None

    def list_doors(self, graph: FloorGraph) -> list[DoorNode]:
        return [node for _, node in sorted(graph.nodes.items(), key=lambda item: item[0])]

    def _resolve_path(self) -> Path:
        raw_dir = self._config.get("storage.floor_state_dir")
        if not isinstance(raw_dir, str) or not raw_dir.strip():
            raise FloorStateError("storage.floor_state_dir must be a non-empty string")
        base = self._config.path.parent
        path = Path(raw_dir).expanduser()
        if not path.is_absolute():
            path = (base / path).resolve()
        return path / self._profile / f"floor_{self._floor_number:04d}" / "floor_state.json"

    def _graph_to_dict(self, graph: FloorGraph) -> dict[str, Any]:
        now = _utc_now()
        return {
            "version": 1,
            "profile": self._profile,
            "floor_number": graph.floor_number,
            "created_at": now,
            "updated_at": now,
            "nodes": [
                {
                    "door_id": node.door_id,
                    "status": node.status,
                    "discovered_at": now,
                    "updated_at": now,
                    "map_position": {"x": node.map_x, "y": node.map_y},
                    "screen_bbox": {
                        "x": node.bbox_x,
                        "y": node.bbox_y,
                        "w": node.bbox_w,
                        "h": node.bbox_h,
                    },
                    "is_room_open_big_icon": node.is_room_open_big_icon,
                    "entered_count": node.entered_count,
                    "notes": node.notes,
                }
                for node in self.list_doors(graph)
            ],
            "edges": [
                {"from_door_id": src, "to_door_id": dst, "kind": kind}
                for src, dst, kind in graph.edges
            ],
            "current_target_door_id": graph.current_target_door_id,
            "last_entered_door_id": graph.last_entered_door_id,
        }

    def _graph_from_dict(self, data: dict[str, Any]) -> FloorGraph:
        if not isinstance(data, dict):
            raise FloorStateError("floor state root must be a mapping")
        floor_number = data.get("floor_number")
        if isinstance(floor_number, bool) or not isinstance(floor_number, int):
            raise FloorStateError("floor_number must be int")
        graph = FloorGraph(floor_number=floor_number)
        nodes = data.get("nodes", [])
        if not isinstance(nodes, list):
            raise FloorStateError("nodes must be a list")
        for item in nodes:
            if not isinstance(item, dict):
                continue
            map_position = item.get("map_position", {})
            screen_bbox = item.get("screen_bbox", {})
            if not isinstance(map_position, dict) or not isinstance(screen_bbox, dict):
                continue
            status = item.get("status", "unvisited")
            if status not in _ALLOWED_STATUSES:
                continue
            door_id = item.get("door_id")
            if not isinstance(door_id, str) or not door_id:
                continue
            graph.nodes[door_id] = DoorNode(
                door_id=door_id,
                status=status,
                map_x=int(map_position.get("x", 0)),
                map_y=int(map_position.get("y", 0)),
                bbox_x=int(screen_bbox.get("x", 0)),
                bbox_y=int(screen_bbox.get("y", 0)),
                bbox_w=int(screen_bbox.get("w", 0)),
                bbox_h=int(screen_bbox.get("h", 0)),
                is_room_open_big_icon=bool(item.get("is_room_open_big_icon", False)),
                entered_count=int(item.get("entered_count", 0)),
                notes=str(item.get("notes", "")),
            )
        edges = data.get("edges", [])
        if isinstance(edges, list):
            for item in edges:
                if not isinstance(item, dict):
                    continue
                src = item.get("from_door_id")
                dst = item.get("to_door_id")
                kind = item.get("kind")
                if isinstance(src, str) and isinstance(dst, str) and isinstance(kind, str):
                    graph.edges.append((src, dst, kind))
        current_target = data.get("current_target_door_id")
        last_entered = data.get("last_entered_door_id")
        graph.current_target_door_id = current_target if isinstance(current_target, str) else None
        graph.last_entered_door_id = last_entered if isinstance(last_entered, str) else None
        return graph


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
