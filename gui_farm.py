from __future__ import annotations

import json
import threading
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any


class FarmTab(ttk.Frame):
    """Панель фарма: выбирает только существующие записи nav.json."""

    PROFILES = ("base", "loc1", "loc2", "loc3", "loc4", "loc5", "loc6", "loc7")

    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.project_dir = Path(getattr(app, "project_dir", Path(__file__).resolve().parent))
        self.running = False
        self.stop_event = threading.Event()
        self.location_var = tk.StringVar()
        self.floor_var = tk.StringVar()
        self.profile_var = tk.StringVar(value=self.PROFILES[0])
        self.status_var = tk.StringVar(value="Готовность: ✗")
        self._build()
        self._reload()

    def _build(self) -> None:
        ttk.Label(self, text="Фарм", font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="Локация").pack(side="left")
        self.location_combo = ttk.Combobox(row, textvariable=self.location_var, state="readonly", width=28)
        self.location_combo.pack(side="left", padx=6)
        self.location_combo.bind("<<ComboboxSelected>>", lambda _: self._reload_floors())
        ttk.Label(row, text="Этаж").pack(side="left", padx=(12, 0))
        self.floor_combo = ttk.Combobox(row, textvariable=self.floor_var, state="readonly", width=24)
        self.floor_combo.pack(side="left", padx=6)
        self.floor_combo.bind("<<ComboboxSelected>>", lambda _: self._refresh())
        ttk.Label(row, text="Профиль").pack(side="left", padx=(12, 0))
        ttk.Combobox(row, textvariable=self.profile_var, values=self.PROFILES, state="readonly", width=12).pack(side="left", padx=6)

        ttk.Label(self, textvariable=self.status_var).pack(anchor="w", pady=(4, 2))
        self.check_frame = ttk.LabelFrame(self, text="Цепочка навигации")
        self.check_frame.pack(fill="x", pady=4)
        self.check_labels = [ttk.Label(self.check_frame, text="✗") for _ in range(4)]
        for label in self.check_labels:
            label.pack(anchor="w", padx=8, pady=1)

        ttk.Label(self, text="Сохранённые этажи").pack(anchor="w", pady=(12, 2))
        self.floor_list = tk.Listbox(self, height=7, exportselection=False)
        self.floor_list.pack(fill="both", expand=True)
        self.floor_list.bind("<<ListboxSelect>>", self._select_list_floor)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=(10, 0))
        self.start_button = ttk.Button(buttons, text="Старт", command=self._start, state="disabled")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Стоп", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)

    def _nav_data(self) -> dict[str, Any]:
        path = self.project_dir / "data" / "base" / "nav.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _locations(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return [x for x in data.get("locations", []) if isinstance(x, dict) and x.get("name")]

    def _reload(self) -> None:
        locations = self._locations(self._nav_data())
        names = [str(x["name"]) for x in locations]
        self.location_combo["values"] = names
        if self.location_var.get() not in names:
            self.location_var.set(names[0] if names else "")
        self._reload_floors()

    def _selected_location(self, data: dict[str, Any]) -> dict[str, Any] | None:
        return next((x for x in self._locations(data) if str(x.get("name")) == self.location_var.get()), None)

    def _reload_floors(self) -> None:
        location = self._selected_location(self._nav_data())
        floors = [x for x in (location or {}).get("floors", []) if isinstance(x, dict) and x.get("name")]
        names = [str(x["name"]) for x in floors]
        self.floor_combo["values"] = names
        if self.floor_var.get() not in names:
            self.floor_var.set(names[0] if names else "")
        self.floor_list.delete(0, tk.END)
        for name in names:
            self.floor_list.insert(tk.END, name)
        self._refresh()

    def _select_list_floor(self, _event=None) -> None:
        selection = self.floor_list.curselection()
        if selection:
            self.floor_var.set(self.floor_list.get(selection[0]))
            self._refresh()

    def _chain(self) -> tuple[bool, bool, bool, bool]:
        data = self._nav_data()
        location = self._selected_location(data)
        floor = next((x for x in (location or {}).get("floors", []) if isinstance(x, dict) and str(x.get("name")) == self.floor_var.get()), None)
        base = data.get("base_to_locations")
        enter = data.get("enter_dungeon")
        return (bool(isinstance(base, dict) and base.get("png")), bool(location and location.get("png")), bool(floor and floor.get("png")), bool(isinstance(enter, dict) and enter.get("png")))

    def _refresh(self) -> None:
        checks = self._chain()
        titles = ("Вход в локации", "Иконка локации", "Этаж", "В данж")
        for label, title, ok in zip(self.check_labels, titles, checks):
            label.configure(text=f"{'✓' if ok else '✗'} {title}")
        ready = all(checks) and bool(self.location_var.get()) and bool(self.floor_var.get())
        self.status_var.set("Готовность: ✓ цепочка настроена" if ready else "Готовность: ✗ настройте цепочку в базе")
        self.start_button.configure(state="normal" if ready and not self.running else "disabled")

    def _start(self) -> None:
        if not all(self._chain()):
            messagebox.showerror("Фарм", "Сначала настройте полную цепочку в панели базы.", parent=self)
            return
        if not hasattr(__import__("nav"), "go_to_dungeon"):
            messagebox.showerror("Фарм", "В nav.py отсутствует функция go_to_dungeon.", parent=self)
            return
        self.running = True
        self.stop_event.clear()
        self.stop_button.configure(state="normal")
        self._refresh()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            import nav
            ok, reason = nav.go_to_dungeon(self.project_dir, self.location_var.get(), self.floor_var.get(), stop_event=self.stop_event)
            if ok and not self.stop_event.is_set():
                command = [__import__("sys").executable, "main.py", "--profile", self.profile_var.get(), "--floor-from", "1", "--floor-to", "1"]
                # main.py has no dungeon-specific flag; these are its real profile/floor flags.
                import subprocess
                subprocess.run(command, cwd=str(self.project_dir), check=False)
            else:
                reason = str(reason)
        except Exception as exc:
            reason = str(exc)
        self.after(0, lambda: self._finished(reason))

    def _finished(self, reason: str) -> None:
        self.running = False
        self.stop_button.configure(state="disabled")
        self.status_var.set("Фарм завершён: " + reason)
        self._refresh()

    def _stop(self) -> None:
        self.stop_event.set()
        self.running = False
        self.stop_button.configure(state="disabled")
        self.status_var.set("Фарм остановлен")
        self._refresh()


FarmPanel = FarmTab


def render_hero_menu(parent, app):
    return FarmTab(parent, app)


__all__ = ["FarmTab", "FarmPanel", "render_hero_menu"]
