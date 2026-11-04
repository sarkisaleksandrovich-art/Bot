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
# ЭТАП 2B: общий лимит боя (зеркало timeouts.battle_finish_timeout_s из config.yaml проекта)
DEFAULT_BATTLE_TIMEOUT_S = 120.0

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


def match_known(project_dir: str | Path, crop: Any) -> Optional[tuple[str, str, str, float]]:
    """FIX-F: возвращает (action, name, png, score) совпавшего объекта, чтобы GUI мог его заменить."""
    runtime = _build_runtime(project_dir)
    settings = _settings(project_dir)
    threshold = settings["match_threshold"]
    best: Optional[tuple[str, str, str, float]] = None
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
            best = (
                str(item.get("action", "")).strip(),
                str(item.get("name", png.stem)).strip(),
                str(item.get("png") or item.get("template") or item.get("file") or "").strip(),
                float(match.score),
            )
    if best is not None and best_score >= threshold:
        return best
    return None


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
    while not stop_event.is_set():
        try:
            runtime.adb.swipe(state.x, state.y, state.x, state.y, state.chunk_ms)
        except Exception:
            time.sleep(max(0.05, state.chunk_ms / 1000.0))


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
                _log(log, "approach stop_event set")
                return False, "stopped"
            frame, stable = _stable_wait(runtime, timeout_s=DEFAULT_STABLE_TIMEOUT_S, log=log)
            found, x, y, dx, score = find_template(project_dir, template, frame=frame)
            _log(log, f"approach scan found={found} score={score:.3f} stable={stable} dx={dx} radius={click_radius_px}")
            if not found:
                return False, "target not found"
            abs_dx = abs(dx)
            if abs_dx <= click_radius_px:
                release(project_dir=project_dir, log=log)
                _log(log, f"approach success dx={dx} radius={click_radius_px} score={score:.3f}")
                return True, "aligned"
            side = "right" if dx > 0 else "left"
            _log(log, f"approach decision dx={dx} radius={click_radius_px} hold_side={side} hold_chunk_ms={settings['hold_chunk_ms']} score={score:.3f}")
            if last_dx is not None and ((last_dx > 0 > dx) or (last_dx < 0 < dx)):
                release(project_dir=project_dir, log=log)
                back_side = "left" if side == "right" else "right"
                _log(log, f"approach overshoot prev_dx={last_dx} new_dx={dx}; corrective_hold={back_side}")
                if not hold(back_side, project_dir=project_dir, log=log):
                    return False, f"hold failed: {back_side}"
                time.sleep(max(0.05, frame_interval_ms / 2000.0))
                release(project_dir=project_dir, log=log)
                current_side = None
                last_dx = dx
                last_abs_dx = abs_dx
                last_frame = frame
                continue
            if current_side != side:
                release(project_dir=project_dir, log=log)
                if not hold(side, project_dir=project_dir, log=log):
                    return False, f"hold failed: {side}"
                current_side = side
            if last_abs_dx is not None and abs_dx > last_abs_dx:
                release(project_dir=project_dir, log=log)
                _log(log, f"approach stop reason=edge_or_passaway prev_abs_dx={last_abs_dx} new_abs_dx={abs_dx}")
                return False, "target moves away or hero stuck at edge"
            if last_frame is not None and current_side is not None and np is not None:
                try:
                    moved = runtime.vision.is_animating(last_frame, frame, DEFAULT_ANIMATION_DIFF_THRESHOLD)
                except Exception:
                    moved = True
                if not moved:
                    release(project_dir=project_dir, log=log)
                    _log(log, "approach stop reason=no movement while holding")
                    return False, "frame did not move while holding"
            last_dx = dx
            last_abs_dx = abs_dx
            last_frame = frame
            time.sleep(max(0.05, frame_interval_ms / 1000.0))
        release(project_dir=project_dir, log=log)
        _log(log, f"approach timeout approach_timeout_s={timeout_s}")
        return False, "approach timeout"
    except Exception as exc:
        release(project_dir=project_dir, log=log)
        _log(log, f"approach exception: {exc}")
        return False, str(exc)
    finally:
        release(project_dir=project_dir, log=log)


def _click_center(runtime: NavigationRuntime, match_result: tuple[bool, int, int, int, float], log: Callable[..., Any]) -> bool:
    found, x, y, dx, score = match_result
    if not found:
        _log(log, f"click_center target missing score={score:.3f}")
        return False
    _log(log, f"click_center x={x} y={y} dx={dx} score={score:.3f}")
    runtime.adb.tap(int(x), int(y))
    return True


