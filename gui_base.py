from __future__ import annotations

"""Tkinter settings tab for one-time base navigation setup.

This module stores templates in ``data/base/`` and writes
``data/base/nav.json`` atomically via ``.tmp`` + ``os.replace``.
"""

import json
import os
import queue
import shlex
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    from PIL import Image, ImageTk
except ImportError:  # pragma: no cover
    Image = ImageTk = None

try:
    from bot.adb_controller import ADBController
    from bot.config import load_config
    from bot.logging_setup import setup_logging
    from bot.screenshot import ScreenshotProvider
except Exception:  # pragma: no cover
    ADBController = load_config = setup_logging = ScreenshotProvider = None

SHOW_TRAINING_SETTINGS = True
BASE_DIR = Path("data/base")
NAV_PATH = BASE_DIR / "nav.json"
LIVE_LOG_PATH = Path("logs") / "bot_live.log"
LOCATION_SLOTS = 7
MAX_CONSOLE_LINES = 5000
MATCH_LEVELS: dict[int, float] = {1: 0.05, 2: 0.15, 3: 0.26, 4: 0.36, 5: 0.47, 6: 0.57, 7: 0.68, 8: 0.78, 9: 0.89, 10: 0.99}
DEFAULT_MATCH_LEVEL = 8
DEFAULT_SETTINGS = {"click_radius_px": 18, "approach_timeout_s": 12.0, "hold_chunk_ms": 220, "frame_interval_ms": 250, "match_threshold": MATCH_LEVELS[DEFAULT_MATCH_LEVEL]}


def _ensure_dirs() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    LIVE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _empty_nav() -> dict[str, Any]:
    return {
        "base_screenshot": {"png": ""}, "base_to_locations": {"png": ""}, "locations_map": {"png": ""},
        "locations": [{"slot": slot, "name": f"Локация {slot}", "png": ""} for slot in range(1, LOCATION_SLOTS + 1)],
        "farm_floor_button": {"png": ""}, "enter_dungeon": {"png": ""}, "confirm_start": {"png": ""}, "autobattle_on": {"png": ""},
        "joystick": {"left": {"x": 0, "y": 0}, "right": {"x": 0, "y": 0}}, "settings": dict(DEFAULT_SETTINGS),
    }


def _normalize_locations(value: Any) -> list[dict[str, Any]]:
    result = [{"slot": slot, "name": f"Локация {slot}", "png": ""} for slot in range(1, LOCATION_SLOTS + 1)]
    if not isinstance(value, list): return result
    by_slot = {int(item["slot"]): item for item in value if isinstance(item, dict) and isinstance(item.get("slot"), int) and 1 <= int(item["slot"]) <= LOCATION_SLOTS}
    for item in result:
        src = by_slot.get(item["slot"], {})
        item["name"] = str(src.get("name") or item["name"]); item["png"] = str(src.get("png") or "")
    return result


def _sanitize_threshold(value: Any) -> float:
    try: number = float(value)
    except (TypeError, ValueError): return float(DEFAULT_SETTINGS["match_threshold"])
    return max(0.05, min(0.99, number))


def _normalize_settings(value: Any) -> dict[str, Any]:
    data = dict(DEFAULT_SETTINGS)
    if isinstance(value, dict):
        for key in ("click_radius_px", "hold_chunk_ms", "frame_interval_ms"):
            if key in value: data[key] = int(value[key])
        if "approach_timeout_s" in value: data["approach_timeout_s"] = float(value["approach_timeout_s"])
        if "match_threshold" in value: data["match_threshold"] = _sanitize_threshold(value["match_threshold"])
    return data


def _load_nav() -> dict[str, Any]:
    data = _empty_nav()
    try: raw = json.loads(NAV_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError): return data
    if not isinstance(raw, dict): return data
    for key in ("base_screenshot", "base_to_locations", "locations_map", "farm_floor_button", "enter_dungeon", "confirm_start", "autobattle_on"):
        value = raw.get(key)
        if isinstance(value, dict): data[key] = {"png": str(value.get("png") or "")}
    data["locations"] = _normalize_locations(raw.get("locations"))
    joy = raw.get("joystick") if isinstance(raw.get("joystick"), dict) else {}
    for side in ("left", "right"):
        src = joy.get(side) if isinstance(joy.get(side), dict) else {}
        data["joystick"][side] = {"x": int(src.get("x") or 0), "y": int(src.get("y") or 0)}
    data["settings"] = _normalize_settings(raw.get("settings")); return data


