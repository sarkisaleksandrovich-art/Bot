from __future__ import annotations

import json
import os
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    from PIL import Image, ImageTk
except ImportError:  # pragma: no cover
    Image = ImageTk = None

try:
    from bot.config import load_config
    from bot.adb_controller import ADBController
    from bot.logging_setup import setup_logging
    from bot.screenshot import ScreenshotProvider
except Exception:  # pragma: no cover
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
        data = json.loads(NAV_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_nav()
    result = _empty_nav()
    if isinstance(data, dict):
        for key in result:
            if key in data:
                result[key] = data[key]
    slots = {
        item["slot"]: item
        for item in result["locations"]
        if isinstance(item, dict) and str(item.get("slot", "")).isdigit()
    } if isinstance(result.get("locations"), list) else {}
    result["locations"] = []
    for slot in range(1, 8):
        item = dict(slots.get(slot, {"slot": slot, "name": "", "png": "", "floors": []}))
        item.setdefault("slot", slot)
        item.setdefault("name", "")
        item.setdefault("png", "")
        item.setdefault("floors", [])
        result["locations"].append(item)
    return result


def _save_nav(data: dict[str, Any]) -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".nav-", suffix=".tmp", dir=BASE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(name, NAV_PATH)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _provider() -> Any:
    if not all((load_config, ADBController, ScreenshotProvider)):
        raise RuntimeError("Не доступны ADB/ScreenshotProvider")
    config = load_config("config.yaml")
    logger = setup_logging(config) if setup_logging else None
    adb = ADBController(config, logger)
    adb.connect()
    return ScreenshotProvider(adb, config, logger)


def capture_region(parent, prompt) -> tuple[Any, tuple[int, int, int, int]] | None:
    if cv2 is None or Image is None or ImageTk is None:
        messagebox.showerror("Ошибка", "Нужны OpenCV и Pillow", parent=parent)
        return None
    try:
        frame = _provider().capture()
    except Exception as exc:
        messagebox.showerror("Ошибка", str(exc), parent=parent)
        return None
    if frame is None or getattr(frame, "size", 0) == 0:
        return None
    height, width = frame.shape[:2]
    scale = min(1.0, 900 / width, 650 / height)
    display = cv2.resize(frame, (int(width * scale), int(height * scale))) if scale != 1 else frame
    image = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(display, cv2.COLOR_BGR2RGB)))
    win = tk.Toplevel(parent)
    win.title(str(prompt))
    canvas = tk.Canvas(win, width=display.shape[1], height=display.shape[0], cursor="crosshair")
    canvas.pack(padx=8, pady=8)
    canvas.create_image(0, 0, anchor="nw", image=image)
    canvas.image = image
    state = {"start": None, "rect": None, "result": None}
    def down(event):
        state["start"] = (max(0, min(event.x, display.shape[1])), max(0, min(event.y, display.shape[0])))
        if state["rect"]:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(*state["start"], *state["start"], outline="red", width=2)
    def move(event):
        if state["start"] and state["rect"]:
            canvas.coords(state["rect"], *state["start"], event.x, event.y)
    def save():
        if not state["start"] or not state["rect"]: return
        x1, y1, x2, y2 = canvas.coords(state["rect"])
        left, right = sorted((max(0, int(x1 / scale)), min(width, int(x2 / scale))))
        top, bottom = sorted((max(0, int(y1 / scale)), min(height, int(y2 / scale))))
        if right > left and bottom > top:
            state["result"] = (frame[top:bottom, left:right].copy(), (left, top, right, bottom))
            win.destroy()
    canvas.bind("<ButtonPress-1>", down)
    canvas.bind("<B1-Motion>", move)
    buttons = ttk.Frame(win); buttons.pack(fill="x", padx=8, pady=(0, 8))
    ttk.Button(buttons, text="Сохранить", command=save).pack(side="left")
    ttk.Button(buttons, text="Отмена", command=win.destroy).pack(side="right")
    win.grab_set(); win.wait_window()
    return state["result"]


