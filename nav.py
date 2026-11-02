from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None


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
    needle = str(name).strip().lower()
    for item in _locations(data):
        if not isinstance(item, dict):
            continue
        candidates = [item.get("name"), item.get("slot")]
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip().lower() == needle:
                return item
    return None


def _floor(location: dict[str, Any], floor_name: str) -> Optional[dict[str, Any]]:
    needle = str(floor_name).strip().lower()
    floors = location.get("floors", [])
    if not isinstance(floors, list):
        return None
    for item in floors:
        if not isinstance(item, dict):
            continue
        candidate = item.get("name")
        if isinstance(candidate, str) and candidate.strip().lower() == needle:
            return item
    return None


def nav_status(project_dir: str | Path) -> dict[str, Any]:
    data, error = _read_nav(project_dir)
    if error:
        return {"ready": False, "error": error, "steps": {"profile": False}}
    assert data is not None
    base = data.get("base_to_locations", {})
    enter = data.get("enter_dungeon", {})
    steps: dict[str, Any] = {
        "profile": True,
        "base_to_locations": _png(project_dir, base.get("png")) is not None if isinstance(base, dict) else False,
        "enter_dungeon": _png(project_dir, enter.get("png")) is not None if isinstance(enter, dict) else False,
        "locations": [],
    }
    for item in _locations(data):
        if not isinstance(item, dict):
            continue
        floor_rows = []
        floors = item.get("floors", []) if isinstance(item.get("floors", []), list) else []
        for floor in floors:
            if isinstance(floor, dict):
                floor_rows.append({
                    "name": floor.get("name"),
                    "ready": _png(project_dir, floor.get("png")) is not None,
                })
        icon_ok = _png(project_dir, item.get("png")) is not None
        steps["locations"].append({
            "slot": item.get("slot"),
            "name": item.get("name"),
            "icon": icon_ok,
            "floors": floor_rows,
            "ready": icon_ok and bool(floor_rows) and all(x["ready"] for x in floor_rows),
        })
    steps["locations_ready"] = bool(steps["locations"]) and all(x["ready"] for x in steps["locations"])
    steps["ready"] = bool(steps["base_to_locations"] and steps["enter_dungeon"] and steps["locations_ready"])
    return {"ready": steps["ready"], "error": None, "steps": steps}


