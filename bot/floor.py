from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

from bot.floor_state import FloorStateError, FloorStateStore
from bot.safety import SafetyStop

if TYPE_CHECKING:
    from bot.adb_controller import ADBController
    from bot.config import Config
    from bot.knowledge import KnowledgeBase
    from bot.profiles import Profile
    from bot.safety import SafetyService
    from bot.screenshot import ScreenshotService
    from bot.vision import Frame, Match, VisionService


class FloorExploreError(Exception):
    ...


FloorStatus = Literal["completed", "stopped", "failed"]


@dataclass(frozen=True)
class FloorResult:
    status: FloorStatus
    rooms_cleared: int
    rooms_skipped: int
    rooms_failed: int
    reason: str


class FloorExplorer:
    def __init__(
        self,
        adb: "ADBController",
        screenshot: "ScreenshotService",
        vision: "VisionService",
        knowledge: "KnowledgeBase",
        safety: "SafetyService",
        teacher_ui,
        config: "Config",
        profile: "Profile",
    ) -> None:
        self.adb = adb
        self.screenshot = screenshot
        self.vision = vision
        self.knowledge = knowledge
        self.safety = safety
        self.teacher_ui = teacher_ui
        self.config = config
        self.profile = profile
        if not hasattr(teacher_ui, "pause_requested") or not hasattr(teacher_ui, "show_and_label"):
            raise FloorExploreError("teacher_ui must provide pause_requested and show_and_label")

    def run(self, floor_number: int) -> FloorResult:
        store = FloorStateStore(self.config, self.profile.name, floor_number)
        graph = store.load()
        rooms_cleared = 0
        rooms_skipped = 0
        rooms_failed = 0

        while True:
            self._pause_check()
            frame = self._wait_stable()
            self._check_danger(frame)
            self._scan_map(store, graph, frame)

            target = store.next_target(graph)
            if target is None:
                next_floor = self._find_best(frame, "next_floor")
                if next_floor is None:
                    self._teach_if_unknown(frame)
                    next_floor = self._find_best(self._wait_stable(), "next_floor")
                if next_floor is None:
                    return FloorResult("failed", rooms_cleared, rooms_skipped, rooms_failed, "next_floor not found")
                self.safety.ensure_action_allowed("next_floor")
                self._tap_match(next_floor)
                self._wait_stable()
                return FloorResult("completed", rooms_cleared, rooms_skipped, rooms_failed, "floor completed")

            self.safety.ensure_action_allowed("enter_room")
            self._tap_door(target)
            entered_frame = self._wait_stable()
            store.mark_in_progress_and_save(graph, target.door_id, notes="entered")
            self._check_danger(entered_frame)
            self._pause_check()

            screen = self._classify(entered_frame)
            if screen is None:
                self._teach_if_unknown(entered_frame)
                entered_frame = self._wait_stable()
                screen = self._classify(entered_frame)

            if screen == "event_screen":
                exit_match = self._find_best(entered_frame, "exit")
                if exit_match is None:
                    store.set_status(graph, target.door_id, "failed", notes="event exit not found")
                    store.save(graph)
                    rooms_failed += 1
                    continue
                self._tap_match(exit_match)
                self._wait_stable()
                store.set_status(graph, target.door_id, "skipped", notes="event skipped by exit")
                store.save(graph)
                rooms_skipped += 1
                continue

            if screen == "passage_screen":
                store.set_status(graph, target.door_id, "passage", notes="passage")
                store.save(graph)
                continue

            if screen == "room_screen":
                if self._find_best(entered_frame, "mob") is not None or self._find_best(entered_frame, "boss") is not None:
                    auto_battle = self._find_best(entered_frame, "auto_battle")
                    if auto_battle is None:
                        store.set_status(graph, target.door_id, "failed", notes="auto_battle not found")
                        store.save(graph)
                        rooms_failed += 1
                        continue
                    self._tap_match(auto_battle)
                    entered_frame = self._wait_battle_end()
                loot = self._find_best(entered_frame, "loot")
                if loot is not None:
                    self._tap_match(loot)
                    entered_frame = self._wait_stable()
                exit_match = self._find_best(entered_frame, "exit")
                if exit_match is None:
                    store.set_status(graph, target.door_id, "failed", notes="room exit not found")
                    store.save(graph)
                    rooms_failed += 1
                    continue
                self._tap_match(exit_match)
                self._wait_stable()
                store.set_status(graph, target.door_id, "cleared", notes="room cleared")
                store.save(graph)
                rooms_cleared += 1
                continue

            store.set_status(graph, target.door_id, "failed", notes="screen not recognized")
            store.save(graph)
            rooms_failed += 1

    def _scan_map(self, store: FloorStateStore, graph, frame: "Frame") -> None:
        door_matches = self._find_all(frame, "door_marker")
        open_matches = self._find_all(frame, "room_open_big_icon")
        for index, match in enumerate(door_matches, start=1):
            cx, cy = self.vision.center_of(match.rect)
            is_open = any(self._overlaps(match.rect, icon.rect) for icon in open_matches)
            store.upsert_door(
                graph,
                door_id=f"d_{index:04d}",
                map_x=cx,
                map_y=cy,
                bbox_x=match.rect.x,
                bbox_y=match.rect.y,
                bbox_w=match.rect.w,
                bbox_h=match.rect.h,
                is_room_open_big_icon=is_open,
            )
        store.save(graph)

    def _classify(self, frame: "Frame") -> str | None:
        return self.vision.classify_screen(frame, self.knowledge.get_templates_for_screen_classification())

    def _find_all(self, frame: "Frame", category: str):
        templates = self.knowledge.get_templates_for_category(category)
        return self.vision.find_all(frame, templates, category=category)

    def _find_best(self, frame: "Frame", category: str):
        matches = self._find_all(frame, category)
        return matches[0] if matches else None

    def _tap_match(self, match: "Match") -> None:
        self.safety.ensure_action_allowed("tap")
        x, y = self.vision.center_of(match.rect)
        self.adb.tap(x, y)

    def _tap_door(self, node) -> None:
        self.safety.ensure_action_allowed("tap")
        x = node.bbox_x + node.bbox_w // 2
        y = node.bbox_y + node.bbox_h // 2
        self.adb.tap(x, y)

    def _wait_stable(self) -> "Frame":
        retries = int(self.config.get("motion.max_stable_retries", 0))
        last_frame = None
        for _ in range(retries + 1):
            self._pause_check()
            frame, stable = self.vision.wait_until_stable(self.screenshot.capture)
            last_frame = frame
            if stable:
                return frame
        if bool(self.config.get("teaching.allow_teach_on_unstable_timeout", True)) and last_frame is not None:
            self._pause_check(last_frame)
        if last_frame is None:
            raise FloorExploreError("failed to capture frame")
        return last_frame

    def _wait_battle_end(self) -> "Frame":
        timeout_s = float(self.config.get("timings.battle_timeout_s", 180))
        poll_s = int(self.config.get("timings.auto_battle_check_interval_ms", 500)) / 1000.0
        deadline = time.monotonic() + timeout_s
        frame = self._wait_stable()
        while True:
            self._pause_check(frame)
            if self._find_best(frame, "mob") is None and self._find_best(frame, "boss") is None:
                return frame
            if time.monotonic() >= deadline:
                raise FloorExploreError("battle timeout")
            time.sleep(poll_s)
            frame = self._wait_stable()
            self._check_danger(frame)

    def _check_danger(self, frame: "Frame") -> None:
        self.safety.check_danger(frame, self.vision, self.knowledge.get_templates_for_category("danger"))

    def _teach_if_unknown(self, frame: "Frame") -> None:
        if self.knowledge.list_templates(enabled_only=True):
            self.teacher_ui.show_and_label(frame, self.knowledge)
        else:
            self.teacher_ui.show_and_label(frame, self.knowledge)

    def _pause_check(self, frame: "Frame" | None = None) -> None:
        if self.teacher_ui.pause_requested():
            if frame is None:
                frame, _ = self.vision.wait_until_stable(self.screenshot.capture)
            self.teacher_ui.show_and_label(frame, self.knowledge)

    @staticmethod
    def _overlaps(a, b) -> bool:
        return not (a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y)
