from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np


Frame = np.ndarray


class ScreenshotError(Exception):
    pass


class ScreenshotProvider:
    def __init__(self, adb: Any, config: Any, logger: Any) -> None:
        self.adb = adb
        self.config = config
        self.logger = logger

        raw = getattr(config, "raw", {}) if config is not None else {}
        capture_cfg = raw.get("capture", {}) if isinstance(raw, dict) else {}

        self._retry_count = int(capture_cfg.get("retry_count", 1) or 1)
        self._retry_delay_ms = int(capture_cfg.get("retry_delay_ms", 0) or 0)

        if self._retry_count < 1:
            self._retry_count = 1
        if self._retry_delay_ms < 0:
            self._retry_delay_ms = 0

    def capture(self) -> Frame:
        last_error: Exception | None = None

        for attempt in range(1, self._retry_count + 1):
            try:
                png_bytes = self.adb.screencap_png()
                buffer = np.frombuffer(png_bytes, dtype=np.uint8)
                frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
                if frame is None or frame.size == 0:
                    raise ScreenshotError("не удалось декодировать PNG-скриншот")
                return frame
            except Exception as exc:
                last_error = exc
                if attempt < self._retry_count and self._retry_delay_ms > 0:
                    time.sleep(self._retry_delay_ms / 1000.0)

        message = f"не удалось снять скриншот после {self._retry_count} попыток"
        if last_error is not None:
            raise ScreenshotError(f"{message}: {last_error}") from last_error
        raise ScreenshotError(message)

    def save(self, frame: Frame, path: str) -> None:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ok = cv2.imwrite(str(output_path), frame)
        if not ok:
            raise ScreenshotError(f"не удалось сохранить скриншот: {output_path}")