def click_loot(project_dir: str | Path, template: Any, log: Callable[..., Any] = print, stop_event: Any = None) -> tuple[bool, str]:
    runtime = _build_runtime(project_dir, log=log)
    ok, reason = approach_target(project_dir, template, log=log, stop_event=stop_event)
    if not ok:
        return False, reason
    frame = runtime.screenshot.capture()
    clicked = _click_center(runtime, find_template(project_dir, template, frame=frame), log)
    return clicked, ("loot clicked" if clicked else "loot target missing after approach")


def _battle_state(project_dir: str | Path, frame: Any, log: Callable[..., Any]) -> str:
    """ЭТАП 2B: состояние боя по кадру.
    "off" — найден autobattle_on (бой идёт, автобой выключен — нажать);
    "on" — найден autobattle_active (бой идёт, автобой включён — ничего не нажимать);
    "none" — кнопок не видно (вне боя). Приоритет у "off"."""
    nav, _ = _read_nav(project_dir)
    off_png = _resolve_path(project_dir, _extract_png(nav.get("autobattle_on")))
    if off_png is not None:
        found, _x, _y, _dx, score = find_template(project_dir, str(off_png), frame=frame)
        if found:
            _log(log, f"battle state=off (autobattle_on score={score:.3f})")
            return "off"
    on_png = _resolve_path(project_dir, _extract_png(nav.get("autobattle_active")))
    if on_png is not None:
        found, _x, _y, _dx, score = find_template(project_dir, str(on_png), frame=frame)
        if found:
            _log(log, f"battle state=on (autobattle_active score={score:.3f})")
            return "on"
    _log(log, "battle state=none (autobattle buttons not visible)")
    return "none"


def wait_battle_end(project_dir: str | Path, log: Callable[..., Any] = print, stop_event: Any = None) -> tuple[bool, str]:
    """ЭТАП 2B: ждём окончания боя по кнопкам автобоя.
    "off" — нажимаем autobattle_on по найденным координатам;
    "on" — бой идёт, ничего не нажимаем;
    серия "none" дольше DEFAULT_STABLE_TIMEOUT_S — бой окончен.
    Общий лимит боя — DEFAULT_BATTLE_TIMEOUT_S."""
    runtime = _build_runtime(project_dir, log=log)
    settings = _settings(project_dir, log=log)
    poll_s = max(0.05, int(settings["frame_interval_ms"]) / 1000.0)
    none_limit_s = DEFAULT_STABLE_TIMEOUT_S
    deadline = time.monotonic() + DEFAULT_BATTLE_TIMEOUT_S
    none_started: Optional[float] = None
    while True:
        if stop_event is not None and stop_event.is_set():
            return False, "stopped"
        if time.monotonic() >= deadline:
            _log(log, f"battle timeout battle_timeout_s={DEFAULT_BATTLE_TIMEOUT_S}")
            return False, "battle timeout"
        frame = runtime.screenshot.capture()
        state = _battle_state(project_dir, frame, log)
        now = time.monotonic()
        if state == "off":
            nav, _ = _read_nav(project_dir)
            off_png = _resolve_path(project_dir, _extract_png(nav.get("autobattle_on")))
            if off_png is not None:
                match = find_template(project_dir, str(off_png), frame=frame)
                _click_center(runtime, match, log)
            time.sleep(0.5)
            none_started = None
            continue
        if state == "on":
            none_started = None
            time.sleep(poll_s)
            continue
        if none_started is None:
            none_started = now
            _log(log, "battle state=none; waiting to confirm battle end")
        elif now - none_started >= none_limit_s:
            _log(log, f"battle finished: no autobattle buttons for {none_limit_s:.1f}s")
            return True, "battle finished"
        time.sleep(poll_s)


