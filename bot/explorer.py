from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .floor import FloorError, FloorExplorer, FloorResult
from .profiles import ProfileManager, ProfileError
from .safety import DangerDetectedError, SafetyError
from .teacher_ui import LabelingCancelledError, TeacherUIError

if TYPE_CHECKING:
    from .adb_controller import ADBController
    from .config import BotConfig
    from .knowledge import KnowledgeBase
    from .safety import SafetyManager
    from .screenshot import Frame, ScreenshotProvider
    from .teacher_ui import TeacherUI
    from .vision import VisionEngine


class ExplorerError(Exception):
    pass


@dataclass(frozen=True)
class _RunContext:
    started_at: str
    floor_from: int
    floor_to: int


class DungeonExplorer:
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
        logger: "logging.Logger",
    ) -> None:
        self.adb = adb
        self.screenshot = screenshot
        self.vision = vision
        self.knowledge = knowledge
        self.safety = safety
        self.teacher_ui = teacher_ui
        self.config = config
        self.profile = profile
        self.logger = logger

        self.profile_manager = ProfileManager(config, logger)
        self.profile_manager.ensure_profile(profile)

        self._motion_cfg = self._section("motion")
        self._teacher_cfg = self._section("teacher")
        self._safety_cfg = self._section("safety")
        self._game_cfg = self._section("game")
        self._debug_cfg = self._section("debug")

        self._stable_frames = int(self._motion_cfg.get("stable_frames", 3))
        self._stable_diff_threshold = float(self._motion_cfg.get("stable_diff_threshold", 0.015))
        self._stable_poll_ms = int(self._motion_cfg.get("stable_poll_ms", 350))
        self._stable_timeout_s = float(self._motion_cfg.get("stable_timeout_s", 8.0))
        self._max_stable_retries = int(self._motion_cfg.get("max_stable_retries", 2))
        raw_downscale = self._motion_cfg.get("downscale_size", [64, 36])
        self._downscale_size = (int(raw_downscale[0]), int(raw_downscale[1]))

        self._max_unknown_stable_screens = int(self._safety_cfg.get("max_unknown_stable_screens", 3))
        self._save_last_frame_on_error = bool(self._debug_cfg.get("save_last_frame_on_error", True))
        self._floors_per_level = int(self._game_cfg.get("floors_per_level", 7))

        self._max_total_runtime_s = float(
            self._section("explorer", required=False).get("max_total_runtime_s", 0.0) or 0.0
        )
        self._max_same_stable_screen_steps = int(
            self._section("explorer", required=False).get("max_same_stable_screen_steps", 6) or 6
        )
        self._max_floor_retries = int(
            self._section("explorer", required=False).get("max_floor_retries", 0) or 0
        )

        self._last_frame: Frame | None = None
        self._last_stable_signature: str | None = None
        self._same_stable_signature_steps = 0
        self._unknown_stable_count = 0
        self._run_context: _RunContext | None = None

    def run(
        self,
        floor_from: int = 1,
        floor_to: int | None = None,
    ) -> list["FloorResult"]:
        """
        Последовательно запускает исследование этажей.

        На каждом шаге и в циклах ожидания обязан проверять teacher_ui.pause_requested().

        :param floor_from: первый этаж
        :param floor_to: последний этаж включительно; если None, использовать game.floors_per_level
        :return: список FloorResult
        :raises ExplorerError: при фатальной ошибке оркестрации
        """
        self.safety.ensure_action_allowed("explore")

        final_floor = self._floors_per_level if floor_to is None else int(floor_to)
        start_floor = int(floor_from)
        if start_floor < 1:
            raise ExplorerError("floor_from must be >= 1")
        if final_floor < start_floor:
            raise ExplorerError("floor_to must be >= floor_from")

        self.profile_manager.ensure_profile(self.profile)
        self._run_context = _RunContext(
            started_at=_utc_now(),
            floor_from=start_floor,
            floor_to=final_floor,
        )
        self._mark_run_started(start_floor)

        results: list[FloorResult] = []
        last_reason = "completed"
        last_status = "completed"

        try:
            for floor_number in range(start_floor, final_floor + 1):
                self._guard_runtime_limit(floor_number)
                self._pause_checkpoint(note=f"Перед запуском этажа {floor_number}")

                precheck_frame = self._capture_stable_frame_for_pause(note=f"Перед этажом {floor_number}")
                self._last_frame = precheck_frame
                self._check_stall_on_stable_frame(precheck_frame, floor_number)
                self._ensure_safe_known_frame(precheck_frame)

                floor_result = self._run_single_floor_with_guard(floor_number)
                results.append(floor_result)
                self.update_progress(floor_number, floor_result)

                last_reason = floor_result.reason
                last_status = floor_result.status

                if floor_result.status in {"failed", "stopped"}:
                    break
                if floor_result.reason and "danger" in floor_result.reason.lower():
                    break

            self._mark_run_finished(last_status, last_reason, results)
            return results

        except DangerDetectedError as exc:
            self._save_error_screenshot("danger")
            self._mark_run_finished("stopped", str(exc), results)
            raise
        except (TeacherUIError, LabelingCancelledError) as exc:
            self._save_error_screenshot("teacher_error")
            self._mark_run_finished("stopped", str(exc), results)
            raise ExplorerError(f"Ошибка обучения/разметки: {exc}") from exc
        except (ProfileError, SafetyError, FloorError) as exc:
            self._save_error_screenshot("explorer_error")
            self._mark_run_finished("failed", str(exc), results)
            raise ExplorerError(str(exc)) from exc
        except Exception as exc:
            self._save_error_screenshot("unexpected")
            self._mark_run_finished("failed", str(exc), results)
            raise ExplorerError(f"Непредвиденная ошибка оркестрации: {exc}") from exc

    def update_progress(
        self,
        floor_number: int,
        result: "FloorResult",
    ) -> None:
        """
        Обновляет progress.json после завершения этажа.

        :raises OSError, ValueError: если не удалось записать прогресс
        """
        progress = self._load_progress_dict()
        totals = progress.setdefault(
            "totals",
            {
                "floors_completed": 0,
                "rooms_cleared": 0,
                "rooms_skipped": 0,
                "rooms_failed": 0,
            },
        )
        history = progress.setdefault("history", [])
        last_run = progress.setdefault(
            "last_run",
            {"started_at": "", "finished_at": "", "status": "", "reason": ""},
        )

        progress["version"] = int(progress.get("version", 1) or 1)
        progress["profile"] = self.profile
        progress["updated_at"] = _utc_now()
        progress["current_floor"] = int(floor_number)

        if result.status == "completed":
            totals["floors_completed"] = int(totals.get("floors_completed", 0)) + 1

        totals["rooms_cleared"] = int(totals.get("rooms_cleared", 0)) + int(result.rooms_cleared)
        totals["rooms_skipped"] = int(totals.get("rooms_skipped", 0)) + int(result.rooms_skipped)
        totals["rooms_failed"] = int(totals.get("rooms_failed", 0)) + int(result.rooms_failed)

        history.append(
            {
                "location": int(progress.get("current_location", 1) or 1),
                "level": int(progress.get("current_level", 1) or 1),
                "floor": int(floor_number),
                "status": result.status,
                "rooms_cleared": int(result.rooms_cleared),
                "rooms_skipped": int(result.rooms_skipped),
                "rooms_failed": int(result.rooms_failed),
                "timestamp": _utc_now(),
                "reason": result.reason,
            }
        )

        last_run["started_at"] = self._run_context.started_at if self._run_context is not None else ""
        last_run["finished_at"] = _utc_now()
        last_run["status"] = result.status
        last_run["reason"] = result.reason

        self._save_progress_dict(progress)

    def run_explore(
        self,
        floor_from: int = 1,
        floor_to: int | None = None,
    ) -> list[FloorResult]:
        return self.run(floor_from=floor_from, floor_to=floor_to)

    def run_teach_only(self) -> int:
        self.safety.ensure_action_allowed("teach")
        return int(self.teacher_ui.teach_only(self.adb, self.screenshot, self.knowledge))

    def _run_single_floor_with_guard(self, floor_number: int) -> FloorResult:
        attempts_left = self._max_floor_retries + 1
        last_error: Exception | None = None

        while attempts_left > 0:
            self._pause_checkpoint(note=f"Старт этажа {floor_number}")
            try:
                floor_explorer = FloorExplorer(
                    self.adb,
                    self.screenshot,
                    self.vision,
                    self.knowledge,
                    self.safety,
                    self.teacher_ui,
                    self.config,
                    self.profile,
                )
                result = floor_explorer.run(floor_number)
                self._last_frame = self._safe_capture_last_frame()
                return result
            except DangerDetectedError:
                raise
            except Exception as exc:
                last_error = exc
                self._last_frame = self._safe_capture_last_frame()
                if attempts_left <= 1:
                    raise
                attempts_left -= 1
                self.logger.warning(
                    "Повтор этажа %s после ошибки: %s. Осталось повторов: %s",
                    floor_number,
                    exc,
                    attempts_left,
                )
                self._pause_checkpoint(note=f"После ошибки этажа {floor_number}")
                continue

        if last_error is not None:
            raise last_error
        raise ExplorerError(f"Не удалось запустить этаж {floor_number}")

    def _pause_checkpoint(self, note: str = "") -> None:
        if not self.teacher_ui.pause_requested():
            return
        frame = self._capture_stable_frame_for_pause(note=note)
        self._last_frame = frame
        self.teacher_ui.notify(note or "Пауза")
        self.teacher_ui.show_and_label(frame, self.knowledge)

    def _capture_stable_frame_for_pause(self, note: str = "") -> Frame:
        frame: Frame | None = None
        stable = False

        for _ in range(self._max_stable_retries + 1):
            self._check_pause_during_wait(note=note)
            frame, stable = self.vision.wait_until_stable(
                self.screenshot.capture,
                stable_frames=self._stable_frames,
                stable_diff_threshold=self._stable_diff_threshold,
                stable_poll_ms=self._stable_poll_ms,
                stable_timeout_s=self._stable_timeout_s,
                downscale_size=self._downscale_size,
            )
            self._last_frame = frame
            if stable:
                return frame

        if frame is None:
            frame = self.screenshot.capture()
            self._last_frame = frame

        self.teacher_ui.notify(
            str(self._teacher_cfg.get("unknown_unstable_prompt", "Экран не стабилизировался"))
            + (f" [{note}]" if note else "")
        )
        return frame

    def _check_pause_during_wait(self, note: str = "") -> None:
        if not self.teacher_ui.pause_requested():
            return
        frame = self._safe_capture_last_frame() or self.screenshot.capture()
        self._last_frame = frame
        self.teacher_ui.notify(note or "Пауза во время ожидания")
        self.teacher_ui.show_and_label(frame, self.knowledge)

    def _ensure_safe_known_frame(self, frame: Frame) -> None:
        templates = self._reload_templates_merged()
        self.safety.ensure_safe_frame(frame, self.vision, templates)

        screen_name = self.vision.classify_screen(frame, templates)
        has_any = False
        if screen_name is not None:
            has_any = True
        else:
            categories = self._allowed_categories_from_knowledge()
            if categories:
                has_any = self.vision.has_any(frame, templates, categories)

        if has_any:
            self._unknown_stable_count = 0
            return

        self._unknown_stable_count += 1
        if self._unknown_stable_count > self._max_unknown_stable_screens:
            raise ExplorerError("Слишком много подряд неизвестных стабильных экранов")

        self.teacher_ui.notify(str(self._teacher_cfg.get("unknown_stable_prompt", "Стабильный экран не распознан")))
        self.teacher_ui.show_and_label(frame, self.knowledge)

    def _reload_templates_merged(self) -> list[Any]:
        profile_templates = list(self.knowledge.get_templates(enabled_only=True))
        common_templates = self._load_common_templates_best_effort()
        merged: list[Any] = []
        seen: set[str] = set()
        for item in profile_templates + common_templates:
            template_id = str(getattr(item, "id", ""))
            if template_id and template_id in seen:
                continue
            if template_id:
                seen.add(template_id)
            merged.append(item)
        return merged

    def _load_common_templates_best_effort(self) -> list[Any]:
        features_name = str(self.config.raw.get("app", {}).get("features_file", "features.json"))
        root_dir = Path(str(self.config.raw.get("profiles", {}).get("root_dir", "./data/profiles")))
        if not root_dir.is_absolute():
            root_dir = self.config.base_dir / root_dir
        common_file = (root_dir / "common" / features_name).resolve()
        if not common_file.exists():
            return []
        try:
            with common_file.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return []
        templates_raw = data.get("templates", [])
        if not isinstance(templates_raw, list):
            return []
        return [self._dict_to_template_record(item) for item in templates_raw if isinstance(item, dict)]

    def _dict_to_template_record(self, item: dict[str, Any]) -> Any:
        from .knowledge import TemplateRecord

        return TemplateRecord(
            id=str(item["id"]),
            category=str(item["category"]),
            file=str(item["file"]),
            width=int(item["width"]),
            height=int(item["height"]),
            source_screen_size=(
                int(item.get("source_screen_size", [0, 0])[0]),
                int(item.get("source_screen_size", [0, 0])[1]),
            ),
            selected_rect=tuple(int(v) for v in item.get("selected_rect", [0, 0, 0, 0])),
            roi_hint=tuple(int(v) for v in item.get("roi_hint", [0, 0, 0, 0])),
            created_at=str(item.get("created_at", "")),
            enabled=bool(item.get("enabled", True)),
            tags=tuple(str(v) for v in item.get("tags", [])),
        )

    def _allowed_categories_from_knowledge(self) -> list[str]:
        try:
            return list(self.knowledge.categories())
        except Exception:
            return [
                "door_marker",
                "party_marker",
                "room_open_big_icon",
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
            ]

    def _check_stall_on_stable_frame(self, frame: Frame, floor_number: int) -> None:
        signature = self._frame_signature(frame)
        if signature == self._last_stable_signature:
            self._same_stable_signature_steps += 1
        else:
            self._last_stable_signature = signature
            self._same_stable_signature_steps = 1

        if self._same_stable_signature_steps >= self._max_same_stable_screen_steps:
            raise ExplorerError(
                f"Обнаружено зависание: один и тот же стабильный экран на этаже {floor_number} "
                f"повторился {self._same_stable_signature_steps} шагов подряд"
            )

    def _frame_signature(self, frame: Frame) -> str:
        import cv2
        import numpy as np

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        small = cv2.resize(gray, self._downscale_size, interpolation=cv2.INTER_AREA)
        return str(hash(np.ascontiguousarray(small).tobytes()))

    def _guard_runtime_limit(self, floor_number: int) -> None:
        if self._max_total_runtime_s <= 0 or self._run_context is None:
            return
        started = _parse_utc(self._run_context.started_at)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        if elapsed > self._max_total_runtime_s:
            raise ExplorerError(
                f"Превышен общий лимит времени запуска перед этажом {floor_number}: {elapsed:.1f}s"
            )

    def _mark_run_started(self, floor_from: int) -> None:
        progress = self._load_progress_dict()
        progress.setdefault("version", 1)
        progress["profile"] = self.profile
        progress["updated_at"] = _utc_now()
        progress["current_floor"] = int(floor_from)
        progress.setdefault("current_location", 1)
        progress.setdefault("current_level", 1)
        progress.setdefault(
            "totals",
            {
                "floors_completed": 0,
                "rooms_cleared": 0,
                "rooms_skipped": 0,
                "rooms_failed": 0,
            },
        )
        progress.setdefault("history", [])
        progress["last_run"] = {
            "started_at": self._run_context.started_at if self._run_context is not None else _utc_now(),
            "finished_at": "",
            "status": "running",
            "reason": "",
        }
        self._save_progress_dict(progress)

    def _mark_run_finished(self, status: str, reason: str, results: list[FloorResult]) -> None:
        progress = self._load_progress_dict()
        last_run = progress.setdefault("last_run", {})
        last_run["started_at"] = self._run_context.started_at if self._run_context is not None else ""
        last_run["finished_at"] = _utc_now()
        last_run["status"] = str(status)
        last_run["reason"] = str(reason)
        progress["updated_at"] = _utc_now()
        if results:
            progress["current_floor"] = int(len(results) + (self._run_context.floor_from - 1 if self._run_context else 0))
        self._save_progress_dict(progress)

    def _load_progress_dict(self) -> dict[str, Any]:
        try:
            return self.profile_manager.load_progress(self.profile)
        except FileNotFoundError:
            self.profile_manager.ensure_profile(self.profile)
            return self.profile_manager.load_progress(self.profile)

    def _save_progress_dict(self, data: dict[str, Any]) -> None:
        self.profile_manager.save_progress(self.profile, data)

    def _save_error_screenshot(self, prefix: str) -> None:
        if not self._save_last_frame_on_error:
            return
        try:
            frame = self._last_frame if self._last_frame is not None else self.screenshot.capture()
            self._last_frame = frame
            if hasattr(self.screenshot, "save_emergency_screenshot"):
                self.screenshot.save_emergency_screenshot(frame=frame, prefix=prefix)
                return
            debug_dir = Path(str(self.config.raw.get("vision", {}).get("debug_dir", "./debug")))
            if not debug_dir.is_absolute():
                debug_dir = self.config.base_dir / debug_dir
            debug_dir.mkdir(parents=True, exist_ok=True)
            path = debug_dir / f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.png"
            self.screenshot.save(frame, str(path))
        except Exception as exc:
            self.logger.warning("Не удалось сохранить аварийный скриншот: %s", exc)

    def _safe_capture_last_frame(self) -> Frame | None:
        try:
            frame = self.screenshot.capture()
            self._last_frame = frame
            return frame
        except Exception:
            return self._last_frame

    def _section(self, name: str, required: bool = True) -> dict[str, Any]:
        raw = getattr(self.config, "raw", {})
        section = raw.get(name)
        if section is None:
            if required:
                return {}
            return {}
        if not isinstance(section, dict):
            return {}
        return section


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)
