from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from tkinter import messagebox, ttk

try:
    from nav import NavigationResult
except ImportError:  # pragma: no cover
    NavigationResult = Any  # type: ignore[misc,assignment]

LOCATION_IDS = tuple(f"loc{i}" for i in range(1, 8))


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _nav_path() -> Path:
    return _project_dir() / "data" / "base" / "nav.json"


def _load_nav() -> dict[str, Any]:
    try:
        value = json.loads(_nav_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"locations": []}
    return value if isinstance(value, dict) else {"locations": []}


def _locations(data: dict[str, Any]) -> list[dict[str, Any]]:
    value = data.get("locations", [])
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _record(data: dict[str, Any], location_id: str) -> dict[str, Any] | None:
    try:
        slot = int(location_id.removeprefix("loc"))
    except ValueError:
        return None
    return next((x for x in _locations(data) if x.get("slot") == slot or x.get("id") == location_id), None)


def _location_name(data: dict[str, Any], location_id: str) -> str:
    item = _record(data, location_id)
    return str(item.get("name") or location_id) if item else location_id


def get_available_floors(location_id: str) -> tuple[str, ...]:
    item = _record(_load_nav(), location_id)
    floors = item.get("floors", []) if item else []
    return tuple(str(x.get("name")) for x in floors if isinstance(x, dict) and x.get("name"))


