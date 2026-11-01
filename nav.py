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
    """Return readiness of every navigation step without accessing the device."""
    data, error = _read_nav(project_dir)
    if error:
        return {"ready": False, "error": error, "steps": {"profile": False}}
    assert data is not None
    steps: dict[str, Any] = {
        "profile": True,
        "base_to_locations": bool(_resolve_png(project_dir, (data.get("base_to_locations") or {}).get("png"))),
        "locations": [],
        "enter_dungeon": bool(_resolve_png(project_dir, (data.get("enter_dungeon") or {}).get("png"))),
    }
    for item in _locations(data):
        floors = []
        for floor in item.get("floors", []) if isinstance(item.get("floors", []), list) else []:
            if isinstance(floor, dict):
                floors.append({"name": floor.get("name"), "ready": bool(_resolve_png(project_dir, floor.get("png")))})
        steps["locations"].append({
            "slot": item.get("slot"),
            "name": item.get("name"),
            "ready": bool(_resolve_png(project_dir, item.get("png"))) and bool(floors),
            "floors": floors,
        })
    steps["locations_ready"] = bool(steps["locations"]) and all(x["ready"] for x in steps["locations"])
    steps["ready"] = bool(steps["base_to_locations"] and steps["enter_dungeon"] and steps["locations_ready"])
    return {"ready": steps["ready"], "error": None, "steps": steps}


