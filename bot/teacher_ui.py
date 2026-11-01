from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np

from bot.knowledge import KnowledgeError, UnknownCategoryError
from bot.vision import Rect

if TYPE_CHECKING:
    from bot.adb_controller import ADBController
    from bot.knowledge import KnowledgeBase
    from bot.screenshot import ScreenshotService
    from bot.screenshot import Frame


class TeacherUIError(Exception):
    pass


class UserAbortTeaching(TeacherUIError):
    pass


_WINDOW_NAME = "teacher_ui"
_PAUSE_KEY_CODE = 32
_ENTER_KEY_CODES = {10, 13}
_BACKSPACE_KEY_CODES = {8, 127}
_QUIT_KEYS = {ord("q"), ord("Q")}

_pause_lock = threading.Lock()
_pause_flag = False
_window_created = False


_CATEGORY_KEYS: dict[int, str] = {
    ord("d"): "door_marker",
    ord("D"): "door_marker",
    ord("m"): "mob",
    ord("M"): "mob",
    ord("g"): "boss",
    ord("G"): "boss",
    ord("l"): "loot",
    ord("L"): "loot",
    ord("e"): "event_screen",
    ord("E"): "event_screen",
    ord("x"): "exit",
    ord("X"): "exit",
    ord("a"): "auto_battle",
    ord("A"): "auto_battle",
    ord("f"): "next_floor",
    ord("F"): "next_floor",
    ord("p"): "party_marker",
    ord("P"): "party_marker",
    ord("z"): "danger",
    ord("Z"): "danger",
    ord("o"): "room_open_big_icon",
    ord("O"): "room_open_big_icon",
    ord("r"): "room_screen",
    ord("R"): "room_screen",
    ord("t"): "passage_screen",
    ord("T"): "passage_screen",
}


@dataclass
class _Annotation:
    kind: str
    category: str
    rect: Rect | None = None
    point: tuple[int, int] | None = None


@dataclass
class _UIState:
    frame: "Frame"
    drawing: bool = False
    drag_started: bool = False
    start_point: tuple[int, int] | None = None
    current_point: tuple[int, int] | None = None
    preview_rect: Rect | None = None
    pending_rect: Rect | None = None
    pending_point: tuple[int, int] | None = None
    annotations: list[_Annotation] | None = None
    dirty: bool = False
    saved_any: bool = False

    def __post_init__(self) -> None:
        if self.annotations is None:
            self.annotations = []


def pause_requested() -> bool:
    with _pause_lock:
        return _pause_flag


def _set_pause(value: bool) -> None:
    global _pause_flag
    with _pause_lock:
        _pause_flag = value


def _ensure_window() -> None:
    global _window_created
    if not _window_created:
        cv2.namedWindow(_WINDOW_NAME, cv2.WINDOW_NORMAL)
        _window_created = True


def _close_window() -> None:
    global _window_created
    if _window_created:
        try:
            cv2.destroyWindow(_WINDOW_NAME)
        except cv2.error:
            pass
        _window_created = False
        try:
            cv2.waitKey(1)
        except cv2.error:
            pass


def _clamp_point(frame: "Frame", x: int, y: int) -> tuple[int, int]:
    h, w = frame.shape[:2]
    x = min(max(int(x), 0), max(w - 1, 0))
    y = min(max(int(y), 0), max(h - 1, 0))
    return x, y


def _rect_from_points(frame: "Frame", p1: tuple[int, int], p2: tuple[int, int]) -> Rect | None:
    x1, y1 = _clamp_point(frame, p1[0], p1[1])
    x2, y2 = _clamp_point(frame, p2[0], p2[1])
    left = min(x1, x2)
    top = min(y1, y2)
    right = max(x1, x2)
    bottom = max(y1, y2)
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return None
    return Rect(x=left, y=top, w=width, h=height)