def _floor_number(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def start_farm_navigation(location_id: str, floor_number: int) -> Any:
    callback = getattr(start_farm_navigation, "callback", None)
    if callback is None:
        raise RuntimeError("Навигация недоступна: в gui.py не настроен callback NavigationController")
    return callback(location_id, floor_number)


def on_start_farm_clicked(location_id: str, floor_number: int) -> Any:
    return start_farm_navigation(location_id, floor_number)


class FarmTab(ttk.Frame):
    """Панель фарма: локация, сохранённый этаж, профиль и запуск цепочки."""

    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.data = _load_nav()
        self.running = False
        self.location_var = tk.StringVar(value=LOCATION_IDS[0])
        self.floor_var = tk.StringVar()
        self.profile_var = tk.StringVar(value="loc1")
        self.status_var = tk.StringVar(value="Цепочка не настроена")
        self._build()
        self._refresh_floors()
        self._refresh_readiness()

    def _build(self) -> None:
        ttk.Label(self, text="Фарм", font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
        row = ttk.Frame(self); row.pack(fill="x", pady=8)
        ttk.Label(row, text="Локация:").pack(side="left")
        self.location_combo = ttk.Combobox(row, textvariable=self.location_var, state="readonly", width=22,
            values=tuple(_location_name(self.data, x) for x in LOCATION_IDS))
        self.location_combo.pack(side="left", padx=6); self.location_combo.bind("<<ComboboxSelected>>", self._location_selected)
        ttk.Label(row, text="Этаж:").pack(side="left")
        self.floor_combo = ttk.Combobox(row, textvariable=self.floor_var, state="readonly", width=18)
        self.floor_combo.pack(side="left", padx=6); self.floor_combo.bind("<<ComboboxSelected>>", lambda _: self._refresh_readiness())
        ttk.Label(row, text="Профиль:").pack(side="left")
        self.profile_combo = ttk.Combobox(row, textvariable=self.profile_var, state="readonly", values=LOCATION_IDS, width=12)
        self.profile_combo.pack(side="left", padx=6); self.profile_combo.bind("<<ComboboxSelected>>", lambda _: self._refresh_readiness())
        ttk.Label(self, textvariable=self.status_var).pack(anchor="w", pady=(2, 8))
        self.readiness = ttk.Frame(self); self.readiness.pack(fill="x")
        self.readiness_labels = []
        for title in ("Вход в локации", "Иконка локации", "Этаж", "В данж"):
            label = ttk.Label(self.readiness, text=f"✗ {title}"); label.pack(anchor="w", pady=2); self.readiness_labels.append(label)
        ttk.Label(self, text="Сохранённые этажи").pack(anchor="w", pady=(8, 0))
        self.floor_list = tk.Listbox(self, height=6, exportselection=False); self.floor_list.pack(fill="both", expand=True, pady=4)
        self.floor_list.bind("<<ListboxSelect>>", self._list_floor_selected)
        buttons = ttk.Frame(self); buttons.pack(fill="x", pady=(6, 0))
        self.start_button = ttk.Button(buttons, text="Старт фарма", command=self._start, state="disabled"); self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Стоп", command=self._stop, state="disabled"); self.stop_button.pack(side="left", padx=8)

    def _selected_location_id(self) -> str:
        value = self.location_var.get()
        return next((x for x in LOCATION_IDS if value in (x, _location_name(self.data, x))), LOCATION_IDS[0])

    def _location_selected(self, _event=None) -> None:
        self.profile_var.set(self._selected_location_id()); self._refresh_floors(); self._refresh_readiness()

    def _refresh_floors(self) -> None:
        floors = get_available_floors(self._selected_location_id())
        self.floor_combo["values"] = floors; self.floor_var.set(floors[0] if floors else "")
        self.floor_list.delete(0, tk.END)
        for floor in floors: self.floor_list.insert(tk.END, floor)

    def _list_floor_selected(self, _event=None) -> None:
        selected = self.floor_list.curselection()
        if selected: self.floor_var.set(self.floor_list.get(selected[0])); self._refresh_readiness()

    def _chain_checks(self) -> tuple[bool, bool, bool, bool]:
        item = _record(self.data, self._selected_location_id())
        return (bool(self.data.get("base_to_locations", {}).get("png")), bool(item and item.get("png")),
                self.floor_var.get() in get_available_floors(self._selected_location_id()),
                bool(self.data.get("enter_dungeon", {}).get("png")))

    def _refresh_readiness(self) -> None:
        checks = self._chain_checks(); names = ("Вход в локации", "Иконка локации", "Этаж", "В данж")
        for label, name, ready in zip(self.readiness_labels, names, checks): label.configure(text=f"{'✓' if ready else '✗'} {name}")
        ready = all(checks) and self.profile_var.get() == self._selected_location_id()
        self.status_var.set("Цепочка готова" if ready else "Цепочка не настроена")
        self.start_button.configure(state="normal" if ready and not self.running else "disabled")

    def _start(self) -> None:
        floor = _floor_number(self.floor_var.get()); location_id = self._selected_location_id()
        if floor is None or not all(self._chain_checks()):
            messagebox.showerror("Фарм", "Сначала настройте всю цепочку навигации.", parent=self); return
        self.running = True; self.start_button.configure(state="disabled"); self.stop_button.configure(state="normal"); self.status_var.set("Запуск навигации…")
        threading.Thread(target=self._run, args=(location_id, floor), daemon=True).start()

    def _run(self, location_id: str, floor: int) -> None:
        try:
            callback: Callable[[str, int], Any] = getattr(self.app, "start_farm_navigation", start_farm_navigation)
            result = callback(location_id, floor)
        except Exception as exc: result = exc
        self.after(0, lambda: self._finished(result))

    def _finished(self, result: Any) -> None:
        self.running = False; self.stop_button.configure(state="disabled")
        ok = bool(getattr(result, "ok", False)) and not isinstance(result, Exception)
        self.status_var.set("Фарм запущен" if ok else "Ошибка запуска")
        if not ok: messagebox.showerror("Фарм", str(getattr(result, "message", result)), parent=self)
        self._refresh_readiness()

    def _stop(self) -> None:
        self.running = False; self.stop_button.configure(state="disabled"); self.status_var.set("Фарм остановлен"); self._refresh_readiness()


def render_hero_menu(parent, app) -> FarmTab:
    return FarmTab(parent, app)


FarmPanel = FarmTab
create_farm_view = lambda parent, **_kwargs: FarmTab(parent, getattr(parent, "app", None))
__all__ = ["FarmTab", "FarmPanel", "create_farm_view", "render_hero_menu", "on_start_farm_clicked", "get_available_floors"]
