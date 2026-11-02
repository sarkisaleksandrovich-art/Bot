from __future__ import annotations

"""GUI for one-time teaching of the base navigation chain.

This module stores templates inside ``data/base/`` and writes
``data/base/nav.json`` atomically via ``tmp + os.replace``.

JSON structure::

    {
      "base_to_locations": {"png": "base_to_locations_....png"},
      "locations": [
        {
          "slot": 1,
          "name": "Forest",
          "png": "location_1_....png",
          "floors": [
            {"name": "1", "png": "location_1_floor_....png"}
          ]
        }
      ],
      "enter_dungeon": {"png": "enter_dungeon_....png"}
    }

All PNG paths in JSON are relative to ``data/base/``.

Exports:
- ``capture_region(parent, prompt) -> tuple[crop_bgr, bbox] | None``
- ``NavigationTab(ttk.Frame)`` with constructor ``(parent, app)``

The tab only teaches templates and optionally calls ``nav.verify_chain``.
It never starts farming.
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
    from bot.adb_controller import ADBController
    from bot.config import load_config
    from bot.logging_setup import setup_logging
    from bot.screenshot import ScreenshotProvider
except Exception:  # pragma: no cover
    ADBController = load_config = setup_logging = ScreenshotProvider = None

BASE_DIR = Path("data/base")
NAV_PATH = BASE_DIR / "nav.json"
LOCATION_SLOTS = 7


def _empty_nav() -> dict[str, Any]:
    return {
        "base_to_locations": {"png": ""},
        "locations": [
            {"slot": slot, "name": "", "png": "", "floors": []}
            for slot in range(1, LOCATION_SLOTS + 1)
        ],
        "enter_dungeon": {"png": ""},
    }


def _normalize_locations(value: Any) -> list[dict[str, Any]]:
    result = [
        {"slot": slot, "name": "", "png": "", "floors": []}
        for slot in range(1, LOCATION_SLOTS + 1)
    ]
    if not isinstance(value, list):
        return result
    by_slot: dict[int, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        slot = item.get("slot")
        if isinstance(slot, int) and 1 <= slot <= LOCATION_SLOTS:
            by_slot[slot] = item
    for item in result:
        src = by_slot.get(item["slot"], {})
        floors = src.get("floors", []) if isinstance(src.get("floors"), list) else []
        item["name"] = str(src.get("name", ""))
        item["png"] = str(src.get("png", ""))
        item["floors"] = [
            {"name": str(f.get("name", "")), "png": str(f.get("png", ""))}
            for f in floors
            if isinstance(f, dict)
        ]
    return result


def _load_nav() -> dict[str, Any]:
    data = _empty_nav()
    try:
        raw = json.loads(NAV_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return data
    if not isinstance(raw, dict):
        return data
    if isinstance(raw.get("base_to_locations"), dict):
        data["base_to_locations"]["png"] = str(raw["base_to_locations"].get("png", ""))
    if isinstance(raw.get("enter_dungeon"), dict):
        data["enter_dungeon"]["png"] = str(raw["enter_dungeon"].get("png", ""))
    data["locations"] = _normalize_locations(raw.get("locations"))
    return data


def _save_nav(data: dict[str, Any]) -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".nav-", suffix=".tmp", dir=BASE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_name, NAV_PATH)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _make_provider() -> Any:
    if not all((load_config, setup_logging, ADBController, ScreenshotProvider)):
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
        frame = _make_provider().capture()
    except Exception as exc:
        messagebox.showerror("Скриншот", str(exc), parent=parent)
        return None
    if frame is None or getattr(frame, "size", 0) == 0:
        messagebox.showerror("Скриншот", "Не удалось получить изображение.", parent=parent)
        return None

    src_h, src_w = frame.shape[:2]
    scale = min(1.0, 1200 / src_w, 800 / src_h)
    shown = cv2.resize(frame, (int(src_w * scale), int(src_h * scale))) if scale != 1.0 else frame
    shown_h, shown_w = shown.shape[:2]

    rgb = cv2.cvtColor(shown, cv2.COLOR_BGR2RGB)
    photo = ImageTk.PhotoImage(Image.fromarray(rgb))

    win = tk.Toplevel(parent)
    win.title(str(prompt))
    win.transient(parent)

    ttk.Label(
        win,
        text=str(prompt),
        wraplength=900,
        justify=tk.LEFT,
    ).pack(anchor="w", padx=8, pady=(8, 4))

    canvas = tk.Canvas(win, width=shown_w, height=shown_h, cursor="crosshair")
    canvas.pack(padx=8, pady=4)
    canvas.create_image(0, 0, image=photo, anchor="nw")
    canvas.image = photo

    state: dict[str, Any] = {"start": None, "rect": None, "result": None}

    def on_down(event):
        x = max(0, min(event.x, shown_w))
        y = max(0, min(event.y, shown_h))
        state["start"] = (x, y)
        if state["rect"]:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(x, y, x, y, outline="red", width=2)

    def on_drag(event):
        if not state["start"] or not state["rect"]:
            return
        x = max(0, min(event.x, shown_w))
        y = max(0, min(event.y, shown_h))
        canvas.coords(state["rect"], state["start"][0], state["start"][1], x, y)

    def on_save():
        if not state["rect"]:
            return
        x1, y1, x2, y2 = canvas.coords(state["rect"])
        left = max(0, min(src_w, int(min(x1, x2) / scale)))
        right = max(0, min(src_w, int(max(x1, x2) / scale)))
        top = max(0, min(src_h, int(min(y1, y2) / scale)))
        bottom = max(0, min(src_h, int(max(y1, y2) / scale)))
        if right <= left or bottom <= top:
            messagebox.showwarning("Выделение", "Сначала выделите область мышью.", parent=win)
            return
        state["result"] = (frame[top:bottom, left:right].copy(), (left, top, right, bottom))
        win.destroy()

    canvas.bind("<ButtonPress-1>", on_down)
    canvas.bind("<B1-Motion>", on_drag)

    buttons = ttk.Frame(win)
    buttons.pack(fill="x", padx=8, pady=(0, 8))
    ttk.Button(buttons, text="Сохранить", command=on_save).pack(side="left")
    ttk.Button(buttons, text="Отмена", command=win.destroy).pack(side="right")

    win.grab_set()
    win.wait_window()
    return state["result"]


def _write_png(crop_bgr, prefix: str) -> str:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    path = BASE_DIR / f"{prefix}_{time.time_ns()}.png"
    if cv2 is None or not cv2.imwrite(str(path), crop_bgr):
        raise RuntimeError("Не удалось сохранить PNG")
    return path.relative_to(BASE_DIR).as_posix()


class NavigationTab(ttk.Frame):
    """Teaching tab for base navigation templates without farm start."""

    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.data = _load_nav()
        self.location_vars = [
            tk.StringVar(value=item["name"])
            for item in self.data["locations"]
        ]
        self.status_vars = [
            tk.StringVar(value="✓" if item.get("png") else "✗")
            for item in self.data["locations"]
        ]
        self.floor_location_var = tk.StringVar(value="1")
        self._build()

    def _build(self) -> None:
        ttk.Label(self, text="Базовые знания", font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
        ttk.Label(
            self,
            text="Сделайте скриншот нужного экрана, выделите кнопку или иконку мышью и нажмите «Сохранить».",
            wraplength=950,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(4, 10))

        ttk.Button(
            self,
            text="Назначить вход в локации",
            command=self._capture_base_to_locations,
        ).pack(anchor="w", pady=(0, 10))

        locations_box = ttk.LabelFrame(self, text="7 локаций", padding=8)
        locations_box.pack(fill="x", pady=(0, 10))
        for index in range(LOCATION_SLOTS):
            row = ttk.Frame(locations_box)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{index + 1}.", width=3).pack(side="left")
            ttk.Entry(row, textvariable=self.location_vars[index], width=26).pack(side="left")
            ttk.Button(
                row,
                text="Привязать к скриншоту",
                command=lambda idx=index: self._bind_location(idx),
            ).pack(side="left", padx=6)
            ttk.Label(row, textvariable=self.status_vars[index], width=2).pack(side="left")

        floor_box = ttk.LabelFrame(self, text="Этаж", padding=8)
        floor_box.pack(fill="both", expand=True, pady=(0, 10))
        floor_row = ttk.Frame(floor_box)
        floor_row.pack(fill="x")
        ttk.Label(floor_row, text="Локация:").pack(side="left")
        combo = ttk.Combobox(
            floor_row,
            textvariable=self.floor_location_var,
            values=[str(i) for i in range(1, LOCATION_SLOTS + 1)],
            state="readonly",
            width=6,
        )
        combo.pack(side="left", padx=(6, 8))
        combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_floors())
        ttk.Button(floor_row, text="Добавить этаж", command=self._add_floor).pack(side="left")

        self.floor_list = tk.Listbox(floor_box, height=6)
        self.floor_list.pack(fill="x", pady=(8, 0))
        self._refresh_floors()

        ttk.Button(
            self,
            text="Назначить кнопку В данж",
            command=self._capture_enter_dungeon,
        ).pack(anchor="w", pady=(0, 6))

        ttk.Button(
            self,
            text="Проверить цепочку",
            command=self._verify_chain,
        ).pack(anchor="w")

    def _capture_and_store(self, prompt: str, prefix: str) -> str | None:
        selected = capture_region(self, prompt)
        if not selected:
            return None
        crop_bgr, _bbox = selected
        return _write_png(crop_bgr, prefix)

    def _capture_base_to_locations(self) -> None:
        try:
            png = self._capture_and_store(
                "Откройте главную базу в эмуляторе. На скриншоте выделите кнопку перехода к локациям.",
                "base_to_locations",
            )
            if png:
                self.data["base_to_locations"] = {"png": png}
                _save_nav(self.data)
        except Exception as exc:
            messagebox.showerror("Сохранение", str(exc), parent=self)

    def _bind_location(self, index: int) -> None:
        try:
            png = self._capture_and_store(
                f"Откройте экран выбора локаций. Выделите иконку для слота {index + 1}.",
                f"location_{index + 1}",
            )
            if png:
                item = self.data["locations"][index]
                item["slot"] = index + 1
                item["name"] = self.location_vars[index].get().strip()
                item["png"] = png
                _save_nav(self.data)
                self.status_vars[index].set("✓")
        except Exception as exc:
            messagebox.showerror("Сохранение", str(exc), parent=self)

    def _add_floor(self) -> None:
        index = int(self.floor_location_var.get()) - 1
        floor_name = simpledialog.askstring("Этаж", "Введите имя этажа:", parent=self)
        if not floor_name or not floor_name.strip():
            return
        try:
            png = self._capture_and_store(
                "Откройте экран с нужным этажом. Выделите кнопку или строку выбора этого этажа.",
                f"location_{index + 1}_floor",
            )
            if png:
                self.data["locations"][index].setdefault("floors", []).append(
                    {"name": floor_name.strip(), "png": png}
                )
                _save_nav(self.data)
                self._refresh_floors()
        except Exception as exc:
            messagebox.showerror("Сохранение", str(exc), parent=self)

    def _refresh_floors(self) -> None:
        if not hasattr(self, "floor_list"):
            return
        self.floor_list.delete(0, tk.END)
        index = int(self.floor_location_var.get()) - 1
        item = self.data["locations"][index]
        for floor in item.get("floors", []):
            self.floor_list.insert(tk.END, f"{floor.get('name', '')}: {floor.get('png', '')}")

    def _capture_enter_dungeon(self) -> None:
        try:
            png = self._capture_and_store(
                "Откройте экран выбранной локации. Выделите кнопку «В данж».",
                "enter_dungeon",
            )
            if png:
                self.data["enter_dungeon"] = {"png": png}
                _save_nav(self.data)
        except Exception as exc:
            messagebox.showerror("Сохранение", str(exc), parent=self)

    def _verify_chain(self) -> None:
        try:
            import nav

            verify = getattr(nav, "verify_chain", None)
            if verify is None:
                messagebox.showinfo(
                    "Проверка",
                    "Функция verify_chain в nav.py отсутствует.",
                    parent=self,
                )
                return
            location_name = self.location_vars[0].get().strip() or "1"
            result = verify(getattr(self.app, "project_dir", "."), location_name, "1")
            messagebox.showinfo(
                "Проверка",
                "Цепочка готова." if result else "Цепочка не готова.",
                parent=self,
            )
        except Exception as exc:
            messagebox.showerror("Проверка", str(exc), parent=self)


__all__ = ["capture_region", "NavigationTab"]
