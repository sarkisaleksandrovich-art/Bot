from __future__ import annotations

import json
import os
import tempfile
from typing import Dict

from .floor import FloorExplorer, FloorResult


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
        if floor_from > floor_to:
            raise ExplorerError("floor_from must be <= floor_to")

        progress = self.load_progress()
        progress["floor_from"] = int(floor_from)
        progress["floor_to"] = int(floor_to)
        progress["status"] = "running"
        self.save_progress(progress)

        for floor_number in range(floor_from, floor_to + 1):
            if self.teacher_ui.pause_requested():
                frame, is_stable = self.vision.wait_until_stable(
                    capture_fn=lambda: self.screenshot.capture(self.adb),
                    stable_frames=int(self.config.motion.stable_frames),
                    stable_diff_threshold=float(self.config.motion.stable_diff_threshold),
                    stable_poll_ms=int(self.config.motion.stable_poll_ms),
                    stable_timeout_s=float(self.config.motion.stable_timeout_s),
                    downscale_size=tuple(self.config.motion.downscale_size),
                    pause_requested_fn=self.teacher_ui.pause_requested,
                )
                if is_stable:
                    self.teacher_ui.show_and_label(frame, self.knowledge)

            result = FloorExplorer(
                self.adb,
                self.screenshot,
                self.vision,
                self.knowledge,
                self.safety,
                self.teacher_ui,
                self.config,
                self.profile,
            ).run(floor_number)

            self._apply_floor_result(progress, floor_number, result)
            self.save_progress(progress)

            if result.status == "danger":
                raise ExplorerError(result.reason)
            if result.status in {"failed", "stopped"}:
                raise ExplorerError(result.reason)

        progress["status"] = "completed"
        self.save_progress(progress)

    def load_progress(self) -> dict:
        path = self.config.progress_file
        if not os.path.exists(path):
            return {
                "version": 1,
                "profile": self.profile.name,
                "started_at": "",
                "updated_at": "",
                "current_floor": 0,
                "floor_from": 0,
                "floor_to": 0,
                "status": "idle",
                "totals": {
                    "floors_completed": 0,
                    "rooms_cleared": 0,
                    "rooms_skipped": 0,
                    "rooms_failed": 0,
                },
                "last_result": {
                    "floor_number": 0,
                    "status": "",
                    "reason": "",
                },
            }
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def save_progress(self, progress: dict) -> None:
        path = self.config.progress_file
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=os.path.dirname(path) or ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(progress, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def _apply_floor_result(self, progress: Dict[str, object], floor_number: int, result: FloorResult) -> None:
        progress["current_floor"] = int(floor_number)
        progress["last_result"] = {
            "floor_number": int(floor_number),
            "status": result.status,
            "reason": result.reason,
        }
        totals = progress.setdefault("totals", {})
        totals["rooms_cleared"] = int(totals.get("rooms_cleared", 0)) + int(result.rooms_cleared)
        totals["rooms_skipped"] = int(totals.get("rooms_skipped", 0)) + int(result.rooms_skipped)
        totals["rooms_failed"] = int(totals.get("rooms_failed", 0)) + int(result.rooms_failed)
        if result.status == "completed":
            totals["floors_completed"] = int(totals.get("floors_completed", 0)) + 1
