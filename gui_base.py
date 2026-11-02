from __future__ import annotations

"""GUI for one-time teaching of the base navigation chain.

The file writes ``data/base/nav.json`` atomically and stores PNG files in
``data/base``.  Its JSON shape is::

    {"base_to_locations": {"png": "..."},
     "locations": [{"slot": 1, "name": "...", "png": "...",
                    "floors": [{"name": "...", "png": "..."}]}],
     "enter_dungeon": {"png": "..."}}

Paths in JSON are relative to ``data/base``.  ``capture_region(parent, prompt)``
returns ``(crop_bgr, bbox)`` or ``None``.  It captures through
``ScreenshotProvider`` and never starts farming.
"""

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

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
    except (OSError, ValueError):
        return _empty_nav()
    result = _empty_nav()
    if isinstance(data, dict):
        for key in ("base_to_locations", "enter_dungeon"):
            if isinstance(data.get(key), dict):
                result[key].update(data[key])
        if isinstance(data.get("locations"), list):
            by_slot = {int(x.get("slot")): x for x in data["locations"]
                       if isinstance(x, dict) and str(x.get("slot", "")).isdigit()}
            for item in result["locations"]:
                old = by_slot.get(item["slot"], {})
                item.update({"name": str(old.get("name", "")),
                             "png": str(old.get("png", "")),
                             "floors": old.get("floors", []) if isinstance(old.get("floors", []), list) else []})
    return result


def _save_nav(data: dict[str, Any]) -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
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


def _provider() -> Any:
    if not all((load_config, ADBController, setup_logging, ScreenshotProvider)):
        raise RuntimeError("Не удалось загрузить ScreenshotProvider")
    config = load_config("config.yaml")
    logger = setup_logging(config)
    adb = ADBController(config, logger)
    adb.connect()
    return ScreenshotProvider(adb, config, logger)


def capture_region(parent, prompt):
    """Capture a device screenshot and let the user select a rectangle.

    Returns ``(crop_bgr, (left, top, right, bottom))`` or ``None``.
    """
    if cv2 is None or Image is None or ImageTk is None:
        messagebox.showerror("Скриншот", "Нужны OpenCV и Pillow.", parent=parent)
        return None
    try:
        frame = _provider().capture()
    except Exception as exc:
        messagebox.showerror("Скриншот", str(exc), parent=parent)
        return None
    if frame is None or getattr(frame, "size", 0) == 0:
        messagebox.showerror("Скриншот", "Не удалось получить изображение.", parent=parent)
        return None
    h, w = frame.shape[:2]
    scale = min(1.0, 1000 / w, 700 / h)
    shown = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1 else frame
    rgb = cv2.cvtColor(shown, cv2.COLOR_BGR2RGB)
    photo = ImageTk.PhotoImage(Image.fromarray(rgb))
    win = tk.Toplevel(parent)
    win.title(str(prompt))
    win.transient(parent)
    canvas = tk.Canvas(win, width=shown.shape[1], height=shown.shape[0], cursor="crosshair")
    canvas.pack(padx=8, pady=8)
    canvas.create_image(0, 0, image=photo, anchor="nw")
    state = {"start": None, "rect": None, "result": None}

    def down(event):
        state["start"] = (max(0, min(event.x, shown.shape[1])), max(0, min(event.y, shown.shape[0])))
        if state["rect"]:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(*state["start"], *state["start"], outline="red", width=2)

    def move(event):
        if state["start"] and state["rect"]:
            canvas.coords(state["rect"], *state["start"], event.x, event.y)

    def save():
        if not state["start"] or not state["rect"]:
            return
        x1, y1, x2, y2 = canvas.coords(state["rect"])
        left, right = sorted((max(0, int(x1 / scale)), min(w, int(x2 / scale))))
        top, bottom = sorted((max(0, int(y1 / scale)), min(h, int(y2 / scale))))
        if right <= left or bottom <= top:
            return
        state["result"] = (frame[top:bottom, left:right].copy(), (left, top, right, bottom))
        win.destroy()

    canvas.bind("<ButtonPress-1>", down)
    canvas.bind("<B1-Motion>", move)
    buttons = ttk.Frame(win); buttons.pack(fill="x", padx=8, pady=(0, 8))
    ttk.Button(buttons, text="Сохранить", command=save).pack(side="left")
    ttk.Button(buttons, text="Отмена", command=win.destroy).pack(side="right")
    win.grab_set(); win.wait_window()
    return state["result"]


def _write_png(crop, prefix: str) -> str:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    path = BASE_DIR / f"{prefix}_{time.time_ns()}.png"
    if cv2 is None or not cv2.imwrite(str(path), crop):
        raise RuntimeError("Не удалось сохранить PNG")
    return path.relative_to(BASE_DIR).as_posix()


