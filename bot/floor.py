from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from .floor_state import FloorState, FloorStateError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FloorResult:
    status: str
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

    def run(self, floor_number: int) -> FloorResult:
        self._pause_guard_stable_only("start_step")
        state = self._create_floor_state(floor_number)
        state.load()
        entry_node_id = state.ensure_entry_node()
        if state.current_node_id is None:
            state.set_current_node(entry_node_id)
        state.save()

        templates = self.knowledge.get_templates(profile=self.profile.name, enabled_only=True)
        map_templates = self._filter_templates(templates, ["door_marker", "party_marker", "room_open_big_icon", "next_floor", "danger"])
        screen_templates = self._filter_templates(templates, ["room_screen", "passage_screen", "event_screen", "mob", "boss", "loot", "exit", "auto_battle", "next_floor", "danger"])

        while True:
            self._pause_guard_stable_only("loop_start")
            stable_map_frame = self._capture_stable_frame_or_teach("map_scan")
            self._check_danger(stable_map_frame, templates)
            self._update_floor_state_from_map(state, stable_map_frame, map_templates)
            open_icon_excluded_ids = self._map_open_icons_to_door_ids(stable_map_frame, state, map_templates)
            target_door_id = state.get_next_target_door_id(exclude_open_room_icon_ids=open_icon_excluded_ids)

            if target_door_id is None:
                if self._tap_next_floor(stable_map_frame, templates):
                    return self._make_result(state, "completed", "next_floor_reached")
                return self._make_result(state, "stopped", "no_target_and_no_next_floor")

            door = state.get_door(target_door_id)
            state.increment_attempt(target_door_id)
            state.save()
            self._tap_point(door.tap_point)
            entered_frame = self._wait_after_action("door_enter")
            state.mark_in_progress(target_door_id)
            self._check_danger(entered_frame, templates)
            self._handle_entered_screen(state, target_door_id, entered_frame, screen_templates)
            state.save()

    def _create_floor_state(self, floor_number: int) -> FloorState:
        path = os.path.join(self.config.state_dir, self.config.floor_state_file_pattern.format(floor_number=floor_number))
        return FloorState(path=path, floor_number=floor_number, profile=self.profile.name)

    def _filter_templates(self, templates, categories: Sequence[str]):
        category_set = set(categories)
        return [template for template in templates if template.category in category_set]

    def _capture_frame(self):
        return self.screenshot.capture(self.adb)

    def _pause_guard_stable_only(self, reason: str) -> None:
        if not self.teacher_ui.pause_requested():
            return
        frame = self._capture_stable_frame_or_teach(reason)
        self.teacher_ui.show_and_label(frame, self.knowledge)

    def _capture_stable_frame_or_teach(self, reason: str):
        motion = self.config.motion
        last_frame = None
        for _ in range(int(motion.max_stable_retries) + 1):
            self.teacher_ui.pause_requested()
            frame, is_stable = self.vision.wait_until_stable(
                capture_fn=self._capture_frame,
                stable_frames=int(motion.stable_frames),
                stable_diff_threshold=float(motion.stable_diff_threshold),
                stable_poll_ms=int(motion.stable_poll_ms),
                stable_timeout_s=float(motion.stable_timeout_s),
                downscale_size=tuple(motion.downscale_size),
                pause_requested_fn=self.teacher_ui.pause_requested,
            )
            last_frame = frame
            if is_stable:
                return frame
        self.teacher_ui.show_and_label(last_frame, self.knowledge)
        return last_frame

    def _check_danger(self, frame, templates) -> None:
        danger_templates = self._filter_templates(templates, ["danger"])
        self.safety.check_danger(frame, self.vision, danger_templates)

    def _best_match(self, frame, templates, category: str):
        relevant = self._filter_templates(templates, [category])
        return self.vision.best_match_by_category(frame, relevant, category)

    def _find_all_category(self, frame, templates, category: str):
        relevant = self._filter_templates(templates, [category])
        return self.vision.find_all(frame, relevant, categories=[category])

    def _update_floor_state_from_map(self, state: FloorState, frame, templates):
        current_node_id = state.current_node_id or state.ensure_entry_node()
        for match in self._find_all_category(frame, templates, "door_marker"):
            state.upsert_door(current_node_id, tuple(int(v) for v in match.bbox), (int(match.center[0]), int(match.center[1])))
        state.save()

    def _map_open_icons_to_door_ids(self, frame, state: FloorState, templates):
        open_matches = self._find_all_category(frame, templates, "room_open_big_icon")
        excluded = []
        for open_match in open_matches:
            ox, oy = int(open_match.center[0]), int(open_match.center[1])
            nearest_id = None
            nearest_dist = None
            for door in state.list_doors():
                dx = int(door.tap_point[0]) - ox
                dy = int(door.tap_point[1]) - oy
                dist = (dx * dx + dy * dy) ** 0.5
                if nearest_dist is None or dist < nearest_dist:
                    nearest_dist = dist
                    nearest_id = door.id
            if nearest_id is not None:
                excluded.append(nearest_id)
        return excluded

    def _tap_point(self, point: Tuple[int, int]) -> None:
        self.safety.validate_tap(int(point[0]), int(point[1]), allowed_rois=None)
        self.adb.tap(int(point[0]), int(point[1]))

    def _wait_after_action(self, reason: str):
        return self._capture_stable_frame_or_teach(reason)

    def _handle_entered_screen(self, state: FloorState, door_id: str, frame, templates) -> None:
        current_screen = self.vision.classify_screen(frame, templates)
        if current_screen is None:
            if not self.vision.find_all(frame, templates):
                self.teacher_ui.show_and_label(frame, self.knowledge)
            raise FloorStateError("screen_classification_failed")
        if current_screen == "passage_screen":
            node_id = state.add_node(kind="passage", screen_ref=None)
            state.mark_passage(door_id, to_node_id=node_id)
            state.set_current_node(node_id)
            return
        if current_screen == "event_screen":
            self._exit_room(frame, templates)
            after_exit = self._wait_after_action("event_exit")
            self._check_danger(after_exit, templates)
            node_id = state.add_node(kind="event", screen_ref=None)
            state.mark_skipped(door_id, to_node_id=node_id)
            state.set_meta_counts(state.progress["rooms_cleared"], state.progress["rooms_skipped"], state.progress["rooms_failed"])
            return
        if current_screen == "room_screen":
            room_kind = "room"
            if self._best_match(frame, templates, "boss") is not None:
                room_kind = "boss"
                self._handle_auto_battle(templates)
            elif self._best_match(frame, templates, "mob") is not None:
                self._handle_auto_battle(templates)
            elif self._best_match(frame, templates, "loot") is not None:
                self._collect_loot(frame, templates)
            self._exit_room(frame, templates)
            after_exit = self._wait_after_action("room_exit")
            self._check_danger(after_exit, templates)
            node_id = state.add_node(kind=room_kind, screen_ref=None)
            state.mark_cleared(door_id, to_node_id=node_id)
            state.set_meta_counts(state.progress["rooms_cleared"], state.progress["rooms_skipped"], state.progress["rooms_failed"])
            return
        raise FloorStateError(f"unsupported_screen: {current_screen}")

    def _handle_auto_battle(self, templates) -> None:
        frame = self._capture_stable_frame_or_teach("before_auto_battle")
        auto_match = self._best_match(frame, templates, "auto_battle")
        if auto_match is not None:
            self._tap_point((int(auto_match.center[0]), int(auto_match.center[1])))
        else:
            action = self.profile.actions.get("auto_battle_tap")
            if action is None:
                raise FloorStateError("auto_battle control not found")
            self._tap_point((int(action.x), int(action.y)))
        deadline = time.time() + float(self.profile.timings.get("battle_settle_timeout_s", 120.0))
        while True:
            self.teacher_ui.pause_requested()
            stable_frame = self._capture_stable_frame_or_teach("battle_wait")
            self._check_danger(stable_frame, templates)
            if self._best_match(stable_frame, templates, "mob") is None and self._best_match(stable_frame, templates, "boss") is None:
                return
            if time.time() >= deadline:
                raise FloorStateError("battle_timeout")

    def _collect_loot(self, frame, templates) -> None:
        loot_match = self._best_match(frame, templates, "loot")
        if loot_match is None:
            raise FloorStateError("loot_not_found")
        self._tap_point((int(loot_match.center[0]), int(loot_match.center[1])))
        self._wait_after_action("loot_tap")

    def _exit_room(self, frame, templates) -> None:
        exit_match = self._best_match(frame, templates, "exit")
        if exit_match is not None:
            self._tap_point((int(exit_match.center[0]), int(exit_match.center[1])))
            return
        action = self.profile.actions.get("exit_tap")
        if action is None:
            raise FloorStateError("exit control not found")
        self._tap_point((int(action.x), int(action.y)))

    def _tap_next_floor(self, frame, templates) -> bool:
        next_match = self._best_match(frame, templates, "next_floor")
        if next_match is not None:
            self._tap_point((int(next_match.center[0]), int(next_match.center[1])))
            self._wait_after_action("next_floor_tap")
            return True
        action = self.profile.actions.get("next_floor_tap")
        if action is None:
            return False
        self._tap_point((int(action.x), int(action.y)))
        self._wait_after_action("next_floor_profile_tap")
        return True

    def _make_result(self, state: FloorState, status: str, reason: str) -> FloorResult:
        return FloorResult(
            status=status,
            rooms_cleared=int(state.meta.get("rooms_cleared", 0)),
            rooms_skipped=int(state.meta.get("rooms_skipped", 0)),
            rooms_failed=int(state.meta.get("rooms_failed", 0)),
            reason=reason,
        )
