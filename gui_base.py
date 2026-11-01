"""Tkinter training tab for the shared ``base`` navigation profile.

Storage contract:
    data/base/nav.json = {
        "base_to_locations": {"png": "..."},
        "locations": [
            {"slot": 1, "name": "...", "png": "...",
             "floors": [{"name": "...", "png": "..."}]}
        ],
        "enter_dungeon": {"png": "..."}
    }

PNG files are stored in ``data/base``.  JSON writes use a temporary file and
``os.replace``.  This module only teaches and stores navigation knowledge; it
does not start farming or perform automatic clicks.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable
import tkinter as tk
from tkinter import messagebox, ttk

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    from bot.config import load_config
    from bot.adb_controller import ADBController
    from bot.logging_setup import setup_logging
    from bot.screenshot import ScreenshotProvider
except Exception:  # allow importing the GUI in lightweight/test environments
    load_config = ADBController = setup_logging = ScreenshotProvider = None

BASE_DIR = Path("data/base")
NAV_PATH = BASE_DIR / "nav.json"


def _empty_nav() -> dict[str, Any]:
    return {
        "base_to_locations": {"png": ""},
        "locations": [
            {"slot": i, "name": "", "png": "", "floors": []}
            for i in range(1, 8)
        ],
        "enter_dungeon": {"png": ""},
    }


def _load_nav() -> dict[str, Any]:
    try:
        with NAV_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("nav.json must contain an object")
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty_nav()
    result = _empty_nav()
    result.update({k: v for k, v in data.items() if k in result})
    locations = result.get("locations")
    if not isinstance(locations, list):
        locations = []
    by_slot = {int(x.get("slot")): x for x in locations if isinstance(x, dict) and str(x.get("slot", "")).isdigit()}
    result["locations"] = [
        by_slot.get(i, {"slot": i, "name": "", "png": "", "floors": []})
        for i in range(1, 8)
    ]
    return result


def _save_nav(data: dict[str, Any]) -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = NAV_PATH.with_name(f".{NAV_PATH.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, NAV_PATH)


def _provider() -> Any:
    if ScreenshotProvider is None or load_config is None:
        raise RuntimeError("Модули ADB/ScreenshotProvider недоступны")
    config = load_config("config.yaml")
    logger = setup_logging(config) if setup_logging else None
    adb = ADBController(config, logger)
    adb.connect()
    return ScreenshotProvider(adb, config, logger)


def capture_region(parent, prompt) -> tuple[Any, tuple[int, int, int, int]] | None:
    """Capture a device screenshot and let the user select a rectangle.

    Returns ``(crop_bgr, bbox)`` in screenshot coordinates, or ``None`` when
    cancelled.  Selection is mouse-only: dragging creates the rectangle and
    the Save/Cancel buttons finish the operation.
    """
    if cv2 is None:
        messagebox.showerror("Скриншот", "Не установлен OpenCV", parent=parent)
        return None
    try:
        frame = _provider().capture()
    except Exception as exc:
        messagebox.showerror("Скриншот", str(exc), parent=parent)
        return None
    if frame is None or getattr(frame, "size", 0) == 0:
        return None

    height, width = frame.shape[:2]
    scale = min(1.0, 900 / width, 650 / height)
    display = cv2.resize(frame, (int(width * scale), int(height * scale))) if scale != 1 else frame
    rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
    try:
        from PIL import Image, ImageTk
        image = ImageTk.PhotoImage(Image.fromarray(rgb))
    except Exception:
        messagebox.showerror("Скриншот", "Для выбора рамки нужен Pillow", parent=parent)
        return None

    win = tk.Toplevel(parent)
    win.title(str(prompt))
    win.transient(parent)
    canvas = tk.Canvas(win, width=display.shape[1], height=display.shape[0], cursor="crosshair")
    canvas.pack(padx=8, pady=8)
    canvas.create_image(0, 0, anchor="nw", image=image)
    canvas.image = image
    state: dict[str, Any] = {"start": None, "rect": None, "result": None}

    def down(event):
        state["start"] = (max(0, min(event.x, canvas.winfo_width())), max(0, min(event.y, canvas.winfo_height())))
        if state["rect"]:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(*state["start"], *state["start"], outline="red", width=2)

    def move(event):
        if state["start"] and state["rect"]:
            canvas.coords(state["rect"], state["start"][0], state["start"][1], event.x, event.y)

    canvas.bind("<ButtonPress-1>", down)
    canvas.bind("<B1-Motion>", move)

    def save():
        if not state["start"] or not state["rect"]:
            return
        x1, y1, x2, y2 = canvas.coords(state["rect"])
        left, right = sorted((int(x1 / scale), int(x2 / scale)))
        top, bottom = sorted((int(y1 / scale), int(y2 / scale)))
        left, top = max(0, left), max(0, top)
        right, bottom = min(width, right), min(height, bottom)
        if right <= left or bottom <= top:
            return
        state["result"] = (frame[top:bottom, left:right].copy(), (left, top, right, bottom))
        win.destroy()

    buttons = ttk.Frame(win)
    buttons.pack(fill="x", padx=8, pady=(0, 8))
    ttk.Button(buttons, text="Сохранить", command=save).pack(side="left")
    ttk.Button(buttons, text="Отмена", command=win.destroy).pack(side="right")
    win.grab_set()
    win.wait_window()
    return state["result"]


def _write_png(crop: Any, prefix: str) -> str:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = BASE_DIR / f"{prefix}_{stamp}_{time.time_ns() % 1000000:06d}.png"
    if cv2 is None or not cv2.imwrite(str(path), crop):
        raise RuntimeError(f"Не удалось сохранить PNG: {path}")
    return str(path.as_posix())


class NavigationTab(ttk.Frame):
    """Step-by-step, click-free teaching UI for base navigation."""
    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.data = _load_nav()
        self.location_vars = [tk.StringVar(value=x.get("name", "")) for x in self.data["locations"]]
        self.status_vars = [tk.StringVar(value="✓" if x.get("png") else "✗") for x in self.data["locations"]]
        self.floor_location = tk.StringVar(value="1")
        self._build()

    def _build(self):
        ttk.Label(self, text="Базовые знания", font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
        ttk.Label(self, text="Откройте нужный экран в эмуляторе, выделите рамкой элемент и сохраните.").pack(anchor="w", pady=(2, 10))
        ttk.Button(self, text="Назначить вход в локации", command=self._base_to_locations).pack(anchor="w", pady=3)
        self.base_status = ttk.Label(self, text="✓" if self.data["base_to_locations"].get("png") else "✗")
        self.base_status.pack(anchor="w")

        loc_box = ttk.LabelFrame(self, text="7 локаций")
        loc_box.pack(fill="x", pady=8)
        for i in range(7):
            row = ttk.Frame(loc_box)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{i + 1}.", width=3).pack(side="left")
            ttk.Entry(row, textvariable=self.location_vars[i], width=24).pack(side="left", padx=3)
            ttk.Button(row, text="Привязать к скриншоту", command=lambda n=i: self._bind_location(n)).pack(side="left", padx=3)
            ttk.Label(row, textvariable=self.status_vars[i], width=3).pack(side="left")

        floor_box = ttk.LabelFrame(self, text="Этаж")
        floor_box.pack(fill="x", pady=8)
        ttk.Label(floor_box, text="Локация:").pack(side="left", padx=4)
        ttk.Combobox(floor_box, textvariable=self.floor_location, values=[str(i) for i in range(1, 8)], state="readonly", width=5).pack(side="left")
        ttk.Button(floor_box, text="Добавить этаж", command=self._add_floor).pack(side="left", padx=8)
        self.floor_list = tk.Listbox(floor_box, height=5, width=45)
        self.floor_list.pack(side="left", padx=4)
        self._refresh_floors()

        ttk.Button(self, text="Назначить кнопку В данж", command=self._enter_dungeon).pack(anchor="w", pady=4)
        ttk.Button(self, text="Проверить цепочку", command=self._check_chain).pack(anchor="w", pady=4)

    def _capture_and_save(self, prompt: str, prefix: str) -> str | None:
        selected = capture_region(self, prompt)
        if selected is None:
            return None
        try:
            return _write_png(selected[0], prefix)
        except Exception as exc:
            messagebox.showerror("Сохранение", str(exc), parent=self)
            return None

    def _base_to_locations(self):
        png = self._capture_and_save("Выделите кнопку перехода к локациям", "base_to_locations")
        if png:
            self.data["base_to_locations"] = {"png": png}
            _save_nav(self.data)
            self.base_status.config(text="✓")

    def _bind_location(self, index: int):
        png = self._capture_and_save(f"Локация {index + 1}: выделите иконку", f"location_{index + 1:02d}")
        if png:
            item = self.data["locations"][index]
            item.update(slot=index + 1, name=self.location_vars[index].get().strip(), png=png)
            _save_nav(self.data)
            self.status_vars[index].set("✓")

    def _add_floor(self):
        index = int(self.floor_location.get()) - 1
        name = tk.simpledialog.askstring("Этаж", "Название этажа:", parent=self) if hasattr(tk, "simpledialog") else None
        if not name:
            from tkinter import simpledialog
            name = simpledialog.askstring("Этаж", "Название этажа:", parent=self)
        if not name:
            return
        png = self._capture_and_save("Выделите шаблон экрана этажа", f"location_{index + 1:02d}_floor")
        if png:
            self.data["locations"][index].setdefault("floors", []).append({"name": name.strip(), "png": png})
            _save_nav(self.data)
            self._refresh_floors()

    def _refresh_floors(self):
        if not hasattr(self, "floor_list"):
            return
        self.floor_list.delete(0, tk.END)
        floors = self.data["locations"][int(self.floor_location.get()) - 1].get("floors", [])
        for floor in floors:
            self.floor_list.insert(tk.END, f"{floor.get('name', '')}: {floor.get('png', '')}")

    def _enter_dungeon(self):
        png = self._capture_and_save("Выделите кнопку В данж", "enter_dungeon")
        if png:
            self.data["enter_dungeon"] = {"png": png}
            _save_nav(self.data)

    def _check_chain(self):
        try:
            import nav
            checker = getattr(nav, "check_chain", None)
            if callable(checker):
                result = checker()
                messagebox.showinfo("Проверка цепочки", str(result), parent=self)
            else:
                messagebox.showinfo("Проверка цепочки", "Функция проверки цепочки в nav.py не найдена.", parent=self)
        except Exception as exc:
            messagebox.showerror("Проверка цепочки", str(exc), parent=self)


def create_base_view(parent, on_location_selected: Callable[[str], None], on_back=None):
    return render_base_screen(parent, on_location_selected, on_back)


def render_base_screen(parent, on_location_selected: Callable[[str], None], on_back=None):
    frame = ttk.Frame(parent, padding=12)
    ttk.Label(frame, text="Главная база", font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
    ttk.Button(frame, text="Перейти к выбору локации", command=lambda: on_location_selected("location_select")).pack(anchor="w", pady=8)
    for item in location_button_data():
        ttk.Button(frame, text=item["title"], command=lambda x=item["location_id"]: on_location_selected(x)).pack(anchor="w", pady=2)
    if on_back:
        ttk.Button(frame, text="Назад", command=on_back).pack(anchor="w", pady=8)
    return frame


def location_button_data() -> tuple[dict, ...]:
    return tuple({"location_id": f"loc{i}", "index": i, "title": f"Локация {i}"} for i in range(1, 8))


def on_location_selected(location_id: str) -> None:
    """Callback placeholder; the application supplies the real callback."""
    return None


__all__ = ["capture_region", "NavigationTab", "create_base_view", "render_base_screen", "location_button_data"]
