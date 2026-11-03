from __future__ import annotations

"""Tkinter settings tab for one-time base navigation setup.

This module stores templates in ``data/base/`` and writes
``data/base/nav.json`` atomically via ``.tmp`` + ``os.replace``.

nav.json structure::

    {
      "base_screenshot": {"png": "base_screenshot_123.png"},
      "base_to_locations": {"png": "base_to_locations_123.png"},
      "locations_map": {"png": "locations_map_123.png"},
      "locations": [
        {"slot": 1, "name": "Локация 1", "png": "location_1_123.png"},
        {"slot": 2, "name": "Локация 2", "png": "location_2_123.png"},
        {"slot": 3, "name": "Локация 3", "png": "location_3_123.png"},
        {"slot": 4, "name": "Локация 4", "png": "location_4_123.png"},
        {"slot": 5, "name": "Локация 5", "png": "location_5_123.png"},
        {"slot": 6, "name": "Локация 6", "png": "location_6_123.png"},
        {"slot": 7, "name": "Локация 7", "png": "location_7_123.png"}
      ],
      "farm_floor_button": {"png": "farm_floor_button_123.png"},
      "enter_dungeon": {"png": "enter_dungeon_123.png"},
      "autobattle_on": {"png": "autobattle_on_123.png"},
      "joystick": {
        "left": {"x": 100, "y": 500},
        "right": {"x": 260, "y": 500}
      },
      "settings": {
        "click_radius_px": 18,
        "approach_timeout_s": 12.0,
        "hold_chunk_ms": 220,
        "frame_interval_ms": 250,
        "match_threshold": 0.78
      }
    }

All PNG file names in JSON are relative to ``data/base/``.
The file keeps ``settings.match_threshold`` as a float in range 0.05..0.99.

Exports:
- ``capture_region(parent, prompt)`` -> ``(crop_bgr, bbox)`` or ``None``
- ``SettingsTab(ttk.Frame)`` with constructor ``(parent, app)``
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
MATCH_LEVELS: dict[int, float] = {
    1: 0.05,
    2: 0.15,
    3: 0.26,
    4: 0.36,
    5: 0.47,
    6: 0.57,
    7: 0.68,
    8: 0.78,
    9: 0.89,
    10: 0.99,
}
DEFAULT_MATCH_LEVEL = 8
DEFAULT_SETTINGS = {
    "click_radius_px": 18,
    "approach_timeout_s": 12.0,
    "hold_chunk_ms": 220,
    "frame_interval_ms": 250,
    "match_threshold": MATCH_LEVELS[DEFAULT_MATCH_LEVEL],
}


def _ensure_dirs() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    LIVE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)



def _empty_nav() -> dict[str, Any]:
    return {
        "base_screenshot": {"png": ""},
        "base_to_locations": {"png": ""},
        "locations_map": {"png": ""},
        "locations": [
            {"slot": slot, "name": f"Локация {slot}", "png": ""}
            for slot in range(1, LOCATION_SLOTS + 1)
        ],
        "farm_floor_button": {"png": ""},
        "enter_dungeon": {"png": ""},
        "autobattle_on": {"png": ""},
        "joystick": {
            "left": {"x": 0, "y": 0},
            "right": {"x": 0, "y": 0},
        },
        "settings": dict(DEFAULT_SETTINGS),
    }



def _normalize_locations(value: Any) -> list[dict[str, Any]]:
    result = [
        {"slot": slot, "name": f"Локация {slot}", "png": ""}
        for slot in range(1, LOCATION_SLOTS + 1)
    ]
    if not isinstance(value, list):
        return result
    by_slot: dict[int, dict[str, Any]] = {}
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("slot"), int):
            slot = int(item["slot"])
            if 1 <= slot <= LOCATION_SLOTS:
                by_slot[slot] = item
    for item in result:
        src = by_slot.get(item["slot"], {})
        item["name"] = str(src.get("name") or item["name"])
        item["png"] = str(src.get("png") or "")
    return result



def _sanitize_threshold(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(DEFAULT_SETTINGS["match_threshold"])
    return max(0.05, min(0.99, number))



def _normalize_settings(value: Any) -> dict[str, Any]:
    data = dict(DEFAULT_SETTINGS)
    if isinstance(value, dict):
        if "click_radius_px" in value:
            data["click_radius_px"] = int(value["click_radius_px"])
        if "approach_timeout_s" in value:
            data["approach_timeout_s"] = float(value["approach_timeout_s"])
        if "hold_chunk_ms" in value:
            data["hold_chunk_ms"] = int(value["hold_chunk_ms"])
        if "frame_interval_ms" in value:
            data["frame_interval_ms"] = int(value["frame_interval_ms"])
        if "match_threshold" in value:
            data["match_threshold"] = _sanitize_threshold(value["match_threshold"])
    return data



def _load_nav() -> dict[str, Any]:
    data = _empty_nav()
    try:
        raw = json.loads(NAV_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return data
    if not isinstance(raw, dict):
        return data
    for key in (
        "base_screenshot",
        "base_to_locations",
        "locations_map",
        "farm_floor_button",
        "enter_dungeon",
        "autobattle_on",
    ):
        value = raw.get(key)
        if isinstance(value, dict):
            data[key] = {"png": str(value.get("png") or "")}
    data["locations"] = _normalize_locations(raw.get("locations"))
    joy = raw.get("joystick") if isinstance(raw.get("joystick"), dict) else {}
    for side in ("left", "right"):
        src = joy.get(side) if isinstance(joy.get(side), dict) else {}
        data["joystick"][side] = {
            "x": int(src.get("x") or 0),
            "y": int(src.get("y") or 0),
        }
    data["settings"] = _normalize_settings(raw.get("settings"))
    return data



def _save_nav(data: dict[str, Any]) -> None:
    _ensure_dirs()
    fd, tmp_name = tempfile.mkstemp(prefix=".nav-", suffix=".tmp", dir=BASE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, NAV_PATH)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)



def _append_live_log(text: str) -> None:
    _ensure_dirs()
    ts = time.strftime("%H:%M:%S")
    with LIVE_LOG_PATH.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{ts} {text}\n")



def _write_png(crop_bgr, prefix: str) -> str:
    _ensure_dirs()
    path = BASE_DIR / f"{prefix}_{time.time_ns()}.png"
    if cv2 is None or not cv2.imwrite(str(path), crop_bgr):
        raise RuntimeError("Не удалось сохранить PNG.")
    return path.name



def _make_provider() -> Any:
    if not all((load_config, setup_logging, ADBController, ScreenshotProvider)):
        raise RuntimeError("Не удалось загрузить ScreenshotProvider.")
    config = load_config("config.yaml")
    logger = setup_logging(config)
    adb = ADBController(config, logger)
    adb.connect()
    return ScreenshotProvider(adb, config, logger)



def _nearest_match_level(threshold: float) -> int:
    threshold = _sanitize_threshold(threshold)
    return min(MATCH_LEVELS, key=lambda level: abs(MATCH_LEVELS[level] - threshold))



def _threshold_from_user(value: str) -> float:
    number = float(value)
    if number > 1.0:
        number = number / 100.0
    if not (0.05 <= number <= 0.99):
        raise ValueError("match_threshold должен быть от 0.05 до 0.99 или от 5 до 99 как проценты.")
    return round(number, 2)


def capture_region(parent, prompt):
    """Capture device screen in a worker thread and let user select a region.

    Returns ``(crop_bgr, (left, top, right, bottom))`` or ``None``.
    The screenshot is taken in a background thread, then a ``Toplevel`` window
    is shown on the main Tk thread. Rectangle selection uses mouse drag.
    """
    if cv2 is None or Image is None or ImageTk is None:
        messagebox.showerror("Скриншот", "Нужны OpenCV и Pillow.", parent=parent)
        return None

    owner = parent.winfo_toplevel()
    state: dict[str, Any] = {"done": False, "frame": None, "error": None, "result": None}

    wait_win = tk.Toplevel(parent)
    wait_win.title("Скриншот")
    wait_win.transient(owner)
    wait_win.grab_set()
    ttk.Label(wait_win, text="Делаю скриншот устройства, подождите...", padding=12).pack()
    wait_win.resizable(False, False)

    def worker() -> None:
        try:
            state["frame"] = _make_provider().capture()
        except Exception as exc:  # pragma: no cover
            state["error"] = exc
        finally:
            state["done"] = True

    threading.Thread(target=worker, daemon=True).start()

    def poll() -> None:
        if not wait_win.winfo_exists():
            return
        if not state["done"]:
            wait_win.after(100, poll)
            return
        wait_win.destroy()
        if state["error"] is not None:
            messagebox.showerror("Скриншот", str(state["error"]), parent=parent)
            return
        frame = state["frame"]
        if frame is None or getattr(frame, "size", 0) == 0:
            messagebox.showerror("Скриншот", "Не удалось получить изображение.", parent=parent)
            return
        _open_crop_window(parent, prompt, frame, state)

    wait_win.after(100, poll)
    owner.wait_window(wait_win)
    if state["result"] is None and state.get("selector") is not None:
        owner.wait_window(state["selector"])
    return state["result"]



def _open_crop_window(parent, prompt: str, frame, state: dict[str, Any]) -> None:
    src_h, src_w = frame.shape[:2]
    scale = min(1.0, 1280 / src_w, 820 / src_h)
    shown = cv2.resize(frame, (int(src_w * scale), int(src_h * scale))) if scale != 1.0 else frame
    shown_h, shown_w = shown.shape[:2]
    rgb = cv2.cvtColor(shown, cv2.COLOR_BGR2RGB)
    photo = ImageTk.PhotoImage(Image.fromarray(rgb))

    win = tk.Toplevel(parent)
    state["selector"] = win
    win.title(str(prompt))
    win.transient(parent.winfo_toplevel())

    ttk.Label(win, text=str(prompt), wraplength=1000, justify=tk.LEFT).pack(anchor="w", padx=8, pady=(8, 4))
    canvas = tk.Canvas(win, width=shown_w, height=shown_h, cursor="crosshair")
    canvas.pack(padx=8, pady=4)
    canvas.create_image(0, 0, image=photo, anchor="nw")
    canvas.image = photo

    box_state: dict[str, Any] = {"start": None, "rect": None}

    def on_down(event) -> None:
        x = max(0, min(event.x, shown_w))
        y = max(0, min(event.y, shown_h))
        box_state["start"] = (x, y)
        if box_state["rect"]:
            canvas.delete(box_state["rect"])
        box_state["rect"] = canvas.create_rectangle(x, y, x, y, outline="red", width=2)

    def on_drag(event) -> None:
        if not box_state["start"] or not box_state["rect"]:
            return
        x = max(0, min(event.x, shown_w))
        y = max(0, min(event.y, shown_h))
        canvas.coords(box_state["rect"], box_state["start"][0], box_state["start"][1], x, y)

    def on_save() -> None:
        if not box_state["rect"]:
            messagebox.showwarning("Выделение", "Сначала выделите область мышью.", parent=win)
            return
        x1, y1, x2, y2 = canvas.coords(box_state["rect"])
        left = max(0, min(src_w, int(min(x1, x2) / scale)))
        right = max(0, min(src_w, int(max(x1, x2) / scale)))
        top = max(0, min(src_h, int(min(y1, y2) / scale)))
        bottom = max(0, min(src_h, int(max(y1, y2) / scale)))
        if right <= left or bottom <= top:
            messagebox.showwarning("Выделение", "Слишком маленькая область.", parent=win)
            return
        state["result"] = (frame[top:bottom, left:right].copy(), (left, top, right, bottom))
        win.destroy()

    canvas.bind("<ButtonPress-1>", on_down)
    canvas.bind("<B1-Motion>", on_drag)

    buttons = ttk.Frame(win)
    buttons.pack(fill="x", padx=8, pady=(0, 8))
    ttk.Button(buttons, text="Сохранить", command=on_save).pack(side="left")
    ttk.Button(buttons, text="Отмена", command=win.destroy).pack(side="right")


class SettingsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.data = _load_nav()
        self._capture_busy = False
        self._console_history: list[str] = []
        self._console_history_index = 0
        self._tail_buffer: deque[str] = deque(maxlen=MAX_CONSOLE_LINES)
        self._console_queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._build_vars()
        self._build_ui()
        self._build_command_registry()
        self._refresh_location_statuses()
        self._refresh_level_text_only()
        self._poll_app_log_queue()

    def _build_vars(self) -> None:
        self.busy_var = tk.StringVar(value="")
        self.level_var = tk.IntVar(value=_nearest_match_level(self.data["settings"].get("match_threshold", MATCH_LEVELS[DEFAULT_MATCH_LEVEL])))
        self.level_text_var = tk.StringVar(value="")
        self.location_name_vars = [tk.StringVar(value=item.get("name", "")) for item in self.data["locations"]]
        self.location_status_vars = [tk.StringVar(value="✗") for _ in self.data["locations"]]
        joy = self.data["joystick"]
        self.left_x_var = tk.StringVar(value=str(joy["left"].get("x", 0)))
        self.left_y_var = tk.StringVar(value=str(joy["left"].get("y", 0)))
        self.right_x_var = tk.StringVar(value=str(joy["right"].get("x", 0)))
        self.right_y_var = tk.StringVar(value=str(joy["right"].get("y", 0)))
        settings = self.data["settings"]
        self.click_radius_var = tk.StringVar(value=str(settings.get("click_radius_px", DEFAULT_SETTINGS["click_radius_px"])))
        self.approach_timeout_var = tk.StringVar(value=str(settings.get("approach_timeout_s", DEFAULT_SETTINGS["approach_timeout_s"])))
        self.hold_chunk_var = tk.StringVar(value=str(settings.get("hold_chunk_ms", DEFAULT_SETTINGS["hold_chunk_ms"])))
        self.frame_interval_var = tk.StringVar(value=str(settings.get("frame_interval_ms", DEFAULT_SETTINGS["frame_interval_ms"])))

    def _build_ui(self) -> None:
        # Прокручиваемый контейнер: Canvas + вертикальный ползунок,
        # чтобы вкладка "Настройки" помещалась на небольших экранах.
        canvas = tk.Canvas(self, highlightthickness=0)
        vscroll = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw", tags="inner")

        def _on_inner_configure(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event) -> None:
            canvas.itemconfigure("inner", width=event.width)

        def _on_mousewheel(event) -> None:
            canvas.yview_scroll(-1 * (event.delta // 120), "units")

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        ttk.Label(inner, text="Настройки", font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        ttk.Label(
            inner,
            text="Эта вкладка нужна для однократной настройки. Здесь вы привязываете скриншоты кнопок и задаёте понятные числовые параметры. Сам фарм при открытии вкладки не запускается.",
            wraplength=1080,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(4, 8))
        ttk.Label(inner, textvariable=self.busy_var, foreground="blue").pack(anchor="w", pady=(0, 6))

        top = ttk.Frame(inner)
        top.pack(fill="x", expand=False)

        nav_box = ttk.LabelFrame(top, text="Навигация", padding=10)
        nav_box.pack(fill="x", expand=False)
        self._build_navigation_block(nav_box)

        if SHOW_TRAINING_SETTINGS:
            training_box = ttk.LabelFrame(top, text="Обучение", padding=10)
            training_box.pack(fill="x", pady=(10, 0))
            self._build_training_block(training_box)

        console_box = ttk.LabelFrame(inner, text="Консоль", padding=8)
        console_box.pack(fill="both", expand=True, pady=(10, 0))
        self._build_console(console_box)

    def _build_navigation_block(self, parent: ttk.LabelFrame) -> None:
        self._capture_row(parent, "Скриншот главной базы", "base_screenshot", "Сделайте скриншот главной базы и выделите характерный участок экрана.", 0)
        self._capture_row(parent, "Кнопка входа в карту локаций", "base_to_locations", "На главной базе выделите кнопку перехода в карту локаций.", 1)
        self._capture_row(parent, "Скриншот карты локаций", "locations_map", "Откройте карту локаций и выделите устойчивый участок экрана карты.", 2)

        ttk.Label(parent, text="7 локаций. Название можно менять отдельно от скриншота.", wraplength=950, justify=tk.LEFT).grid(row=3, column=0, columnspan=5, sticky="w", pady=(8, 4))
        for idx in range(LOCATION_SLOTS):
            row = 4 + idx
            ttk.Label(parent, text=f"Локация {idx + 1}").grid(row=row, column=0, sticky="w", padx=(0, 6), pady=2)
            ttk.Entry(parent, textvariable=self.location_name_vars[idx], width=28).grid(row=row, column=1, sticky="we", padx=(0, 6), pady=2)
            ttk.Button(parent, text="Привязать к скриншоту", command=lambda i=idx: self._bind_location(i)).grid(row=row, column=2, sticky="w", padx=(0, 6), pady=2)
            ttk.Label(parent, textvariable=self.location_status_vars[idx], width=2).grid(row=row, column=3, sticky="w", pady=2)
            ttk.Button(parent, text="Сохранить имя", command=lambda i=idx: self._save_location_name(i)).grid(row=row, column=4, sticky="w", pady=2)

        base_row = 4 + LOCATION_SLOTS
        self._capture_row(parent, "Кнопка Фарм текущего этажа", "farm_floor_button", "Откройте экран нужного этажа и выделите кнопку, которая запускает фарм текущего этажа.", base_row)

        start_row = base_row + 1
        ttk.Label(parent, text="Кнопка старт").grid(row=start_row, column=0, sticky="w", padx=(0, 6), pady=2)
        ttk.Button(parent, text="Сделать скриншот", command=self._screenshot_button_clicked).grid(row=start_row, column=1, sticky="w", padx=(0, 6), pady=2)
        ttk.Label(parent, text="Снимает весь экран устройства через ADB и сохраняет PNG в папку logs/, путь появится в консоли.", wraplength=650, justify=tk.LEFT).grid(row=start_row, column=3, columnspan=2, sticky="w", pady=2)

        self._capture_row(parent, "Кнопка Запуск данжа", "enter_dungeon", "Выделите кнопку запуска данжа после выбора этажа.", base_row + 2)
        self._capture_row(parent, "Шаблон включённого автобоя", "autobattle_on", "Когда автобой уже включён, выделите его заметный участок, чтобы бот понимал, что режим активен.", base_row + 3)

        joy_row = base_row + 4
        joy_frame = ttk.LabelFrame(parent, text="Края джойстика", padding=8)
        joy_frame.grid(row=joy_row, column=0, columnspan=5, sticky="we", pady=(8, 0))
        ttk.Label(joy_frame, text="Нужно указать две точки: левый и правый край джойстика. Это не прямоугольник, а просто координаты точек на экране.", wraplength=950, justify=tk.LEFT).grid(row=0, column=0, columnspan=10, sticky="w", pady=(0, 6))
        ttk.Button(joy_frame, text="Выбрать точки на скриншоте", command=self._capture_joystick_points).grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Label(joy_frame, text="Левый x").grid(row=1, column=1, sticky="e")
        ttk.Entry(joy_frame, textvariable=self.left_x_var, width=8).grid(row=1, column=2, sticky="w", padx=(4, 8))
        ttk.Label(joy_frame, text="Левый y").grid(row=1, column=3, sticky="e")
        ttk.Entry(joy_frame, textvariable=self.left_y_var, width=8).grid(row=1, column=4, sticky="w", padx=(4, 8))
        ttk.Label(joy_frame, text="Правый x").grid(row=1, column=5, sticky="e")
        ttk.Entry(joy_frame, textvariable=self.right_x_var, width=8).grid(row=1, column=6, sticky="w", padx=(4, 8))
        ttk.Label(joy_frame, text="Правый y").grid(row=1, column=7, sticky="e")
        ttk.Entry(joy_frame, textvariable=self.right_y_var, width=8).grid(row=1, column=8, sticky="w", padx=(4, 8))
        ttk.Button(joy_frame, text="Сохранить координаты", command=self._save_joystick_entries).grid(row=1, column=9, sticky="w", padx=(8, 0))
        ttk.Label(joy_frame, text="Если точки стоят неверно, движение может быть слишком коротким или слишком длинным.", wraplength=950, justify=tk.LEFT).grid(row=2, column=0, columnspan=10, sticky="w", pady=(6, 0))

        ttk.Button(parent, text="Проверить цепочку", command=self._verify_chain_clicked).grid(row=joy_row + 1, column=0, sticky="w", pady=(10, 0))
        parent.columnconfigure(1, weight=1)

    def _capture_row(self, parent, label: str, key: str, help_text: str, row: int) -> None:
        status_var = tk.StringVar(value="✓" if self.data.get(key, {}).get("png") else "✗")
        setattr(self, f"_{key}_status_var", status_var)
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=2)
        ttk.Button(parent, text="Сделать скриншот", command=lambda k=key, p=help_text: self._capture_single_template(k, p)).grid(row=row, column=1, sticky="w", padx=(0, 6), pady=2)
        ttk.Label(parent, textvariable=status_var, width=2).grid(row=row, column=2, sticky="w", pady=2)
        ttk.Label(parent, text=help_text, wraplength=650, justify=tk.LEFT).grid(row=row, column=3, columnspan=2, sticky="w", pady=2)

    def _build_training_block(self, parent: ttk.LabelFrame) -> None:
        self._number_row(parent, 0, "Радиус нажатия", self.click_radius_var, "click_radius_px", "Насколько далеко от точной точки бот может нажимать. Больше число — шире допустимая зона.")
        self._number_row(parent, 1, "Время на подход, сек", self.approach_timeout_var, "approach_timeout_s", "Сколько секунд бот ждёт, пока персонаж дойдёт до цели, прежде чем считать, что что-то пошло не так.")
        self._number_row(parent, 2, "Длина шага удержания, мс", self.hold_chunk_var, "hold_chunk_ms", "Сколько миллисекунд держать движение за один шаг. Слишком мало — медленно, слишком много — грубо.")
        self._number_row(parent, 3, "Проверка кадра, мс", self.frame_interval_var, "frame_interval_ms", "Как часто бот перепроверяет картинку. Меньше — быстрее реакция, больше — меньше нагрузка.")

        ttk.Label(parent, text="Чувствительность распознавания").grid(row=4, column=0, sticky="w", pady=(8, 2))
        scale = tk.Scale(parent, from_=1, to=10, orient=tk.HORIZONTAL, showvalue=False, variable=self.level_var, resolution=1, command=lambda _value: self._on_level_changed_preview())
        scale.grid(row=4, column=1, sticky="we", padx=(0, 8), pady=(8, 2))
        ttk.Label(parent, textvariable=self.level_text_var, wraplength=420, justify=tk.LEFT).grid(row=4, column=2, sticky="w", pady=(8, 2))
        ttk.Button(parent, text="Сохранить уровень", command=self._save_level).grid(row=4, column=3, sticky="w", pady=(8, 2))
        ttk.Label(
            parent,
            text="Чем выше уровень, тем строже бот сравнивает картинку: реже путает объекты, но может не узнать знакомые. На низких уровнях 1–4 бот может принимать чужие объекты за знакомые — это подходит для проверки, а не для фарма. Если бот не узнаёт нужное, снижайте уровень на 1. Если путает объекты, повышайте уровень.",
            wraplength=980,
            justify=tk.LEFT,
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(4, 0))
        parent.columnconfigure(1, weight=1)

    def _number_row(self, parent, row: int, label: str, var: tk.StringVar, key: str, help_text: str) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2, padx=(0, 8))
        ttk.Entry(parent, textvariable=var, width=12).grid(row=row, column=1, sticky="w", pady=2)
        ttk.Button(parent, text="Сохранить", command=lambda k=key: self._save_numeric_field(k)).grid(row=row, column=2, sticky="w", padx=(8, 8), pady=2)
        ttk.Label(parent, text=help_text, wraplength=720, justify=tk.LEFT).grid(row=row, column=3, sticky="w", pady=2)

    def _build_console(self, parent: ttk.LabelFrame) -> None:
        text_wrap = ttk.Frame(parent)
        text_wrap.pack(fill="both", expand=True)
        self.console_text = tk.Text(text_wrap, wrap=tk.WORD, state=tk.DISABLED, height=16)
        scrollbar = ttk.Scrollbar(text_wrap, orient=tk.VERTICAL, command=self.console_text.yview)
        self.console_text.configure(yscrollcommand=scrollbar.set)
        self.console_text.pack(side=tk.LEFT, fill="both", expand=True)
        scrollbar.pack(side=tk.RIGHT, fill="y")

        entry_row = ttk.Frame(parent)
        entry_row.pack(fill="x", pady=(8, 0))
        self.console_entry = ttk.Entry(entry_row)
        self.console_entry.pack(side="left", fill="x", expand=True)
        self.console_entry.bind("<Return>", self._on_console_execute)
        self.console_entry.bind("<Up>", self._history_up)
        self.console_entry.bind("<Down>", self._history_down)
        ttk.Button(entry_row, text="Выполнить", command=self._on_console_execute).pack(side="left", padx=(8, 0))

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="Очистить", command=self._console_clear).pack(side="left")
        ttk.Button(buttons, text="Копировать всё", command=self._console_copy_all).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Сохранить в файл", command=self._console_save_to_file).pack(side="left", padx=(8, 0))
        ttk.Label(buttons, text="Команды: help, status, settings, set, tail, screenshot, find, hold, release, stop, loot, mob, event, go, memory", wraplength=680, justify=tk.LEFT).pack(side="right")

    def _build_command_registry(self) -> None:
        self.commands: dict[str, Callable[[list[str]], Any]] = {
            "help": self._cmd_help,
            "status": self._cmd_status,
            "settings": self._cmd_settings,
            "set": self._cmd_set,
            "tail": self._cmd_tail,
            "screenshot": self._cmd_screenshot,
            "find": self._cmd_find,
            "hold": self._cmd_hold,
            "release": self._cmd_release,
            "stop": self._cmd_stop,
            "loot": self._cmd_loot,
            "mob": self._cmd_mob,
            "event": self._cmd_event,
            "go": self._cmd_go,
            "memory": self._cmd_memory,
        }

    def _refresh_location_statuses(self) -> None:
        for idx, item in enumerate(self.data["locations"]):
            self.location_status_vars[idx].set("✓" if item.get("png") else "✗")
        for key in ("base_screenshot", "base_to_locations", "locations_map", "farm_floor_button", "enter_dungeon", "autobattle_on"):
            getattr(self, f"_{key}_status_var").set("✓" if self.data.get(key, {}).get("png") else "✗")

    def _refresh_level_text_only(self) -> None:
        level = int(round(self.level_var.get()))
        level = max(1, min(10, level))
        self.level_var.set(level)
        threshold = MATCH_LEVELS[level]
        self.level_text_var.set(f"Уровень {level} из 10 (порог {threshold:.2f})")

    def _on_level_changed_preview(self) -> None:
        self._refresh_level_text_only()

    def _save_level(self) -> None:
        level = int(self.level_var.get())
        self.data["settings"]["match_threshold"] = MATCH_LEVELS[level]
        _save_nav(self.data)
        self._log_console(f"Сохранён уровень распознавания {level} из 10 (порог {MATCH_LEVELS[level]:.2f}).")
        self._refresh_level_text_only()

    def _save_numeric_field(self, key: str) -> None:
        mapping = {
            "click_radius_px": self.click_radius_var,
            "approach_timeout_s": self.approach_timeout_var,
            "hold_chunk_ms": self.hold_chunk_var,
            "frame_interval_ms": self.frame_interval_var,
        }
        var = mapping[key]
        try:
            if key == "approach_timeout_s":
                value: Any = float(var.get().strip())
            else:
                value = int(var.get().strip())
        except ValueError:
            messagebox.showerror("Настройки", "Введите число в поле настроек.", parent=self)
            return
        self.data["settings"][key] = value
        _save_nav(self.data)
        self._log_console(f"Сохранено {key} = {value}")

    def _capture_single_template(self, key: str, prompt: str) -> None:
        if self._capture_busy:
            messagebox.showinfo("Скриншот", "Уже выполняется другое действие со скриншотом.", parent=self)
            return
        self._capture_busy = True
        self.busy_var.set("Идёт захват скриншота...")
        self.after(0, lambda: self._capture_single_template_ui(key, prompt))

    def _capture_single_template_ui(self, key: str, prompt: str) -> None:
        try:
            result = capture_region(self, prompt)
            error = None
        except Exception as exc:
            result = None
            error = exc
        self._after_template_capture(key, result, error)

    def _after_template_capture(self, key: str, result, error: Exception | None) -> None:
        self._capture_busy = False
        self.busy_var.set("")
        if error is not None:
            messagebox.showerror("Скриншот", str(error), parent=self)
            return
        if not result:
            return
        crop_bgr, _bbox = result
        png = _write_png(crop_bgr, key)
        self.data[key] = {"png": png}
        _save_nav(self.data)
        self._refresh_location_statuses()
        self._log_console(f"Сохранён шаблон {key}: {png}")

    def _bind_location(self, index: int) -> None:
        prompt = f"Откройте карту локаций и выделите иконку или кнопку для локации {index + 1}."
        if self._capture_busy:
            messagebox.showinfo("Скриншот", "Уже выполняется другое действие со скриншотом.", parent=self)
            return
        self._capture_busy = True
        self.busy_var.set("Идёт захват скриншота...")
        self.after(0, lambda: self._bind_location_ui(index, prompt))

    def _bind_location_ui(self, index: int, prompt: str) -> None:
        try:
            result = capture_region(self, prompt)
            error = None
        except Exception as exc:
            result = None
            error = exc
        self._after_location_capture(index, result, error)

    def _after_location_capture(self, index: int, result, error: Exception | None) -> None:
        self._capture_busy = False
        self.busy_var.set("")
        if error is not None:
            messagebox.showerror("Скриншот", str(error), parent=self)
            return
        if not result:
            return
        crop_bgr, _bbox = result
        png = _write_png(crop_bgr, f"location_{index + 1}")
        item = self.data["locations"][index]
        item["slot"] = index + 1
        item["name"] = self.location_name_vars[index].get().strip() or f"Локация {index + 1}"
        item["png"] = png
        _save_nav(self.data)
        self._refresh_location_statuses()
        self._log_console(f"Сохранена локация {index + 1}: {item['name']} -> {png}")

    def _save_location_name(self, index: int) -> None:
        self.data["locations"][index]["name"] = self.location_name_vars[index].get().strip() or f"Локация {index + 1}"
        _save_nav(self.data)
        self._log_console(f"Сохранено имя локации {index + 1}: {self.data['locations'][index]['name']}")

    def _capture_joystick_points(self) -> None:
        if cv2 is None or Image is None or ImageTk is None:
            messagebox.showerror("Джойстик", "Нужны OpenCV и Pillow.", parent=self)
            return
        if self._capture_busy:
            messagebox.showinfo("Скриншот", "Уже выполняется другое действие со скриншотом.", parent=self)
            return
        self._capture_busy = True
        self.busy_var.set("Идёт захват скриншота...")

        def worker() -> None:
            try:
                frame = _make_provider().capture()
                error = None
            except Exception as exc:
                frame = None
                error = exc
            self.after(0, lambda: self._open_joystick_selector(frame, error))

        threading.Thread(target=worker, daemon=True).start()

    def _open_joystick_selector(self, frame, error: Exception | None) -> None:
        self._capture_busy = False
        self.busy_var.set("")
        if error is not None:
            messagebox.showerror("Джойстик", str(error), parent=self)
            return
        if frame is None or getattr(frame, "size", 0) == 0:
            messagebox.showerror("Джойстик", "Не удалось получить скриншот.", parent=self)
            return

        src_h, src_w = frame.shape[:2]
        scale = min(1.0, 1280 / src_w, 820 / src_h)
        shown = cv2.resize(frame, (int(src_w * scale), int(src_h * scale))) if scale != 1.0 else frame
        rgb = cv2.cvtColor(shown, cv2.COLOR_BGR2RGB)
        photo = ImageTk.PhotoImage(Image.fromarray(rgb))

        state = {"points": []}
        win = tk.Toplevel(self)
        win.title("Края джойстика")
        ttk.Label(win, text="Кликните сначала по левому краю джойстика, потом по правому. После двух точек нажмите Сохранить.", wraplength=960, justify=tk.LEFT).pack(anchor="w", padx=8, pady=(8, 4))
        canvas = tk.Canvas(win, width=shown.shape[1], height=shown.shape[0], cursor="crosshair")
        canvas.pack(padx=8, pady=4)
        canvas.create_image(0, 0, image=photo, anchor="nw")
        canvas.image = photo

        def on_click(event) -> None:
            if len(state["points"]) >= 2:
                return
            x = max(0, min(event.x, shown.shape[1]))
            y = max(0, min(event.y, shown.shape[0]))
            ox = int(x / scale)
            oy = int(y / scale)
            state["points"].append((ox, oy))
            color = "red" if len(state["points"]) == 1 else "lime"
            canvas.create_oval(x - 4, y - 4, x + 4, y + 4, outline=color, width=2)
            canvas.create_text(x + 10, y - 10, text="L" if len(state["points"]) == 1 else "R", fill=color, anchor="w")

        def on_save() -> None:
            if len(state["points"]) != 2:
                messagebox.showwarning("Джойстик", "Нужно выбрать две точки.", parent=win)
                return
            left, right = state["points"]
            self.left_x_var.set(str(left[0]))
            self.left_y_var.set(str(left[1]))
            self.right_x_var.set(str(right[0]))
            self.right_y_var.set(str(right[1]))
            self._save_joystick_entries()
            win.destroy()

        canvas.bind("<Button-1>", on_click)
        buttons = ttk.Frame(win)
        buttons.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(buttons, text="Сохранить", command=on_save).pack(side="left")
        ttk.Button(buttons, text="Отмена", command=win.destroy).pack(side="right")

    def _save_joystick_entries(self) -> None:
        try:
            left = {"x": int(self.left_x_var.get().strip()), "y": int(self.left_y_var.get().strip())}
            right = {"x": int(self.right_x_var.get().strip()), "y": int(self.right_y_var.get().strip())}
        except ValueError:
            messagebox.showerror("Джойстик", "Координаты джойстика должны быть целыми числами.", parent=self)
            return
        self.data["joystick"] = {"left": left, "right": right}
        _save_nav(self.data)
        self._log_console(f"Сохранены координаты джойстика: left={left}, right={right}")

    def _verify_chain_clicked(self) -> None:
        try:
            import nav
        except Exception as exc:
            messagebox.showerror("Проверка", str(exc), parent=self)
            return
        verify = getattr(nav, "verify_chain", None)
        if verify is None:
            messagebox.showinfo("Проверка", "Функция nav.verify_chain не найдена.", parent=self)
            return
        first_name = self.location_name_vars[0].get().strip() or "1"

        def run() -> None:
            try:
                ok, reason = verify(getattr(self.app, "project_dir", "."), first_name, log=self._threadsafe_console_log)
                self._console_queue.put(("log", f"Проверка цепочки: ok={ok}, reason={reason}"))
            except Exception as exc:
                self._console_queue.put(("log", f"Проверка цепочки завершилась ошибкой: {exc}"))

        threading.Thread(target=run, daemon=True).start()

    def _poll_app_log_queue(self) -> None:
        app_log_queue = getattr(self.app, "log_queue", None)
        if app_log_queue is not None:
            try:
                while True:
                    msg = app_log_queue.get_nowait()
                    self._log_console(str(msg))
            except queue.Empty:
                pass
        try:
            while True:
                kind, payload = self._console_queue.get_nowait()
                if kind == "log":
                    self._log_console(payload)
                elif kind == "set_match_level":
                    self.after(0, lambda value=payload: self._apply_match_level_from_command(value))
        except queue.Empty:
            pass
        self.after(100, self._poll_app_log_queue)

    def _apply_match_level_from_command(self, level: int) -> None:
        self.level_var.set(level)
        self._refresh_level_text_only()

    def _log_console(self, text: str) -> None:
        line = str(text)
        self._tail_buffer.append(line)
        _append_live_log(line)
        self.console_text.configure(state=tk.NORMAL)
        self.console_text.insert(tk.END, line + "\n")
        line_count = int(self.console_text.index("end-1c").split(".")[0])
        if line_count > MAX_CONSOLE_LINES:
            excess = line_count - MAX_CONSOLE_LINES
            self.console_text.delete("1.0", f"{excess + 1}.0")
        self.console_text.see(tk.END)
        self.console_text.configure(state=tk.DISABLED)

    def _threadsafe_console_log(self, text: Any) -> None:
        self._console_queue.put(("log", str(text)))

    def _on_console_execute(self, event=None) -> None:
        if event is not None:
            del event
        text = self.console_entry.get().strip()
        if not text:
            return
        self.console_entry.delete(0, tk.END)
        self._console_history.append(text)
        self._console_history_index = len(self._console_history)
        self._log_console(f"> {text}")

        def run() -> None:
            try:
                self._dispatch_command(text)
            except Exception as exc:
                self._console_queue.put(("log", f"Ошибка команды: {exc}"))

        threading.Thread(target=run, daemon=True).start()

    def _history_up(self, event=None):
        if not self._console_history:
            return "break"
        self._console_history_index = max(0, self._console_history_index - 1)
        self.console_entry.delete(0, tk.END)
        self.console_entry.insert(0, self._console_history[self._console_history_index])
        return "break"

    def _history_down(self, event=None):
        if not self._console_history:
            return "break"
        self._console_history_index = min(len(self._console_history), self._console_history_index + 1)
        self.console_entry.delete(0, tk.END)
        if self._console_history_index < len(self._console_history):
            self.console_entry.insert(0, self._console_history[self._console_history_index])
        return "break"

    def _console_clear(self) -> None:
        self.console_text.configure(state=tk.NORMAL)
        self.console_text.delete("1.0", tk.END)
        self.console_text.configure(state=tk.DISABLED)

    def _console_copy_all(self) -> None:
        text = self.console_text.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self._log_console("Весь лог скопирован в буфер обмена.")

    def _console_save_to_file(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".log", filetypes=[("Log files", "*.log"), ("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        Path(path).write_text(self.console_text.get("1.0", "end-1c"), encoding="utf-8")
        self._log_console(f"Лог сохранён в файл: {path}")

    def _dispatch_command(self, text: str) -> None:
        try:
            parts = shlex.split(text)
        except ValueError as exc:
            self._console_queue.put(("log", f"Не удалось разобрать команду: {exc}"))
            return
        if not parts:
            return
        name = parts[0].lower()
        handler = self.commands.get(name)
        if handler is None:
            self._console_queue.put(("log", f"Неизвестная команда: {parts[0]}. Введите help, чтобы увидеть список команд."))
            return
        dangerous = {"screenshot", "find", "hold", "loot", "mob", "event", "go"}
        if name in dangerous and getattr(self.app, "current_process", None) is not None:
            self._console_queue.put(("log", "Сейчас бот уже запущен. Эта команда заблокирована, чтобы не мешать работе. Разрешены только stop и release."))
            return
        handler(parts[1:])

    def _cmd_help(self, args: list[str]) -> None:
        del args
        self._console_queue.put(("log", "Команды: help; status; settings; set имя значение; tail N; screenshot; find имя_шаблона; hold left|right миллисекунды; release; stop; loot имя_шаблона; mob имя_шаблона; event имя_шаблона; go название_локации; memory"))

    def _cmd_status(self, args: list[str]) -> None:
        del args
        running = getattr(self.app, "current_process", None) is not None
        self._console_queue.put(("log", f"Статус GUI: бот {'запущен' if running else 'не запущен'}, nav.json={'есть' if NAV_PATH.exists() else 'нет'}, live_log={LIVE_LOG_PATH}"))

    def _cmd_settings(self, args: list[str]) -> None:
        del args
        self._console_queue.put(("log", json.dumps(self.data.get("settings", {}), ensure_ascii=False, indent=2)))

    def _cmd_set(self, args: list[str]) -> None:
        if len(args) != 2:
            self._console_queue.put(("log", "Использование: set имя значение"))
            return
        name = args[0].lower()
        value = args[1]
        settings = self.data.setdefault("settings", dict(DEFAULT_SETTINGS))
        try:
            if name == "click_radius_px":
                parsed: Any = int(value)
            elif name == "approach_timeout_s":
                parsed = float(value)
            elif name == "hold_chunk_ms":
                parsed = int(value)
            elif name == "frame_interval_ms":
                parsed = int(value)
            elif name == "match_threshold":
                parsed = _threshold_from_user(value)
                self._console_queue.put(("set_match_level", _nearest_match_level(parsed)))
            elif name == "match_level":
                parsed_level = int(value)
                if not 1 <= parsed_level <= 10:
                    raise ValueError("match_level должен быть целым числом от 1 до 10.")
                parsed = MATCH_LEVELS[parsed_level]
                self._console_queue.put(("set_match_level", parsed_level))
            else:
                self._console_queue.put(("log", "Можно менять только: click_radius_px, approach_timeout_s, hold_chunk_ms, frame_interval_ms, match_threshold, match_level"))
                return
        except ValueError as exc:
            self._console_queue.put(("log", str(exc)))
            return
        settings["match_threshold"] = parsed if name in {"match_threshold", "match_level"} else settings.get("match_threshold", DEFAULT_SETTINGS["match_threshold"])
        if name not in {"match_threshold", "match_level"}:
            settings[name] = parsed
        _save_nav(self.data)
        self._console_queue.put(("log", f"Сохранено {name} = {parsed}"))

    def _cmd_tail(self, args: list[str]) -> None:
        if len(args) != 1:
            self._console_queue.put(("log", "Использование: tail N"))
            return
        try:
            count = max(1, int(args[0]))
        except ValueError:
            self._console_queue.put(("log", "N должен быть целым числом."))
            return
        for line in list(self._tail_buffer)[-count:]:
            self._console_queue.put(("log", line))

    def _screenshot_button_clicked(self) -> None:
        if getattr(self.app, "current_process", None) is not None:
            self._console_queue.put(("log", "Сейчас бот уже запущен. Скриншот заблокирован, чтобы не мешать работе."))
            return
        self._console_queue.put(("log", "Скриншот устройства через ADB, подождите..."))

        def run() -> None:
            try:
                import nav
            except Exception as exc:
                self._console_queue.put(("log", str(exc)))
                return
            func = getattr(nav, "take_screenshot", None)
            if func is None:
                self._console_queue.put(("log", "В nav.py нет функции take_screenshot."))
                return
            try:
                result = func(getattr(self.app, "project_dir", "."))
                path = result[0] if isinstance(result, tuple) else result
                self._console_queue.put(("log", f"Скриншот сохранён: {path}"))
            except Exception as exc:
                self._console_queue.put(("log", f"Скриншот не удался: {exc}"))

        threading.Thread(target=run, daemon=True).start()

    def _cmd_screenshot(self, args: list[str]) -> None:
        del args
        try:
            import nav
        except Exception as exc:
            self._console_queue.put(("log", str(exc)))
            return
        func = getattr(nav, "take_screenshot", None)
        if func is None:
            self._console_queue.put(("log", "В nav.py нет функции take_screenshot."))
            return
        result = func(getattr(self.app, "project_dir", "."))
        self._console_queue.put(("log", f"take_screenshot: {result}"))

    def _cmd_find(self, args: list[str]) -> None:
        if len(args) != 1:
            self._console_queue.put(("log", "Использование: find имя_шаблона"))
            return
        key = args[0]
        template = self.data.get(key)
        if isinstance(template, dict):
            template = template.get("png")
        if not template:
            self._console_queue.put(("log", f"Шаблон не найден в gui_base.py: {key}"))
            return
        try:
            import nav
        except Exception as exc:
            self._console_queue.put(("log", str(exc)))
            return
        func = getattr(nav, "find_template", None)
        if func is None:
            self._console_queue.put(("log", "В nav.py нет функции find_template."))
            return
        found, x, y, dx, score = func(getattr(self.app, "project_dir", "."), template)
        self._console_queue.put(("log", f"find_template({key}): found={found}, x={x}, y={y}, dx={dx}, score={score}"))

    def _cmd_hold(self, args: list[str]) -> None:
        if len(args) != 2:
            self._console_queue.put(("log", "Использование: hold left|right миллисекунды"))
            return
        side = args[0].lower()
        if side not in {"left", "right"}:
            self._console_queue.put(("log", "Сторона должна быть left или right."))
            return
        try:
            ms = int(args[1])
        except ValueError:
            self._console_queue.put(("log", "Длительность должна быть целым числом миллисекунд."))
            return
        if ms < 1 or ms > 5000:
            self._console_queue.put(("log", "Максимум 5000 мс, минимум 1 мс."))
            return
        try:
            import nav
        except Exception as exc:
            self._console_queue.put(("log", str(exc)))
            return
        hold_func = getattr(nav, "hold", None)
        release_func = getattr(nav, "release", None)
        if hold_func is None or release_func is None:
            self._console_queue.put(("log", "В nav.py нет функций hold/release."))
            return
        project_dir = getattr(self.app, "project_dir", ".")
        hold_ok = False
        try:
            hold_ok = bool(hold_func(side, project_dir=project_dir, log=self._threadsafe_console_log))
            if not hold_ok:
                self._console_queue.put(("log", f"hold {side} {ms}: nav.hold вернул False."))
                return
            self._console_queue.put(("log", f"hold {side} {ms}: команда принята. Автоматический release обязателен после удержания."))
            time.sleep(ms / 1000.0)
        finally:
            try:
                release_func(project_dir=project_dir, log=self._threadsafe_console_log)
            except Exception as exc:
                self._console_queue.put(("log", f"release завершился ошибкой: {exc}"))

    def _cmd_release(self, args: list[str]) -> None:
        del args
        try:
            import nav
        except Exception as exc:
            self._console_queue.put(("log", str(exc)))
            return
        func = getattr(nav, "release", None)
        if func is None:
            self._console_queue.put(("log", "В nav.py нет функции release."))
            return
        func(project_dir=getattr(self.app, "project_dir", "."), log=self._threadsafe_console_log)
        self._console_queue.put(("log", "release: команда выполнена."))

    def _cmd_stop(self, args: list[str]) -> None:
        del args
        stop_process = getattr(self.app, "stop_process", None)
        if stop_process is None:
            self._console_queue.put(("log", "У app нет метода stop_process."))
            return
        self.after(0, stop_process)
        self._console_queue.put(("log", "stop: отправлена команда остановки."))

    def _cmd_loot(self, args: list[str]) -> None:
        if len(args) != 1:
            self._console_queue.put(("log", "Использование: loot имя_шаблона"))
            return
        key = args[0]
        template = self.data.get(key)
        if isinstance(template, dict):
            template = template.get("png")
        if not template:
            self._console_queue.put(("log", f"Шаблон не найден в gui_base.py: {key}"))
            return
        try:
            import nav
        except Exception as exc:
            self._console_queue.put(("log", str(exc)))
            return
        func = getattr(nav, "click_loot", None)
        if func is None:
            self._console_queue.put(("log", "В nav.py нет функции click_loot."))
            return
        result = func(getattr(self.app, "project_dir", "."), template, log=self._threadsafe_console_log)
        self._console_queue.put(("log", f"loot: {result}"))

    def _cmd_mob(self, args: list[str]) -> None:
        if len(args) != 1:
            self._console_queue.put(("log", "Использование: mob имя_шаблона"))
            return
        key = args[0]
        template = self.data.get(key)
        if isinstance(template, dict):
            template = template.get("png")
        if not template:
            self._console_queue.put(("log", f"Шаблон не найден в gui_base.py: {key}"))
            return
        try:
            import nav
        except Exception as exc:
            self._console_queue.put(("log", str(exc)))
            return
        func = getattr(nav, "attack_mob", None)
        if func is None:
            self._console_queue.put(("log", "В nav.py нет функции attack_mob."))
            return
        result = func(getattr(self.app, "project_dir", "."), template, log=self._threadsafe_console_log)
        self._console_queue.put(("log", f"mob: {result}"))

    def _cmd_event(self, args: list[str]) -> None:
        if len(args) != 1:
            self._console_queue.put(("log", "Использование: event имя_шаблона"))
            return
        key = args[0]
        template = self.data.get(key)
        if isinstance(template, dict):
            template = template.get("png")
        if not template:
            self._console_queue.put(("log", f"Шаблон не найден в gui_base.py: {key}"))
            return
        try:
            import nav
        except Exception as exc:
            self._console_queue.put(("log", str(exc)))
            return
        func = getattr(nav, "handle_event", None)
        if func is None:
            self._console_queue.put(("log", "В nav.py нет функции handle_event."))
            return
        result = func(getattr(self.app, "project_dir", "."), template, log=self._threadsafe_console_log)
        self._console_queue.put(("log", f"event: {result}"))

    def _cmd_go(self, args: list[str]) -> None:
        if len(args) != 1:
            self._console_queue.put(("log", "Использование: go название_локации"))
            return
        try:
            import nav
        except Exception as exc:
            self._console_queue.put(("log", str(exc)))
            return
        if not hasattr(nav, "go_to_dungeon"):
            self._console_queue.put(("log", "В nav.py нет функции go_to_dungeon."))
            return
        ok, reason = nav.go_to_dungeon(getattr(self.app, "project_dir", "."), args[0], log=self._threadsafe_console_log, stop_event=None)
        self._console_queue.put(("log", f"go: ok={ok}, reason={reason}"))

    def _cmd_memory(self, args: list[str]) -> None:
        del args
        try:
            import nav
        except Exception as exc:
            self._console_queue.put(("log", str(exc)))
            return
        func = getattr(nav, "memory_summary", None)
        if func is None:
            self._console_queue.put(("log", "В nav.py нет функции memory_summary."))
            return
        result = func(getattr(self.app, "project_dir", "."))
        self._console_queue.put(("log", str(result)))


__all__ = ["capture_region", "SettingsTab", "SHOW_TRAINING_SETTINGS", "MATCH_LEVELS"]
