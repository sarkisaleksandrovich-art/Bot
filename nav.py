from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None

DEFAULT_MATCH_THRESHOLD = 0.78
MIN_MATCH_THRESHOLD = 0.05
MAX_MATCH_THRESHOLD = 0.99
DEFAULT_CLICK_RADIUS_PX = 40
DEFAULT_APPROACH_TIMEOUT_S = 12.0
DEFAULT_HOLD_CHUNK_MS = 250
DEFAULT_FRAME_INTERVAL_MS = 250
DEFAULT_STABLE_TIMEOUT_S = 3.0
DEFAULT_FLOOR_NUMBER = 1
DEFAULT_ANIMATION_DIFF_THRESHOLD = 0.002

ACTION_KIND_LABELS = {
    "loot": "Лут",
    "mob": "Моб",
    "event_choice": "Событие",
    "event_skip": "Событие",
    "skip": "Пропуск",
}


@dataclass(frozen=True)
class NavigationRuntime:
    adb: Any
    screenshot: Any
    vision: Any


@dataclass(frozen=True)
class HoldWorkerState:
    side: str
    x: int
    y: int
    chunk_ms: int


_HOLD_LOCK = threading.RLock()
_HOLD_THREAD: Optional[threading.Thread] = None
_HOLD_STOP_EVENT: Optional[threading.Event] = None
_HOLD_ACTIVE_SIDE: Optional[str] = None
_MOTIONEVENT_SUPPORTED: Optional[bool] = None
_RUNTIME_CACHE: dict[str, NavigationRuntime] = {}


def _log(log: Callable[..., Any], message: str) -> None:
    try:
        log(message)
    except TypeError:
        log("%s", message)


def _project_key(project_dir: str | Path) -> str:
    return str(Path(project_dir).resolve())


def _nav_path(project_dir: str | Path) -> Path:
    return Path(project_dir).resolve() / "data" / "base" / "nav.json"


def _actions_path(project_dir: str | Path) -> Path:
    return Path(project_dir).resolve() / "data" / "base" / "actions.json"


