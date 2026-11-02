"""Template-driven dungeon navigation.

The module deliberately keeps the public legacy API, while the real device path
is used when an ADB controller/screenshot/vision triple is supplied by the host.
The screen classifier is conservative: it reports UNKNOWN when no template can
be proven, rather than guessing. Device execution is not verified in CI.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional


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
    location_id: Optional[str] = None
    floor_number: Optional[int] = None


def _nav_path(project_dir: str | Path) -> Path:
    return Path(project_dir).resolve() / "data" / "base" / "nav.json"


def _read_nav(project_dir: str | Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
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


def _png(project_dir: str | Path, value: Any) -> Optional[Path]:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if not path.is_absolute():
        path = Path(project_dir).resolve() / "data" / "base" / path
    return path if path.is_file() else None


def _locations(data: dict[str, Any]) -> list[dict[str, Any]]:
    value = data.get("locations", [])
    return value if isinstance(value, list) else []


def _location(data: dict[str, Any], name: str) -> Optional[dict[str, Any]]:
    for item in _locations(data):
        if isinstance(item, dict) and str(item.get("name", item.get("slot", ""))) == name:
            return item
    return None


def nav_status(project_dir: str | Path) -> dict[str, Any]:
    """Return readiness of every configured link in the navigation chain."""
    data, error = _read_nav(project_dir)
    if error:
        return {"ready": False, "error": error, "steps": {"profile": False}}
    assert data is not None
    base = data.get("base_to_locations", {})
    enter = data.get("enter_dungeon", {})
    steps: dict[str, Any] = {
        "profile": True,
        "base_to_locations": _png(project_dir, base.get("png") if isinstance(base, dict) else None) is not None,
        "enter_dungeon": _png(project_dir, enter.get("png") if isinstance(enter, dict) else None) is not None,
        "locations": [],
    }
    for item in _locations(data):
        if not isinstance(item, dict):
            continue
        floors = []
        for floor in item.get("floors", []) if isinstance(item.get("floors", []), list) else []:
            if isinstance(floor, dict):
                floors.append({"name": floor.get("name"), "ready": _png(project_dir, floor.get("png")) is not None})
        icon_ok = _png(project_dir, item.get("png")) is not None
        steps["locations"].append({"slot": item.get("slot"), "name": item.get("name"), "ready": icon_ok and bool(floors) and all(f["ready"] for f in floors), "icon": icon_ok, "floors": floors})
    steps["locations_ready"] = bool(steps["locations"]) and all(x["ready"] for x in steps["locations"])
    steps["ready"] = bool(steps["base_to_locations"] and steps["enter_dungeon"] and steps["locations_ready"])
    return {"ready": steps["ready"], "error": None, "steps": steps}


class NavigationController:
    """Small retrying FSM. Dependencies are optional for offline validation."""
    def __init__(self, project_dir: str | Path, log: Callable[..., Any] = print, stop_event: Any = None, *, adb: Any = None, screenshot: Any = None, vision: Any = None, **_: Any):
        self.project_dir = Path(project_dir).resolve()
        self.log, self.stop_event = log, stop_event
        self.adb, self.screenshot, self.vision = adb, screenshot, vision

    def _stopped(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _tap_template(self, png: Path, label: str, timeout: float = 20.0, retries: int = 2) -> bool:
        if not (self.adb and self.screenshot and self.vision):
            self.log(f"navigation requires adb/screenshot/vision for {label}")
            return False
        started = time.monotonic()
        for attempt in range(1, retries + 1):
            while time.monotonic() - started < timeout:
                if self._stopped():
                    return False
                try:
                    frame = self.vision.wait_until_stable(self.screenshot.capture, stable_timeout_s=3.0)
                    match = self.vision.find(frame, png, (0, 0, frame.shape[1], frame.shape[0]), 0.86)
                    if match:
                        x1, y1, x2, y2 = match.rect
                        self.adb.tap((x1 + x2) // 2, (y1 + y2) // 2)
                        time.sleep(0.7)
                        return True
                except Exception as exc:
                    self.log(f"{label}, attempt {attempt}: {exc}")
                time.sleep(0.35)
        return False

    def navigate_from_current_screen(self, location_id: str, floor_number: int) -> NavigationResult:
        data, error = _read_nav(self.project_dir)
        if error:
            return NavigationResult(False, ScreenState.UNKNOWN, error, location_id=location_id, floor_number=floor_number)
        assert data is not None
        loc = _location(data, location_id)
        if loc is None:
            return NavigationResult(False, ScreenState.LOCATION_SELECT, f"unknown location: {location_id}", location_id=location_id, floor_number=floor_number)
        floors = loc.get("floors", [])
        floor = next((x for x in floors if isinstance(x, dict) and str(x.get("name")) == str(floor_number)), None)
        if floor is None:
            return NavigationResult(False, ScreenState.FLOOR, f"unknown floor: {floor_number}", location_id=location_id, floor_number=floor_number)
        sequence = [(data.get("base_to_locations", {}).get("png"), "base to locations"), (loc.get("png"), "location"), (floor.get("png"), "floor"), (data.get("enter_dungeon", {}).get("png"), "enter dungeon")]
        for raw, label in sequence:
            path = _png(self.project_dir, raw)
            if path is None:
                return NavigationResult(False, ScreenState.UNKNOWN, f"template missing: {label}", location_id=location_id, floor_number=floor_number)
            if not self._tap_template(path, label):
                return NavigationResult(False, ScreenState.UNKNOWN, f"timeout waiting for {label}", location_id=location_id, floor_number=floor_number)
        return NavigationResult(True, ScreenState.FARM, "navigation chain completed", 1, location_id, floor_number)


def go_to_dungeon(project_dir: str | Path, location_name: str, floor_name: str, log: Callable[..., Any] = print, stop_event: Any = None) -> tuple[bool, str]:
    """Navigate and return ``(success, reason)``. Device wiring is host-specific."""
    try:
        floor = int(floor_name)
    except (TypeError, ValueError):
        return False, f"invalid floor: {floor_name}"
    result = NavigationController(project_dir, log, stop_event).navigate_from_current_screen(location_name, floor)
    if not result.ok:
        log(result.message)
    return result.ok, result.message


def verify_chain(project_dir: str | Path, location_name: str, floor_name: str, log: Callable[..., Any] = print) -> bool:
    """Validate profile/templates and exercise the same path without farm logic."""
    status = nav_status(project_dir)
    if not status["ready"]:
        log(status.get("error") or "navigation profile is incomplete")
        return False
    ok, reason = go_to_dungeon(project_dir, location_name, floor_name, log=log)
    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--go", nargs=2, metavar=("LOCATION", "FLOOR"))
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    if args.go:
        ok, reason = go_to_dungeon(args.project_dir, args.go[0], args.go[1])
        print(reason)
        raise SystemExit(0 if ok else 1)
    print(json.dumps(nav_status(args.project_dir), ensure_ascii=False, indent=2))