def attack_mob(project_dir: str | Path, template: Any, log: Callable[..., Any] = print, stop_event: Any = None) -> tuple[bool, str]:
    """ЭТАП 2B: бой начинается от контакта персонажей с мобом, а не от тапа.
    Подходим вплотную (approach_target), затем ждём вход в бой по кнопкам автобоя,
    включаем автобой по найденным координатам и дожидаемся конца боя."""
    runtime = _build_runtime(project_dir, log=log)
    ok, reason = approach_target(project_dir, template, log=log, stop_event=stop_event)
    if not ok:
        return False, reason
    settings = _settings(project_dir, log=log)
    poll_s = max(0.05, int(settings["frame_interval_ms"]) / 1000.0)
    deadline = time.monotonic() + float(settings["approach_timeout_s"])
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            return False, "stopped"
        frame = runtime.screenshot.capture()
        state = _battle_state(project_dir, frame, log)
        if state == "off":
            nav, _ = _read_nav(project_dir)
            off_png = _resolve_path(project_dir, _extract_png(nav.get("autobattle_on")))
            if off_png is not None:
                match = find_template(project_dir, str(off_png), frame=frame)
                _click_center(runtime, match, log)
            return wait_battle_end(project_dir, log=log, stop_event=stop_event)
        if state == "on":
            _log(log, "battle already running (autobattle_active visible)")
            return wait_battle_end(project_dir, log=log, stop_event=stop_event)
        _log(log, "battle not started yet; waiting for aggro")
        time.sleep(poll_s)
    _log(log, "battle not started within approach_timeout_s")
    return False, "battle not started"


def handle_event(project_dir: str | Path, template: Any, log: Callable[..., Any] = print, stop_event: Any = None) -> tuple[bool, str]:
    runtime = _build_runtime(project_dir, log=log)
    ok, reason = approach_target(project_dir, template, log=log, stop_event=stop_event)
    if not ok:
        return False, reason
    frame = runtime.screenshot.capture()
    clicked = _click_center(runtime, find_template(project_dir, template, frame=frame), log)
    return clicked, ("event opened" if clicked else "event target missing after approach")


def _wait_and_click_confirm_start(project_dir: str | Path, runtime: NavigationRuntime, log: Callable[..., Any], stop_event: Any) -> tuple[bool, str]:
    """ШАГ 2: после клика «Войти в данж» ждём кнопку «Подтвердить старт», кликаем по центру
    и убеждаемся, что экран сменился (кнопка исчезла). Повторный клик по исчезнувшей
    кнопке не делается. Неуспех — (False, reason)."""
    nav, _ = _read_nav(project_dir)
    confirm_png = _resolve_path(project_dir, _extract_png(nav.get("confirm_start")))
    if confirm_png is None:
        _log(log, "confirm_start template missing; fallback to time.sleep(1.0)")
        time.sleep(1.0)
        return True, "confirm skipped"
    settings = _settings(project_dir, log=log)
    timeout_s = float(settings["approach_timeout_s"])
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        if stop_event is not None and stop_event.is_set():
            return False, "stopped"
        frame = runtime.screenshot.capture()
        match = find_template(project_dir, str(confirm_png), frame=frame)
        if not match[0]:
            _log(log, "confirm_start button not visible; screen already changed")
            return True, "confirm button absent"
        clicked = _click_center(runtime, match, log)
        if not clicked:
            return False, "failed to click confirm_start"
        time.sleep(0.5)
        frame = runtime.screenshot.capture()
        found, _x, _y, _dx, score = find_template(project_dir, str(confirm_png), frame=frame)
        if not found:
            _log(log, "confirm_start clicked; screen changed")
            return True, "confirm clicked"
        _log(log, f"confirm_start still visible after click score={score:.3f}; retrying")
    _log(log, f"confirm_start timeout approach_timeout_s={timeout_s}")
    return False, "confirm_start timeout"


def start_battle(project_dir: str | Path, log: Callable[..., Any] = print, stop_event: Any = None) -> tuple[bool, str]:
    runtime = _build_runtime(project_dir, log=log)
    nav, _ = _read_nav(project_dir)
    enter_png = _resolve_path(project_dir, _extract_png(nav.get("enter_dungeon")))
    if stop_event is not None and stop_event.is_set():
        return False, "stopped"
    if enter_png is None:
        return False, "enter_dungeon template missing"
    frame, _ = _stable_wait(runtime, 4.0, log)
    started = _click_center(runtime, find_template(project_dir, str(enter_png), frame=frame), log)
    if not started:
        return False, "failed to start battle"
    ok, reason = _wait_and_click_confirm_start(project_dir, runtime, log, stop_event)
    if not ok:
        return False, reason
    if stop_event is not None and stop_event.is_set():
        return False, "stopped"
    # ЭТАП 2B: включение автобоя без слепых тапов — только по найденным координатам шаблона
    battle_frame = runtime.screenshot.capture()
    state = _battle_state(project_dir, battle_frame, log)
    if state == "off":
        nav, _ = _read_nav(project_dir)
        off_png = _resolve_path(project_dir, _extract_png(nav.get("autobattle_on")))
        if off_png is not None:
            match = find_template(project_dir, str(off_png), frame=battle_frame)
            if _click_center(runtime, match, log):
                time.sleep(0.5)
    elif state == "on":
        _log(log, "autobattle already active; nothing to press")
    else:
        _log(log, "autobattle buttons not visible; nothing to press")
    return True, "battle started"


