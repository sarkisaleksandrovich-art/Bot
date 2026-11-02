from __future__ import annotations

import os
import time

import cv2
import numpy as np


class ScreenshotError(Exception):
    pass


class ScreenshotProvider:
    def __init__(self, adb, config, logger):
        self._adb = adb; self._logger = logger; raw = getattr(config, "raw", None); section = raw.get("capture") if isinstance(raw, dict) else None; section = section if isinstance(section, dict) else {}
        self._retry_count = int(section.get("retry_count", 1)); self._retry_delay_ms = int(section.get("retry_delay_ms", 100))

    def capture(self):
        attempts = max(1, self._retry_count + 1); last_error = None
        for attempt in range(attempts):
            try:
                data = self._adb.screencap_png(); frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None or frame.size == 0: raise ScreenshotError("не удалось декодировать PNG-скриншот")
                return frame
            except Exception as exc:
                last_error = exc; self._logger.warning("Скриншот, попытка %d из %d не удалась: %s", attempt + 1, attempts, exc)
                if attempt < attempts - 1: time.sleep(self._retry_delay_ms / 1000.0)
        raise ScreenshotError("не удалось получить скриншот: %s" % last_error) from last_error

    def save(self, frame, path):
        folder = os.path.dirname(path)
        if folder: os.makedirs(folder, exist_ok=True)
        if not cv2.imwrite(path, frame): raise ScreenshotError("не удалось сохранить скриншот: %s" % path)
        return path
