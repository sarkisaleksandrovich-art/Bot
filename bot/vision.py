from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, TYPE_CHECKING
import time

import cv2
import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    from bot.config import Config


class VisionError(Exception):
    pass


Frame = NDArray[np.uint8]


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Match:
    category: str
    score: float
    rect: Rect
    template_id: str | None = None


@dataclass(frozen=True)
class Template:
    id: str
    category: str
    image: Frame
    roi: Rect
    threshold: float
    enabled: bool = True


class VisionService:
    SCREEN_CATEGORIES: tuple[str, ...] = (
        "room_screen",
        "passage_screen",
        "event_screen",
    )

    def __init__(self, config: "Config") -> None:
        self.config = config

        try:
            self.default_threshold = float(config.get("matching.default_threshold"))
            self.classify_threshold = float(config.get("matching.classify_threshold"))
            self.motion_stable_frames = int(config.get("motion.stable_frames"))
            self.motion_stable_diff_threshold = float(config.get("motion.stable_diff_threshold"))
            self.motion_stable_poll_ms = int(config.get("motion.stable_poll_ms"))
            self.motion_stable_timeout_s = float(config.get("motion.stable_timeout_s"))
            downscale = config.get("motion.downscale_size")
        except Exception as exc:
            raise VisionError("Missing or invalid matching/motion configuration") from exc

        if not isinstance(downscale, (list, tuple)) or len(downscale) != 2:
            raise VisionError("motion.downscale_size must be a sequence of two integers")

        self.motion_downscale_size = (int(downscale[0]), int(downscale[1]))
        self._validate_runtime_params(
            stable_frames=self.motion_stable_frames,
            stable_diff_threshold=self.motion_stable_diff_threshold,
            stable_poll_ms=self.motion_stable_poll_ms,
            stable_timeout_s=self.motion_stable_timeout_s,
            downscale_size=self.motion_downscale_size,
        )

    def wait_until_stable(
        self,
        capture_fn: Callable[[], Frame],
        stable_frames: int | None = None,
        stable_diff_threshold: float | None = None,
        stable_poll_ms: int | None = None,
        stable_timeout_s: float | None = None,
        downscale_size: tuple[int, int] | None = None,
    ) -> tuple[Frame, bool]:
        stable_frames = self.motion_stable_frames if stable_frames is None else stable_frames
        stable_diff_threshold = (
            self.motion_stable_diff_threshold
            if stable_diff_threshold is None
            else stable_diff_threshold
        )
        stable_poll_ms = self.motion_stable_poll_ms if stable_poll_ms is None else stable_poll_ms
        stable_timeout_s = self.motion_stable_timeout_s if stable_timeout_s is None else stable_timeout_s
        downscale_size = self.motion_downscale_size if downscale_size is None else downscale_size

        self._validate_runtime_params(
            stable_frames=stable_frames,
            stable_diff_threshold=stable_diff_threshold,
            stable_poll_ms=stable_poll_ms,
            stable_timeout_s=stable_timeout_s,
            downscale_size=downscale_size,
        )

        deadline = time.monotonic() + float(stable_timeout_s)
        prev_small_gray: Frame | None = None
        consecutive_stable = 0
        last_full_frame: Frame | None = None

        while True:
            frame = capture_fn()
            self._validate_frame(frame)
            last_full_frame = frame

            small_gray = self._prepare_motion_frame(frame, downscale_size)

            if prev_small_gray is not None:
                diff = self._mean_absdiff(prev_small_gray, small_gray)
                if diff <= float(stable_diff_threshold):
                    consecutive_stable += 1
                else:
                    consecutive_stable = 0

                if consecutive_stable >= int(stable_frames):
                    return last_full_frame, True

            prev_small_gray = small_gray

            if time.monotonic() >= deadline:
                return last_full_frame, False

            time.sleep(float(stable_poll_ms) / 1000.0)

    def is_animating(self, prev: Frame, cur: Frame, threshold: float) -> bool:
        self._validate_frame(prev)
        self._validate_frame(cur)

        if threshold < 0:
            raise VisionError("threshold must be >= 0")

        prev_small = self._prepare_motion_frame(prev, self.motion_downscale_size)
        cur_small = self._prepare_motion_frame(cur, self.motion_downscale_size)
        diff = self._mean_absdiff(prev_small, cur_small)
        return diff > float(threshold)

    def find(
        self,
        frame: Frame,
        template: Template,
        roi: Rect | None = None,
        threshold: float | None = None,
    ) -> Match | None:
        self._validate_frame(frame)

        if not template.enabled:
            return None

        if template.image is None or template.image.size == 0:
            raise VisionError(f"Template '{template.id}' has empty image")

        threshold = template.threshold if threshold is None else threshold
        search_roi = template.roi if roi is None else roi

        if threshold < -1.0 or threshold > 1.0:
            raise VisionError("threshold must be in range [-1.0, 1.0]")

        frame_gray = self._to_gray(frame)
        template_gray = self._to_gray(template.image)
        roi_fixed = self._fit_rect_to_frame(search_roi, frame_gray)

        haystack = self.crop(frame_gray, roi_fixed)

        th, tw = template_gray.shape[:2]
        hh, hw = haystack.shape[:2]

        if th <= 0 or tw <= 0:
            raise VisionError(f"Template '{template.id}' has invalid size")
        if hh < th or hw < tw:
            raise VisionError(
                f"Template '{template.id}' size {tw}x{th} is larger than ROI {hw}x{hh}"
            )

        result = cv2.matchTemplate(haystack, template_gray, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if float(max_val) < float(threshold):
            return None

        local_x, local_y = int(max_loc[0]), int(max_loc[1])
        full_x = roi_fixed.x + local_x
        full_y = roi_fixed.y + local_y
        rect = Rect(x=full_x, y=full_y, w=tw, h=th)

        return Match(
            category=template.category,
            score=float(max_val),
            rect=rect,
            template_id=template.id,
        )

    def find_any(
        self,
        frame: Frame,
        templates: Iterable[Template],
        category: str | None = None,
    ) -> Match | None:
        matches = self.find_all(frame=frame, templates=templates, category=category)
        return matches[0] if matches else None

    def find_all(
        self,
        frame: Frame,
        templates: Iterable[Template],
        category: str | None = None,
    ) -> list[Match]:
        self._validate_frame(frame)

        matches: list[Match] = []
        for template in templates:
            if not template.enabled:
                continue
            if category is not None and template.category != category:
                continue

            try:
                match = self.find(frame=frame, template=template)
            except VisionError:
                continue

            if match is not None:
                matches.append(match)

        matches.sort(key=lambda item: item.score, reverse=True)
        return matches

    def classify_screen(
        self,
        frame: Frame,
        templates: Iterable[Template],
    ) -> str | None:
        self._validate_frame(frame)

        best_by_category: dict[str, Match] = {}
        any_screen_template = False

        for template in templates:
            if not template.enabled:
                continue
            if template.category not in self.SCREEN_CATEGORIES:
                continue

            any_screen_template = True
            threshold = max(float(template.threshold), float(self.classify_threshold))

            try:
                match = self.find(
                    frame=frame,
                    template=template,
                    roi=template.roi,
                    threshold=threshold,
                )
            except VisionError:
                continue

            if match is None:
                continue

            current_best = best_by_category.get(template.category)
            if current_best is None or match.score > current_best.score:
                best_by_category[template.category] = match

        if not any_screen_template or not best_by_category:
            return None

        best_match = max(best_by_category.values(), key=lambda item: item.score)
        return best_match.category

    def crop(self, frame: Frame, rect: Rect) -> Frame:
        self._validate_frame(frame)
        self._validate_rect(rect)

        height, width = frame.shape[:2]
        if rect.x < 0 or rect.y < 0:
            raise VisionError("rect coordinates must be non-negative")
        if rect.w <= 0 or rect.h <= 0:
            raise VisionError("rect width and height must be positive")
        if rect.x + rect.w > width or rect.y + rect.h > height:
            raise VisionError("rect is out of frame bounds")

        return frame[rect.y:rect.y + rect.h, rect.x:rect.x + rect.w].copy()

    def center_of(self, rect: Rect) -> tuple[int, int]:
        self._validate_rect(rect)
        return rect.x + rect.w // 2, rect.y + rect.h // 2

    def load_templates(
        self,
        items: Iterable[dict],
        *,
        optional: bool = True,
    ) -> list[Template]:
        templates: list[Template] = []

        for item in items:
            try:
                template_id = str(item["id"])
                category = str(item["category"])
                image_path = Path(item["image_path"])
                threshold = float(item.get("match", {}).get("threshold", self.default_threshold))
                enabled = bool(item.get("enabled", True))
                roi_data = item["roi"]
                roi = Rect(
                    x=int(roi_data["x"]),
                    y=int(roi_data["y"]),
                    w=int(roi_data["w"]),
                    h=int(roi_data["h"]),
                )
            except Exception as exc:
                if optional:
                    continue
                raise VisionError("Invalid template metadata") from exc

            image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
            if image is None or image.size == 0:
                if optional:
                    continue
                raise VisionError(f"Failed to load template image: {image_path}")

            templates.append(
                Template(
                    id=template_id,
                    category=category,
                    image=image,
                    roi=roi,
                    threshold=threshold,
                    enabled=enabled,
                )
            )

        return templates

    def save_debug_image(
        self,
        frame: Frame,
        path: str | Path,
        matches: Iterable[Match] | None = None,
        rois: Iterable[Rect] | None = None,
    ) -> None:
        self._validate_frame(frame)

        vis = frame.copy()
        if vis.ndim == 2:
            vis = cv2.cvtColor(vis, cv2.COLOR_GRAY2BGR)

        if rois is not None:
            for roi in rois:
                self._validate_rect(roi)
                cv2.rectangle(
                    vis,
                    (roi.x, roi.y),
                    (roi.x + roi.w, roi.y + roi.h),
                    (255, 0, 0),
                    2,
                )

        if matches is not None:
            for match in matches:
                rect = match.rect
                center = self.center_of(rect)
                cv2.rectangle(
                    vis,
                    (rect.x, rect.y),
                    (rect.x + rect.w, rect.y + rect.h),
                    (0, 255, 0),
                    2,
                )
                cv2.circle(vis, center, 4, (0, 0, 255), -1)
                label = f"{match.category}:{match.score:.3f}"
                cv2.putText(
                    vis,
                    label,
                    (rect.x, max(15, rect.y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 0),
                    1,
                    cv2.LINE_AA,
                )

        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ok = cv2.imwrite(str(output_path), vis)
        if not ok:
            raise VisionError(f"Failed to save debug image to {output_path}")

    def wait_for(
        self,
        capture_fn: Callable[[], Frame],
        template: Template,
        roi: Rect | None = None,
        threshold: float | None = None,
        timeout_s: float = 5.0,
    ) -> Match | None:
        if timeout_s <= 0:
            raise VisionError("timeout_s must be > 0")

        deadline = time.monotonic() + float(timeout_s)
        poll_s = float(self.motion_stable_poll_ms) / 1000.0

        while True:
            frame = capture_fn()
            match = self.find(frame=frame, template=template, roi=roi, threshold=threshold)
            if match is not None:
                return match

            if time.monotonic() >= deadline:
                return None

            time.sleep(poll_s)

    def _prepare_motion_frame(self, frame: Frame, downscale_size: tuple[int, int]) -> Frame:
        self._validate_frame(frame)

        if (
            not isinstance(downscale_size, tuple)
            or len(downscale_size) != 2
            or int(downscale_size[0]) <= 0
            or int(downscale_size[1]) <= 0
        ):
            raise VisionError("downscale_size must be a tuple of two positive integers")

        gray = self._to_gray(frame)
        return cv2.resize(
            gray,
            (int(downscale_size[0]), int(downscale_size[1])),
            interpolation=cv2.INTER_AREA,
        )

    def _mean_absdiff(self, prev_gray: Frame, cur_gray: Frame) -> float:
        if prev_gray.shape != cur_gray.shape:
            raise VisionError("Frames for diff must have identical shapes")

        diff = cv2.absdiff(prev_gray, cur_gray)
        return float(np.mean(diff.astype(np.float32) / 255.0))

    def _to_gray(self, frame: Frame) -> Frame:
        self._validate_frame(frame)

        if frame.ndim == 2:
            return frame
        if frame.ndim == 3 and frame.shape[2] == 1:
            return frame[:, :, 0]
        if frame.ndim == 3 and frame.shape[2] == 3:
            return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if frame.ndim == 3 and frame.shape[2] == 4:
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)

        raise VisionError("Unsupported frame format for grayscale conversion")

    def _fit_rect_to_frame(self, rect: Rect, frame: Frame) -> Rect:
        self._validate_rect(rect)
        height, width = frame.shape[:2]

        if rect.x < 0 or rect.y < 0:
            raise VisionError("ROI coordinates must be non-negative")
        if rect.w <= 0 or rect.h <= 0:
            raise VisionError("ROI width and height must be positive")

        x = min(rect.x, width)
        y = min(rect.y, height)
        w = min(rect.w, width - x)
        h = min(rect.h, height - y)

        if w <= 0 or h <= 0:
            raise VisionError("ROI is outside frame bounds")

        return Rect(x=x, y=y, w=w, h=h)

    def _validate_frame(self, frame: Frame) -> None:
        if frame is None:
            raise VisionError("frame is None")
        if not isinstance(frame, np.ndarray):
            raise VisionError("frame must be numpy.ndarray")
        if frame.size == 0:
            raise VisionError("frame is empty")
        if frame.ndim not in (2, 3):
            raise VisionError("frame must have 2 or 3 dimensions")

    def _validate_rect(self, rect: Rect) -> None:
        if not isinstance(rect, Rect):
            raise VisionError("rect must be Rect")
        if rect.w <= 0 or rect.h <= 0:
            raise VisionError("rect width and height must be positive")

    def _validate_runtime_params(
        self,
        *,
        stable_frames: int,
        stable_diff_threshold: float,
        stable_poll_ms: int,
        stable_timeout_s: float,
        downscale_size: tuple[int, int],
    ) -> None:
        if int(stable_frames) <= 0:
            raise VisionError("stable_frames must be > 0")
        if float(stable_diff_threshold) < 0:
            raise VisionError("stable_diff_threshold must be >= 0")
        if int(stable_poll_ms) < 0:
            raise VisionError("stable_poll_ms must be >= 0")
        if float(stable_timeout_s) <= 0:
            raise VisionError("stable_timeout_s must be > 0")
        if (
            not isinstance(downscale_size, tuple)
            or len(downscale_size) != 2
            or int(downscale_size[0]) <= 0
            or int(downscale_size[1]) <= 0
        ):
            raise VisionError("downscale_size must contain two positive integers")