def run_event(project_dir: str | Path, template: Any, action: str = "event_choice", log: Callable[..., Any] = print, stop_event: Any = None) -> tuple[bool, str]:
    ok, reason = handle_event(project_dir, template, log=log, stop_event=stop_event)
    if not ok:
        return False, reason
    _log(log, f"run_event action={action}")
    return True, f"event handled: {action}"


def _nav_chain_templates(project_dir: str | Path, location_name: str, floor_number: int | None = None) -> dict[str, Optional[str]]:
    nav, _ = _read_nav(project_dir)
    location_item = None
    for item in nav.get("locations", []) if isinstance(nav.get("locations"), list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("name", "")).strip().lower() == str(location_name).strip().lower():
            location_item = item
            break
    farm_floor = nav.get("farm_floor_button")
    if isinstance(farm_floor, dict) and floor_number is not None:
        floor_specific = None
        for key in (str(floor_number), f"floor_{floor_number}", f"{floor_number}"):
            if key in farm_floor:
                floor_specific = farm_floor.get(key)
                break
        farm_floor = floor_specific if floor_specific is not None else _extract_png(farm_floor)
    # FIX-E: base_screenshot и locations_map хранятся как {"png": ...}, читаем через _extract_png
    return {
        "base": str(_resolve_path(project_dir, _extract_png(nav.get("base_screenshot"))) or "") or None,
        "base_to_locations": str(_resolve_path(project_dir, _extract_png(nav.get("base_to_locations"))) or "") or None,
        "locations_map": str(_resolve_path(project_dir, _extract_png(nav.get("locations_map"))) or "") or None,
        "location_icon": str(_resolve_path(project_dir, (location_item or {}).get("png")) or "") or None,
        "farm_floor_button": str(_resolve_path(project_dir, farm_floor) or "") or None,
        "enter_dungeon": str(_resolve_path(project_dir, _extract_png(nav.get("enter_dungeon"))) or "") or None,
        "inside_dungeon": str(_resolve_path(project_dir, _extract_png(nav.get("inside_dungeon"))) or "") or None,
    }


def _go_home(project_dir: str | Path, log: Callable[..., Any], stop_event: Any) -> tuple[bool, str]:
    runtime = _build_runtime(project_dir, log=log)
    chain = _nav_chain_templates(project_dir, "", floor_number=None)
    base_tpl = chain.get("base")
    if not base_tpl:
        _log(log, "go_home skipped: base_screenshot missing")
        return True, "base template missing; home recovery skipped"
    deadline = time.monotonic() + 15.0
    last_score = 0.0
    try:
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                return False, "stopped"
            frame, _ = _stable_wait(runtime, 2.0, log)
            found, x, y, dx, score = find_template(project_dir, base_tpl, frame=frame)
            last_score = score
            _log(log, f"go_home base score={score:.3f} found={found}")
            if found:
                return True, "already at base"
            runtime.adb.back()
            _log(log, "go_home action=back")
            time.sleep(1.0)
        return False, f"failed to return to base; last_score={last_score:.3f}"
    finally:
        release(project_dir=project_dir, log=log)