class NavigationTab(ttk.Frame):
    """Step-by-step base-screen teaching tab; it does not invoke farming."""

    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.data = _load_nav()
        self.location_vars = [tk.StringVar(value=x["name"]) for x in self.data["locations"]]
        self.status_vars = [tk.StringVar(value="✓" if x["png"] else "✗") for x in self.data["locations"]]
        self.floor_location = tk.StringVar(value="1")
        self._build()

    def _build(self):
        ttk.Label(self, text="Базовые знания", font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
        ttk.Button(self, text="Назначить вход в локации", command=self._base_to_locations).pack(anchor="w", pady=5)
        ttk.LabelFrame(self, text="7 локаций").pack(fill="x", pady=8)
        box = self.winfo_children()[-1]
        for i in range(7):
            row = ttk.Frame(box); row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{i + 1}.", width=3).pack(side="left")
            ttk.Entry(row, textvariable=self.location_vars[i], width=24).pack(side="left")
            ttk.Button(row, text="Привязать к скриншоту", command=lambda n=i: self._bind_location(n)).pack(side="left", padx=5)
            ttk.Label(row, textvariable=self.status_vars[i], width=2).pack(side="left")
        floors = ttk.LabelFrame(self, text="Этаж")
        floors.pack(fill="both", expand=True, pady=8)
        row = ttk.Frame(floors); row.pack(fill="x")
        ttk.Label(row, text="Локация:").pack(side="left")
        combo = ttk.Combobox(row, textvariable=self.floor_location, values=[str(i) for i in range(1, 8)], state="readonly", width=5)
        combo.pack(side="left", padx=5); combo.bind("<<ComboboxSelected>>", lambda _: self._refresh_floors())
        ttk.Button(row, text="Добавить этаж", command=self._add_floor).pack(side="left")
        self.floor_list = tk.Listbox(floors, height=5); self.floor_list.pack(fill="x", pady=5)
        self._refresh_floors()
        ttk.Button(self, text="Назначить кнопку В данж", command=self._enter_dungeon).pack(anchor="w", pady=3)
        ttk.Button(self, text="Проверить цепочку", command=self._verify).pack(anchor="w", pady=3)

    def _capture_save(self, prompt, prefix):
        selected = capture_region(self, prompt)
        return _write_png(selected[0], prefix) if selected else None

    def _base_to_locations(self):
        try:
            png = self._capture_save("Выделите кнопку перехода к локациям", "base_to_locations")
            if png:
                self.data["base_to_locations"] = {"png": png}; _save_nav(self.data)
        except Exception as exc: messagebox.showerror("Сохранение", str(exc), parent=self)

    def _bind_location(self, index):
        try:
            png = self._capture_save(f"Локация {index + 1}: выделите иконку", f"location_{index + 1}")
            if png:
                item = self.data["locations"][index]; item.update(slot=index + 1, name=self.location_vars[index].get().strip(), png=png)
                _save_nav(self.data); self.status_vars[index].set("✓")
        except Exception as exc: messagebox.showerror("Сохранение", str(exc), parent=self)

    def _add_floor(self):
        index = int(self.floor_location.get()) - 1
        name = simpledialog.askstring("Этаж", "Название этажа:", parent=self)
        if not name or not name.strip(): return
        try:
            png = self._capture_save("Выделите иконку этажа", f"location_{index + 1}_floor")
            if png:
                self.data["locations"][index].setdefault("floors", []).append({"name": name.strip(), "png": png})
                _save_nav(self.data); self._refresh_floors()
        except Exception as exc: messagebox.showerror("Сохранение", str(exc), parent=self)

    def _refresh_floors(self):
        if not hasattr(self, "floor_list"): return
        self.floor_list.delete(0, tk.END)
        item = self.data["locations"][int(self.floor_location.get()) - 1]
        for floor in item.get("floors", []): self.floor_list.insert(tk.END, f"{floor.get('name', '')}: {floor.get('png', '')}")

    def _enter_dungeon(self):
        try:
            png = self._capture_save("Выделите кнопку «В данж»", "enter_dungeon")
            if png: self.data["enter_dungeon"] = {"png": png}; _save_nav(self.data)
        except Exception as exc: messagebox.showerror("Сохранение", str(exc), parent=self)

    def _verify(self):
        try:
            import nav
            check = getattr(nav, "verify_chain", None)
            if check is None:
                messagebox.showinfo("Проверка", "Функция проверки цепочки в nav.py отсутствует.", parent=self); return
            result = check(getattr(self.app, "project_dir", "."), self.location_vars[0].get(), "1")
            messagebox.showinfo("Проверка", "Цепочка готова." if result else "Цепочка не готова.", parent=self)
        except Exception as exc: messagebox.showerror("Проверка", str(exc), parent=self)


__all__ = ["capture_region", "NavigationTab"]
