from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .floor_state import DoorNotFoundError, FloorStateStore, NoAvailableDoorError
from .safety import DangerDetectedError, SafetyError
from .teacher_ui import LabelingCancelledError

if TYPE_CHECKING:
    from .adb_controller import ADBController
    from .config import BotConfig
    from .knowledge import KnowledgeBase, TemplateRecord
    from .safety import SafetyManager
    from .screenshot import Frame, ScreenshotProvider
    from .teacher_ui import TeacherUI
    from .vision import Match, VisionEngine


class FloorError(Exception):
    pass


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
        screenshot: "ScreenshotProvider",
        vision: "VisionEngine",
        knowledge: "KnowledgeBase",
        safety: "SafetyManager",
        teacher_ui: "TeacherUI",
        config: "BotConfig",
        profile: str,
    ) -> None:
        self.adb = adb
        self.screenshot = screenshot
        self.vision = vision
        self.knowledge = knowledge
        self.safety = safety
        self.teacher_ui = teacher_ui
        self.config = config
        self.profile = profile

        self.logger = logging.getLogger("bot.floor")
        self.floor_state = FloorStateStore(config, profile, self.logger)

        self._motion_cfg = self._section("motion")
        self._timeouts_cfg = self._section("timeouts")
        self._teacher_cfg = self._section("teacher")
        self._input_cfg = self._section("input")
        self._debug_cfg = self._section("debug")
        self._game_cfg = self._section("game")

        self._stable_frames = int(self._motion_cfg.get("stable_frames", 3))
        self._stable_diff_threshold = float(self._motion_cfg.get("stable_diff_threshold", 0.015))
        self._stable_poll_ms = int(self._motion_cfg.get("stable_poll_ms", 350))
        self._stable_timeout_s = float(self._motion_cfg.get("stable_timeout_s", 8.0))
        self._max_stable_retries = int(self._motion_cfg.get("max_stable_retries", 2))
        raw_downscale = self._motion_cfg.get("downscale_size", [64, 36])
        self._downscale_size = (int(raw_downscale[0]), int(raw_downscale[1]))

        self._battle_finish_timeout_s = float(self._timeouts_cfg.get("battle_finish_timeout_s", 120.0))
        self._tap_jitter_px = int(self._input_cfg.get("tap_jitter_px", 0))
        self._clamp_to_screen = bool(self._input_cfg.get("clamp_to_screen", True))
        self._save_last_frame_on_error = bool(self._debug_cfg.get("save_last_frame_on_error", True))
        self._teach_only = False
        self._max_unknown_stable_screens = int(self._section("safety").get("max_unknown_stable_screens", 3))

    def run(self, floor_number: int) -> FloorResult:
        self.safety.ensure_action_allowed("explore")
        templates = self._reload_templates()
        state = self._load_or_reset_state(floor_number)

        while True:
            self._handle_pause_on_step()
            templates = self._reload_templates()
            frame = self._stable_capture_or_teach("unknown_stable_prompt", templates)
            self.safety.ensure_safe_frame(frame, self.vision, templates)
            self._apply_safety_blocks()

            map_info = self._detect_map_objects(frame, templates)
            if map_info["party_match"] is not None:
                state = self.floor_state.set_progress_value(state, "party_marker", list(map_info["party_match"].rect))

            state = self.floor_state.upsert_visible_doors(state, [match.rect for match in map_info["door_matches"]])
            state = self._mark_open_big_icons(state, map_info["open_room_matches"])
            self.floor_state.save(state)

            try:
                target = self.floor_state.choose_next_target(state)
            except NoAvailableDoorError:
                next_floor_match = map_info["next_floor_match"]
                if next_floor_match is None:
                    self.teacher_ui.notify("Непосещённых дверей нет, но next_floor не найден. Нужна разметка.")
                    self._pause_and_label(frame)
                    continue
                if self._teach_only:
                    return self._result_from_state(state, "stopped", "teach_only: next_floor найден, нажатие не выполнено")
                self._tap_match(next_floor_match, frame)
                next_frame = self._wait_after_motion("next_floor")
                self.safety.ensure_safe_frame(next_frame, self.vision, self._reload_templates())
                return self._result_from_state(state, "completed", "этаж завершён")

            if self._teach_only:
                return self._result_from_state(state, "stopped", "teach_only: дверь выбрана, нажатие не выполнено")

            self._tap_rect_center(target.marker_rect, frame)
            entered_frame = self._wait_after_motion("enter_door")
            self.safety.ensure_safe_frame(entered_frame, self.vision, self._reload_templates())
            state = self.floor_state.mark_in_progress_and_flush(state, target.door_id, note="entered")
            state = self._handle_entered_door(state, target.door_id, entered_frame)

    def _handle_entered_door(self, state: dict, door_id: str, frame: "Frame") -> dict:
        templates = self._reload_templates()
        self._handle_pause_in_wait_loop(frame)
        screen_name = self._classify_room_or_passage(frame, templates)

        if screen_name is None:
            self.teacher_ui.notify("Не удалось классифицировать стабильный кадр после входа. Нужна разметка.")
            self._pause_and_label(frame)
            templates = self._reload_templates()
            frame = self._stable_capture_or_teach("unknown_stable_prompt", templates)
            self.safety.ensure_safe_frame(frame, self.vision, templates)
            screen_name = self._classify_room_or_passage(frame, templates)
            if screen_name is None:
                state = self.floor_state.mark_door_status(state, door_id, "failed", note="screen_classification_failed")
                self.floor_state.save(state)
                return state

        if screen_name == "passage_screen":
            state = self.floor_state.mark_door_status(state, door_id, "passage", note="passage_screen")
            self.floor_state.save(state)
            return state

        if screen_name == "event_screen":
            return self._exit_room_with_status(state, door_id, frame, templates, final_status="skipped")

        if screen_name != "room_screen":
            state = self.floor_state.mark_door_status(state, door_id, "failed", note=f"unsupported_screen:{screen_name}")
            self.floor_state.save(state)
            return state

        return self._clear_room(state, door_id, frame, templates)

    def _clear_room(self, state: dict, door_id: str, frame: "Frame", templates: list["TemplateRecord"]) -> dict:
        loot_matches = self.vision.find_category(frame, templates, "loot")
        mob_matches = self.vision.find_category(frame, templates, "mob")
        boss_matches = self.vision.find_category(frame, templates, "boss")
        auto_matches = self.vision.find_category(frame, templates, "auto_battle")

        if boss_matches or mob_matches:
            if auto_matches and not self._teach_only:
                self._tap_match(auto_matches[0], frame)
                frame = self._wait_after_motion("auto_battle")
                templates = self._reload_templates()
                self.safety.ensure_safe_frame(frame, self.vision, templates)
            frame = self._wait_battle_end(templates)
            templates = self._reload_templates()
        elif loot_matches and not self._teach_only:
            self._tap_match(loot_matches[0], frame)
            frame = self._wait_after_motion("loot")
            templates = self._reload_templates()
            self.safety.ensure_safe_frame(frame, self.vision, templates)

        return self._exit_room_with_status(state, door_id, frame, templates, final_status="cleared")

    def _exit_room_with_status(
        self,
        state: dict,
        door_id: str,
        frame: "Frame",
        templates: list["TemplateRecord"],
        final_status: str,
    ) -> dict:
        exit_matches = self.vision.find_category(frame, templates, "exit")
        if not exit_matches:
            self.teacher_ui.notify("Не найден exit на стабильном кадре. Нужна разметка.")
            self._pause_and_label(frame)
            templates = self._reload_templates()
            frame = self._stable_capture_or_teach("unknown_stable_prompt", templates)
            self.safety.ensure_safe_frame(frame, self.vision, templates)
            exit_matches = self.vision.find_category(frame, templates, "exit")
        if not exit_matches:
            state = self.floor_state.mark_door_status(state, door_id, "failed", note="exit_not_found")
            self.floor_state.save(state)
            return state
        if not self._teach_only:
            self._tap_match(exit_matches[0], frame)
            map_frame = self._wait_after_motion("exit_room")
            self.safety.ensure_safe_frame(map_frame, self.vision, self._reload_templates())
        state = self.floor_state.mark_door_status(state, door_id, final_status, note=f"{final_status}_after_exit")
        self.floor_state.save(state)
        return state

    def _wait_battle_end(self, templates: list["TemplateRecord"]) -> "Frame":
        started = time.monotonic()
        last_frame = self.screenshot.capture()
        while True:
            self._handle_pause_in_wait_loop(last_frame)
            stable_frame = self._stable_capture_or_teach("unknown_stable_prompt", templates)
            templates = self._reload_templates()
            self.safety.ensure_safe_frame(stable_frame, self.vision, templates)
            auto_matches = self.vision.find_category(stable_frame, templates, "auto_battle")
            room_name = self._classify_room_or_passage(stable_frame, templates)
            if room_name in {"room_screen", "event_screen", "passage_screen"} and not auto_matches:
                return stable_frame
            if time.monotonic() - started >= self._battle_finish_timeout_s:
                return stable_frame
            last_frame = stable_frame
            time.sleep(max(0.05, self._stable_poll_ms / 1000.0))

    def _detect_map_objects(self, frame: "Frame", templates: list["TemplateRecord"]) -> dict[str, Any]:
        party_matches = self.vision.find_category(frame, templates, "party_marker")
        door_matches = self.vision.find_category(frame, templates, "door_marker")
        open_room_matches = self.vision.find_category(frame, templates, "room_open_big_icon")
        next_floor_matches = self.vision.find_category(frame, templates, "next_floor")
        return {
            "party_match": party_matches[0] if party_matches else None,
            "door_matches": door_matches,
            "open_room_matches": open_room_matches,
            "next_floor_match": next_floor_matches[0] if next_floor_matches else None,
        }

    def _classify_room_or_passage(self, frame: "Frame", templates: list["TemplateRecord"]) -> str | None:
        direct = self.vision.classify_screen(frame, templates)
        if direct in {"room_screen", "passage_screen", "event_screen"}:
            return direct
        return None

    def _stable_capture_or_teach(self, reason_key: str, templates: list["TemplateRecord"]) -> "Frame":
        last_frame = None
        for _ in range(self._max_stable_retries + 1):
            self._handle_pause_on_step()
            frame, stable = self.vision.wait_until_stable(
                self.screenshot.capture,
                stable_frames=self._stable_frames,
                stable_diff_threshold=self._stable_diff_threshold,
                stable_poll_ms=self._stable_poll_ms,
                stable_timeout_s=self._stable_timeout_s,
                downscale_size=self._downscale_size,
            )
            last_frame = frame
            if stable:
                if self.vision.classify_screen(frame, templates) is None and not self.vision.has_any(frame, templates, self._categories_for_presence_check()):
                    self.teacher_ui.notify(str(self._teacher_cfg.get(reason_key, "Стабильный экран не распознан. Разметьте объект(ы).")))
                    self._pause_and_label(frame)
                    continue
                return frame
        if last_frame is None:
            raise FloorError("vision.wait_until_stable не вернул кадр")
        self.teacher_ui.notify(str(self._teacher_cfg.get("unknown_unstable_prompt", "Экран не стабилизировался. Если это безопасно, разметьте вручную.")))
        self._pause_and_label(last_frame)
        return last_frame

    def _wait_after_motion(self, reason: str) -> "Frame":
        del reason
        return self._stable_capture_or_teach("unknown_unstable_prompt", self._reload_templates())

    def _pause_and_label(self, frame: "Frame") -> None:
        self.safety.ensure_action_allowed("teach")
        self.teacher_ui.show_and_label(frame, self.knowledge)

    def _handle_pause_on_step(self) -> None:
        if self.teacher_ui.pause_requested():
            frame = self.screenshot.capture()
            self._pause_and_label(frame)

    def _handle_pause_in_wait_loop(self, frame: "Frame") -> None:
        if self.teacher_ui.pause_requested():
            self._pause_and_label(frame)

    def _mark_open_big_icons(self, state: dict, matches: list["Match"]) -> dict:
        for match in matches:
            door = self._closest_door_by_rect(state, match.rect)
            if door is not None:
                try:
                    state = self.floor_state.mark_open_room_icon(state, door.door_id, present=True)
                except DoorNotFoundError:
                    pass
        return state

    def _closest_door_by_rect(self, state: dict, rect: tuple[int, int, int, int]):
        best = None
        rx = (rect[0] + rect[2]) / 2.0
        ry = (rect[1] + rect[3]) / 2.0
        for node in state.get("nodes", []):
            for door in node.get("doors", []):
                mx1, my1, mx2, my2 = door.get("marker_rect", [0, 0, 0, 0])
                dx = ((mx1 + mx2) / 2.0) - rx
                dy = ((my1 + my2) / 2.0) - ry
                dist = dx * dx + dy * dy
                if best is None or dist < best[0]:
                    best = (dist, door.get("door_id"))
        return self._find_door_record_by_id(state, str(best[1])) if best is not None else None

    def _find_door_record_by_id(self, state: dict, door_id: str):
        for node in state.get("nodes", []):
            for door in node.get("doors", []):
                if door.get("door_id") == door_id:
                    from .floor_state import DoorRecord
                    return DoorRecord(
                        door_id=str(door["door_id"]),
                        marker_rect=(int(door["marker_rect"][0]), int(door["marker_rect"][1]), int(door["marker_rect"][2]), int(door["marker_rect"][3])),
                        status=str(door["status"]),
                        target_node_id=str(door["target_node_id"]) if door.get("target_node_id") is not None else None,
                        discovered_at=str(door["discovered_at"]),
                        updated_at=str(door["updated_at"]),
                        notes=str(door.get("notes", "")),
                    )
        return None

    def _tap_match(self, match: "Match", frame: "Frame") -> None:
        self._tap_rect_center(match.rect, frame)

    def _tap_rect_center(self, rect: tuple[int, int, int, int], frame: "Frame") -> None:
        if self._teach_only:
            return
        h, w = frame.shape[:2]
        x = int((rect[0] + rect[2]) / 2)
        y = int((rect[1] + rect[3]) / 2)
        if self._clamp_to_screen:
            x = min(max(0, x), max(0, w - 1))
            y = min(max(0, y), max(0, h - 1))
        self.adb.tap(x, y)

    def _apply_safety_blocks(self) -> None:
        self.safety.ensure_action_allowed("explore")
        for blocked_action in ("shop", "ads", "pvp"):
            if self.safety.is_action_allowed(blocked_action):
                raise SafetyError(f"Действие {blocked_action} разрешено, что противоречит требованию задачи")

    def _reload_templates(self) -> list["TemplateRecord"]:
        try:
            return self.knowledge.get_templates(enabled_only=True)
        except TypeError:
            return self.knowledge.get_templates()

    def _load_or_reset_state(self, floor_number: int) -> dict:
        try:
            state = self.floor_state.load()
            if int(state.get("floor_number", 0)) != int(floor_number):
                state = self.floor_state.reset_for_floor(floor_number)
            return state
        except FileNotFoundError:
            return self.floor_state.reset_for_floor(floor_number)

    def _result_from_state(self, state: dict, status: str, reason: str) -> FloorResult:
        stats = state.get("stats", {})
        return FloorResult(
            status=status,
            rooms_cleared=int(stats.get("rooms_cleared", 0)),
            rooms_skipped=int(stats.get("rooms_skipped", 0)),
            rooms_failed=int(stats.get("rooms_failed", 0)),
            reason=reason,
        )

    def _categories_for_presence_check(self) -> list[str]:
        categories = list(self.knowledge.categories())
        return [category for category in categories if category != "danger"]

    def _section(self, name: str) -> dict[str, Any]:
        raw = getattr(self.config, "raw", {})
        section = raw.get(name, {}) if isinstance(raw, dict) else {}
        return section if isinstance(section, dict) else {}