def nav_status(project_dir: str | Path) -> dict[str, Any]:
    nav, nav_notes = _read_nav(project_dir)
    actions, action_notes = _read_actions(project_dir)
    settings = _settings(project_dir)
    locations = nav.get("locations", []) if isinstance(nav.get("locations"), list) else []
    screen = None
    screen_score = 0.0
    shot_path = None
    try:
        shot_path, frame = take_screenshot(project_dir)
        candidates = {
            "base": _extract_png(nav.get("base_screenshot")),
            "locations_map": _extract_png(nav.get("locations_map")),
            "base_to_locations": _extract_png(nav.get("base_to_locations")),
            "enter_dungeon": _extract_png(nav.get("enter_dungeon")),
        }
        best = (None, 0.0)
        for name, template in candidates.items():
            found, x, y, dx, score = find_template(project_dir, template, frame=frame)
            if found and score >= best[1]:
                best = (name, score)
        screen, screen_score = best
    except Exception as exc:
        nav_notes.append(f"nav_status runtime note: {exc}")
    return {
        "ready": bool(nav),
        "screen": screen,
        "screen_score": screen_score,
        "notes": nav_notes + action_notes,
        "match_threshold": settings["match_threshold"],
        "nav_json_present": _nav_path(project_dir).exists(),
        "actions_json_present": _actions_path(project_dir).exists(),
        "locations_count": len([x for x in locations if isinstance(x, dict)]),
        "known_actions_count": len(_load_actions_list(project_dir)),
        "last_screenshot": shot_path,
        "nav_format": {
            "base_screenshot": nav.get("base_screenshot"),
            "base_to_locations": nav.get("base_to_locations"),
            "locations_map": nav.get("locations_map"),
            "locations": locations,
            "farm_floor_button": nav.get("farm_floor_button"),
            "enter_dungeon": nav.get("enter_dungeon"),
            "autobattle_on": nav.get("autobattle_on"),
            "autobattle_active": nav.get("autobattle_active"),
            "joystick": nav.get("joystick"),
            "settings": {
                "click_radius_px": settings["click_radius_px"],
                "approach_timeout_s": settings["approach_timeout_s"],
                "hold_chunk_ms": settings["hold_chunk_ms"],
                "frame_interval_ms": settings["frame_interval_ms"],
                "match_threshold": settings["match_threshold"],
            },
        },
        "actions_format": actions or {"objects": [{"name": "...", "png": "...", "action": "loot|mob|event_choice|event_skip|skip"}]},
    }


def _run_known_objects(project_dir: str | Path, frame: Any, log: Callable[..., Any], stop_event: Any) -> tuple[bool, str]:
    objects = find_known_objects(project_dir, frame)
    if not objects:
        return True, "no known objects"
    for kind, name, x, y, w, h, score in objects:
        if stop_event is not None and stop_event.is_set():
            return False, "stopped"
        _log(log, f"known_object kind={kind} name={name} x={x} y={y} w={w} h={h} score={score:.3f}")
        action_template = None
        for item in _load_actions_list(project_dir):
            if str(item.get("name", "")).strip() == name and str(item.get("action", "")).strip() == kind:
                action_template = item.get("png") or item.get("template") or item.get("file")
                break
        if kind == "loot" and action_template:
            ok, reason = click_loot(project_dir, action_template, log=log, stop_event=stop_event)
            if not ok:
                return False, reason
        elif kind == "mob" and action_template:
            ok, reason = attack_mob(project_dir, action_template, log=log, stop_event=stop_event)
            if not ok:
                return False, reason
        elif kind in {"event_choice", "event_skip"} and action_template:
            ok, reason = run_event(project_dir, action_template, action=kind, log=log, stop_event=stop_event)
            if not ok:
                return False, reason
        elif kind == "skip":
            _log(log, f"known skip object ignored name={name} score={score:.3f}")
        else:
            _log(log, f"Пропущен неизвестный объект на x={x}")
    return True, "known objects handled"