def _read_json_file(path: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, f"{path} must contain a JSON object"
    return data, None


def _read_nav(project_dir: str | Path) -> tuple[dict[str, Any], list[str]]:
    path = _nav_path(project_dir)
    data, error = _read_json_file(path)
    notes: list[str] = []
    if error:
        notes.append(f"nav.json read error: {error}")
    return data or {}, notes


def _read_actions(project_dir: str | Path) -> tuple[dict[str, Any], list[str]]:
    path = _actions_path(project_dir)
    data, error = _read_json_file(path)
    notes: list[str] = []
    if error:
        notes.append(f"actions.json read error: {error}")
    return data or {}, notes


def _resolve_path(project_dir: str | Path, value: Any) -> Optional[Path]:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = Path(value)
    if raw.is_absolute():
        return raw if raw.exists() else None
    candidates = [
        Path(project_dir).resolve() / value,
        Path(project_dir).resolve() / "data" / "base" / value,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _sanitize_threshold(raw_value: Any, log: Callable[..., Any] = print) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        _log(log, f"match_threshold invalid={raw_value!r}; fallback to {DEFAULT_MATCH_THRESHOLD:.2f}")
        return DEFAULT_MATCH_THRESHOLD
    if not (MIN_MATCH_THRESHOLD <= value <= MAX_MATCH_THRESHOLD):
        _log(log, f"match_threshold out_of_range={value!r}; fallback to {DEFAULT_MATCH_THRESHOLD:.2f}")
        return DEFAULT_MATCH_THRESHOLD
    return value


def _settings(project_dir: str | Path, log: Callable[..., Any] = print) -> dict[str, Any]:
    nav, _ = _read_nav(project_dir)
    settings = nav.get("settings", {}) if isinstance(nav.get("settings"), dict) else {}
    return {
        "click_radius_px": int(settings.get("click_radius_px", DEFAULT_CLICK_RADIUS_PX) or DEFAULT_CLICK_RADIUS_PX),
        "approach_timeout_s": float(settings.get("approach_timeout_s", DEFAULT_APPROACH_TIMEOUT_S) or DEFAULT_APPROACH_TIMEOUT_S),
        "hold_chunk_ms": int(settings.get("hold_chunk_ms", DEFAULT_HOLD_CHUNK_MS) or DEFAULT_HOLD_CHUNK_MS),
        "frame_interval_ms": int(settings.get("frame_interval_ms", DEFAULT_FRAME_INTERVAL_MS) or DEFAULT_FRAME_INTERVAL_MS),
        "match_threshold": _sanitize_threshold(settings.get("match_threshold", DEFAULT_MATCH_THRESHOLD), log=log),
    }


def _load_image(path: Path) -> Any:
    if cv2 is None:
        raise RuntimeError("OpenCV is not available")
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None or getattr(image, "size", 0) == 0:
        raise RuntimeError(f"template unreadable: {path}")
    return image


def _center_from_rect(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    x1, y1, x2, y2 = rect
    return (x1 + x2) // 2, (y1 + y2) // 2


def _frame_shape(frame: Any) -> tuple[int, int]:
    if frame is None or not hasattr(frame, "shape"):
        raise RuntimeError("frame is missing")
    height, width = frame.shape[:2]
    return int(width), int(height)


def _extract_png(config_value: Any) -> Any:
    if isinstance(config_value, dict):
        return config_value.get("png")
    return config_value


def _stable_wait(runtime: NavigationRuntime, timeout_s: float, log: Callable[..., Any]) -> tuple[Any, bool]:
    try:
        stable = runtime.vision.wait_until_stable(runtime.screenshot.capture, stable_timeout_s=timeout_s)
        if isinstance(stable, tuple) and len(stable) == 2:
            frame, ok = stable
            _log(log, f"wait_until_stable timeout={timeout_s:.2f}s stable={bool(ok)}")
            return frame, bool(ok)
        _log(log, f"wait_until_stable timeout={timeout_s:.2f}s stable=True")
        return stable, True
    except Exception as exc:
        _log(log, f"wait_until_stable error: {exc}")
        raise


def _build_runtime(project_dir: str | Path, log: Callable[..., Any] = print) -> NavigationRuntime:
    key = _project_key(project_dir)
    cached = _RUNTIME_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from bot.config import load_config
        from bot.logging_setup import setup_logging
        from bot.adb_controller import ADBController
        from bot.screenshot import ScreenshotProvider
        from bot.vision import VisionEngine
    except Exception as exc:
        raise RuntimeError(f"failed to import bot modules: {exc}") from exc
    config = load_config(str(Path(project_dir).resolve() / "config.yaml"))
    logger = setup_logging(config)
    adb = ADBController(config, logger)
    adb.connect()
    screenshot = ScreenshotProvider(adb, config, logger)
    vision = VisionEngine(config, logger)
    runtime = NavigationRuntime(adb=adb, screenshot=screenshot, vision=vision)
    _RUNTIME_CACHE[key] = runtime
    _log(log, "runtime initialized")
    return runtime


def take_screenshot(project_dir: str | Path) -> tuple[str, Any]:
    runtime = _build_runtime(project_dir)
    frame = runtime.screenshot.capture()
    logs_dir = Path(project_dir).resolve() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = logs_dir / f"shot_{stamp}.png"
    runtime.screenshot.save(frame, str(path))
    return str(path), frame


def find_template(project_dir: str | Path, template: Any, frame: Any = None) -> tuple[bool, int, int, int, float]:
    runtime = _build_runtime(project_dir)
    settings = _settings(project_dir)
    threshold = settings["match_threshold"]
    work_frame = frame if frame is not None else runtime.screenshot.capture()
    template_path = _resolve_path(project_dir, template)
    if template_path is None:
        return False, 0, 0, 0, 0.0
    image = _load_image(template_path)
    width, height = _frame_shape(work_frame)
    try:
        match = runtime.vision.find(work_frame, image, (0, 0, width, height), threshold)
    except Exception:
        return False, 0, 0, 0, 0.0
    if match is None:
        return False, 0, 0, 0, 0.0
    x, y = _center_from_rect(match.rect)
    dx = x - (width // 2)
    return True, x, y, dx, float(match.score)


def _load_actions_list(project_dir: str | Path) -> list[dict[str, Any]]:
    actions, _ = _read_actions(project_dir)
    raw = actions.get("objects", actions.get("actions", actions.get("templates", [])))
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def find_known_objects(project_dir: str | Path, frame: Any) -> list[tuple[str, str, int, int, int, int, float]]:
    runtime = _build_runtime(project_dir)
    settings = _settings(project_dir)
    threshold = settings["match_threshold"]
    found: list[tuple[str, str, int, int, int, int, float]] = []
    for item in _load_actions_list(project_dir):
        png = _resolve_path(project_dir, item.get("png") or item.get("template") or item.get("file"))
        if png is None:
            continue
        try:
            image = _load_image(png)
            width, height = _frame_shape(frame)
            match = runtime.vision.find(frame, image, (0, 0, width, height), threshold)
        except Exception:
            continue
        if match is None:
            continue
        x1, y1, x2, y2 = match.rect
        found.append((
            str(item.get("action", "unknown")),
            str(item.get("name", png.stem)),
            int(x1),
            int(y1),
            int(x2 - x1),
            int(y2 - y1),
            float(match.score),
        ))
    found.sort(key=lambda row: row[6], reverse=True)
    return found


def annotate_known(frame: Any, objects: list[tuple[str, str, int, int, int, int, float]]) -> Any:
    if cv2 is None:
        return frame
    canvas = frame.copy()
    for kind, name, x, y, w, h, score in objects:
        label = ACTION_KIND_LABELS.get(kind, name)
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(canvas, f"{label} {score:.3f}", (x, max(20, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1, cv2.LINE_AA)
    return canvas


def memory_summary(project_dir: str | Path) -> str:
    counts = {"loot": 0, "mob": 0, "event_choice": 0, "event_skip": 0, "skip": 0}
    for item in _load_actions_list(project_dir):
        action = str(item.get("action", "")).strip()
        if action in counts:
            counts[action] += 1
    events = counts["event_choice"] + counts["event_skip"]
    return f"В памяти: лут {counts['loot']}, мобы {counts['mob']}, события {events}"


def match_known(project_dir: str | Path, crop: Any) -> Optional[str]:
    runtime = _build_runtime(project_dir)
    settings = _settings(project_dir)
    threshold = settings["match_threshold"]
    best_action: Optional[str] = None
    best_score = 0.0
    for item in _load_actions_list(project_dir):
        png = _resolve_path(project_dir, item.get("png") or item.get("template") or item.get("file"))
        if png is None:
            continue
        try:
            image = _load_image(png)
            ch, cw = crop.shape[:2]
            match = runtime.vision.find(crop, image, (0, 0, cw, ch), threshold)
        except Exception:
            continue
        if match is None:
            continue
        if float(match.score) > best_score:
            best_score = float(match.score)
            best_action = str(item.get("action", "")).strip() or None
    return best_action if best_score >= threshold else None


def _joystick(project_dir: str | Path) -> dict[str, tuple[int, int]]:
    nav, _ = _read_nav(project_dir)
    joystick = nav.get("joystick", {}) if isinstance(nav.get("joystick"), dict) else {}
    result: dict[str, tuple[int, int]] = {}
    for side in ("left", "right"):
        item = joystick.get(side, {}) if isinstance(joystick.get(side), dict) else {}
        try:
            result[side] = (int(item.get("x")), int(item.get("y")))
        except Exception:
            pass
    return result


def _hold_swipe_worker(runtime: NavigationRuntime, state: HoldWorkerState, stop_event: threading.Event) -> None:
    # One longer-lived swipe keeps the fallback movement active without spawning
    # a new adb process for every short chunk.
    try:
        runtime.adb.swipe(state.x, state.y, state.x, state.y, max(state.chunk_ms, 1000))
    except Exception:
        pass
    while not stop_event.wait(0.1):
        pass


def _probe_motionevent(runtime: NavigationRuntime) -> bool:
    global _MOTIONEVENT_SUPPORTED
    if _MOTIONEVENT_SUPPORTED is not None:
        return _MOTIONEVENT_SUPPORTED
    try:
        runtime.adb.shell("input motionevent DOWN 1 1")
        runtime.adb.shell("input motionevent UP 1 1")
        _MOTIONEVENT_SUPPORTED = True
    except Exception:
        _MOTIONEVENT_SUPPORTED = False
    return _MOTIONEVENT_SUPPORTED


def hold(side: str, project_dir: str | Path = ".", log: Callable[..., Any] = print) -> bool:
    global _HOLD_THREAD, _HOLD_STOP_EVENT, _HOLD_ACTIVE_SIDE
    runtime = _build_runtime(project_dir, log=log)
    points = _joystick(project_dir)
    settings = _settings(project_dir, log=log)
    if side not in points:
        _log(log, f"hold side={side} failed: joystick point missing")
        return False
    x, y = points[side]
    chunk_ms = int(settings["hold_chunk_ms"])
    with _HOLD_LOCK:
        if _HOLD_ACTIVE_SIDE == side and (((_HOLD_THREAD is not None and _HOLD_THREAD.is_alive())) or (_HOLD_THREAD is None and _probe_motionevent(runtime))):
            _log(log, f"hold side={side} already active")
            return True
        release(project_dir=project_dir, log=log)
        if _probe_motionevent(runtime):
            try:
                runtime.adb.shell(f"input motionevent DOWN {x} {y}")
                _HOLD_THREAD = None
                _HOLD_STOP_EVENT = None
                _HOLD_ACTIVE_SIDE = side
                _log(log, f"hold side={side} mode=motionevent x={x} y={y} chunk_ms={chunk_ms}")
                return True
            except Exception as exc:
                _log(log, f"hold motionevent failed: {exc}; fallback=swipe_loop")
        stop_event = threading.Event()
        state = HoldWorkerState(side=side, x=x, y=y, chunk_ms=chunk_ms)
        thread = threading.Thread(target=_hold_swipe_worker, args=(runtime, state, stop_event), daemon=True)
        _HOLD_STOP_EVENT = stop_event
        _HOLD_THREAD = thread
        _HOLD_ACTIVE_SIDE = side
        thread.start()
        _log(log, f"hold side={side} mode=swipe_loop x={x} y={y} chunk_ms={chunk_ms}")
        return True


def release(project_dir: str | Path = ".", log: Callable[..., Any] = print) -> None:
    global _HOLD_THREAD, _HOLD_STOP_EVENT, _HOLD_ACTIVE_SIDE
    runtime = _build_runtime(project_dir, log=log)
    with _HOLD_LOCK:
        active = _HOLD_ACTIVE_SIDE
        if _HOLD_STOP_EVENT is not None:
            _HOLD_STOP_EVENT.set()
        if _probe_motionevent(runtime):
            points = _joystick(project_dir)
            up_x, up_y = points.get(active or "", (1, 1))
            try:
                runtime.adb.shell(f"input motionevent UP {up_x} {up_y}")
            except Exception:
                pass
        if _HOLD_THREAD is not None and _HOLD_THREAD.is_alive():
            _HOLD_THREAD.join(timeout=1.0)
        _HOLD_THREAD = None
        _HOLD_STOP_EVENT = None
        _HOLD_ACTIVE_SIDE = None
        _log(log, f"release side={active}")


def approach_target(project_dir: str | Path, template: Any, log: Callable[..., Any] = print, stop_event: Any = None) -> tuple[bool, str]:
    runtime = _build_runtime(project_dir, log=log)
    settings = _settings(project_dir, log=log)
    click_radius_px = int(settings["click_radius_px"])
    timeout_s = float(settings["approach_timeout_s"])
    frame_interval_ms = int(settings["frame_interval_ms"])
    started = time.monotonic()
    last_dx: Optional[int] = None
    last_abs_dx: Optional[int] = None
    last_frame: Any = None
    current_side: Optional[str] = None
    try:
        while time.monotonic() - started < timeout_s:
            if stop_event is not None and stop_event.is_set():
                return False, "stopped"
            frame = runtime.screenshot.capture()
            last_frame = frame
            found, _, _, dx, _ = find_template(project_dir, template, frame=frame)
            if not found:
                time.sleep(frame_interval_ms / 1000.0)
                continue
            if abs(dx) <= click_radius_px:
                return True, "target centered"
            side = "left" if dx < 0 else "right"
            if side != current_side:
                if current_side is not None:
                    release(project_dir=project_dir, log=log)
                if not hold(side, project_dir=project_dir, log=log):
                    return False, f"failed to hold {side}"
                current_side = side
            last_dx = dx
            last_abs_dx = abs(dx)
            time.sleep(frame_interval_ms / 1000.0)
        return False, f"approach timeout (last_dx={last_dx}, last_abs_dx={last_abs_dx})"
    finally:
        if current_side is not None:
            release(project_dir=project_dir, log=log)


def _template(config: dict[str, Any], key: str) -> Any:
    return _extract_png(config.get(key))


def _load_nav_for_navigation(project_dir: str | Path) -> tuple[dict[str, Any], list[str]]:
    return _read_nav(project_dir)


def _sleep_or_stop(stop_event: Any, seconds: float) -> bool:
    if stop_event is None:
        time.sleep(seconds)
        return False
    return bool(stop_event.wait(seconds))


def _tap_match(project_dir: str | Path, template: Any, log: Callable[..., Any], stop_event: Any) -> tuple[bool, str]:
    runtime = _build_runtime(project_dir, log=log)
    frame = runtime.screenshot.capture()
    found, x, y, _, score = find_template(project_dir, template, frame=frame)
    if not found:
        return False, f"template not found: {template!r}"
    if stop_event is not None and stop_event.is_set():
        return False, "stopped"
    runtime.adb.tap(x, y)
    _log(log, f"tap x={x} y={y} score={score:.3f}")
    return True, "ok"


def go_to_dungeon(project_dir: str | Path, location_id: str, floor: int = DEFAULT_FLOOR_NUMBER, log: Callable[..., Any] = print, stop_event: Any = None) -> tuple[bool, str]:
    if floor < 1:
        return False, "floor must be >= 1"
    nav, notes = _load_nav_for_navigation(project_dir)
    for note in notes:
        _log(log, note)
    if not location_id:
        return False, "location_id is required"
    location_template = None
    for item in nav.get("locations", []):
        if isinstance(item, dict) and str(item.get("name")) == str(location_id):
            location_template = item.get("png")
            break
    if location_template is None:
        location_template = location_id
    steps = [
        ("base_to_locations", _template(nav, "base_to_locations")),
        ("location", location_template),
        ("farm_floor_button", _template(nav, "farm_floor_button")),
        ("enter_dungeon", _template(nav, "enter_dungeon")),
    ]
    for name, template in steps:
        if not template:
            return False, f"missing navigation template: {name}"
        ok, reason = _tap_match(project_dir, template, log, stop_event)
        if not ok:
            return False, f"{name}: {reason}"
        if _sleep_or_stop(stop_event, 0.5):
            return False, "stopped"
    return True, "dungeon entered"


def run_navigation(project_dir: str | Path, location_id: str, floor: int = DEFAULT_FLOOR_NUMBER, log: Callable[..., Any] = print, stop_event: Any = None) -> tuple[bool, str]:
    return go_to_dungeon(project_dir, location_id, floor=floor, log=log, stop_event=stop_event)


def main() -> int:
    parser = argparse.ArgumentParser(description="Navigation helper")
    parser.add_argument("project_dir", nargs="?", default=".")
    parser.add_argument("location")
    parser.add_argument("--floor", type=int, default=DEFAULT_FLOOR_NUMBER)
    args = parser.parse_args()
    ok, reason = go_to_dungeon(args.project_dir, args.location, floor=args.floor)
    print(reason)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
