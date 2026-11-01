from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


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


class NavigationController:
    """Template-driven, retrying navigation state machine.

    The controller deliberately uses the repository's existing public contracts:
    ``screenshot.capture()``, ``vision.find()``/``find_any()``, and
    ``adb.tap()``/``adb.back()``.  It does not import GUI widgets or modify the
    existing profile knowledge files.
    """

    def __init__(
        self,
        adb: Any,
        screenshot: Any,
        vision: Any,
        base_dir: Path,
        logger: Any,
        max_recovery_steps: int = 3,
    ) -> None:
        if max_recovery_steps < 0:
            raise ValueError("max_recovery_steps must be >= 0")
        self.adb = adb
        self.screenshot = screenshot
        self.vision = vision
        self.base_dir = Path(base_dir)
        self.logger = logger
        self.max_recovery_steps = max_recovery_steps
        self._knowledge: dict[str, Any] | None = None

    def load_base_knowledge(self) -> dict[str, Any]:
        path = self.base_dir / "data" / "profiles" / "base" / "knowledge.json"
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            self._log("error", "Unable to load base knowledge %s: %s", path, exc)
            return {}
        self._knowledge = data if isinstance(data, dict) else {}
        return self._knowledge

    def detect_screen(self) -> ScreenState:
        knowledge = self._knowledge or self.load_base_knowledge()
        if not knowledge:
            return ScreenState.UNKNOWN
        try:
            frame = self.screenshot.capture()
        except Exception as exc:  # normal device/capture failure is a state result
            self._log("warning", "Screen capture failed: %s", exc)
            return ScreenState.UNKNOWN

        records: list[tuple[ScreenState, dict[str, Any]]] = []
        screen_templates = knowledge.get("screen_templates", {})
        for name, state in (("main_base", ScreenState.MAIN_BASE),
                            ("location_select", ScreenState.LOCATION_SELECT),
                            ("hero_menu_start", ScreenState.HERO_MENU)):
            record = screen_templates.get(name)
            if isinstance(record, dict) and record.get("enabled", True):
                records.append((state, record))

        best: tuple[float, ScreenState] | None = None
        for state, record in records:
            match = self._find(frame, record)
            if match is not None and (best is None or match.score > best[0]):
                best = (match.score, state)
        if best is not None:
            return best[1]
        return ScreenState.UNKNOWN

    def recover_to_main_base(self) -> NavigationResult:
        for attempt in range(self.max_recovery_steps + 1):
            state = self.detect_screen()
            if state == ScreenState.MAIN_BASE:
                return NavigationResult(True, state, attempts=attempt + 1)
            if attempt >= self.max_recovery_steps:
                break
            try:
                self.adb.back()
            except Exception as exc:
                return NavigationResult(False, state, f"ADB back failed: {exc}", attempt + 1)
            time.sleep(0.25)
        return NavigationResult(False, ScreenState.UNKNOWN,
                                "main_base was not detected during recovery",
                                self.max_recovery_steps + 1)

    def go_to_location_select(self) -> NavigationResult:
        state = self.detect_screen()
        if state == ScreenState.LOCATION_SELECT:
            return NavigationResult(True, state, attempts=1)
        if state != ScreenState.MAIN_BASE:
            return NavigationResult(False, state, "expected main_base", 1)
        record = self._screen_record("main_base")
        result = self._tap_record(record, "location_select")
        if not result.ok:
            return result
        return self._wait_for((ScreenState.LOCATION_SELECT,), result.attempts)

    def select_location(self, location_id: str) -> NavigationResult:
        self._validate_location(location_id)
        state = self.detect_screen()
        if state != ScreenState.LOCATION_SELECT:
            return NavigationResult(False, state, "expected location_select", 1)
        record = next((item for item in self._knowledge_or_empty().get("location_icons", [])
                       if item.get("location_id") == location_id), None)
        if not isinstance(record, dict):
            return NavigationResult(False, state, f"missing template for {location_id}", 1)
        result = self._tap_record(record, "location")
        if not result.ok:
            return result
        return self._wait_for((ScreenState.LOCATION, ScreenState.FLOOR), result.attempts)

    def select_floor(self, location_id: str, floor_number: int) -> NavigationResult:
        self._validate_location(location_id)
        if not isinstance(floor_number, int) or isinstance(floor_number, bool) or floor_number < 1:
            raise ValueError("floor_number must be an integer >= 1")
        state = self.detect_screen()
        if state not in (ScreenState.LOCATION, ScreenState.FLOOR):
            return NavigationResult(False, state, "expected location or floor screen", 1)
        manifest = (self.base_dir / "data" / "profiles" / location_id / "floors" /
                    f"floor_{floor_number:03d}" / "floor_templates.json")
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return NavigationResult(False, ScreenState.UNKNOWN,
                                    f"cannot load floor manifest {manifest}: {exc}", 1)
        templates = [item for item in data.get("templates", [])
                     if item.get("enabled", True) and item.get("category") in
                     ("floor_screen", "floor", "next_floor")]
        if not templates:
            return NavigationResult(False, state, "no active floor template", 1)
        result = self._tap_record(templates[0], "floor")
        if not result.ok:
            return result
        return self._wait_for((ScreenState.FLOOR, ScreenState.HERO_MENU), result.attempts)

    def open_hero_menu(self) -> NavigationResult:
        state = self.detect_screen()
        if state == ScreenState.HERO_MENU:
            return NavigationResult(True, state, attempts=1)
        if state != ScreenState.FLOOR:
            return NavigationResult(False, state, "expected floor screen", 1)
        return NavigationResult(False, state,
                                "hero_menu template must be supplied by floor manifest",
                                1)

    def start_farm(self) -> NavigationResult:
        state = self.detect_screen()
        if state != ScreenState.HERO_MENU:
            return NavigationResult(False, state, "expected hero menu", 1)
        result = self._tap_record(self._screen_record("hero_menu_start"), "farm")
        if not result.ok:
            return result
        return self._wait_for((ScreenState.FARM,), result.attempts,
                              allow_unknown_after_click=True)

    def navigate_to_farm_start(self, target: NavigationTarget) -> NavigationResult:
        self._validate_location(target.location_id)
        if not isinstance(target.floor_number, int) or target.floor_number < 1:
            raise ValueError("target.floor_number must be >= 1")
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

    def _knowledge_or_empty(self) -> dict[str, Any]:
        return self._knowledge or self.load_base_knowledge()

    def _screen_record(self, name: str) -> dict[str, Any]:
        record = self._knowledge_or_empty().get("screen_templates", {}).get(name, {})
        return record if isinstance(record, dict) else {}

    def _find(self, frame: Any, record: dict[str, Any]) -> Any | None:
        path = self._resolve(record, base=self.base_dir / "data" / "profiles" / "base")
        if not path.exists():
            return None
        threshold = float(record.get("threshold", 0.86))
        roi = tuple(record["roi"]) if record.get("roi") else None
        try:
            return self.vision.find(frame, str(path), roi, threshold)
        except TypeError:
            try:
                return self.vision.find(frame, str(path), threshold)
            except Exception:
                return None
        except Exception:
            return None

    def _tap_record(self, record: dict[str, Any], expected: str) -> NavigationResult:
        if not record:
            return NavigationResult(False, ScreenState.UNKNOWN, "empty template record", 1)
        try:
            frame = self.screenshot.capture()
            match = self._find(frame, record)
            if match is None:
                return NavigationResult(False, ScreenState.UNKNOWN,
                                        f"template not found: {record.get('id', '?')}", 1)
            x1, y1, x2, y2 = match.rect
            self.adb.tap((x1 + x2) // 2, (y1 + y2) // 2)
            self._log("debug", "Tapped %s score=%.3f rect=%s", record.get("id"), match.score, match.rect)
            return NavigationResult(True, ScreenState.UNKNOWN, attempts=1)
        except Exception as exc:
            return NavigationResult(False, ScreenState.UNKNOWN, f"tap failed: {exc}", 1)

    def _wait_for(self, expected: tuple[ScreenState, ...], attempts: int,
                  allow_unknown_after_click: bool = False) -> NavigationResult:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            state = self.detect_screen()
            if state in expected:
                return NavigationResult(True, state, attempts=attempts + 1)
            if allow_unknown_after_click and state == ScreenState.UNKNOWN:
                time.sleep(0.25)
                continue
            time.sleep(0.25)
        return NavigationResult(False, ScreenState.UNKNOWN,
                                "transition was not confirmed", attempts + 1)

    @staticmethod
    def _resolve(base: Path, record: dict[str, Any]) -> Path:
        value = record.get("file")
        if not isinstance(value, str) or not value:
            return Path("__missing_template__")
        path = Path(value)
        return path if path.is_absolute() else base / path

    @staticmethod
    def _validate_location(location_id: str) -> None:
        if location_id not in _LOCATION_IDS:
            raise ValueError(f"location_id must be one of {_LOCATION_IDS}")

    def _log(self, method: str, message: str, *args: Any) -> None:
        logger_method = getattr(self.logger, method, None)
        if callable(logger_method):
            logger_method(message, *args)
