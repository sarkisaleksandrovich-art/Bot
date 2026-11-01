from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from bot.floor import FloorExploreError, FloorExplorer, FloorResult
from bot.profiles import ProfileError, load_profile, read_progress, write_progress
from bot.safety import SafetyStop

if TYPE_CHECKING:
    from bot.adb_controller import ADBController
    from bot.config import Config
    from bot.knowledge import KnowledgeBase
    from bot.profiles import Profile
    from bot.safety import SafetyService
    from bot.screenshot import ScreenshotService
    from bot.vision import Frame, VisionService


class ExplorerError(Exception):
    ...


RunStatus = Literal["completed", "stopped", "failed"]


@dataclass(frozen=True)
class ExploreResult:
    status: RunStatus
    floors_completed: int
    rooms_cleared: int
    rooms_skipped: int
    rooms_failed: int
    reason: str


class Explorer:
    def __init__(
        self,
        floor_explorer: "FloorExplorer",
        teacher_ui,
        config: "Config",
        knowledge: "KnowledgeBase",
    ) -> None:
        """
        teacher_ui нужен, чтобы проверять pause_requested() в циклах.
        Исключения:
            - ExplorerError
        """
        if floor_explorer is None:
            raise ExplorerError("floor_explorer is required")
        if teacher_ui is None:
            raise ExplorerError("teacher_ui is required")
        if config is None:
            raise ExplorerError("config is required")
        if knowledge is None:
            raise ExplorerError("knowledge is required")
        if not hasattr(teacher_ui, "pause_requested"):
            raise ExplorerError("teacher_ui.pause_requested() is required")
        if not hasattr(teacher_ui, "show_and_label"):
            raise ExplorerError("teacher_ui.show_and_label(...) is required")

        self.floor_explorer = floor_explorer
        self.teacher_ui = teacher_ui
        self.config = config
        self.knowledge = knowledge
        self.logger = logging.getLogger(__name__)

        self._profile_name = self._resolve_profile_name()
        self._profile = self._safe_load_profile(self._profile_name)

        self._adb = getattr(floor_explorer, "adb", None)
        self._screenshot = getattr(floor_explorer, "screenshot", None)
        self._vision = getattr(floor_explorer, "vision", None)
        self._safety = getattr(floor_explorer, "safety", None)

        if self._adb is None or self._screenshot is None or self._vision is None or self._safety is None:
            raise ExplorerError(
                "floor_explorer must expose adb, screenshot, vision and safety attributes"
            )

        self.motion_stable_frames = self._cfg_int("motion.stable_frames")
        self.motion_stable_diff_threshold = self._cfg_float("motion.stable_diff_threshold")
        self.motion_stable_poll_ms = self._cfg_int("motion.stable_poll_ms")
        self.motion_stable_timeout_s = self._cfg_float("motion.stable_timeout_s")
        self.motion_max_stable_retries = self._cfg_int("motion.max_stable_retries")

        self.teaching_enabled = bool(self.config.get("teaching.enabled", True))
        self.allow_teach_on_unstable_timeout = bool(
            self.config.get("teaching.allow_teach_on_unstable_timeout", True)
        )
        self.unstable_screen_label = str(
            self.config.get("teaching.unstable_screen_label", "экран не стабилизировался")
        )

        self.max_floor_runtime_s = self._cfg_float_optional("explorer.max_floor_runtime_s", 900.0)
        self.max_total_runtime_s = self._cfg_float_optional("explorer.max_total_runtime_s", 7200.0)
        self.max_same_screen_steps = self._cfg_int_optional("explorer.max_same_screen_steps", 8)
        self.max_wait_cycles = self._cfg_int_optional("explorer.max_wait_cycles", 64)

    def run(self, floor_from: int, floor_to: int) -> ExploreResult:
        """
        Последовательно вызывает floor_explorer.run(...) для этажей [floor_from, floor_to].

        Обязательные правила:
        - на каждом шаге и в циклах ожидания учитывать teacher_ui.pause_requested()
        - при pause_requested() корректно останавливаться со status='stopped'
        - агрегировать статистику по FloorResult

        Возвращает:
            ExploreResult

        Исключения:
            - ExplorerError
            - SafetyStop
            - FloorExploreError
        """
        if not isinstance(floor_from, int) or not isinstance(floor_to, int):
            raise ExplorerError("floor_from and floor_to must be integers")
        if floor_from <= 0 or floor_to <= 0:
            raise ExplorerError("floor_from and floor_to must be > 0")
        if floor_from > floor_to:
            raise ExplorerError("floor_from must be <= floor_to")

        started_at = time.monotonic()
        floors_completed = 0
        rooms_cleared = 0
        rooms_skipped = 0
        rooms_failed = 0
        same_screen_streak = 0
        last_stable_signature: str | None = None

        self._write_progress_state(
            floor_from=floor_from,
            floor_to=floor_to,
            current_floor=floor_from,
            active=True,
            mode="teach",
            last_error="",
        )

        try:
            for floor_number in range(floor_from, floor_to + 1):
                self._guard_total_runtime(started_at)

                pause_result = self._handle_pause_if_requested(before_floor=floor_number)
                if pause_result is not None:
                    self._write_final_progress(
                        active=False,
                        floor_from=floor_from,
                        floor_to=floor_to,
                        current_floor=floor_number,
                        floors_completed=floors_completed,
                        rooms_cleared=rooms_cleared,
                        rooms_skipped=rooms_skipped,
                        rooms_failed=rooms_failed,
                        last_error=pause_result.reason,
                    )
                    return pause_result

                self._write_progress_state(
                    floor_from=floor_from,
                    floor_to=floor_to,
                    current_floor=floor_number,
                    active=True,
                    mode="teach",
                    last_error="",
                )

                signature = self._stable_screen_signature_for_guard()
                if signature is not None and signature == last_stable_signature:
                    same_screen_streak += 1
                else:
                    same_screen_streak = 1 if signature is not None else 0
                last_stable_signature = signature

                if same_screen_streak >= self.max_same_screen_steps:
                    reason = (
                        "Детектор зависания: один и тот же стабильный экран "
                        f"наблюдается {same_screen_streak} шагов подряд."
                    )
                    self._save_emergency_screenshot("stuck_same_screen")
                    self._write_final_progress(
                        active=False,
                        floor_from=floor_from,
                        floor_to=floor_to,
                        current_floor=floor_number,
                        floors_completed=floors_completed,
                        rooms_cleared=rooms_cleared,
                        rooms_skipped=rooms_skipped,
                        rooms_failed=rooms_failed,
                        last_error=reason,
                    )
                    return ExploreResult(
                        status="failed",
                        floors_completed=floors_completed,
                        rooms_cleared=rooms_cleared,
                        rooms_skipped=rooms_skipped,
                        rooms_failed=rooms_failed,
                        reason=reason,
                    )

                floor_started_at = time.monotonic()
                result = self._run_single_floor_with_guards(floor_number, floor_started_at)

                rooms_cleared += result.rooms_cleared
                rooms_skipped += result.rooms_skipped
                rooms_failed += result.rooms_failed

                if result.status == "completed":
                    floors_completed += 1
                    self._write_final_progress(
                        active=True,
                        floor_from=floor_from,
                        floor_to=floor_to,
                        current_floor=floor_number,
                        floors_completed=floors_completed,
                        rooms_cleared=rooms_cleared,
                        rooms_skipped=rooms_skipped,
                        rooms_failed=rooms_failed,
                        last_error="",
                        last_completed_floor=floor_number,
                    )
                    continue

                if result.status == "stopped":
                    self._write_final_progress(
                        active=False,
                        floor_from=floor_from,
                        floor_to=floor_to,
                        current_floor=floor_number,
                        floors_completed=floors_completed,
                        rooms_cleared=rooms_cleared,
                        rooms_skipped=rooms_skipped,
                        rooms_failed=rooms_failed,
                        last_error=result.reason,
                    )
                    return ExploreResult(
                        status="stopped",
                        floors_completed=floors_completed,
                        rooms_cleared=rooms_cleared,
                        rooms_skipped=rooms_skipped,
                        rooms_failed=rooms_failed,
                        reason=result.reason,
                    )

                self._write_final_progress(
                    active=False,
                    floor_from=floor_from,
                    floor_to=floor_to,
                    current_floor=floor_number,
                    floors_completed=floors_completed,
                    rooms_cleared=rooms_cleared,
                    rooms_skipped=rooms_skipped,
                    rooms_failed=rooms_failed,
                    last_error=result.reason,
                )
                return ExploreResult(
                    status="failed",
                    floors_completed=floors_completed,
                    rooms_cleared=rooms_cleared,
                    rooms_skipped=rooms_skipped,
                    rooms_failed=rooms_failed,
                    reason=result.reason,
                )

            final_reason = f"Диапазон этажей [{floor_from}, {floor_to}] завершён."
            self._write_final_progress(
                active=False,
                floor_from=floor_from,
                floor_to=floor_to,
                current_floor=floor_to,
                floors_completed=floors_completed,
                rooms_cleared=rooms_cleared,
                rooms_skipped=rooms_skipped,
                rooms_failed=rooms_failed,
                last_error="",
                last_completed_floor=floor_to if floors_completed > 0 else None,
            )
            return ExploreResult(
                status="completed",
                floors_completed=floors_completed,
                rooms_cleared=rooms_cleared,
                rooms_skipped=rooms_skipped,
                rooms_failed=rooms_failed,
                reason=final_reason,
            )

        except SafetyStop:
            self._save_emergency_screenshot("safety_stop")
            self._write_final_progress(
                active=False,
                floor_from=floor_from,
                floor_to=floor_to,
                current_floor=None,
                floors_completed=floors_completed,
                rooms_cleared=rooms_cleared,
                rooms_skipped=rooms_skipped,
                rooms_failed=rooms_failed,
                last_error="Остановка по SafetyStop.",
            )
            raise
        except FloorExploreError as exc:
            self._save_emergency_screenshot("floor_error")
            self._write_final_progress(
                active=False,
                floor_from=floor_from,
                floor_to=floor_to,
                current_floor=None,
                floors_completed=floors_completed,
                rooms_cleared=rooms_cleared,
                rooms_skipped=rooms_skipped,
                rooms_failed=rooms_failed,
                last_error=str(exc),
            )
            raise
        except Exception as exc:
            self._save_emergency_screenshot("explorer_error")
            self._write_final_progress(
                active=False,
                floor_from=floor_from,
                floor_to=floor_to,
                current_floor=None,
                floors_completed=floors_completed,
                rooms_cleared=rooms_cleared,
                rooms_skipped=rooms_skipped,
                rooms_failed=rooms_failed,
                last_error=str(exc),
            )
            raise ExplorerError(str(exc)) from exc


