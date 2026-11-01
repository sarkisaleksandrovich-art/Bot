from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

import cv2
import numpy as np

if TYPE_CHECKING:
    from bot.config import BotConfig
    from bot.knowledge import KnowledgeBase, TemplateRecord

Frame = np.ndarray


class VisionError(Exception):
    pass


@dataclass(frozen=True)
class Match:
    category: str
    score: float
    rect: tuple[int, int, int, int]
    template_id: str


@dataclass(frozen=True)
class LoadedTemplate:
    template_id: str
    category: str
    image_gray: Frame
    roi: tuple[int, int, int, int] | None
    threshold: float
    optional: bool


class VisionEngine:
    SCREEN_CATEGORIES = ("room_screen", "passage_screen", "event_screen")

    def __init__(self, config: "BotConfig", logger: "logging.Logger") -> None:
        self.config = config
        self.logger = logger
        self._vision_cfg = self._get_section("vision")
        self._motion_cfg = self._get_section("motion")
        self._debug_cfg = self._get_section("debug", required=False)
        self._default_threshold = float(self._vision_cfg.get("default_match_threshold", 0.88))
        self._template_method = self._resolve_template_method(
            str(self._vision_cfg.get("template_method", "TM_CCOEFF_NORMED"))
        )
        self._save_debug_images = bool(self._vision_cfg.get("save_debug_images", False))
        self._debug_dir = str(self._vision_cfg.get("debug_dir", "./debug"))
        self._min_roi_area = int(self._vision_cfg.get("min_roi_area", 100))
        self._verbose_vision = bool(self._debug_cfg.get("verbose_vision", False)) if self._debug_cfg else False
        self._template_cache: dict[str, LoadedTemplate] = {}

    def wait_until_stable(
        self,
        capture_fn: "Callable[[], Frame]",
        stable_frames: int | None = None,
        stable_diff_threshold: float | None = None,
        stable_poll_ms: int | None = None,
        stable_timeout_s: float | None = None,
        downscale_size: tuple[int, int] | None = None,
    ) -> tuple["Frame", bool]:
        """
        Ждёт стабилизации кадра после движения/перехода.

        Стабилизация = несколько подряд почти одинаковых уменьшенных кадров.
        Сравнение делается по усреднённым копиям кадров.
        При успехе возвращает последний кадр и True.
        При таймауте возвращает последний кадр и False.

        :param capture_fn: функция захвата текущего кадра
        :param stable_frames: override из motion.stable_frames
        :param stable_diff_threshold: override из motion.stable_diff_threshold
        :param stable_poll_ms: override из motion.stable_poll_ms
        :param stable_timeout_s: override из motion.stable_timeout_s
        :param downscale_size: override из motion.downscale_size
        :return: (frame, is_stable)
        :raises ScreenshotError: если capture_fn не смог получить кадр
        :raises VisionError: при внутренних ошибках сравнения
        """
        target_stable_frames = int(stable_frames or self._motion_cfg.get("stable_frames", 3))
        diff_threshold = float(
            stable_diff_threshold
            if stable_diff_threshold is not None
            else self._motion_cfg.get("stable_diff_threshold", 0.015)
        )
        poll = int(stable_poll_ms if stable_poll_ms is not None else self._motion_cfg.get("stable_poll_ms", 350))
        timeout = float(
            stable_timeout_s if stable_timeout_s is not None else self._motion_cfg.get("stable_timeout_s", 8.0)
        )
        ds_size = downscale_size or self._motion_downscale_size()

        if target_stable_frames <= 0:
            raise VisionError("stable_frames must be > 0")
        if diff_threshold < 0:
            raise VisionError("stable_diff_threshold must be >= 0")
        if poll < 0:
            raise VisionError("stable_poll_ms must be >= 0")
        if timeout < 0:
            raise VisionError("stable_timeout_s must be >= 0")

        started = time.monotonic()
        prev_small_gray: Frame | None = None
        stable_count = 0
        last_frame: Frame | None = None

        while True:
            last_frame = capture_fn()
            cur_small_gray = self._prepare_motion_frame(last_frame, ds_size)

            if prev_small_gray is None:
                stable_count = 1
            else:
                mean_diff = self._mean_absdiff_normalized(prev_small_gray, cur_small_gray)
                if mean_diff < diff_threshold:
                    stable_count += 1
                else:
                    stable_count = 1

                if self._verbose_vision:
                    self.logger.debug(
                        "wait_until_stable: mean_diff=%.6f stable_count=%d/%d",
                        mean_diff,
                        stable_count,
                        target_stable_frames,
                    )

            prev_small_gray = cur_small_gray

            if stable_count >= target_stable_frames:
                return last_frame, True

            if time.monotonic() - started >= timeout:
                if last_frame is None:
                    raise VisionError("wait_until_stable timed out before receiving any frame")
                return last_frame, False

            time.sleep(poll / 1000.0)

    def find(
        self,
        frame: "Frame",
        template: "Frame",
        roi: tuple[int, int, int, int],
        threshold: float,
    ) -> Match | None:
        """
        Ищет один шаблон внутри ROI кадра через matchTemplate.

        :param frame: текущий кадр
        :param template: шаблон-фрагмент
        :param roi: (x1, y1, x2, y2)
        :param threshold: порог совпадения
        :return: Match или None
        :raises VisionError: если ROI невалиден или template больше ROI
        """
        validated_roi = self._normalize_roi(frame, roi)
        frame_gray = self._to_gray(frame)
        template_gray = self._to_gray(template)
        return self._match_template(
            frame_gray=frame_gray,
            template_gray=template_gray,
            roi=validated_roi,
            threshold=threshold,
            category="",
            template_id="",
        )

    def find_any(
        self,
        frame: "Frame",
        templates: list["TemplateRecord"],
        threshold: float | None = None,
        roi: tuple[int, int, int, int] | None = None,
    ) -> Match | None:
        matches = self.find_all(frame=frame, templates=templates, threshold=threshold, roi=roi)
        if not matches:
            return None
        return matches[0]

    def find_all(
        self,
        frame: "Frame",
        templates: list["TemplateRecord"],
        threshold: float | None = None,
        roi: tuple[int, int, int, int] | None = None,
    ) -> list[Match]:
        frame_gray = self._to_gray(frame)
        matches: list[Match] = []
        for record in templates:
            loaded = self._load_template_record(record, threshold_override=threshold)
            effective_roi = self._merge_rois(frame, roi, loaded.roi)
            match = self._match_template(
                frame_gray=frame_gray,
                template_gray=loaded.image_gray,
                roi=effective_roi,
                threshold=loaded.threshold,
                category=loaded.category,
                template_id=loaded.template_id,
            )
            if match is not None:
                matches.append(match)

        matches.sort(key=lambda item: item.score, reverse=True)
        return matches

    def find_profile_matches(
        self,
        capture_fn: "Callable[[], Frame]",
        knowledge: "KnowledgeBase",
        threshold: float | None = None,
        roi: tuple[int, int, int, int] | None = None,
        enabled_only: bool = True,
    ) -> tuple[Frame, list["TemplateRecord"], list[Match]]:
        frame = capture_fn()
        templates = knowledge.get_templates(enabled_only=enabled_only)
        effective_threshold = threshold if threshold is not None else self.config.default_match_threshold
        matches = self.find_all(frame=frame, templates=templates, threshold=effective_threshold, roi=roi)
        return frame, templates, matches

    def find_category(
        self,
        frame: "Frame",
        templates: list["TemplateRecord"],
        category: str,
        threshold: float | None = None,
        roi: tuple[int, int, int, int] | None = None,
    ) -> list[Match]:
        """
        Ищет все активные шаблоны указанной категории.

        :param frame: текущий кадр
        :param templates: список шаблонов из knowledge
        :param category: категория
        :param threshold: порог; если None, используется config vision.default_match_threshold
        :param roi: общий ROI; если None, используется roi_hint шаблона или весь экран
        :return: список Match, отсортированный по score по убыванию
        :raises VisionError: при ошибке поиска
        """
        filtered = [tpl for tpl in templates if getattr(tpl, "enabled", True) and tpl.category == category]
        return self.find_all(frame=frame, templates=filtered, threshold=threshold, roi=roi)

    def classify_screen(
        self,
        frame: "Frame",
        templates: list["TemplateRecord"],
    ) -> str | None:
        """
        Классифицирует экран по наличию шаблонов экранных категорий.

        Допустимые возвращаемые имена:
          "room_screen"
          "passage_screen"
          "event_screen"
          None

        danger не является именем экрана; его надо проверять отдельно через safety/vision.

        :param frame: текущий кадр
        :param templates: шаблоны знаний
        :return: имя экрана или None
        :raises VisionError: при ошибке поиска
        """
        frame_gray = self._to_gray(frame)
        best_match: Match | None = None
        best_screen: str | None = None

        for screen_name in self.SCREEN_CATEGORIES:
            screen_templates = [
                tpl for tpl in templates if getattr(tpl, "enabled", True) and tpl.category == screen_name
            ]
            for record in screen_templates:
                loaded = self._load_template_record(record)
                match = self._match_template(
                    frame_gray=frame_gray,
                    template_gray=loaded.image_gray,
                    roi=loaded.roi,
                    threshold=loaded.threshold,
                    category=loaded.category,
                    template_id=loaded.template_id,
                )
                if match is None:
                    continue
                if best_match is None or match.score > best_match.score:
                    best_match = match
                    best_screen = screen_name

        return best_screen

    def has_any(
        self,
        frame: "Frame",
        templates: list["TemplateRecord"],
        categories: list[str],
        threshold: float | None = None,
    ) -> bool:
        """
        Проверяет, найден ли хотя бы один шаблон из категорий.

        :return: bool
        :raises VisionError: при ошибке поиска
        """
        for category in categories:
            if self.find_category(frame, templates, category, threshold=threshold):
                return True
        return False

    def crop(
        self,
        frame: "Frame",
        rect: tuple[int, int, int, int],
    ) -> "Frame":
        """
        Возвращает вырезанный фрагмент кадра.

        :raises VisionError: если rect невалиден
        """
        x1, y1, x2, y2 = self._normalize_roi(frame, rect)
        return frame[y1:y2, x1:x2].copy()

    def wait_for(
        self,
        capture_fn: "Callable[[], Frame]",
        template: Any,
        roi: tuple[int, int, int, int] | None,
        threshold: float,
        timeout_s: float,
    ) -> Match | None:
        started = time.monotonic()
        poll_ms = int(self._motion_cfg.get("stable_poll_ms", 350))

        while True:
            frame = capture_fn()

            if isinstance(template, np.ndarray):
                effective_roi = roi or (0, 0, frame.shape[1], frame.shape[0])
                match = self.find(frame, template, effective_roi, threshold)
            else:
                loaded = self._load_template_record(template, threshold_override=threshold)
                effective_roi = self._merge_rois(frame, roi, loaded.roi)
                frame_gray = self._to_gray(frame)
                match = self._match_template(
                    frame_gray=frame_gray,
                    template_gray=loaded.image_gray,
                    roi=effective_roi,
                    threshold=loaded.threshold,
                    category=loaded.category,
                    template_id=loaded.template_id,
                )

            if match is not None:
                return match

            if time.monotonic() - started >= timeout_s:
                return None

            time.sleep(poll_ms / 1000.0)

    def is_animating(self, prev: "Frame", cur: "Frame", threshold: float) -> bool:
        ds_size = self._motion_downscale_size()
        prev_small = self._prepare_motion_frame(prev, ds_size)
        cur_small = self._prepare_motion_frame(cur, ds_size)
        mean_diff = self._mean_absdiff_normalized(prev_small, cur_small)
        return mean_diff >= threshold

    def load_templates(
        self,
        records: list["TemplateRecord"],
        thresholds_by_category: dict[str, float] | None = None,
        optional_categories: set[str] | None = None,
    ) -> list[LoadedTemplate]:
        loaded: list[LoadedTemplate] = []
        for record in records:
            threshold_override = None
            if thresholds_by_category and record.category in thresholds_by_category:
                threshold_override = thresholds_by_category[record.category]
            tpl = self._load_template_record(record, threshold_override=threshold_override)
            loaded.append(
                LoadedTemplate(
                    template_id=tpl.template_id,
                    category=tpl.category,
                    image_gray=tpl.image_gray,
                    roi=tpl.roi,
                    threshold=tpl.threshold,
                    optional=record.category in (optional_categories or set()),
                )
            )
        return loaded

    def save_debug_image(
        self,
        frame: "Frame",
        path: str | None = None,
        matches: list[Match] | None = None,
        rois: list[tuple[int, int, int, int]] | None = None,
        label: str = "vision",
    ) -> str:
        canvas = frame.copy()

        if rois:
            for roi in rois:
                x1, y1, x2, y2 = self._normalize_roi(frame, roi)
                cv2.rectangle(canvas, (x1, y1), (x2, y2), (255, 255, 0), 2)

        if matches:
            for match in matches:
                x1, y1, x2, y2 = match.rect
                cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 255, 0), 2)
                text = f"{match.category}:{match.score:.3f}:{match.template_id}"
                cv2.putText(
                    canvas,
                    text,
                    (x1, max(15, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    (0, 255, 0),
                    1,
                    cv2.LINE_AA,
                )

        if path is None:
            os.makedirs(self._debug_dir, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
            path = os.path.join(self._debug_dir, f"{label}_{stamp}.png")
        else:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

        ok = cv2.imwrite(path, canvas)
        if not ok:
            raise VisionError(f"Failed to save debug image: {path}")

        if self._save_debug_images or self._verbose_vision:
            self.logger.debug("Saved vision debug image: %s", path)
        return path

    def _load_template_record(
        self,
        record: "TemplateRecord",
        threshold_override: float | None = None,
    ) -> LoadedTemplate:
        cache_key = f"{record.id}:{threshold_override if threshold_override is not None else 'default'}"
        cached = self._template_cache.get(cache_key)
        if cached is not None:
            return cached

        image = cv2.imread(record.file, cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise VisionError(f"Template image is unreadable: {record.file}")
        if image.size == 0:
            raise VisionError(f"Template image is empty: {record.file}")

        roi = self._rect_to_xyxy(tuple(record.roi_hint)) if getattr(record, "roi_hint", None) else None
        loaded = LoadedTemplate(
            template_id=record.id,
            category=record.category,
            image_gray=image,
            roi=roi,
            threshold=float(threshold_override if threshold_override is not None else self._default_threshold),
            optional=False,
        )
        self._template_cache[cache_key] = loaded
        return loaded

    def _match_template(
        self,
        frame_gray: Frame,
        template_gray: Frame,
        roi: tuple[int, int, int, int] | None,
        threshold: float,
        category: str,
        template_id: str,
    ) -> Match | None:
        h, w = frame_gray.shape[:2]
        effective_roi = roi if roi is not None else (0, 0, w, h)
        x1, y1, x2, y2 = self._normalize_roi(frame_gray, effective_roi)
        search = frame_gray[y1:y2, x1:x2]

        th, tw = template_gray.shape[:2]
        rh, rw = search.shape[:2]
        if tw > rw or th > rh:
            raise VisionError("template is larger than ROI")

        try:
            result = cv2.matchTemplate(search, template_gray, self._template_method)
        except cv2.error as exc:
            raise VisionError(f"matchTemplate failed: {exc}") from exc

        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        score = float(max_val)
        if score < threshold:
            return None

        top_left_x = x1 + int(max_loc[0])
        top_left_y = y1 + int(max_loc[1])
        rect = (top_left_x, top_left_y, top_left_x + tw, top_left_y + th)
        return Match(
            category=category,
            score=score,
            rect=rect,
            template_id=template_id,
        )

    def _to_gray(self, frame: Frame) -> Frame:
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            raise VisionError("frame must be a non-empty numpy.ndarray")
        if frame.ndim == 2:
            return frame
        if frame.ndim == 3 and frame.shape[2] == 1:
            return frame[:, :, 0]
        if frame.ndim == 3 and frame.shape[2] == 3:
            return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if frame.ndim == 3 and frame.shape[2] == 4:
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
        raise VisionError(f"Unsupported frame shape for grayscale conversion: {frame.shape}")

    def _prepare_motion_frame(self, frame: Frame, downscale_size: tuple[int, int]) -> Frame:
        gray = self._to_gray(frame)
        width, height = downscale_size
        if width <= 0 or height <= 0:
            raise VisionError("downscale_size must contain positive integers")
        try:
            resized = cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA)
        except cv2.error as exc:
            raise VisionError(f"Failed to resize motion frame: {exc}") from exc
        return resized

    def _mean_absdiff_normalized(self, prev_gray: Frame, cur_gray: Frame) -> float:
        if prev_gray.shape != cur_gray.shape:
            raise VisionError("Motion frames must have identical shape for comparison")
        diff = cv2.absdiff(prev_gray, cur_gray)
        return float(np.mean(diff) / 255.0)

    def _normalize_roi(
        self,
        frame: Frame,
        roi: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, int, int]:
        if roi is None:
            h, w = frame.shape[:2]
            return 0, 0, w, h

        if len(roi) != 4:
            raise VisionError("ROI must contain exactly 4 integers")

        x1, y1, x2, y2 = (int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3]))
        h, w = frame.shape[:2]
        x1 = max(0, min(x1, w))
        x2 = max(0, min(x2, w))
        y1 = max(0, min(y1, h))
        y2 = max(0, min(y2, h))

        if x2 <= x1 or y2 <= y1:
            raise VisionError(f"Invalid ROI after clamping: {(x1, y1, x2, y2)}")

        if (x2 - x1) * (y2 - y1) < self._min_roi_area:
            raise VisionError(f"ROI area is below configured minimum: {(x1, y1, x2, y2)}")

        return x1, y1, x2, y2

    def _merge_rois(
        self,
        frame: Frame,
        roi_a: tuple[int, int, int, int] | None,
        roi_b: tuple[int, int, int, int] | None,
    ) -> tuple[int, int, int, int] | None:
        if roi_a is None and roi_b is None:
            return None
        if roi_a is None:
            return self._normalize_roi(frame, roi_b)
        if roi_b is None:
            return self._normalize_roi(frame, roi_a)

        ax1, ay1, ax2, ay2 = self._normalize_roi(frame, roi_a)
        bx1, by1, bx2, by2 = self._normalize_roi(frame, roi_b)
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        return self._normalize_roi(frame, (ix1, iy1, ix2, iy2))

    def _motion_downscale_size(self) -> tuple[int, int]:
        raw = self._motion_cfg.get("downscale_size", [64, 36])
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise VisionError("motion.downscale_size must be a 2-item list/tuple")
        return int(raw[0]), int(raw[1])

    def _resolve_template_method(self, method_name: str) -> int:
        if method_name != "TM_CCOEFF_NORMED":
            self.logger.warning(
                "Unsupported template method '%s'; falling back to TM_CCOEFF_NORMED",
                method_name,
            )
        return cv2.TM_CCOEFF_NORMED

    def _rect_to_xyxy(self, rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
        if x2 <= x1 or y2 <= y1:
            raise VisionError(f"Invalid rect/roi_hint: {rect}")
        return x1, y1, x2, y2

    def _get_section(self, key: str, required: bool = True) -> dict[str, Any]:
        raw = getattr(self.config, "raw", None)
        if not isinstance(raw, dict):
            raise VisionError("config.raw must be a dict")
        section = raw.get(key)
        if section is None:
            if required:
                raise VisionError(f"Missing config section: {key}")
            return {}
        if not isinstance(section, dict):
            raise VisionError(f"Config section must be a dict: {key}")
        return section
