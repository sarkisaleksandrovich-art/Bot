from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Any, Dict, Optional, Tuple

from .floor import FloorExplorer, FloorResult
from .profiles import load_profile, read_progress, write_progress

logger = logging.getLogger(__name__)


class ExplorerError(RuntimeError):
    pass


class GameExplorer:
    def __init__(
        self,
        adb: "ADBController",
        screenshot: "ScreenshotService",
        vision: "VisionService",
        knowledge: "KnowledgeBase",
        safety: "SafetyService",
        teacher_ui: "TeacherUI",
        config: "AppConfig",
        profile: "ProfileConfig",
    ) -> None:
        self.adb = adb
        self.screenshot = screenshot
        self.vision = vision
        self.knowledge = knowledge
        self.safety = safety
        self.teacher_ui = teacher_ui
        self.config = config
        self.profile = profile

    def run(self, floor_from: int, floor_to: int) -> None:
        if floor_from <= 0 or floor_to <= 0:
            raise ExplorerError("floor_from and floor_to must be positive")
        if floor_from > floor_to:
            raise ExplorerError("floor_from must be <= floor_to")

        progress = self.load_progress()
        progress["floor_from"] = int(floor_from)
        progress["floor_to"] = int(floor_to)
        progress["current_floor"] = int(progress.get("current_floor", floor_from - 1))
        progress["status"] = "running"
        self.save_progress(progress)

        started_at = time.time()
        max_total_seconds = float(self._raw_exploration_value("max_total_runtime_s", 0) or 0)
        max_same_screen_steps = int(self._raw_exploration_value("stuck_same_screen_steps", 0) or 0)
        last_screen_key: Optional[Tuple[int, int, int]] = None
        same_screen_steps = 0

        for floor_number in range(int(floor_from), int(floor_to) + 1):
            self._check_pause_before_iteration(f"before_floor_{floor_number}")
            if max_total_seconds > 0 and (time.time() - started_at) > max_total_seconds:
                progress["status"] = "stopped"
                progress["last_result"] = {
                    "floor_number": floor_number - 1,
                    "status": "stopped",
                    "reason": "max_total_runtime_exceeded",
                }
                self.save_progress(progress)
                raise ExplorerError("Остановка: превышен общий лимит времени run_explore.")

            stable_frame, is_stable = self._wait_stable_for_outer_loop(f"floor_{floor_number}_precheck")
            if is_stable:
                screen_key = self._stable_screen_key(stable_frame)
                if last_screen_key == screen_key:
                    same_screen_steps += 1
                else:
                    same_screen_steps = 1
                last_screen_key = screen_key

                if max_same_screen_steps > 0 and same_screen_steps >= max_same_screen_steps:
                    progress["status"] = "stopped"
                    progress["last_result"] = {
                        "floor_number": floor_number - 1,
                        "status": "stopped",
                        "reason": "stuck_same_stable_screen",
                    }
                    self.save_progress(progress)
                    raise ExplorerError("Остановка: детектор зависания, один и тот же стабильный экран повторяется слишком долго.")
            else:
                last_screen_key = None
                same_screen_steps = 0
                self._handle_pause_or_unknown(stable_frame, "экран не стабилизировался")

            floor_explorer = FloorExplorer(
                adb=self.adb,
                screenshot=self.screenshot,
                vision=self.vision,
                knowledge=self.knowledge,
                safety=self.safety,
                teacher_ui=self.teacher_ui,
                config=self.config,
                profile=self.profile,
            )

            try:
                result = floor_explorer.run(floor_number)
            except Exception as exc:
                self._save_emergency_screenshot(f"floor_{floor_number}_error")
                progress["status"] = "error"
                progress["last_result"] = {
                    "floor_number": floor_number,
                    "status": "error",
                    "reason": str(exc),
                }
                self.save_progress(progress)
                raise

            self._apply_floor_result(progress, floor_number, result)
            self.save_progress(progress)

            if result.status == "danger":
                raise ExplorerError(f"Остановка по danger на этаже {floor_number}: {result.reason}")
            if result.status == "failed":
                raise ExplorerError(f"Остановка по failed на этаже {floor_number}: {result.reason}")
            if result.status == "stopped":
                raise ExplorerError(f"Остановка на этаже {floor_number}: {result.reason}")

        progress["status"] = "completed"
        self.save_progress(progress)

    def load_progress(self) -> dict:
        profile_data = load_profile(self.config, getattr(self.profile, "name", None))
        progress = read_progress(profile_data)
        progress.setdefault("version", 1)
        progress.setdefault("profile", getattr(self.profile, "name", "default"))
        progress.setdefault("started_at", progress.get("updated_at", ""))
        progress.setdefault("updated_at", "")
        progress.setdefault("current_floor", 0)
        progress.setdefault("floor_from", 0)
        progress.setdefault("floor_to", 0)
        progress.setdefault("status", "idle")
        progress.setdefault(
            "totals",
            {
                "floors_completed": 0,
                "rooms_cleared": 0,
                "rooms_skipped": 0,
                "rooms_failed": 0,
            },
        )
        progress.setdefault(
            "last_result",
            {
                "floor_number": 0,
                "status": "",
                "reason": "",
            },
        )
        return progress

    def save_progress(self, progress: dict) -> None:
        profile_data = load_profile(self.config, getattr(self.profile, "name", None))
        write_progress(profile_data, progress)

    def run_explore(self, floor_from: int, floor_to: int) -> None:
        self.run(floor_from, floor_to)

    def run_teach_only(self) -> None:
        self.teacher_ui.teach_only(
            adb=self.adb,
            screenshot=self.screenshot,
            knowledge=self.knowledge,
        )

    def _apply_floor_result(self, progress: Dict[str, Any], floor_number: int, result: FloorResult) -> None:
        progress["current_floor"] = int(floor_number)
        progress["last_result"] = {
            "floor_number": int(floor_number),
            "status": str(result.status),
            "reason": str(result.reason),
        }

        totals = progress.setdefault("totals", {})
        totals["rooms_cleared"] = int(totals.get("rooms_cleared", 0)) + int(result.rooms_cleared)
        totals["rooms_skipped"] = int(totals.get("rooms_skipped", 0)) + int(result.rooms_skipped)
        totals["rooms_failed"] = int(totals.get("rooms_failed", 0)) + int(result.rooms_failed)
        if result.status == "completed":
            totals["floors_completed"] = int(totals.get("floors_completed", 0)) + 1

        if result.status == "completed":
            progress["status"] = "running"
        elif result.status == "danger":
            progress["status"] = "stopped"
        elif result.status == "failed":
            progress["status"] = "error"
        else:
            progress["status"] = "stopped"

    def _check_pause_before_iteration(self, reason: str) -> None:
        if self.teacher_ui.pause_requested():
            frame, is_stable = self._wait_stable_for_outer_loop(reason)
            if is_stable:
                self.teacher_ui.show_and_label(frame, self.knowledge)
            else:
                self._handle_pause_or_unknown(frame, "экран не стабилизировался")

    def _wait_stable_for_outer_loop(self, reason: str):
        _ = reason
        motion = self.config.motion
        return self.vision.wait_until_stable(
            capture_fn=lambda: self.screenshot.capture(self.adb),
            stable_frames=int(motion.stable_frames),
            stable_diff_threshold=float(motion.stable_diff_threshold),
            stable_poll_ms=int(motion.stable_poll_ms),
            stable_timeout_s=float(motion.stable_timeout_s),
            downscale_size=tuple(motion.downscale_size),
            pause_requested_fn=self.teacher_ui.pause_requested,
        )

    def _handle_pause_or_unknown(self, frame, message: str) -> None:
        logger.warning(message)
        self.teacher_ui.show_and_label(frame, self.knowledge)

    def _save_emergency_screenshot(self, reason: str) -> None:
        try:
            frame = self.screenshot.capture(self.adb)
            if hasattr(self.screenshot, "save_emergency"):
                self.screenshot.save_emergency(frame, reason=reason)
        except Exception:
            logger.exception("Failed to save emergency screenshot")

    def _stable_screen_key(self, frame) -> Tuple[int, int, int]:
        small = self.vision._prepare_motion_frame(frame, tuple(self.config.motion.downscale_size))
        return int(small.shape[0]), int(small.shape[1]), int(small.mean())

    def _raw_exploration_value(self, key: str, default: Any) -> Any:
        raw = getattr(self.config, "raw", {})
        if not isinstance(raw, dict):
            return default
        section = raw.get("exploration", {})
        if not isinstance(section, dict):
            return default
        return section.get(key, default)


def run_explore(
    adb: "ADBController",
    screenshot: "ScreenshotService",
    vision: "VisionService",
    knowledge: "KnowledgeBase",
    safety: "SafetyService",
    teacher_ui: "TeacherUI",
    config: "AppConfig",
    profile: "ProfileConfig",
    floor_from: int,
    floor_to: int,
) -> None:
    GameExplorer(
        adb=adb,
        screenshot=screenshot,
        vision=vision,
        knowledge=knowledge,
        safety=safety,
        teacher_ui=teacher_ui,
        config=config,
        profile=profile,
    ).run_explore(floor_from, floor_to)


def run_teach_only(
    adb: "ADBController",
    screenshot: "ScreenshotService",
    vision: "VisionService",
    knowledge: "KnowledgeBase",
    safety: "SafetyService",
    teacher_ui: "TeacherUI",
    config: "AppConfig",
    profile: "ProfileConfig",
) -> None:
    _ = vision
    _ = safety
    GameExplorer(
        adb=adb,
        screenshot=screenshot,
        vision=vision,
        knowledge=knowledge,
        safety=safety,
        teacher_ui=teacher_ui,
        config=config,
        profile=profile,
    ).run_teach_only()