def run_explore(
    adb: "ADBController",
    screenshot: "ScreenshotService",
    vision: "VisionService",
    knowledge: "KnowledgeBase",
    safety: "SafetyService",
    teacher_ui,
    config: "Config",
    floor_from: int,
    floor_to: int,
) -> ExploreResult:
    profile_name = _resolve_profile_name_from_config(config)
    profile = load_profile(config, profile_name)
    floor_explorer = FloorExplorer(
        adb=adb,
        screenshot=screenshot,
        vision=vision,
        knowledge=knowledge,
        safety=safety,
        teacher_ui=teacher_ui,
        config=config,
        profile=profile,
    )
    explorer = Explorer(
        floor_explorer=floor_explorer,
        teacher_ui=teacher_ui,
        config=config,
        knowledge=knowledge,
    )
    return explorer.run(floor_from, floor_to)


def run_teach_only(
    adb: "ADBController",
    screenshot: "ScreenshotService",
    vision: "VisionService",
    knowledge: "KnowledgeBase",
    safety: "SafetyService",
    teacher_ui,
    config: "Config",
) -> ExploreResult:
    del adb, vision, safety
    if not hasattr(teacher_ui, "teach_only"):
        raise ExplorerError("teacher_ui.teach_only(...) is required")

    profile_name = _resolve_profile_name_from_config(config)
    progress = _safe_read_progress(config, profile_name)
    _update_progress_run_state(progress, active=True, mode="teach_only", last_error="")
    _atomic_write_progress(config, profile_name, progress)

    try:
        teacher_ui.teach_only(
            getattr(knowledge, "_config", None) and None or None,
            screenshot,
            knowledge,
        )
    except TypeError:
        teacher_ui.teach_only(None, screenshot, knowledge)
    except Exception as exc:
        progress = _safe_read_progress(config, profile_name)
        _update_progress_run_state(progress, active=False, mode="teach_only", last_error=str(exc))
        _atomic_write_progress(config, profile_name, progress)
        raise

    progress = _safe_read_progress(config, profile_name)
    _update_progress_run_state(progress, active=False, mode="teach_only", last_error="")
    _atomic_write_progress(config, profile_name, progress)
    return ExploreResult(
        status="completed",
        floors_completed=0,
        rooms_cleared=0,
        rooms_skipped=0,
        rooms_failed=0,
        reason="Режим teach_only завершён.",
    )


    def _run_single_floor_with_guards(self, floor_number: int, floor_started_at: float) -> FloorResult:
        self._guard_floor_runtime(floor_number, floor_started_at)
        self._check_pause_wait_loop(f"before floor {floor_number}")
        return self.floor_explorer.run(floor_number)

    def _handle_pause_if_requested(self, before_floor: int) -> ExploreResult | None:
        if not self.teacher_ui.pause_requested():
            return None

        frame = self._wait_stable_frame_for_ui()
        self.teacher_ui.show_and_label(frame, self.knowledge)

        if self.teacher_ui.pause_requested():
            return ExploreResult(
                status="stopped",
                floors_completed=0,
                rooms_cleared=0,
                rooms_skipped=0,
                rooms_failed=0,
                reason=f"Пауза запрошена перед этажом {before_floor}.",
            )
        return None

    def _wait_stable_frame_for_ui(self) -> "Frame":
        last_frame = None
        for _ in range(self.motion_max_stable_retries + 1):
            self._check_pause_wait_loop("wait_stable_frame_for_ui")
            frame, stable = self._vision.wait_until_stable(
                self._screenshot.capture,
                stable_frames=self.motion_stable_frames,
                stable_diff_threshold=self.motion_stable_diff_threshold,
                stable_poll_ms=self.motion_stable_poll_ms,
                stable_timeout_s=self.motion_stable_timeout_s,
            )
            last_frame = frame
            if stable:
                return frame

        if last_frame is None:
            last_frame = self._screenshot.capture()

        if self.allow_teach_on_unstable_timeout:
            return last_frame

        return last_frame

    def _stable_screen_signature_for_guard(self) -> str | None:
        try:
            frame, stable = self._vision.wait_until_stable(
                self._screenshot.capture,
                stable_frames=self.motion_stable_frames,
                stable_diff_threshold=self.motion_stable_diff_threshold,
                stable_poll_ms=self.motion_stable_poll_ms,
                stable_timeout_s=self.motion_stable_timeout_s,
            )
            if not stable:
                return None
            return self._frame_signature(frame)
        except Exception:
            return None

    def _frame_signature(self, frame: "Frame") -> str:
        try:
            import cv2
            import numpy as np
        except Exception as exc:
            raise ExplorerError("OpenCV and numpy are required for stall detection") from exc

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        small = cv2.resize(gray, (32, 18), interpolation=cv2.INTER_AREA)
        return small.tobytes().hex()

    def _check_pause_wait_loop(self, reason: str) -> None:
        cycles = 0
        while self.teacher_ui.pause_requested():
            cycles += 1
            if cycles > self.max_wait_cycles:
                raise ExplorerError(f"Превышен лимит ожидания в паузе: {reason}")
            frame = self._wait_stable_frame_for_ui()
            self.teacher_ui.show_and_label(frame, self.knowledge)
            time.sleep(max(self.motion_stable_poll_ms, 50) / 1000.0)

    def _guard_floor_runtime(self, floor_number: int, started_at: float) -> None:
        if time.monotonic() - started_at > self.max_floor_runtime_s:
            raise ExplorerError(f"Превышен лимит времени на этаж {floor_number}")

    def _guard_total_runtime(self, started_at: float) -> None:
        if time.monotonic() - started_at > self.max_total_runtime_s:
            raise ExplorerError("Превышен общий лимит времени run_explore")

    def _write_progress_state(
        self,
        *,
        floor_from: int,
        floor_to: int,
        current_floor: int | None,
        active: bool,
        mode: str,
        last_error: str,
    ) -> None:
        progress = _safe_read_progress(self.config, self._profile_name)
        progress.setdefault("version", 1)
        progress["profile"] = self._profile_name
        progress["updated_at"] = _utc_now()

        run_state = progress.setdefault("run_state", {})
        if not isinstance(run_state, dict):
            run_state = {}
            progress["run_state"] = run_state
        run_state["active"] = active
        run_state["mode"] = mode
        run_state.setdefault("started_at", _utc_now())
        run_state["last_error"] = last_error

        game_progress = progress.setdefault("game_progress", {})
        if not isinstance(game_progress, dict):
            game_progress = {}
            progress["game_progress"] = game_progress
        game_progress.setdefault("current_location", 1)
        game_progress["current_floor"] = current_floor
        game_progress["target_floor_from"] = floor_from
        game_progress["target_floor_to"] = floor_to

        _atomic_write_progress(self.config, self._profile_name, progress)

    def _write_final_progress(
        self,
        *,
        active: bool,
        floor_from: int,
        floor_to: int,
        current_floor: int | None,
        floors_completed: int,
        rooms_cleared: int,
        rooms_skipped: int,
        rooms_failed: int,
        last_error: str,
        last_completed_floor: int | None = None,
    ) -> None:
        progress = _safe_read_progress(self.config, self._profile_name)
        progress.setdefault("version", 1)
        progress["profile"] = self._profile_name
        progress["updated_at"] = _utc_now()

        run_state = progress.setdefault("run_state", {})
        if not isinstance(run_state, dict):
            run_state = {}
            progress["run_state"] = run_state
        run_state["active"] = active
        run_state.setdefault("mode", "teach")
        run_state.setdefault("started_at", _utc_now())
        run_state["last_error"] = last_error

        game_progress = progress.setdefault("game_progress", {})
        if not isinstance(game_progress, dict):
            game_progress = {}
            progress["game_progress"] = game_progress
        game_progress.setdefault("current_location", 1)
        game_progress["current_floor"] = current_floor
        game_progress["target_floor_from"] = floor_from
        game_progress["target_floor_to"] = floor_to
        if last_completed_floor is not None:
            game_progress["last_completed_floor"] = last_completed_floor

        stats = progress.setdefault("stats", {})
        if not isinstance(stats, dict):
            stats = {}
            progress["stats"] = stats
        stats["floors_completed"] = floors_completed
        stats["rooms_cleared"] = rooms_cleared
        stats["rooms_skipped"] = rooms_skipped
        stats["rooms_failed"] = rooms_failed

        _atomic_write_progress(self.config, self._profile_name, progress)

    def _save_emergency_screenshot(self, prefix: str) -> None:
        try:
            if hasattr(self._screenshot, "save_emergency"):
                self._screenshot.save_emergency(prefix=prefix)
                return
            frame = self._screenshot.capture()
            if hasattr(self._screenshot, "save_capture"):
                self._screenshot.save_capture(frame=frame, prefix=prefix)
        except Exception as exc:
            self.logger.warning("Failed to save emergency screenshot: %s", exc)

    def _resolve_profile_name(self) -> str:
        profile = self.config.get("profiles.active")
        if not isinstance(profile, str) or not profile.strip():
            raise ExplorerError("profiles.active must be a non-empty string")
        return profile.strip()

    def _safe_load_profile(self, profile_name: str) -> "Profile":
        try:
            return load_profile(self.config, profile_name)
        except ProfileError as exc:
            raise ExplorerError(f"Failed to load profile '{profile_name}': {exc}") from exc

    def _cfg_int(self, key: str) -> int:
        value = self.config.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ExplorerError(f"Config key '{key}' must be int")
        return value

    def _cfg_float(self, key: str) -> float:
        value = self.config.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ExplorerError(f"Config key '{key}' must be float")
        return float(value)

    def _cfg_int_optional(self, key: str, default: int) -> int:
        value = self.config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ExplorerError(f"Config key '{key}' must be int")
        return value

    def _cfg_float_optional(self, key: str, default: float) -> float:
        value = self.config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ExplorerError(f"Config key '{key}' must be float")
        return float(value)


