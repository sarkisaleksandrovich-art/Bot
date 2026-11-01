from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

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

        if self._is_teach_only_mode():
            return FloorResult(
                status="stopped",
                rooms_cleared=0,
                rooms_skipped=0,
                rooms_failed=0,
                reason="teach_only_mode",
            )

        state = self._create_floor_state(floor_number)
        state.load()
        entry_node_id = state.ensure_entry_node()
        if state.current_node_id is None:
            state.set_current_node(entry_node_id)
        state.save()

        in_progress = state.get_found_in_progress()
        if in_progress:
            frame = self._capture_stable_frame_or_teach("found_in_progress")
            self._check_danger(frame)
            self._ask_user_to_review_stable_frame(frame, "Найден in_progress в floor_state. Нужна проверка пользователя, наугад не входим.")
            return self._make_result(state, status="stopped", reason="in_progress_found")

        templates = self._get_templates()
        map_templates = self._filter_templates(
            templates,
            categories=["door_marker", "party_marker", "room_open_big_icon", "next_floor", "danger"],
        )
        screen_templates = self._filter_templates(
            templates,
            categories=[
                "room_screen",
                "passage_screen",
                "event_screen",
                "mob",
                "boss",
                "loot",
                "exit",
                "auto_battle",
                "next_floor",
                "danger",
            ],
        )

        while True:
            self._pause_guard_stable_only("loop_start")
            stable_map_frame = self._capture_stable_frame_or_teach("map_scan")
            self._check_danger(stable_map_frame)

            party_match = self._best_match(stable_map_frame, map_templates, "party_marker")
            if party_match is None:
                self._ask_user_to_review_stable_frame(stable_map_frame, "На стабильном кадре не найден маркер отряда.")
                return self._make_result(state, status="stopped", reason="party_marker_not_found")

            discovered_door_ids = self._update_floor_state_from_map(
                state=state,
                frame=stable_map_frame,
                templates=map_templates,
            )

            if not discovered_door_ids and not self._has_any_known_object(stable_map_frame, map_templates):
                self._ask_user_to_review_stable_frame(stable_map_frame, "На стабильном кадре нет известных объектов.")
                return self._make_result(state, status="stopped", reason="no_known_objects")

            open_icon_excluded_ids = self._map_open_icons_to_door_ids(stable_map_frame, state, map_templates)
            target_door_id = state.get_next_target_door_id(exclude_open_room_icon_ids=open_icon_excluded_ids)

            if target_door_id is None:
                if self._tap_next_floor(stable_map_frame, templates):
                    return self._make_result(state, status="completed", reason="next_floor_reached")
                self._ask_user_to_review_stable_frame(stable_map_frame, "Не осталось unvisited дверей и не найден переход на следующий этаж.")
                return self._make_result(state, status="stopped", reason="no_target_and_no_next_floor")

            door = state.get_door(target_door_id)
            max_attempts = int(self.config.max_attempts_per_door)
            if int(door.attempts) >= max_attempts:
                state.mark_failed(target_door_id, "max_attempts_exceeded")
                state.save()
                continue

            try:
                state.increment_attempt(target_door_id)
                state.save()
                self._tap_point(door.tap_point)
                entered_frame = self._wait_after_action("door_enter")
                state.mark_in_progress(target_door_id)
                self._check_danger(entered_frame)
                self._handle_entered_screen(
                    state=state,
                    door_id=target_door_id,
                    frame=entered_frame,
                    templates=screen_templates,
                )
                state.save()
            except Exception as exc:
                if exc.__class__.__name__ == "SafetyStop":
                    raise
                logger.exception("Door handling failed: %s", exc)
                state.mark_failed(target_door_id, str(exc))
                state.save()
                if state.get_door(target_door_id).attempts >= max_attempts:
                    pass

    def _create_floor_state(self, floor_number: int) -> FloorState:
        path = os.path.join(
            self.config.state_dir,
            self.config.floor_state_file_pattern.format(floor_number=floor_number),
        )
        return FloorState(
            path=path,
            floor_number=floor_number,
            profile=self.profile.name,
        )

    def _get_templates(self):
        return self.knowledge.get_templates(profile=self.profile.name, enabled_only=True)

    def _filter_templates(self, templates, categories: Sequence[str]):
        category_set = set(categories)
        return [template for template in templates if template.category in category_set]

    def _profile_roi(self, name: str) -> Optional[Tuple[int, int, int, int]]:
        roi = self.profile.rois.get(name)
        if roi is None:
            return None
        return (int(roi.x), int(roi.y), int(roi.w), int(roi.h))

    def _capture_frame(self):
        return self.screenshot.capture(self.adb)

    def _pause_guard_stable_only(self, reason: str) -> None:
        if not self.teacher_ui.pause_requested():
            return
        frame = self._capture_stable_frame_or_teach(reason)
        self._ask_user_to_review_stable_frame(frame, f"Пауза пользователя: {reason}")

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
        self._ask_user_to_review_stable_frame(last_frame, f"экран не стабилизировался: {reason}")
        return last_frame

    def _ask_user_to_review_stable_frame(self, frame, message: str) -> None:
        logger.warning(message)
        self.teacher_ui.show_and_label(frame, self.knowledge)

    def _check_danger(self, frame) -> None:
        templates = self._filter_templates(self._get_templates(), categories=["danger"])
        self.safety.check_danger(frame, self.vision, templates)

    def _best_match(self, frame, templates, category: str):
        relevant = self._filter_templates(templates, [category])
        return self.vision.best_match_by_category(frame, relevant, category)

    def _find_all_category(self, frame, templates, category: str):
        relevant = self._filter_templates(templates, [category])
        return self.vision.find_all(frame, relevant, categories=[category])

    def _has_any_known_object(self, frame, templates) -> bool:
        matches = self.vision.find_all(frame, templates)
        return bool(matches)

    def _update_floor_state_from_map(self, state: FloorState, frame, templates) -> List[str]:
        current_node_id = state.current_node_id or state.ensure_entry_node()
        door_matches = self._find_all_category(frame, templates, "door_marker")
        door_ids: List[str] = []
        for match in door_matches:
            bbox = tuple(int(v) for v in match.bbox)
            tap_point = (int(match.center[0]), int(match.center[1]))
            door_id = state.upsert_door(current_node_id, bbox, tap_point)
            door_ids.append(door_id)
        state.save()
        return door_ids

    def _map_open_icons_to_door_ids(self, frame, state: FloorState, templates) -> List[str]:
        open_matches = self._find_all_category(frame, templates, "room_open_big_icon")
        if not open_matches:
            return []
        excluded: List[str] = []
        for open_match in open_matches:
            ox, oy = int(open_match.center[0]), int(open_match.center[1])
            nearest_id: Optional[str] = None
            nearest_dist: Optional[float] = None
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
        allowed_rois = None
        self.safety.validate_tap(int(point[0]), int(point[1]), allowed_rois=allowed_rois)
        self.adb.tap(int(point[0]), int(point[1]))

    def _wait_after_action(self, reason: str):
        _ = reason
        return self._capture_stable_frame_or_teach(reason)

    def _handle_entered_screen(self, state: FloorState, door_id: str, frame, templates) -> None:
        current_screen = self._classify_screen_by_roi(frame, templates)
        if current_screen is None:
            if not self._has_any_known_object(frame, templates):
                self._ask_user_to_review_stable_frame(frame, "На стабильном кадре после входа ничего не распознано.")
            else:
                self._ask_user_to_review_stable_frame(frame, "Сомнительная классификация экрана после входа.")
            raise FloorStateError("screen_classification_failed")

        if current_screen == "passage_screen":
            node_id = state.add_node(kind="passage", screen_ref=None)
            state.mark_passage(door_id, to_node_id=node_id)
            state.set_current_node(node_id)
            return

        if current_screen == "event_screen":
            self._exit_room(frame, templates)
            after_exit = self._wait_after_action("event_exit")
            self._check_danger(after_exit)
            node_id = state.add_node(kind="event", screen_ref=None)
            state.mark_skipped(door_id, to_node_id=node_id)
            state.set_meta_counts(
                rooms_cleared=state.progress["rooms_cleared"],
                rooms_skipped=state.progress["rooms_skipped"],
                rooms_failed=state.progress["rooms_failed"],
            )
            return

        if current_screen == "room_screen":
            room_kind = "room"
            if self._best_match(frame, templates, "boss") is not None:
                room_kind = "boss"
                self._handle_auto_battle(templates)
            elif self._best_match(frame, templates, "mob") is not None:
                room_kind = "room"
                self._handle_auto_battle(templates)
            elif self._best_match(frame, templates, "loot") is not None:
                self._collect_loot(frame, templates)
            else:
                self._ask_user_to_review_stable_frame(frame, "Комната распознана, но её тип неясен на стабильном кадре.")
                raise FloorStateError("room_type_unknown")

            self._exit_room(frame, templates)
            after_exit = self._wait_after_action("room_exit")
            self._check_danger(after_exit)
            node_id = state.add_node(kind=room_kind, screen_ref=None)
            state.mark_cleared(door_id, to_node_id=node_id)
            state.set_meta_counts(
                rooms_cleared=state.progress["rooms_cleared"],
                rooms_skipped=state.progress["rooms_skipped"],
                rooms_failed=state.progress["rooms_failed"],
            )
            return

        raise FloorStateError(f"unsupported_screen: {current_screen}")

    def _classify_screen_by_roi(self, frame, templates) -> Optional[str]:
        screen_categories = ["room_screen", "passage_screen", "event_screen"]
        best_category: Optional[str] = None
        best_score: Optional[float] = None
        for category in screen_categories:
            roi = self._profile_roi(category)
            category_templates = self._filter_templates(templates, [category])
            for template in category_templates:
                match = self.vision.find(frame, template, roi=roi, threshold=max(float(template.threshold), float(self.config.matching_classify_threshold)))
                if match is None:
                    continue
                if best_score is None or float(match.score) > best_score:
                    best_score = float(match.score)
                    best_category = category
        return best_category

    def _handle_auto_battle(self, templates) -> None:
        self._pause_guard_stable_only("before_auto_battle")
        frame = self._capture_stable_frame_or_teach("before_auto_battle")
        auto_match = self._best_match(frame, templates, "auto_battle")
        if auto_match is not None:
            self._tap_point((int(auto_match.center[0]), int(auto_match.center[1])))
            frame = self._wait_after_action("auto_battle_tap")
        else:
            action = self.profile.actions.get("auto_battle_tap")
            if action is None:
                raise FloorStateError("auto_battle control not found and profile.actions.auto_battle_tap is absent")
            self._tap_point((int(action.x), int(action.y)))
            frame = self._wait_after_action("auto_battle_profile_tap")

        deadline = time.time() + float(self.profile.timings.get("battle_settle_timeout_s", 120.0))
        while True:
            self.teacher_ui.pause_requested()
            stable_frame = self._capture_stable_frame_or_teach("battle_wait")
            self._check_danger(stable_frame)
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
            raise FloorStateError("exit control not found and profile.actions.exit_tap is absent")
        self._tap_point((int(action.x), int(action.y)))

    def _tap_next_floor(self, frame, templates) -> bool:
        self._pause_guard_stable_only("before_next_floor")
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
        rooms_cleared = int(state.meta.get("rooms_cleared", 0))
        rooms_skipped = int(state.meta.get("rooms_skipped", 0))
        rooms_failed = int(state.meta.get("rooms_failed", 0))
        return FloorResult(
            status=status,
            rooms_cleared=rooms_cleared,
            rooms_skipped=rooms_skipped,
            rooms_failed=rooms_failed,
            reason=reason,
        )

    def _is_teach_only_mode(self) -> bool:
        raw = getattr(self.config, "raw", {})
        if not isinstance(raw, dict):
            return False
        app_section = raw.get("app")
        if not isinstance(app_section, dict):
            return False
        return bool(app_section.get("teach_only", False))