class NavigationController:
    """Small template-driven adapter; device execution is deliberately lazy."""

    def __init__(self, project_dir: str | Path, log: Callable[..., Any] = print, stop_event: Any = None):
        self.project_dir = Path(project_dir).resolve()
        self.log = log
        self.stop_event = stop_event
        self.max_attempts = 3
        self.timeout = 12.0
        self.poll = 0.5
        self._adb: Any = None
        self._screenshot: Any = None
        self._vision: Any = None
        self._state = ScreenState.UNKNOWN
        self._load_error: str | None = None

    def _cancelled(self) -> bool:
        return bool(self.stop_event is not None and self.stop_event.is_set())

    def _ensure_device(self) -> str | None:
        if self._adb is not None:
            return None
        try:
            from bot.adb_controller import ADBController
            from bot.config import load_config
            from bot.screenshot import ScreenshotProvider
            from bot.vision import VisionEngine
            config = load_config(str(self.project_dir / "config.yaml"))
            self._adb = ADBController(config, logging.getLogger(__name__))
            self._screenshot = ScreenshotProvider(self._adb, config, logging.getLogger(__name__))
            self._vision = VisionEngine(config, logging.getLogger(__name__))
            self._adb.connect()
            return None
        except Exception as exc:
            return f"device is unavailable: {exc}"

    def _capture(self) -> Any:
        return self._screenshot.capture()

    def _match(self, frame: Any, path: Path) -> Any:
        import cv2
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            return None
        return self._vision.find(frame, image, (0, 0, frame.shape[1], frame.shape[0]), 0.88)

    def _tap(self, path: Path | None, expected: tuple[ScreenState, ...], label: str) -> NavigationResult:
        if path is None:
            return NavigationResult(False, self._state, f"template is missing: {label}")
        last = self._state
        for attempt in range(1, self.max_attempts + 1):
            if self._cancelled():
                return NavigationResult(False, self._state, "cancelled", attempt)
            device_error = self._ensure_device()
            if device_error:
                return NavigationResult(False, self._state, device_error, attempt)
            try:
                frame = self._capture()
                match = self._match(frame, path)
                if match is not None:
                    x1, y1, x2, y2 = match.rect
                    self._adb.tap((x1 + x2) // 2, (y1 + y2) // 2)
                    deadline = time.monotonic() + self.timeout
                    while time.monotonic() < deadline and not self._cancelled():
                        last = self.detect_screen()
                        if last in expected:
                            self._state = last
                            return NavigationResult(True, last, attempts=attempt)
                        time.sleep(self.poll)
            except Exception as exc:
                self.log(f"navigation retry {label}: {exc}")
            time.sleep(self.poll)
        return NavigationResult(False, last, f"Navigation timeout: expected {','.join(x.value for x in expected)}", self.max_attempts)

    def detect_screen(self) -> ScreenState:
        data, error = _read_nav(self.project_dir)
        if error:
            self._load_error = error
            return ScreenState.UNKNOWN
        device_error = self._ensure_device()
        if device_error:
            self._load_error = device_error
            return ScreenState.UNKNOWN
        try:
            frame = self._capture()
            records = [((data.get("base_to_locations") or {}).get("png"), ScreenState.MAIN_BASE)]
            records += [((data.get("enter_dungeon") or {}).get("png"), ScreenState.HERO_MENU)]
            for item in _locations(data):
                records.append((item.get("png"), ScreenState.LOCATION))
                for floor in item.get("floors", []):
                    if isinstance(floor, dict):
                        records.append((floor.get("png"), ScreenState.FLOOR))
            best: tuple[float, ScreenState] | None = None
            for raw, state in records:
                path = _resolve_png(self.project_dir, raw)
                if path:
                    match = self._match(frame, path)
                    if match is not None and (best is None or match.score > best[0]):
                        best = (match.score, state)
            self._state = best[1] if best else ScreenState.UNKNOWN
        except Exception:
            self._state = ScreenState.UNKNOWN
        return self._state

    def recover_to_main_base(self) -> NavigationResult:
        for attempt in range(1, self.max_attempts + 1):
            state = self.detect_screen()
            if state == ScreenState.MAIN_BASE:
                return NavigationResult(True, state, attempts=attempt)
            error = self._ensure_device()
            if error:
                return NavigationResult(False, state, error, attempt)
            try:
                self._adb.back()
            except Exception as exc:
                return NavigationResult(False, state, f"ADB back failed: {exc}", attempt)
            time.sleep(self.poll)
        return NavigationResult(False, ScreenState.UNKNOWN, "main base was not detected during recovery", self.max_attempts)

    def go_to_location_select(self) -> NavigationResult:
        data, error = _read_nav(self.project_dir)
        if error: return NavigationResult(False, ScreenState.UNKNOWN, error)
        if self.detect_screen() == ScreenState.LOCATION_SELECT:
            return NavigationResult(True, ScreenState.LOCATION_SELECT)
        return self._tap(_resolve_png(self.project_dir, (data or {}).get("base_to_locations", {}).get("png")), (ScreenState.LOCATION_SELECT,), "base_to_locations")

    def select_location(self, location_id: str) -> NavigationResult:
        data, error = _read_nav(self.project_dir)
        if error: return NavigationResult(False, ScreenState.UNKNOWN, error)
        item = _location(data or {}, location_id)
        if item is None: return NavigationResult(False, ScreenState.LOCATION_SELECT, f"Unknown location id: {location_id}")
        return self._tap(_resolve_png(self.project_dir, item.get("png")), (ScreenState.LOCATION,), f"location:{location_id}")

    def select_floor(self, location_id: str, floor_number: int) -> NavigationResult:
        data, error = _read_nav(self.project_dir)
        if error: return NavigationResult(False, ScreenState.UNKNOWN, error)
        item = _location(data or {}, location_id)
        floor = next((x for x in (item or {}).get("floors", []) if isinstance(x, dict) and str(x.get("name")) == str(floor_number)), None)
        if floor is None: return NavigationResult(False, ScreenState.LOCATION, f"Floor template not found: {location_id}/{floor_number}")
        return self._tap(_resolve_png(self.project_dir, floor.get("png")), (ScreenState.FLOOR, ScreenState.HERO_MENU), f"floor:{location_id}/{floor_number}")

    def open_hero_menu(self) -> NavigationResult:
        if self.detect_screen() == ScreenState.HERO_MENU:
            return NavigationResult(True, ScreenState.HERO_MENU)
        data, error = _read_nav(self.project_dir)
        if error: return NavigationResult(False, ScreenState.UNKNOWN, error)
        return self._tap(_resolve_png(self.project_dir, (data or {}).get("enter_dungeon", {}).get("png")), (ScreenState.HERO_MENU,), "enter_dungeon")

    def start_farm(self) -> NavigationResult:
        if self.detect_screen() == ScreenState.FARM:
            return NavigationResult(True, ScreenState.FARM)
        return NavigationResult(False, self._state, "farm start confirmation is not available in data/base/nav.json")

    def navigate_from_current_screen(self, location_id: str, floor_number: int) -> NavigationResult:
        for step in (self.recover_to_main_base, self.go_to_location_select, lambda: self.select_location(location_id), lambda: self.select_floor(location_id, floor_number), self.open_hero_menu):
            result = step()
            if not result.ok: return result
        return self.start_farm()

    navigate_to_farm = navigate_from_current_screen


def go_to_dungeon(project_dir: str | Path, location_name: str, floor_name: str, log: Callable[..., Any] = print, stop_event: Any = None) -> bool:
    try:
        floor_number = int(floor_name)
    except (TypeError, ValueError):
        log(f"invalid floor: {floor_name}")
        return False
    result = NavigationController(project_dir, log, stop_event).navigate_from_current_screen(location_name, floor_number)
    if not result.ok: log(result.message)
    return result.ok


def verify_chain(project_dir: str | Path, location_name: str, floor_name: str, log: Callable[..., Any] = print) -> bool:
    status = nav_status(project_dir)
    if not status.get("ready"):
        log(status.get("error") or "navigation profile is incomplete")
        return False
    data, error = _read_nav(project_dir)
    item = _location(data or {}, location_name) if not error else None
    floor = next((x for x in (item or {}).get("floors", []) if str(x.get("name")) == str(floor_name)), None)
    if item is None:
        log(f"Unknown location id: {location_name}")
        return False
    if floor is None or not _resolve_png(project_dir, floor.get("png")):
        log(f"Floor template not found: {location_name}/{floor_name}")
        return False
    log("navigation chain is ready")
    return True


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Template-driven game navigation")
    parser.add_argument("--go", nargs=2, metavar=("LOCATION", "FLOOR"))
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    if not args.go: parser.print_help(); return 0
    return 0 if go_to_dungeon(args.project_dir, args.go[0], args.go[1]) else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