def _resolve_profile_name_from_config(config: "Config") -> str:
    profile = config.get("profiles.active")
    if not isinstance(profile, str) or not profile.strip():
        raise ExplorerError("profiles.active must be a non-empty string")
    return profile.strip()


def _safe_read_progress(config: "Config", profile_name: str) -> dict[str, Any]:
    try:
        progress = read_progress(config, profile_name)
        if not isinstance(progress, dict):
            raise ExplorerError("progress must be a dict")
        return progress
    except Exception:
        return {
            "version": 1,
            "profile": profile_name,
            "updated_at": _utc_now(),
            "run_state": {
                "active": False,
                "mode": "teach",
                "started_at": _utc_now(),
                "last_error": "",
            },
            "game_progress": {
                "current_location": 1,
                "current_floor": None,
                "target_floor_from": None,
                "target_floor_to": None,
            },
            "stats": {
                "floors_completed": 0,
                "rooms_cleared": 0,
                "rooms_skipped": 0,
                "rooms_failed": 0,
            },
        }


def _update_progress_run_state(progress: dict[str, Any], *, active: bool, mode: str, last_error: str) -> None:
    progress.setdefault("version", 1)
    progress["updated_at"] = _utc_now()
    run_state = progress.setdefault("run_state", {})
    if not isinstance(run_state, dict):
        run_state = {}
        progress["run_state"] = run_state
    run_state["active"] = active
    run_state["mode"] = mode
    run_state.setdefault("started_at", _utc_now())
    run_state["last_error"] = last_error


def _atomic_write_progress(config: "Config", profile_name: str, progress: dict[str, Any]) -> None:
    try:
        write_progress(config, profile_name, progress)
        return
    except Exception:
        pass

    raw = config.get("storage.progress_file")
    if not isinstance(raw, str) or not raw.strip():
        raise ExplorerError("storage.progress_file must be a non-empty string")

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (config.path.parent / path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(progress, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
