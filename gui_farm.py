from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from tkinter import messagebox, ttk

try:
    from gui_base import capture_region
except Exception:
    capture_region = None

try:
    from nav import NavigationResult, NavigationTarget, navigate_to_farm_start
except Exception:
    NavigationResult = NavigationTarget = None
    navigate_to_farm_start = None


LOCATION_IDS = tuple(f"loc{i}" for i in range(1, 8))
DEFAULT_LOCATIONS = tuple(f"Локация {i}" for i in range(1, 8))


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _nav_path() -> Path:
    return _project_dir() / "data" / "base" / "nav.json"


def _load_nav() -> dict[str, Any]:
    try:
        data = json.loads(_nav_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"locations": []}
    return data if isinstance(data, dict) else {"locations": []}


def _locations(data: dict[str, Any]) -> list[dict[str, Any]]:
    values = data.get("locations", [])
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _location_record(location_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
    try:
        slot = int(location_id.removeprefix("loc"))
    except ValueError:
        return None
    return next((item for item in _locations(data) if int(item.get("slot", 0)) == slot), None)


def _location_name(location_id: str, data: dict[str, Any]) -> str:
    record = _location_record(location_id, data)
    return str(record.get("name") or f"Локация {location_id.removeprefix('loc')}") if record else f"Локация {location_id.removeprefix('loc')}"


def get_available_floors(location_id: str) -> tuple[int, ...]:
    """Return floors that have a stored PNG/manifest for the selected location."""
    data = _load_nav()
    record = _location_record(location_id, data)
    result: set[int] = set()
    if record:
        for index, floor in enumerate(record.get("floors", []), 1):
            if isinstance(floor, dict) and floor.get("png"):
                result.add(index)
    root = _project_dir() / "data" / "profiles" / location_id / "templates" / "floors"
    if root.is_dir():
        for path in root.glob("floor_*"):
            if path.is_dir() and path.name[6:].isdigit() and (path / "manifest.json").is_file():
                result.add(int(path.name[6:]))
    return tuple(sorted(result))


def _floor_png(location_id: str, floor: int) -> bool:
    data = _load_nav()
    record = _location_record(location_id, data)
    if not record:
        return False
    floors = record.get("floors", [])
    if floor <= len(floors) and isinstance(floors[floor - 1], dict):
        value = floors[floor - 1].get("png")
        if value and (_project_dir() / value).is_file():
            return True
    return floor in get_available_floors(location_id)


def start_selected_farm(location_id: str, floor_number: int):
    if navigate_to_farm_start is None or NavigationTarget is None:
        return None
    return navigate_to_farm_start(NavigationTarget(location_id, floor_number))


def show_navigation_result(result: Any, parent: tk.Misc | None = None) -> None:
    if result is None:
        messagebox.showerror("Навигация", "Функция навигации в nav.py недоступна.", parent=parent)
        return
    ok = bool(getattr(result, "ok", False))
    message = getattr(result, "message", "") or ("Фарм запущен" if ok else "Навигация завершилась ошибкой")
    (messagebox.showinfo if ok else messagebox.showerror)("Навигация", message, parent=parent)


def _start_cli(location_id: str, floor: int) -> subprocess.Popen[str]:
    command = [sys.executable, "main.py", "--profile", location_id, "--floor-from", str(floor), "--floor-to", str(floor)]
    return subprocess.Popen(command, cwd=str(_project_dir()), text=True)


class FarmTab(ttk.Frame):
    """Farm panel: location, floor and profile selectors plus safe navigation."""

    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.data = _load_nav()
        self.running = False
        self.process: subprocess.Popen[str] | None = None
        self.location_var = tk.StringVar(value=LOCATION_IDS[0])
        self.floor_var = tk.StringVar()
        self.profile_var = tk.StringVar(value=getattr(app, "profile_var", tk.StringVar(value="loc1")).get() or "loc1")
        self.status_var = tk.StringVar(value="✗ цепочка не настроена")
        self._build()
        self._refresh_floors()
        self._refresh_readiness()

    def _build(self) -> None:
        ttk.Label(self, text="Фарм", font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="Локация:").pack(side="left")
        self.location_combo = ttk.Combobox(row, textvariable=self.location_var, state="readonly", values=LOCATION_IDS, width=12)
        self.location_combo.pack(side="left", padx=6)
        self.location_combo.bind("<<ComboboxSelected>>", lambda _: (self._refresh_floors(), self._refresh_readiness()))
        ttk.Label(row, text="Этаж:").pack(side="left")
        self.floor_combo = ttk.Combobox(row, textvariable=self.floor_var, state="readonly", width=10)
        self.floor_combo.pack(side="left", padx=6)
        self.floor_combo.bind("<<ComboboxSelected>>", lambda _: self._refresh_readiness())
        ttk.Label(row, text="Профиль:").pack(side="left")
        self.profile_combo = ttk.Combobox(row, textvariable=self.profile_var, state="readonly", values=LOCATION_IDS, width=12)
        self.profile_combo.pack(side="left", padx=6)
        self.profile_combo.bind("<<ComboboxSelected>>", lambda _: self._refresh_readiness())
        ttk.Label(self, textvariable=self.status_var).pack(anchor="w", pady=4)
        self.readiness = ttk.LabelFrame(self, text="Готовность цепочки")
        self.readiness.pack(fill="x", pady=6)
        self.readiness_labels: list[ttk.Label] = []
        for text in ("Вход в локации", "Иконка локации", "Этаж", "В данж"):
            label = ttk.Label(self.readiness, text=f"✗ {text}")
            label.pack(anchor="w", padx=8, pady=2)
            self.readiness_labels.append(label)
        self.floor_list = tk.Listbox(self, height=6, exportselection=False)
        self.floor_list.pack(fill="both", expand=True, pady=6)
        buttons = ttk.Frame(self)
        buttons.pack(fill="x")
        self.start_button = ttk.Button(buttons, text="Старт", command=self._start, state="disabled")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Стоп", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="Обновить", command=self._reload).pack(side="left")

    def _refresh_floors(self) -> None:
        floors = get_available_floors(self.location_var.get())
        self.floor_combo["values"] = [str(value) for value in floors]
        self.floor_var.set(str(floors[0]) if floors else "")
        if hasattr(self, "floor_list"):
            self.floor_list.delete(0, tk.END)
            for value in floors:
                self.floor_list.insert(tk.END, f"Этаж {value}")

    def _refresh_readiness(self) -> None:
        data = _load_nav()
        record = _location_record(self.location_var.get(), data)
        checks = [
            bool(data.get("base_to_locations", {}).get("png")),
            bool(record and record.get("png")),
            bool(self.floor_var.get().isdigit() and _floor_png(self.location_var.get(), int(self.floor_var.get()))),
            bool(data.get("enter_dungeon", {}).get("png")),
        ]
        names = ("Вход в локации", "Иконка локации", "Этаж", "В данж")
        for label, name, check in zip(self.readiness_labels, names, checks):
            label.configure(text=f"{'✓' if check else '✗'} {name}")
        ready = all(checks) and self.profile_var.get() in LOCATION_IDS and self.floor_var.get().isdigit()
        self.status_var.set("✓ цепочка готова" if ready else "✗ цепочка не настроена")
        self.start_button.configure(state="normal" if ready and not self.running else "disabled")

    def _reload(self) -> None:
        self.data = _load_nav()
        self._refresh_floors()
        self._refresh_readiness()

    def _start(self) -> None:
        if self.running or not self.floor_var.get().isdigit():
            return
        location_id = self.location_var.get()
        floor = int(self.floor_var.get())
        if not all((self._chain_ready(),)):
            self._refresh_readiness()
            return
        self.running = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("Старт: навигация…")
        threading.Thread(target=self._run, args=(location_id, floor), daemon=True).start()

    def _chain_ready(self) -> bool:
        data = _load_nav()
        record = _location_record(self.location_var.get(), data)
        return bool(data.get("base_to_locations", {}).get("png") and record and record.get("png") and self.floor_var.get().isdigit() and _floor_png(self.location_var.get(), int(self.floor_var.get())) and data.get("enter_dungeon", {}).get("png"))

    def _run(self, location_id: str, floor: int) -> None:
        try:
            result = start_selected_farm(location_id, floor)
            if result is None:
                result = _start_cli(location_id, floor)
            self.after(0, lambda: show_navigation_result(result, self))
        except Exception as exc:
            self.after(0, lambda: messagebox.showerror("Старт фарма", str(exc), parent=self))
        finally:
            self.after(0, self._finished)

    def _finished(self) -> None:
        self.running = False
        self.stop_button.configure(state="disabled")
        self._refresh_readiness()

    def _stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self.running = False
        self.status_var.set("Фарм остановлен")
        self.stop_button.configure(state="disabled")
        self._refresh_readiness()


def render_floor_selector(parent, location_id: str, on_floor_selected: Callable[[int], None], on_back=None):
    frame = ttk.Frame(parent, padding=12)
    floors = get_available_floors(location_id)
    variable = tk.StringVar(value=str(floors[0]) if floors else "")
    ttk.Label(frame, text=f"Этажи: {_location_name(location_id, _load_nav())}").pack(anchor="w")
    combo = ttk.Combobox(frame, textvariable=variable, values=[str(x) for x in floors], state="readonly")
    combo.pack(anchor="w", pady=6)
    ttk.Button(frame, text="Выбрать", command=lambda: on_floor_selected(int(variable.get())) if variable.get().isdigit() else None).pack(anchor="w")
    if on_back:
        ttk.Button(frame, text="Назад", command=on_back).pack(anchor="w", pady=6)
    return frame


def create_farm_view(parent, location_id: str, on_started=None, on_back=None):
    return FarmTab(parent, getattr(parent, "app", None))


# Compatibility aliases for existing callers.
FarmPanel = FarmTab
open_farm_window = lambda parent=None, config=None, logger=None, on_start=None: FarmTab(parent or tk.Tk(), getattr(parent, "app", None))
launch_farm_window = open_farm_window

__all__ = ["FarmTab", "FarmPanel", "create_farm_view", "render_floor_selector", "start_selected_farm", "get_available_floors", "show_navigation_result"]