def _draw_text_lines(image: np.ndarray, lines: list[str], start_x: int = 10, start_y: int = 20) -> None:
    y = start_y
    for line in lines:
        cv2.putText(image, line, (start_x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(image, line, (start_x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        y += 20


def _render(state: _UIState) -> np.ndarray:
    canvas = state.frame.copy()

    for annotation in state.annotations or []:
        if annotation.rect is not None:
            r = annotation.rect
            cv2.rectangle(canvas, (r.x, r.y), (r.x + r.w, r.y + r.h), (0, 255, 0), 2)
            cv2.putText(canvas, annotation.category, (r.x, max(15, r.y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
        elif annotation.point is not None:
            x, y = annotation.point
            cv2.circle(canvas, (x, y), 6, (0, 255, 255), 2)
            cv2.putText(canvas, annotation.category, (x + 8, max(15, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)

    if state.preview_rect is not None:
        r = state.preview_rect
        cv2.rectangle(canvas, (r.x, r.y), (r.x + r.w, r.y + r.h), (255, 0, 0), 2)

    if state.pending_point is not None and state.pending_rect is None:
        cv2.circle(canvas, state.pending_point, 5, (255, 0, 255), 2)

    lines = [
        "Пауза: разметка активна",
        "Мышь: рамка или клик-точка",
        "Backspace: отменить последнюю разметку",
        "Enter: сохранить разметку",
        "Space: продолжить работу",
        "q: выйти",
        "Категории: d дверь, m моб, g босс, l лут, e событие, x выход, a авто-бой, f след. этаж, p маркер отряда, z опасное, o открытая комната, r экран комнаты, t экран пути",
    ]

    if state.pending_rect is not None:
        lines.append("Выбрана рамка: нажмите клавишу категории")
    elif state.pending_point is not None:
        lines.append("Выбрана точка: нажмите клавишу категории")
    else:
        lines.append("Сначала выделите рамку или кликните по точке")

    _draw_text_lines(canvas, lines)
    return canvas


def _mouse_callback(event: int, x: int, y: int, _flags: int, userdata: _UIState) -> None:
    state = userdata
    if state.frame is None:
        return

    point = _clamp_point(state.frame, x, y)

    if event == cv2.EVENT_LBUTTONDOWN:
        state.drawing = True
        state.drag_started = False
        state.start_point = point
        state.current_point = point
        state.preview_rect = None
        state.pending_rect = None
        state.pending_point = None
    elif event == cv2.EVENT_MOUSEMOVE and state.drawing:
        state.current_point = point
        rect = _rect_from_points(state.frame, state.start_point or point, point)
        if rect is not None:
            state.drag_started = True
            state.preview_rect = rect
        else:
            state.preview_rect = None
    elif event == cv2.EVENT_LBUTTONUP and state.drawing:
        state.drawing = False
        state.current_point = point
        rect = _rect_from_points(state.frame, state.start_point or point, point)
        if rect is not None:
            state.pending_rect = rect
            state.pending_point = None
            state.preview_rect = rect
        else:
            state.pending_rect = None
            state.preview_rect = None
            state.pending_point = point


def _save_pending_annotation(state: _UIState, knowledge: "KnowledgeBase", category: str) -> None:
    if state.pending_rect is not None:
        rect = state.pending_rect
        knowledge.add_template_from_frame(
            frame=state.frame,
            rect=rect,
            category=category,
            tags=None,
            screen_tag="manual",
            note="labeled in teacher_ui",
        )
        state.annotations.append(_Annotation(kind="rect", category=category, rect=rect))
        state.saved_any = True
        state.dirty = True
        state.pending_rect = None
        state.preview_rect = None
        return

    if state.pending_point is not None:
        x, y = state.pending_point
        full_roi = Rect(x=0, y=0, w=int(state.frame.shape[1]), h=int(state.frame.shape[0]))
        if hasattr(knowledge, "add_click_point"):
            knowledge.add_click_point(category=category, x=x, y=y, roi=full_roi, note="labeled click point in teacher_ui")
        else:
            raise TeacherUIError("KnowledgeBase does not support click-point annotations.")
        state.annotations.append(_Annotation(kind="point", category=category, point=(x, y)))
        state.saved_any = True
        state.dirty = True
        state.pending_point = None
        return

    raise TeacherUIError("No pending selection to save.")


def show_and_label(frame: "Frame", knowledge: "KnowledgeBase") -> bool:
    if not isinstance(frame, np.ndarray) or frame.size == 0:
        raise TeacherUIError("Frame must be a non-empty numpy array.")

    _set_pause(True)
    state = _UIState(frame=frame.copy())

    try:
        _ensure_window()
        cv2.setMouseCallback(_WINDOW_NAME, _mouse_callback, state)

        while True:
            display = _render(state)
            cv2.imshow(_WINDOW_NAME, display)
            key = cv2.waitKey(30) & 0xFF

            if key == 255:
                continue

            if key == _PAUSE_KEY_CODE:
                if state.dirty:
                    try:
                        knowledge.save()
                        state.dirty = False
                    except (KnowledgeError, OSError) as exc:
                        raise TeacherUIError(f"Failed to save knowledge: {exc}") from exc
                _set_pause(False)
                return state.saved_any

            if key in _QUIT_KEYS:
                raise UserAbortTeaching("User aborted teaching.")

            if key in _ENTER_KEY_CODES:
                if state.dirty:
                    try:
                        knowledge.save()
                        state.dirty = False
                    except (KnowledgeError, OSError) as exc:
                        raise TeacherUIError(f"Failed to save knowledge: {exc}") from exc
                    return True
                return state.saved_any

            if key in _BACKSPACE_KEY_CODES:
                removed = False
                if state.pending_rect is not None or state.pending_point is not None:
                    state.pending_rect = None
                    state.pending_point = None
                    state.preview_rect = None
                    removed = True
                elif state.annotations:
                    last = state.annotations.pop()
                    if hasattr(knowledge, "undo_last_annotation"):
                        try:
                            removed = bool(knowledge.undo_last_annotation())
                        except (KnowledgeError, OSError) as exc:
                            raise TeacherUIError(f"Failed to undo annotation: {exc}") from exc
                        if removed:
                            state.dirty = True
                            if not state.annotations:
                                state.saved_any = False
                    else:
                        state.annotations.append(last)
                        raise TeacherUIError("KnowledgeBase does not support undo_last_annotation().")
                if removed:
                    continue

            if key in _CATEGORY_KEYS:
                category = _CATEGORY_KEYS[key]
                try:
                    _save_pending_annotation(state, knowledge, category)
                except (UnknownCategoryError, KnowledgeError):
                    raise
                except Exception as exc:
                    raise TeacherUIError(str(exc)) from exc
                continue

    except cv2.error as exc:
        raise TeacherUIError(f"OpenCV UI error: {exc}") from exc
    finally:
        try:
            cv2.setMouseCallback(_WINDOW_NAME, lambda *_args: None)
        except cv2.error:
            pass
        _close_window()


def teach_only(adb: "ADBController", screenshot: "ScreenshotService", knowledge: "KnowledgeBase") -> None:
    del adb
    _set_pause(False)

    try:
        while True:
            frame = screenshot.capture()
            _ensure_window()
            preview = frame.copy()
            _draw_text_lines(
                preview,
                [
                    "Режим teach-only",
                    "Space: пауза и разметка текущего кадра",
                    "q: выйти",
                ],
            )
            cv2.imshow(_WINDOW_NAME, preview)
            key = cv2.waitKey(100) & 0xFF

            if key == _PAUSE_KEY_CODE:
                _set_pause(True)
                show_and_label(frame, knowledge)
                _set_pause(False)
                continue

            if key in _QUIT_KEYS:
                raise UserAbortTeaching("User aborted teach-only mode.")

            time.sleep(0.05)
    except cv2.error as exc:
        raise TeacherUIError(f"OpenCV UI error: {exc}") from exc
    finally:
        _close_window()
