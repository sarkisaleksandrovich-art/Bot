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


_LOCATION_IDS = tuple(f"loc{i}" for i in range(1, 8))
_STATE_KEYS = {
    "main_base": ScreenState.MAIN_BASE,
    "location_select": ScreenState.LOCATION_SELECT,
    "location": ScreenState.LOCATION,
    "floor": ScreenState.FLOOR,
    "hero_menu": ScreenState.HERO_MENU,
    "farm": ScreenState.FARM,
}


def _profile_path(project_dir: str | Path) -> Path:
    return Path(project_dir).resolve() / "data" / "base" / "nav.json"


def _read_profile(project_dir: str | Path) -> tuple[dict[str, Any] | None, str | None]:
    path = _profile_path(project_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"navigation profile is missing: {path}"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"cannot read navigation profile: {exc}"
    if not isinstance(data, dict):
        return None, "navigation profile must be a JSON object"
    return data, None


def _resolve(project_dir: str | Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else Path(project_dir).resolve() / "data" / "base" / path


def _png(value: Any, project_dir: str | Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = _resolve(project_dir, value)
    return path if path.is_file() else None


def _locations(profile: dict[str, Any]) -> list[dict[str, Any]]:
    value = profile.get("locations", [])
    return value if isinstance(value, list) else []


def _location(profile: dict[str, Any], name: str) -> dict[str, Any] | None:
    for item in _locations(profile):
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def nav_status(project_dir: str | Path) -> dict[str, Any]:
    """Return readiness of every navigation step; never raises for bad/missing data."""
    profile, error = _read_profile(project_dir)
    if error:
        return {"ready": False, "error": error, "steps": {"profile": False}}
    locations = _locations(profile or {})
    steps: dict[str, Any] = {
        "profile": True,
        "base_to_locations": bool(_png((profile or {}).get("base_to_locations", {}).get("png"), project_dir)),
        "locations": [],
        "enter_dungeon": bool(_png((profile or {}).get("enter_dungeon", {}).get("png"), project_dir)),
    }
    for item in locations:
        if not isinstance(item, dict):
            continue
        floors = item.get("floors", []) if isinstance(item.get("floors", []), list) else []
        steps["locations"].append({
            "slot": item.get("slot"),
            "name": item.get("name"),
            "png": bool(_png(item.get("png"), project_dir)),
            "floors": [
                {"name": f.get("name"), "png": bool(_png(f.get("png"), project_dir))}
                for f in floors if isinstance(f, dict)
            ],
        })
    steps["locations_ready"] = bool(steps["locations"]) and all(
        x["png"] and any(f["png"] for f in x["floors"]) for x in steps["locations"]
    )
    steps["ready"] = bool(steps["base_to_locations"] and steps["enter_dungeon"] and steps["locations_ready"])
    return {"ready": steps["ready"], "error": None, "steps": steps}


class NavigationController:
    """Template-driven navigation adapter. Device execution is intentionally lazy."""

    def __init__(self, project_dir: str | Path, log: Callable[..., Any] = print, stop_event: Any = None):
        self.project_dir = Path(project_dir).resolve()
        self.log = log
        self.stop_event = stop_event
        self.logger = logging.getLogger(__name__)
        self.max_attempts = 3
        self.timeout = 12.0
        self.poll = 0.5
        self._adb: Any = None
        self._screenshot: Any = None
        self._vision: Any = None
        self._state = ScreenState.UNKNOWN

    def _cancelled(self) -> bool:
        return bool(self.stop_event is not None and self.stop_event.is_set())

    def _log(self, message: str) -> None:
        try:
            self.log(message)
        except Exception:
            self.logger.debug(message, exc_info=True)

    def _ensure_device(self) -> str | None:
        if self._adb is not None:
            return None
        try:
            from bot.adb_controller import ADBController
            from bot.screenshot import ScreenshotProvider
            from bot.vision import VisionEngine
            try:
                from bot.config import load_config
                config = load_config(str(self.project_dir / "config.yaml"))
                self._adb = ADBController(config, self.logger)
                self._screenshot = ScreenshotProvider(self._adb, config, self.logger)
                self._vision = VisionEngine(config, self.logger)
            except Exception as exc:
                return f"cannot initialize device services: {exc}"
            self._adb.connect()
        except Exception as exc:
            return f"device is unavailable: {exc}"
        return None

    def _capture(self) -> Any:
        return self._screenshot.capture()

    def _match(self, frame: Any, path: Path) -> Any | None:
        try:
            import cv2
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                return None
            return self._vision.find(frame, image, (0, 0, frame.shape[1], frame.shape[0]), 0.88)
        except Exception:
            return None

    def _tap_template(self, path: Path) -> bool:
        frame = self._capture()
        match = self._match(frame, path)
        if match is None:
            return False
        x1, y1, x2, y2 = match.rect
        self._adb.tap((x1 + x2) // 2, (y1 + y2) // 2)
        return True

    def detect_screen(self) -> ScreenState:
        profile, error = _read_profile(self.project_dir)
        if error or not profile:
            return ScreenState.UNKNOWN
        if self._ensure_device():
            return ScreenState.UNKNOWN
        try:
            frame = self._capture()
        except Exception:
            return ScreenState.UNKNOWN
        candidates: list[tuple[float, ScreenState]] = []
        records = [
            (profile.get("base_to_locations", {}), ScreenState.MAIN_BASE),
            (profile.get("enter_dungeon", {}), ScreenState.HERO_MENU),
        ]
        for item in _locations(profile):
            if isinstance(item, dict):
                records.append((item, ScreenState.LOCATION))
                for floor in item.get("floors", []):
                    if isinstance(floor, dict):
                        records.append((floor, ScreenState.FLOOR))
        for record, state in records:
            path = _png(record.get("png") if isinstance(record, dict) else None, self.project_dir)
            if path is None:
                continue
            match = self._match(frame, path)
            if match is not None:
                candidates.append((float(getattr(match, "score", 1.0)), state))
        if candidates:
            self._state = max(candidates, key=lambda x: x[0])[1]
        return self._state

    def _result(self, ok: bool, state: ScreenState, message: str = "", attempts: int = 1) -> NavigationResult:
        return NavigationResult(ok, state, message, attempts)

    def _wait_state(self, expected: tuple[ScreenState, ...], deadline: float) -> ScreenState:
        while time.monotonic() < deadline and not self._cancelled():
            state = self.detect_screen()
            if state in expected:
                return state
            time.sleep(self.poll)
        return ScreenState.UNKNOWN

    def recover_to_main_base(self) -> NavigationResult:
        for attempt in range(1, self.max_attempts + 1):
            if self._cancelled():
                return self._result(False, ScreenState.UNKNOWN, "cancelled", attempt)
            state = self.detect_screen()
            if state == ScreenState.MAIN_BASE:
                return self._result(True, state, attempts=attempt)
            if self._ensure_device():
                return self._result(False, state, "device is unavailable", attempt)
            try:
                self._adb.back()
            except Exception as exc:
                return self._result(False, state, f"ADB back failed: {exc}", attempt)
            time.sleep(self.poll)
        return self._result(False, ScreenState.UNKNOWN, "main base was not detected during recovery", self.max_attempts)

    def _click_step(self, path: Path | None, expected: tuple[ScreenState, ...], label: str) -> NavigationResult:
        if path is None:
            return self._result(False, self._state, f"template is missing: {label}")
        for attempt in range(1, self.max_attempts + 1):
            if self._cancelled():
                return self._result(False, self._state, "cancelled", attempt)
            if self._ensure_device():
                return self._result(False, self._state, "device is unavailable", attempt)
            try:
                if not self._tap_template(path):
                    time.sleep(self.poll)
                    continue
                state = self._wait_state(expected, time.monotonic() + self.timeout)
                if state in expected:
                    self._state = state
                    return self._result(True, state, attempts=attempt)
            except Exception as exc:
                self._log(f"navigation retry {label}: {exc}")
        return self._result(False, ScreenState.UNKNOWN, f"expected screen was not detected after {label}", self.max_attempts)

    def go_to_location_select(self) -> NavigationResult:
        profile, error = _read_profile(self.project_dir)
        if error or not profile:
            return self._result(False, ScreenState.UNKNOWN, error or "base knowledge file is missing")
        state = self.detect_screen()
        if state == ScreenState.LOCATION_SELECT:
            return self._result(True, state)
        if state != ScreenState.MAIN_BASE:
            return self._result(False, state, "expected main_base")
        return self._click_step(_png(profile.get("base_to_locations", {}).get("png"), self.project_dir), (ScreenState.LOCATION_SELECT,), "base_to_locations")

    def select_location(self, location_id: str) -> NavigationResult:
        if location_id not in _LOCATION_IDS:
            raise ValueError(f"location_id must be one of {_LOCATION_IDS}")
        profile, error = _read_profile(self.project_dir)
        if error or not profile:
            return self._result(False, ScreenState.UNKNOWN, error or "base knowledge file is missing")
        state = self.detect_screen()
        if state != ScreenState.LOCATION_SELECT:
            return self._result(False, state, "expected location_select")
        item = next((x for x in _locations(profile) if isinstance(x, dict) and str(x.get("slot")) == location_id), None)
        if item is None:
            return self._result(False, state, f"location template is missing: {location_id}")
        return self._click_step(_png(item.get("png"), self.project_dir), (ScreenState.LOCATION,), location_id)

    def select_floor(self, location_id: str, floor_number: int) -> NavigationResult:
        if location_id not in _LOCATION_IDS:
            raise ValueError(f"location_id must be one of {_LOCATION_IDS}")
        if not isinstance(floor_number, int) or isinstance(floor_number, bool) or floor_number < 1:
            raise ValueError("floor_number must be a positive integer")
        profile, error = _read_profile(self.project_dir)
        if error or not profile:
            return self._result(False, ScreenState.UNKNOWN, error or "base knowledge file is missing")
        item = next((x for x in _locations(profile) if isinstance(x, dict) and str(x.get("slot")) == location_id), None)
        floor = next((x for x in (item or {}).get("floors", []) if isinstance(x, dict) and str(x.get("name")) == str(floor_number)), None)
        state = self.detect_screen()
        if floor is None:
            return self._result(False, ScreenState.LOCATION, f"floor template is missing: {location_id}/{floor_number}")
        return self._click_step(_png(floor.get("png"), self.project_dir), (ScreenState.FLOOR,), f"{location_id}/{floor_number}")

    def open_hero_menu(self) -> NavigationResult:
        profile, error = _read_profile(self.project_dir)
        if error or not profile:
            return self._result(False, ScreenState.UNKNOWN, error or "base knowledge file is missing")
        state = self.detect_screen()
        if state == ScreenState.HERO_MENU:
            return self._result(True, state)
        return self._click_step(_png(profile.get("enter_dungeon", {}).get("png"), self.project_dir), (ScreenState.HERO_MENU,), "enter_dungeon")

    def start_farm(self) -> NavigationResult:
        profile, error = _read_profile(self.project_dir)
        if error or not profile:
            return self._result(False, ScreenState.UNKNOWN, error or "base knowledge file is missing")
        state = self.detect_screen()
        if state != ScreenState.HERO_MENU:
            return self._result(False, state, "expected hero_menu")
        # The compact nav.json contract has one final action: enter_dungeon.
        return self._click_step(_png(profile.get("enter_dungeon", {}).get("png"), self.project_dir), (ScreenState.FARM,), "enter_dungeon")

    def navigate_to_farm_start(self, target: NavigationTarget) -> NavigationResult:
        state = self.detect_screen()
        if state == ScreenState.FARM:
            return self._result(True, state, "already in farm")
        steps = (
            self.recover_to_main_base,
            self.go_to_location_select,
            lambda: self.select_location(target.location_id),
            lambda: self.select_floor(target.location_id, target.floor_number),
            self.open_hero_menu,
            self.start_farm,
        )
        attempts = 0
        for step in steps:
            result = step()
            attempts += result.attempts
            if not result.ok:
                return NavigationResult(False, result.state, result.message, attempts)
        return NavigationResult(True, ScreenState.FARM, "farm start confirmed", attempts)


def _controller(project_dir: str | Path, log: Callable[..., Any] = print, stop_event: Any = None) -> NavigationController:
    return NavigationController(project_dir, log, stop_event)


def detect_screen(project_dir: str | Path = ".") -> ScreenState:
    return _controller(project_dir).detect_screen()


def recover_to_main_base(project_dir: str | Path = ".") -> NavigationResult:
    return _controller(project_dir).recover_to_main_base()


def go_to_location_select(project_dir: str | Path = ".") -> NavigationResult:
    return _controller(project_dir).go_to_location_select()


def select_location(project_dir: str | Path, location_id: str) -> NavigationResult:
    return _controller(project_dir).select_location(location_id)


def select_floor(project_dir: str | Path, location_id: str, floor_number: int) -> NavigationResult:
    return _controller(project_dir).select_floor(location_id, floor_number)


def open_hero_menu(project_dir: str | Path = ".") -> NavigationResult:
    return _controller(project_dir).open_hero_menu()


def start_farm(project_dir: str | Path = ".") -> NavigationResult:
    return _controller(project_dir).start_farm()


def navigate_to_farm_start(project_dir: str | Path, target: NavigationTarget) -> NavigationResult:
    return _controller(project_dir).navigate_to_farm_start(target)


def verify_chain(project_dir: str | Path, location_name: str, floor_name: str, log: Callable[..., Any] = print) -> bool:
    """Validate the complete profile chain without touching the device."""
    profile, error = _read_profile(project_dir)
    if error:
        log(error)
        return False
    item = _location(profile or {}, location_name)
    floor = next((f for f in (item or {}).get("floors", []) if isinstance(f, dict) and f.get("name") == floor_name), None)
    checks = [
        ("base_to_locations", _png((profile or {}).get("base_to_locations", {}).get("png"), project_dir)),
        (f"location:{location_name}", _png((item or {}).get("png"), project_dir) if item else None),
        (f"floor:{floor_name}", _png((floor or {}).get("png"), project_dir) if floor else None),
        ("enter_dungeon", _png((profile or {}).get("enter_dungeon", {}).get("png"), project_dir)),
    ]
    ok = True
    for label, path in checks:
        ready = path is not None
        log(f"{label}: {'OK' if ready else 'MISSING'}")
        ok = ok and ready
    return ok


def go_to_dungeon(project_dir: str | Path, location_name: str, floor_name: str, log: Callable[..., Any] = print, stop_event: Any = None) -> bool:
    """Run the device chain; returns False and logs the first honest reason on failure."""
    profile, error = _read_profile(project_dir)
    if error:
        log(error)
        return False
    item = _location(profile or {}, location_name)
    if item is None:
        log(f"location is missing: {location_name}")
        return False
    try:
        slot = str(item.get("slot", ""))
        floor = next(f for f in item.get("floors", []) if isinstance(f, dict) and f.get("name") == floor_name)
        target = NavigationTarget(slot, int(floor_name))
    except (StopIteration, ValueError, TypeError):
        log(f"floor is missing or invalid: {location_name}/{floor_name}")
        return False
    result = _controller(project_dir, log, stop_event).navigate_to_farm_start(target)
    if not result.ok:
        log(f"navigation failed: {result.message}")
    return result.ok


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Template-driven game navigation")
    parser.add_argument("--go", nargs=2, metavar=("LOCATION", "FLOOR"))
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    if not args.go:
        parser.print_help()
        return 0
    return 0 if go_to_dungeon(args.project_dir, args.go[0], args.go[1]) else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
