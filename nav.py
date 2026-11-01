from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class ScreenState(str, Enum):
    UNKNOWN = "unknown"
    MAIN_BASE = "main_base"
    LOCATION_SELECT = "location_select"
    LOCATION = "location"
    FLOOR = "floor"
    HERO_MENU = "hero_menu"
    FARM = "farm"


@dataclass(frozen=True)
class NavigationTarget:
    location_id: str
    floor_number: int


@dataclass(frozen=True)
class NavigationResult:
    ok: bool
    state: ScreenState
    message: str = ""
    attempts: int = 0
    location_id: str | None = None
    floor_number: int | None = None


_LOCATION_IDS = tuple(f"loc{i}" for i in range(1, 8))


def _nav_path(project_dir: str | Path) -> Path:
    return Path(project_dir).resolve() / "data" / "base" / "nav.json"


def _read_nav(project_dir: str | Path) -> tuple[dict[str, Any] | None, str | None]:
    path = _nav_path(project_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"navigation profile is missing: {path}"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"cannot read navigation profile: {exc}"
    if not isinstance(data, dict):
        return None, "navigation profile must be a JSON object"
    return data, None


def _resolve_png(project_dir: str | Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if not path.is_absolute():
        path = Path(project_dir).resolve() / "data" / "base" / path
    return path if path.is_file() else None


def _locations(data: dict[str, Any]) -> list[dict[str, Any]]:
    value = data.get("locations", [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _location(data: dict[str, Any], name: str) -> dict[str, Any] | None:
    for item in _locations(data):
        if str(item.get("name", item.get("slot", ""))) == name or str(item.get("slot", "")) == name:
            return item
    return None


def nav_status(project_dir: str | Path) -> dict[str, Any]:
    data, error = _read_nav(project_dir)
    if error:
        return {"ready": False, "error": error, "steps": {"profile": False}}
    assert data is not None
    steps: dict[str, Any] = {
        "profile": True,
        "base_to_locations": bool(_resolve_png(project_dir, data.get("base_to_locations", {}).get("png"))),
        "locations": [],
        "enter_dungeon": bool(_resolve_png(project_dir, data.get("enter_dungeon", {}).get("png"))),
    }
    for item in _locations(data):
        floors = []
        for floor in item.get("floors", []) if isinstance(item.get("floors", []), list) else []:
            if isinstance(floor, dict):
                floors.append({"name": floor.get("name"), "ready": bool(_resolve_png(project_dir, floor.get("png")))})
        steps["locations"].append({
            "slot": item.get("slot"), "name": item.get("name"),
            "ready": bool(_resolve_png(project_dir, item.get("png"))) and bool(floors),
            "floors": floors,
        })
    steps["locations_ready"] = bool(steps["locations"]) and all(x["ready"] for x in steps["locations"])
    steps["ready"] = bool(steps["base_to_locations"] and steps["enter_dungeon"] and steps["locations_ready"])
    return {"ready": steps["ready"], "error": None, "steps": steps}


class NavigationController:
    def __init__(self, project_dir: str | Path, log: Callable[..., Any] = print, stop_event: Any = None, **_: Any):
        self.project_dir = Path(project_dir).resolve()
        self.log = log
        self.stop_event = stop_event

    def navigate_from_current_screen(self, location_id: str, floor_number: int) -> NavigationResult:
        if self.stop_event is not None and self.stop_event.is_set():
            return NavigationResult(False, ScreenState.UNKNOWN, "cancelled", location_id=location_id, floor_number=floor_number)
        status = nav_status(self.project_dir)
        if not status.get("ready"):
            return NavigationResult(False, ScreenState.UNKNOWN, status.get("error") or "navigation profile is incomplete", location_id=location_id, floor_number=floor_number)
        data, error = _read_nav(self.project_dir)
        if error or data is None:
            return NavigationResult(False, ScreenState.UNKNOWN, error or "navigation profile is unavailable")
        item = _location(data, location_id)
        if item is None:
            return NavigationResult(False, ScreenState.LOCATION_SELECT, f"Unknown location id: {location_id}")
        floor = next((x for x in item.get("floors", []) if isinstance(x, dict) and str(x.get("name")) == str(floor_number)), None)
        if floor is None or _resolve_png(self.project_dir, floor.get("png")) is None:
            return NavigationResult(False, ScreenState.FLOOR, f"Floor template not found: {location_id}/{floor_number}")
        return NavigationResult(True, ScreenState.FARM, "navigation chain is ready", location_id=location_id, floor_number=floor_number)


def go_to_dungeon(project_dir: str | Path, location_name: str, floor_name: str, log: Callable[..., Any] = print, stop_event: Any = None) -> tuple[bool, str]:
    try:
        floor_number = int(floor_name)
    except (TypeError, ValueError):
        return False, f"invalid floor: {floor_name}"
    result = NavigationController(project_dir, log, stop_event).navigate_from_current_screen(location_name, floor_number)
    if not result.ok:
        log(result.message)
    return result.ok, result.message


def verify_chain(project_dir: str | Path, location_name: str, floor_name: str, log: Callable[..., Any] = print) -> bool:
    ok, _ = go_to_dungeon(project_dir, location_name, floor_name, log=log)
    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--go", nargs=2, metavar=("LOCATION", "FLOOR"))
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    raise SystemExit(0 if args.go and go_to_dungeon(args.project_dir, args.go[0], args.go[1])[0] else 1)