class NavigationController:
    def __init__(
        self,
        project_dir: str | Path = ".",
        log: Callable[..., Any] = print,
        stop_event: Any = None,
        *,
        adb: Any = None,
        screenshot: Any = None,
        vision: Any = None,
        base_dir: str | Path | None = None,
        logger: Any = None,
        **_: Any,
    ):
        chosen_dir = base_dir if base_dir is not None else project_dir
        self.project_dir = Path(chosen_dir).resolve()
        self.log = logger.info if logger is not None and hasattr(logger, "info") else log
        self.stop_event = stop_event
        self.adb = adb
        self.screenshot = screenshot
        self.vision = vision
        self.last_reason = ""

    def _stopped(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _sleep(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stopped():
                self.last_reason = "stopped"
                return False
            time.sleep(min(0.1, max(0.01, deadline - time.monotonic())))
        return True

    def _capture_stable(self, timeout: float = 6.0) -> tuple[Any, bool]:
        if not (self.screenshot and self.vision):
            self.last_reason = "navigation requires screenshot and vision"
            return None, False
        try:
            stable = self.vision.wait_until_stable(self.screenshot.capture, stable_timeout_s=timeout)
            if isinstance(stable, tuple) and len(stable) == 2:
                return stable[0], bool(stable[1])
            return stable, True
        except Exception as exc:
            self.last_reason = f"stable frame capture failed: {exc}"
            return None, False

    def _load_template(self, path: Path) -> Any:
        if cv2 is None:
            raise RuntimeError("opencv is not available")
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None or getattr(image, "size", 0) == 0:
            raise RuntimeError(f"template image is unreadable: {path}")
        return image

    def _find_template(self, frame: Any, png: Path, threshold: float = 0.86) -> Any:
        template = self._load_template(png)
        return self.vision.find(frame, template, (0, 0, frame.shape[1], frame.shape[0]), threshold)

    def _tap_center(self, rect: tuple[int, int, int, int]) -> bool:
        if not self.adb:
            self.last_reason = "navigation requires adb"
            return False
        x1, y1, x2, y2 = rect
        self.adb.tap((x1 + x2) // 2, (y1 + y2) // 2)
        return True

    def _visible(self, png: Path, label: str, threshold: float = 0.86) -> bool:
        frame, _ = self._capture_stable(timeout=3.0)
        if frame is None:
            return False
        try:
            return self._find_template(frame, png, threshold) is not None
        except Exception as exc:
            self.last_reason = f"{label}: {exc}"
            return False

    def detect_current_screen(self, data: dict[str, Any]) -> ScreenState:
        try:
            checks: list[tuple[ScreenState, Optional[Path]]] = []
            enter = data.get("enter_dungeon", {})
            base = data.get("base_to_locations", {})
            if isinstance(enter, dict):
                checks.append((ScreenState.HERO_MENU, _png(self.project_dir, enter.get("png"))))
            if isinstance(base, dict):
                checks.append((ScreenState.MAIN_BASE, _png(self.project_dir, base.get("png"))))
            for state, png in checks:
                if png and self._visible(png, state.value):
                    return state
            for item in _locations(data):
                if not isinstance(item, dict):
                    continue
                icon = _png(self.project_dir, item.get("png"))
                if icon and self._visible(icon, "location-icon"):
                    return ScreenState.LOCATION_SELECT
                floors = item.get("floors", []) if isinstance(item.get("floors", []), list) else []
                for floor in floors:
                    if isinstance(floor, dict):
                        floor_png = _png(self.project_dir, floor.get("png"))
                        if floor_png and self._visible(floor_png, "floor-button"):
                            return ScreenState.LOCATION
        except Exception:
            pass
        return ScreenState.UNKNOWN

    def return_to_base(self, data: dict[str, Any], timeout: float = 25.0) -> tuple[bool, str]:
        started = time.monotonic()
        last_state = ScreenState.UNKNOWN
        while time.monotonic() - started < timeout:
            if self._stopped():
                return False, "stopped"
            state = self.detect_current_screen(data)
            last_state = state
            if state == ScreenState.MAIN_BASE:
                return True, "base detected"
            if self.adb is None:
                return False, "navigation requires adb"
            try:
                self.adb.back()
            except Exception as exc:
                return False, f"back failed: {exc}"
            self._sleep(1.2)
        return False, f"failed to return to base from {last_state.value}"

    def _tap_template_until_next(
        self,
        png: Path,
        label: str,
        next_png: Optional[Path] = None,
        *,
        threshold: float = 0.86,
        timeout: float = 18.0,
        retries: int = 3,
    ) -> tuple[bool, str]:
        started = time.monotonic()
        for attempt in range(1, retries + 1):
            while time.monotonic() - started < timeout:
                if self._stopped():
                    return False, "stopped"
                frame, stable = self._capture_stable(timeout=4.0)
                if frame is None:
                    self._sleep(0.4)
                    continue
                try:
                    match = self._find_template(frame, png, threshold)
                except Exception as exc:
                    return False, f"{label}: {exc}"
                if match is None:
                    self._sleep(0.4)
                    continue
                if not self._tap_center(match.rect):
                    return False, self.last_reason or f"failed to tap {label}"
                self._sleep(1.0 if stable else 1.4)
                if next_png is None:
                    return True, f"{label} tapped"
                verify_until = time.monotonic() + 6.0
                while time.monotonic() < verify_until:
                    verify_frame, _ = self._capture_stable(timeout=3.0)
                    if verify_frame is None:
                        self._sleep(0.2)
                        continue
                    try:
                        if self._find_template(verify_frame, next_png, threshold) is not None:
                            return True, f"{label} tapped"
                    except Exception:
                        pass
                    self._sleep(0.3)
            self.log(f"retry {attempt}/{retries} for {label}")
        return False, f"timeout waiting for {label}"

    def navigate_from_current_screen(self, location_id: str, floor_number: int) -> NavigationResult:
        return self._run_chain(location_id=location_id, floor_name=str(floor_number), verify_only=False)

    def _run_chain(self, location_id: str, floor_name: str, verify_only: bool) -> NavigationResult:
        data, error = _read_nav(self.project_dir)
        floor_number = int(floor_name) if str(floor_name).isdigit() else None
        if error:
            return NavigationResult(False, ScreenState.UNKNOWN, error, location_id=location_id, floor_number=floor_number)
        assert data is not None
        loc = _location(data, location_id)
        if loc is None:
            return NavigationResult(False, ScreenState.LOCATION_SELECT, f"unknown location: {location_id}", location_id=location_id, floor_number=floor_number)
        floor = _floor(loc, floor_name)
        if floor is None:
            return NavigationResult(False, ScreenState.FLOOR, f"unknown floor: {floor_name}", location_id=location_id, floor_number=floor_number)

        base_png = _png(self.project_dir, data.get("base_to_locations", {}).get("png") if isinstance(data.get("base_to_locations"), dict) else None)
        loc_png = _png(self.project_dir, loc.get("png"))
        floor_png = _png(self.project_dir, floor.get("png"))
        enter_png = _png(self.project_dir, data.get("enter_dungeon", {}).get("png") if isinstance(data.get("enter_dungeon"), dict) else None)
        required = [(base_png, "base_to_locations"), (loc_png, "location"), (floor_png, "floor"), (enter_png, "enter_dungeon")]
        for png, label in required:
            if png is None:
                return NavigationResult(False, ScreenState.UNKNOWN, f"template missing: {label}", location_id=location_id, floor_number=floor_number)

        ok, reason = self.return_to_base(data)
        if not ok:
            return NavigationResult(False, ScreenState.UNKNOWN, reason, 1, location_id, floor_number)

        ok, reason = self._tap_template_until_next(base_png, "base → locations", next_png=loc_png)
        if not ok:
            return NavigationResult(False, ScreenState.MAIN_BASE, reason, 1, location_id, floor_number)

        ok, reason = self._tap_template_until_next(loc_png, f"location {loc.get('name')}", next_png=floor_png)
        if not ok:
            return NavigationResult(False, ScreenState.LOCATION_SELECT, reason, 1, location_id, floor_number)

        ok, reason = self._tap_template_until_next(floor_png, f"floor {floor.get('name')}", next_png=enter_png)
        if not ok:
            return NavigationResult(False, ScreenState.LOCATION, reason, 1, location_id, floor_number)

        if verify_only:
            return NavigationResult(True, ScreenState.HERO_MENU, "chain verified up to hero menu", 1, location_id, floor_number)

        ok, reason = self._tap_template_until_next(enter_png, "enter dungeon", next_png=None)
        if not ok:
            return NavigationResult(False, ScreenState.HERO_MENU, reason, 1, location_id, floor_number)
        return NavigationResult(True, ScreenState.FARM, "navigation chain completed", 1, location_id, floor_number)


def go_to_dungeon(
    project_dir: str | Path,
    location_name: str,
    floor_name: str,
    log: Callable[..., Any] = print,
    stop_event: Any = None,
) -> tuple[bool, str]:
    status = nav_status(project_dir)
    if not status["ready"]:
        reason = status.get("error") or "navigation profile is incomplete"
        log(reason)
        return False, reason
    controller = NavigationController(project_dir=project_dir, log=log, stop_event=stop_event)
    result = controller._run_chain(location_name, floor_name, verify_only=False)
    if not result.ok:
        log(result.message)
    return result.ok, result.message


def verify_chain(
    project_dir: str | Path,
    location_name: str,
    floor_name: str,
    log: Callable[..., Any] = print,
) -> bool:
    status = nav_status(project_dir)
    if not status["ready"]:
        log(status.get("error") or "navigation profile is incomplete")
        return False
    controller = NavigationController(project_dir=project_dir, log=log)
    result = controller._run_chain(location_name, floor_name, verify_only=True)
    if not result.ok:
        log(result.message)
    return result.ok


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
