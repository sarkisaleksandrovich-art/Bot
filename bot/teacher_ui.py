from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


Frame = np.ndarray


class TeacherUIError(Exception):
    pass


class LabelingCancelledError(TeacherUIError):
    pass


@dataclass(frozen=True)
class _PendingAnnotation:
    rect: tuple[int, int, int, int] | None
    point: tuple[int, int] | None


class TeacherUI:
    _CATEGORY_KEYS: dict[int, str] = {
        ord("d"): "door_marker",
        ord("m"): "mob",
        ord("g"): "boss",
        ord("l"): "loot",
        ord("e"): "event_screen",
        ord("x"): "exit",
        ord("a"): "auto_battle",
        ord("f"): "next_floor",
        ord("p"): "party_marker",
        ord("z"): "danger",
        ord("o"): "room_open_big_icon",
        ord("r"): "room_screen",
        ord("t"): "passage_screen",
    }

    _KEY_NAMES: dict[int, str] = {
        ord("d"): "d дверь",
        ord("m"): "m моб",
        ord("g"): "g босс",
        ord("l"): "l лут",
        ord("e"): "e событие",
        ord("x"): "x выход",
        ord("a"): "a авто-бой",
        ord("f"): "f следующий этаж",
        ord("p"): "p маркер отряда",
        ord("z"): "z опасное",
        ord("o"): "o большая иконка открытой комнаты",
        ord("r"): "r экран комнаты",
        ord("t"): "t экран пути дальше",
        13: "Enter сохранить",
        8: "Backspace отменить последнюю",
        32: "Space продолжить",
        ord("q"): "q выйти",
    }

    def __init__(self, config: "BotConfig", logger: "logging.Logger") -> None:
        self.config = config
        self.logger = logger
        raw = getattr(config, "raw", {}) if config is not None else {}
        teacher_cfg = raw.get("teacher", {}) if isinstance(raw, dict) else {}

        self._window_name = str(teacher_cfg.get("window_name", "Bot Teacher"))
        self._preview_scale = float(teacher_cfg.get("preview_scale", 1.0) or 1.0)
        if self._preview_scale <= 0:
            self._preview_scale = 1.0

        self._pause_event = threading.Event()
        self._closed = False
        self._mouse_down = False
        self._drag_start: tuple[int, int] | None = None
        self._drag_current: tuple[int, int] | None = None
        self._pending: _PendingAnnotation | None = None
        self._saved_count = 0
        self._message = ""
        self._message_until = 0.0
        self._ui_lock = threading.RLock()
        self._window_created = False

    def pause_requested(self) -> bool:
        key = cv2.waitKey(1) & 0xFF
        if key == 32:
            self._pause_event.set()
            return True
        return self._pause_event.is_set()

    def show_and_label(
        self,
        frame: "Frame",
        knowledge: "KnowledgeBase"
    ) -> list["TemplateRecord"]:
        if not isinstance(frame, np.ndarray) or frame.ndim < 2:
            raise TeacherUIError("frame must be numpy.ndarray")

        with self._ui_lock:
            self._ensure_window()
            self._reset_session_state()
            self._pause_event.set()
            base_frame = frame.copy()
            added: list[Any] = []
            self._message = "Выделите рамкой или кликните. Затем нажмите клавишу типа."
            self._message_until = 0.0

            try:
                while True:
                    display = self._compose_display(base_frame)
                    cv2.imshow(self._window_name, display)
                    key = cv2.waitKey(30) & 0xFF

                    if key == 255:
                        if self._window_was_closed():
                            raise LabelingCancelledError("window closed")
                        continue

                    if key == ord("q"):
                        self._pause_event.clear()
                        self.close()
                        raise LabelingCancelledError("labeling cancelled")

                    if key == 8:
                        undone = False
                        if self._pending is not None:
                            self._pending = None
                            undone = True
                        else:
                            undo_fn = getattr(knowledge, "undo_last_annotation", None)
                            if callable(undo_fn):
                                undone = bool(undo_fn())
                                if undone and added:
                                    added.pop()
                        self.notify("Последняя разметка отменена" if undone else "Нечего отменять")
                        continue

                    if key == 13:
                        save_fn = getattr(knowledge, "save", None)
                        if callable(save_fn):
                            save_fn()
                        self.notify("Разметка сохранена")
                        return list(added)

                    if key == 32:
                        save_fn = getattr(knowledge, "save", None)
                        if callable(save_fn):
                            save_fn()
                        self._pause_event.clear()
                        self.notify("Продолжение работы")
                        return list(added)

                    if key in self._CATEGORY_KEYS:
                        if self._pending is None:
                            self.notify("Сначала выделите рамку или кликните")
                            continue
                        category = self._CATEGORY_KEYS[key]
                        try:
                            if self._pending.rect is not None:
                                record = knowledge.add_template(base_frame, self._pending.rect, category)
                                added.append(record)
                                self._saved_count += 1
                            elif self._pending.point is not None:
                                add_click = getattr(knowledge, "add_click_point", None)
                                if not callable(add_click):
                                    raise TeacherUIError("knowledge does not support click points")
                                x, y = self._pending.point
                                record = add_click(x, y, category)
                                added.append(record)
                                self._saved_count += 1
                            else:
                                raise TeacherUIError("empty pending annotation")
                        except Exception as exc:
                            raise TeacherUIError(f"failed to save annotation: {exc}") from exc
                        self._pending = None
                        self.notify(f"Добавлено: {self._KEY_NAMES.get(key, category)}")
                        continue

                    if self._window_was_closed():
                        raise LabelingCancelledError("window closed")
            finally:
                self._pending = None
                self._mouse_down = False
                self._drag_start = None
                self._drag_current = None

    def teach_only(
        self,
        adb: "ADBController",
        screenshot: "ScreenshotProvider",
        knowledge: "KnowledgeBase"
    ) -> int:
        del adb
        total = 0
        self.notify("Режим teach-only: Пробел пауза/разметка, q выход")
        try:
            while True:
                key = cv2.waitKey(30) & 0xFF
                if key == ord("q"):
                    break
                if key == 32:
                    self._pause_event.set()
                if not self._pause_event.is_set():
                    continue
                frame = screenshot.capture()
                try:
                    added = self.show_and_label(frame, knowledge)
                    total += len(added)
                except LabelingCancelledError:
                    break
        finally:
            self.close()
        return total

    def notify(self, message: str) -> None:
        self.logger.info(message)
        self._message = str(message)
        self._message_until = time.time() + 2.0

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            cv2.destroyWindow(self._window_name)
        except cv2.error:
            pass
        try:
            cv2.waitKey(1)
        except cv2.error:
            pass

    def _ensure_window(self) -> None:
        if self._closed:
            self._closed = False
        try:
            cv2.namedWindow(self._window_name, cv2.WINDOW_NORMAL)
            cv2.setMouseCallback(self._window_name, self._on_mouse)
            self._window_created = True
        except cv2.error as exc:
            raise TeacherUIError(f"failed to create OpenCV window: {exc}") from exc

    def _reset_session_state(self) -> None:
        self._mouse_down = False
        self._drag_start = None
        self._drag_current = None
        self._pending = None

    def _compose_display(self, frame: Frame) -> Frame:
        canvas = frame.copy()

        if self._pending is not None:
            if self._pending.rect is not None:
                x1, y1, x2, y2 = self._pending.rect
                cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 255, 255), 2)
            elif self._pending.point is not None:
                x, y = self._pending.point
                cv2.circle(canvas, (x, y), 8, (0, 255, 255), 2)
                cv2.drawMarker(canvas, (x, y), (0, 255, 255), cv2.MARKER_CROSS, 18, 2)

        if self._mouse_down and self._drag_start is not None and self._drag_current is not None:
            x1, y1 = self._drag_start
            x2, y2 = self._drag_current
            cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 200, 0), 2)

        instructions = [
            "Space: продолжить  Enter: сохранить  Backspace: отменить  q: выйти",
            "Типы: d дверь, m моб, g босс, l лут, e событие, x выход, a авто-бой, f этаж, p отряд, z опасное, o открытая комната, r экран комнаты, t экран пути",
        ]

        y = 24
        for line in instructions:
            cv2.putText(canvas, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(canvas, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
            y += 24

        if self._message and (self._message_until == 0.0 or time.time() <= self._message_until):
            cv2.putText(canvas, self._message, (10, y + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(canvas, self._message, (10, y + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1, cv2.LINE_AA)

        if self._preview_scale != 1.0:
            h, w = canvas.shape[:2]
            new_w = max(1, int(round(w * self._preview_scale)))
            new_h = max(1, int(round(h * self._preview_scale)))
            canvas = cv2.resize(canvas, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        return canvas

    def _on_mouse(self, event: int, x: int, y: int, flags: int, param: Any = None) -> None:
        del flags, param
        if self._preview_scale != 1.0:
            x = int(round(x / self._preview_scale))
            y = int(round(y / self._preview_scale))

        if event == cv2.EVENT_LBUTTONDOWN:
            self._mouse_down = True
            self._drag_start = (x, y)
            self._drag_current = (x, y)
            self._pending = None
            return

        if event == cv2.EVENT_MOUSEMOVE and self._mouse_down:
            self._drag_current = (x, y)
            return

        if event == cv2.EVENT_LBUTTONUP and self._mouse_down:
            self._mouse_down = False
            start = self._drag_start
            end = (x, y)
            self._drag_start = None
            self._drag_current = None
            if start is None:
                return
            x1, y1 = start
            x2, y2 = end
            if abs(x2 - x1) < 4 and abs(y2 - y1) < 4:
                self._pending = _PendingAnnotation(rect=None, point=(x1, y1))
                self.notify("Точка выбрана, нажмите клавишу типа")
                return
            left, right = sorted((x1, x2))
            top, bottom = sorted((y1, y2))
            self._pending = _PendingAnnotation(rect=(left, top, right, bottom), point=None)
            self.notify("Рамка выбрана, нажмите клавишу типа")

    def _window_was_closed(self) -> bool:
        if not self._window_created:
            return False
        try:
            value = cv2.getWindowProperty(self._window_name, cv2.WND_PROP_VISIBLE)
        except cv2.error:
            return True
        return value < 1
