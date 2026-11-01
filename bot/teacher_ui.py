from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


class TeacherUIError(RuntimeError):
    pass


@dataclass
class _Annotation:
    kind: str
    category: str
    bbox: Optional[Tuple[int, int, int, int]] = None
    point: Optional[Tuple[int, int]] = None
    template_id: Optional[str] = None


class TeacherUI:
    _WINDOW_NAME = "Teacher UI"
    _POLL_MS = 30
    _BBOX_COLOR = (0, 255, 0)
    _POINT_COLOR = (0, 255, 255)
    _TEXT_COLOR = (255, 255, 255)
    _TEXT_BG = (0, 0, 0)
    _PANEL_BG = (32, 32, 32)
    _CATEGORY_KEYS: Dict[int, Tuple[str, str]] = {
        ord("d"): ("door_marker", "дверь"),
        ord("m"): ("mob", "моб"),
        ord("g"): ("boss", "босс"),
        ord("l"): ("loot", "лут"),
        ord("e"): ("event_screen", "событие"),
        ord("x"): ("exit", "выход"),
        ord("a"): ("auto_battle", "авто-бой"),
        ord("f"): ("next_floor", "следующий этаж"),
        ord("p"): ("party_marker", "маркер отряда"),
        ord("z"): ("danger", "опасное"),
        ord("o"): ("room_open_big_icon", "большая иконка открытой комнаты"),
        ord("r"): ("room_screen", "экран комнаты"),
        ord("t"): ("passage_screen", "экран пути дальше"),
    }
    _POINT_KEY_TO_ACTION: Dict[int, Tuple[str, str]] = {
        ord("d"): ("door_marker", "точка двери"),
        ord("m"): ("mob", "точка моба"),
        ord("g"): ("boss", "точка босса"),
        ord("l"): ("loot", "точка лута"),
        ord("e"): ("event_screen", "точка события"),
        ord("x"): ("exit", "точка выхода"),
        ord("a"): ("auto_battle", "точка авто-боя"),
        ord("f"): ("next_floor", "точка следующего этажа"),
        ord("p"): ("party_marker", "точка маркера отряда"),
        ord("z"): ("danger", "точка опасного"),
        ord("o"): ("room_open_big_icon", "точка открытой комнаты"),
        ord("r"): ("room_screen", "точка экрана комнаты"),
        ord("t"): ("passage_screen", "точка экрана пути дальше"),
    }

    def __init__(
        self,
        pause_hotkey: str = "space",
    ) -> None:
        self.pause_hotkey = pause_hotkey
        self._pause_latched = False
        self._window_created = False
        self._dragging = False
        self._drag_start: Optional[Tuple[int, int]] = None
        self._drag_current: Optional[Tuple[int, int]] = None
        self._pending_bbox: Optional[Tuple[int, int, int, int]] = None
        self._pending_point: Optional[Tuple[int, int]] = None
        self._annotations: List[_Annotation] = []
        self._last_frame_shape: Optional[Tuple[int, int, int]] = None
        self._status_text = "Пробел — пауза/продолжить"
        self._saved_count = 0
        self._profile_name = "common"
        self._default_threshold = 0.90

    def pause_requested(self) -> bool:
        key = cv2.waitKey(1) & 0xFF
        if key == 255:
            return False
        if key == ord(" "):
            self._pause_latched = True
            return True
        if key == ord("q"):
            self._pause_latched = True
            return True
        return False

    def show_and_label(
        self,
        frame,
        knowledge: "KnowledgeBase",
    ) -> None:
        if frame is None or not isinstance(frame, np.ndarray):
            raise TeacherUIError("frame must be a numpy.ndarray")
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise TeacherUIError("frame must be a BGR image with shape (H, W, 3)")

        self._profile_name = self._detect_profile_name(knowledge)
        self._default_threshold = self._detect_default_threshold(knowledge)
        self._saved_count = 0
        self._annotations = []
        self._pending_bbox = None
        self._pending_point = None
        self._dragging = False
        self._drag_start = None
        self._drag_current = None
        self._last_frame_shape = frame.shape
        self._status_text = "Пауза: выделите рамку или кликните точку. Enter — сохранить, Space — продолжить"

        self._ensure_window()
        cv2.setMouseCallback(self._WINDOW_NAME, self._on_mouse)

        while True:
            display = self._build_display_frame(frame)
            cv2.imshow(self._WINDOW_NAME, display)
            key = cv2.waitKey(self._POLL_MS)
            action = self._handle_label_key(key, frame, knowledge)

            if action == "save":
                try:
                    knowledge.save()
                except Exception as exc:
                    raise TeacherUIError(f"Не удалось сохранить разметку: {exc}") from exc
                self._status_text = f"Сохранено: {self._saved_count}. Пробел — продолжить"
            elif action == "continue":
                self._clear_pending_selection()
                self._pause_latched = False
                break
            elif action == "quit":
                self.close()
                raise TeacherUIError("Пользователь завершил работу")

        self._safe_destroy_window()

    def teach_only(
        self,
        adb: "ADBController",
        screenshot: "ScreenshotService",
        knowledge: "KnowledgeBase",
    ) -> None:
        self._ensure_window()
        latest_frame: Optional[np.ndarray] = None
        last_capture_error: Optional[str] = None

        try:
            while True:
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord(" "):
                    if latest_frame is None:
                        self._status_text = "Нет кадра для разметки"
                    else:
                        self._pause_latched = True
                        self.show_and_label(latest_frame, knowledge)
                        self._status_text = "Режим teach-only: играйте сами, Пробел — разметка, q — выход"
                        self._ensure_window()

                try:
                    latest_frame = screenshot.capture(adb)
                    last_capture_error = None
                except Exception as exc:
                    last_capture_error = str(exc)
                    canvas = np.zeros((220, 900, 3), dtype=np.uint8)
                    self._draw_multiline_text(
                        canvas,
                        [
                            "Режим teach-only",
                            f"Ошибка скриншота: {last_capture_error}",
                            "Пробел — попробовать разметить последний удачный кадр",
                            "q — выход",
                        ],
                        10,
                        30,
                        24,
                    )
                    cv2.imshow(self._WINDOW_NAME, canvas)
                    time.sleep(0.1)
                    continue

                preview = latest_frame.copy()
                self._draw_overlay_banner(
                    preview,
                    [
                        "Режим teach-only: бот ничего не нажимает",
                        "Пробел — пауза и разметка, q — выход",
                    ],
                )
                cv2.imshow(self._WINDOW_NAME, preview)
                time.sleep(0.05)
        finally:
            self.close()

    def close(self) -> None:
        self._safe_destroy_window()

    def _handle_label_key(self, key: int, frame: np.ndarray, knowledge: "KnowledgeBase") -> Optional[str]:
        if key < 0:
            return None
        code = key & 0xFF

        if code == 13:
            return "save"
        if code == ord(" "):
            return "continue"
        if code == ord("q"):
            return "quit"
        if code in (8, 127):
            self._undo_last(knowledge)
            return None
        if code in self._CATEGORY_KEYS:
            if self._pending_bbox is not None:
                self._save_bbox_annotation(code, frame, knowledge)
            elif self._pending_point is not None:
                self._save_point_annotation(code, knowledge)
            else:
                self._status_text = "Сначала выделите рамку или кликните точку"
            return None
        return None

    def _save_bbox_annotation(self, key_code: int, frame: np.ndarray, knowledge: "KnowledgeBase") -> None:
        if self._pending_bbox is None:
            self._status_text = "Нет выделенной рамки"
            return
        category, label = self._CATEGORY_KEYS[key_code]
        h, w = frame.shape[:2]
        roi = (0, 0, int(w), int(h))
        notes = f"teacher_ui:{label}"
        try:
            template_id = knowledge.add_template(
                frame=frame,
                bbox=self._pending_bbox,
                category=category,
                profile=self._profile_name,
                roi=roi,
                threshold=self._default_threshold,
                notes=notes,
                source_frame_path=None,
            )
        except Exception as exc:
            raise TeacherUIError(f"Не удалось сохранить шаблон {label}: {exc}") from exc
        self._annotations.append(
            _Annotation(
                kind="bbox",
                category=category,
                bbox=self._pending_bbox,
                template_id=template_id,
            )
        )
        self._saved_count += 1
        self._status_text = f"Добавлен шаблон: {label} ({template_id})"
        self._pending_bbox = None
        self._pending_point = None

    def _save_point_annotation(self, key_code: int, knowledge: "KnowledgeBase") -> None:
        if self._pending_point is None:
            self._status_text = "Нет точки"
            return
        action_name, label = self._POINT_KEY_TO_ACTION[key_code]
        if not hasattr(knowledge, "add_click_point"):
            self._status_text = "knowledge не поддерживает add_click_point, точка не сохранена"
            self._pending_point = None
            return
        try:
            knowledge.add_click_point(
                action_name=action_name,
                point=self._pending_point,
                profile=self._profile_name,
                notes=f"teacher_ui:{label}",
            )
        except Exception as exc:
            raise TeacherUIError(f"Не удалось сохранить точку {label}: {exc}") from exc
        self._annotations.append(
            _Annotation(
                kind="point",
                category=action_name,
                point=self._pending_point,
            )
        )
        self._saved_count += 1
        self._status_text = f"Добавлена точка: {label} {self._pending_point}"
        self._pending_point = None
        self._pending_bbox = None

    def _undo_last(self, knowledge: "KnowledgeBase") -> None:
        if self._pending_bbox is not None or self._pending_point is not None:
            self._clear_pending_selection()
            self._status_text = "Отменено текущее выделение"
            return
        if not self._annotations:
            self._status_text = "Отменять нечего"
            return
        undone = False
        if hasattr(knowledge, "undo_last_annotation"):
            try:
                undone = bool(knowledge.undo_last_annotation())
            except Exception as exc:
                raise TeacherUIError(f"Не удалось отменить последнюю разметку: {exc}") from exc
        last = self._annotations.pop()
        if self._saved_count > 0:
            self._saved_count -= 1
        self._status_text = (
            f"Отменена разметка: {last.category}" if undone or last is not None else "Отмена не выполнена"
        )

    def _build_display_frame(self, frame: np.ndarray) -> np.ndarray:
        canvas = frame.copy()
        for item in self._annotations:
            if item.kind == "bbox" and item.bbox is not None:
                x, y, w, h = item.bbox
                cv2.rectangle(canvas, (x, y), (x + w, y + h), (255, 128, 0), 2)
                self._put_label(canvas, item.category, x, y)
            elif item.kind == "point" and item.point is not None:
                x, y = item.point
                cv2.circle(canvas, (x, y), 6, (255, 128, 0), -1)
                self._put_label(canvas, item.category, x, y)

        if self._dragging and self._drag_start is not None and self._drag_current is not None:
            bbox = self._normalized_bbox(self._drag_start, self._drag_current)
            if bbox is not None:
                x, y, w, h = bbox
                cv2.rectangle(canvas, (x, y), (x + w, y + h), self._BBOX_COLOR, 2)
        elif self._pending_bbox is not None:
            x, y, w, h = self._pending_bbox
            cv2.rectangle(canvas, (x, y), (x + w, y + h), self._BBOX_COLOR, 2)
            self._put_label(canvas, "ожидает клавишу типа", x, y)

        if self._pending_point is not None:
            x, y = self._pending_point
            cv2.circle(canvas, (x, y), 6, self._POINT_COLOR, -1)
            self._put_label(canvas, "точка: нажмите клавишу типа", x, y)

        self._draw_overlay_banner(
            canvas,
            [
                self._status_text,
                "Рамка мышью + клавиша типа | клик без рамки — точка",
                "Backspace — отмена, Enter — сохранить, Space — продолжить, q — выход",
                "d дверь, m моб, g босс, l лут, e событие, x выход, a авто-бой, f следующий этаж",
                "p маркер отряда, z опасное, o открытая комната, r экран комнаты, t экран пути дальше",
            ],
        )
        return canvas

    def _on_mouse(self, event: int, x: int, y: int, flags: int, param) -> None:
        _ = flags
        _ = param
        if self._last_frame_shape is None:
            return
        h, w = self._last_frame_shape[:2]
        x = max(0, min(int(x), w - 1))
        y = max(0, min(int(y), h - 1))

        if event == cv2.EVENT_LBUTTONDOWN:
            self._dragging = True
            self._drag_start = (x, y)
            self._drag_current = (x, y)
            self._pending_bbox = None
            self._pending_point = None
            self._status_text = "Тяните рамку или отпустите без движения для точки"
        elif event == cv2.EVENT_MOUSEMOVE and self._dragging:
            self._drag_current = (x, y)
        elif event == cv2.EVENT_LBUTTONUP and self._dragging:
            self._dragging = False
            self._drag_current = (x, y)
            if self._drag_start is None:
                self._status_text = "Ошибка выделения"
                return
            bbox = self._normalized_bbox(self._drag_start, self._drag_current)
            if bbox is None or (bbox[2] <= 3 and bbox[3] <= 3):
                self._pending_bbox = None
                self._pending_point = (x, y)
                self._status_text = "Точка выбрана. Нажмите клавишу типа"
            else:
                self._pending_point = None
                self._pending_bbox = bbox
                self._status_text = "Рамка выбрана. Нажмите клавишу типа"
            self._drag_start = None
            self._drag_current = None

    def _normalized_bbox(
        self,
        start: Tuple[int, int],
        end: Tuple[int, int],
    ) -> Optional[Tuple[int, int, int, int]]:
        if self._last_frame_shape is None:
            return None
        h, w = self._last_frame_shape[:2]
        x1, y1 = start
        x2, y2 = end
        left = max(0, min(x1, x2))
        top = max(0, min(y1, y2))
        right = min(w - 1, max(x1, x2))
        bottom = min(h - 1, max(y1, y2))
        width = int(right - left + 1)
        height = int(bottom - top + 1)
        if width <= 0 or height <= 0:
            return None
        return int(left), int(top), width, height

    def _clear_pending_selection(self) -> None:
        self._pending_bbox = None
        self._pending_point = None
        self._dragging = False
        self._drag_start = None
        self._drag_current = None

    def _ensure_window(self) -> None:
        if not self._window_created:
            cv2.namedWindow(self._WINDOW_NAME, cv2.WINDOW_NORMAL)
            self._window_created = True

    def _safe_destroy_window(self) -> None:
        if self._window_created:
            try:
                cv2.destroyWindow(self._WINDOW_NAME)
            except cv2.error:
                pass
            try:
                cv2.waitKey(1)
            except cv2.error:
                pass
            self._window_created = False

    def _draw_overlay_banner(self, frame: np.ndarray, lines: List[str]) -> None:
        line_height = 22
        margin = 8
        panel_height = margin * 2 + line_height * len(lines)
        panel_width = frame.shape[1]
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (panel_width, panel_height), self._PANEL_BG, -1)
        cv2.addWeighted(overlay, 0.65, frame, 0.35, 0.0, frame)
        self._draw_multiline_text(frame, lines, 10, 24, line_height)

    def _draw_multiline_text(
        self,
        frame: np.ndarray,
        lines: List[str],
        x: int,
        y: int,
        line_height: int,
    ) -> None:
        for idx, line in enumerate(lines):
            cv2.putText(
                frame,
                line,
                (x, y + idx * line_height),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                self._TEXT_COLOR,
                1,
                cv2.LINE_AA,
            )

    def _put_label(self, frame: np.ndarray, text: str, x: int, y: int) -> None:
        (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        top_left = (max(0, x), max(0, y - th - baseline - 6))
        bottom_right = (min(frame.shape[1] - 1, top_left[0] + tw + 6), min(frame.shape[0] - 1, top_left[1] + th + baseline + 6))
        cv2.rectangle(frame, top_left, bottom_right, self._TEXT_BG, -1)
        cv2.putText(
            frame,
            text,
            (top_left[0] + 3, bottom_right[1] - baseline - 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            self._TEXT_COLOR,
            1,
            cv2.LINE_AA,
        )

    def _detect_profile_name(self, knowledge: "KnowledgeBase") -> str:
        profile_name = getattr(knowledge, "active_profile_name", None)
        if isinstance(profile_name, str) and profile_name.strip():
            return profile_name.strip()
        return "common"

    def _detect_default_threshold(self, knowledge: "KnowledgeBase") -> float:
        threshold = getattr(knowledge, "default_threshold", None)
        if isinstance(threshold, (float, int)):
            value = float(threshold)
            if 0.0 < value <= 1.0:
                return value
        return 0.90