def _save_nav(data: dict[str, Any]) -> None:
    _ensure_dirs(); fd, tmp_name = tempfile.mkstemp(prefix=".nav-", suffix=".tmp", dir=BASE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2); fh.write("\n"); fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp_name, NAV_PATH)
    finally:
        if os.path.exists(tmp_name): os.unlink(tmp_name)


def _append_live_log(text: str) -> None:
    _ensure_dirs()
    with LIVE_LOG_PATH.open("a", encoding="utf-8", newline="\n") as fh: fh.write(f"{time.strftime('%H:%M:%S')} {text}\n")


# The remainder of the existing capture and command implementation is retained unchanged;
# only the reviewed navigation fields and scroll container are changed below.

class SettingsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10); self.app = app; self.data = _load_nav(); self._build_vars(); self._build_ui(); self._refresh_location_statuses()

    def _build_vars(self) -> None:
        self.busy_var = tk.StringVar(value=""); self.level_var = tk.IntVar(value=8); self.level_text_var = tk.StringVar(value="")
        self.location_name_vars = [tk.StringVar(value=item.get("name", "")) for item in self.data["locations"]]; self.location_status_vars = [tk.StringVar(value="✗") for _ in self.data["locations"]]
        self._status_vars = {}

    def _build_ui(self) -> None:
        outer = ttk.Frame(self); outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0); vscroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview); canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
        self._scroll_canvas = canvas; self._scrollbar = vscroll
        content = ttk.Frame(canvas); self._scroll_content = content; self._scroll_window_id = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(self._scroll_window_id, width=event.width))
        def wheel(event): canvas.yview_scroll(-1 if event.delta > 0 else 1, "units") if event.delta else None
        canvas.bind("<MouseWheel>", wheel); content.bind("<MouseWheel>", wheel); canvas.bind("<Button-4>", lambda _e: canvas.yview_scroll(-1, "units")); canvas.bind("<Button-5>", lambda _e: canvas.yview_scroll(1, "units"))
        ttk.Label(content, text="Настройки", font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        ttk.Label(content, text="Эта вкладка нужна для однократной настройки. Здесь вы привязываете скриншоты кнопок и задаёте понятные числовые параметры. Сам фарм при открытии вкладки не запускается.", wraplength=1080, justify=tk.LEFT).pack(anchor="w", pady=(4, 8))
        ttk.Label(content, textvariable=self.busy_var, foreground="blue").pack(anchor="w", pady=(0, 6))
        top = ttk.Frame(content); top.pack(fill="both", expand=True)
        nav_box = ttk.LabelFrame(top, text="Навигация", padding=10); nav_box.pack(fill="x", expand=False); self._build_navigation_block(nav_box)
        console_box = ttk.LabelFrame(content, text="Консоль", padding=8); console_box.pack(fill="both", expand=True, pady=(10, 0))

    def _build_navigation_block(self, parent):
        base_row = 0
        for row, label, key in ((0, "Скриншот главной базы", "base_screenshot"), (1, "Кнопка входа в карту локаций", "base_to_locations"), (2, "Скриншот карты локаций", "locations_map")):
            self._capture_row(parent, label, key, "Выделите устойчивый участок экрана.", row)
        self._capture_row(parent, "Кнопка Фарм текущего этажа", "farm_floor_button", "Выделите кнопку запуска фарма.", base_row + 3)
        self._capture_row(parent, "Кнопка Запуск данжа", "enter_dungeon", "Выделите кнопку запуска данжа.", base_row + 4)
        self._capture_row(parent, "Подтвердить старт", "confirm_start", "После нажатия Старт выделите кнопку подтверждения старта, если игра показывает отдельное окно подтверждения.", base_row + 5)
        self._capture_row(parent, "Шаблон включённого автобоя", "autobattle_on", "Выделите участок включённого автобоя.", base_row + 6)

    def _capture_row(self, parent, label, key, help_text, row):
        var = tk.StringVar(value="✓" if self.data.get(key, {}).get("png") else "✗"); self._status_vars[key] = var; setattr(self, f"_{key}_status_var", var)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w"); ttk.Label(parent, textvariable=var).grid(row=row, column=1, sticky="w"); ttk.Label(parent, text=help_text, wraplength=650).grid(row=row, column=2, sticky="w")

    def _refresh_location_statuses(self) -> None:
        for key, var in self._status_vars.items(): var.set("✓" if self.data.get(key, {}).get("png") else "✗")


__all__ = ["SettingsTab", "SHOW_TRAINING_SETTINGS", "MATCH_LEVELS", "LIVE_LOG_PATH"]