def _write_png(crop: Any, prefix: str) -> str:
    if cv2 is None or not cv2.imwrite(str(BASE_DIR / "x.png"), crop):
        raise RuntimeError("Не удалось сохранить PNG")
    path = BASE_DIR / f"{prefix}_{os.getpid()}_{id(crop)}.png"
    if not cv2.imwrite(str(path), crop):
        raise RuntimeError(f"PNG: {path}")
    return path.as_posix()


class NavigationTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.data = _load_nav()
        self.location_vars = [tk.StringVar(value=x.get("name", "")) for x in self.data["locations"]]
        self.status_vars = [tk.StringVar(value="✓" if x.get("png") else "✗") for x in self.data["locations"]]
        self.floor_location = tk.StringVar(value="1")
        self._build()

    def _build(self):
        ttk.Label(self, text="Навигационная база", font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
        ttk.Button(self, text="Назначить вход в локации", command=self._base_to_locations).pack(anchor="w", pady=3)
        self.base_status = ttk.Label(self, text="✓" if self.data["base_to_locations"].get("png") else "✗")
        self.base_status.pack(anchor="w")
        box = ttk.LabelFrame(self, text="7 локаций"); box.pack(fill="x", pady=8)
        for i in range(7):
            row = ttk.Frame(box); row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{i+1}.", width=3).pack(side="left")
            ttk.Entry(row, textvariable=self.location_vars[i], width=24).pack(side="left")
            ttk.Button(row, text="Привязать к скриншоту", command=lambda n=i: self._bind_location(n)).pack(side="left", padx=3)
            ttk.Label(row, textvariable=self.status_vars[i], width=3).pack(side="left")
        floors = ttk.Frame(self); floors.pack(fill="x")
        ttk.Label(floors, text="Этаж локации:").pack(side="left")
        ttk.Combobox(floors, textvariable=self.floor_location, values=[str(i) for i in range(1, 8)], state="readonly", width=4).pack(side="left")
        ttk.Button(floors, text="Добавить этаж", command=self._add_floor).pack(side="left", padx=5)
        self.floor_list = tk.Listbox(self, height=5, width=60); self.floor_list.pack(anchor="w", pady=4)
        self._refresh_floors()
        ttk.Button(self, text="Назначить В данж", command=self._enter_dungeon).pack(anchor="w", pady=4)

    def _capture_save(self, prompt, prefix):
        selected = capture_region(self, prompt)
        if selected is None: return None
        return _write_png(selected[0], prefix)

    def _base_to_locations(self):
        png = self._capture_save("Вход в локации", "base_to_locations")
        if png:
            self.data["base_to_locations"] = {"png": png}; _save_nav(self.data); self.base_status.config(text="✓")

    def _bind_location(self, index):
        png = self._capture_save(f"Локация {index + 1}", f"location_{index + 1:02d}")
        if png:
            self.data["locations"][index].update(slot=index + 1, name=self.location_vars[index].get().strip(), png=png)
            _save_nav(self.data); self.status_vars[index].set("✓")

    def _add_floor(self):
        index = int(self.floor_location.get()) - 1
        name = simpledialog.askstring("Этаж", "Название этажа:", parent=self)
        if not name: return
        png = self._capture_save("Этаж", f"location_{index + 1:02d}_floor")
        if png:
            self.data["locations"][index].setdefault("floors", []).append({"name": name.strip(), "png": png})
            _save_nav(self.data); self._refresh_floors()

    def _refresh_floors(self):
        if not hasattr(self, "floor_list"): return
        self.floor_list.delete(0, tk.END)
        index = int(self.floor_location.get()) - 1
        for floor in self.data["locations"][index].get("floors", []):
            self.floor_list.insert(tk.END, f"{floor.get('name', '')}: {floor.get('png', '')}")

    def _enter_dungeon(self):
        png = self._capture_save("В данж", "enter_dungeon")
        if png:
            self.data["enter_dungeon"] = {"png": png}; _save_nav(self.data)


__all__ = ["capture_region", "NavigationTab"]