def _go_path(project_dir: str | Path, location_name: str, log: Callable[..., Any], stop_event: Any, launch: bool, floor_number: int | None = None) -> tuple[bool, str]:
    runtime = _build_runtime(project_dir, log=log)
    nav, _ = _read_nav(project_dir)
    # FIX-A: если этаж передан вызывающим кодом, используем его, иначе selected_floor из nav.json
    if floor_number is None:
        try:
            floor_number = int(nav.get("selected_floor", DEFAULT_FLOOR_NUMBER) or DEFAULT_FLOOR_NUMBER)
        except (TypeError, ValueError):
            floor_number = DEFAULT_FLOOR_NUMBER
    chain = _nav_chain_templates(project_dir, location_name, floor_number=floor_number)
    for key, value in chain.items():
        if value is None and key not in {"base", "locations_map", "inside_dungeon"}:
            return False, f"template missing: {key}"
    try:
        # Если бот уже внутри локации (якорь inside_dungeon найден), навигация
        # с возвратом на базу пропускается — сразу начинаем фарм.
        inside_tpl = chain.get("inside_dungeon")
        if inside_tpl and not (stop_event is not None and stop_event.is_set()):
            frame, _ = _stable_wait(runtime, 4.0, log)
            found, x, y, dx, score = find_template(project_dir, inside_tpl, frame=frame)
            _log(log, f"inside_dungeon anchor score={score:.3f} found={found}")
            if found:
                _log(log, "уже внутри локации; навигация пропущена")
                if not launch:
                    return True, "chain verified (inside dungeon)"
                frame = runtime.screenshot.capture()
                ok, reason = _run_known_objects(project_dir, frame, log, stop_event)
                if not ok:
                    return False, reason
                return True, "navigation complete (already inside dungeon)"
        ok, reason = _go_home(project_dir, log, stop_event)
        if not ok:
            return False, reason
        base_tpl = chain["base_to_locations"]
        map_tpl = chain["locations_map"]
        icon_tpl = chain["location_icon"]
        floor_tpl = chain["farm_floor_button"]
        enter_tpl = chain["enter_dungeon"]
        if stop_event is not None and stop_event.is_set():
            return False, "stopped"
        frame, _ = _stable_wait(runtime, 4.0, log)
        if base_tpl:
            found, x, y, dx, score = find_template(project_dir, base_tpl, frame=frame)
            _log(log, f"base->locations score={score:.3f} found={found}")
            if found:
                runtime.adb.tap(x, y)
                time.sleep(1.0)
        if stop_event is not None and stop_event.is_set():
            return False, "stopped"
        frame, _ = _stable_wait(runtime, 4.0, log)
        if map_tpl:
            found, x, y, dx, score = find_template(project_dir, map_tpl, frame=frame)
            _log(log, f"locations_map score={score:.3f} found={found}")
        if stop_event is not None and stop_event.is_set():
            return False, "stopped"
        frame, _ = _stable_wait(runtime, 4.0, log)
        found, x, y, dx, score = find_template(project_dir, icon_tpl, frame=frame)
        _log(log, f"location_icon score={score:.3f} found={found}")
        if not found:
            return False, "location icon not found"
        runtime.adb.tap(x, y)
        time.sleep(1.0)
        if stop_event is not None and stop_event.is_set():
            return False, "stopped"
        frame, _ = _stable_wait(runtime, 4.0, log)
        found, x, y, dx, score = find_template(project_dir, floor_tpl, frame=frame)
        _log(log, f"farm_floor_button score={score:.3f} found={found}")
        if not found:
            return False, "farm floor button not found"
        runtime.adb.tap(x, y)
        time.sleep(1.0)
        if stop_event is not None and stop_event.is_set():
            return False, "stopped"
        frame, _ = _stable_wait(runtime, 4.0, log)
        found, x, y, dx, score = find_template(project_dir, enter_tpl, frame=frame)
        _log(log, f"enter_dungeon score={score:.3f} found={found}")
        if not found:
            return False, "hero/start menu not found"
        if not launch:
            return True, "chain verified"
        frame = runtime.screenshot.capture()
        ok, reason = _run_known_objects(project_dir, frame, log, stop_event)
        if not ok:
            return False, reason
        ok, reason = start_battle(project_dir, log=log, stop_event=stop_event)
        if not ok:
            return False, reason
        return True, "navigation complete"
    finally:
        release(project_dir=project_dir, log=log)


def go_to_dungeon(project_dir: str | Path, location_name: str, log: Callable[..., Any] = print, stop_event: Any = None, floor_number: int | None = None) -> tuple[bool, str]:
    # FIX-A: floor_number приходит из main.py --floor (None = взять selected_floor из nav.json)
    return _go_path(project_dir, location_name, log, stop_event, True, floor_number=floor_number)


def verify_chain(project_dir: str | Path, location_name: str, log: Callable[..., Any] = print, stop_event: Any = None, floor_number: int | None = None) -> tuple[bool, str]:
    return _go_path(project_dir, location_name, log, stop_event, False, floor_number=floor_number)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--go", nargs=1, metavar=("LOCATION",))
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()
    if args.go:
        ok, reason = go_to_dungeon(args.project_dir, args.go[0])
        print(reason)
        print("Не проверено запуском на устройстве.")
        raise SystemExit(0 if ok else 1)
    print(json.dumps(nav_status(args.project_dir), ensure_ascii=False, indent=2))
