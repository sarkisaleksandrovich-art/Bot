from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox, ttk


class FarmTab(ttk.Frame):
    """Farm panel: choose taught location/floor/profile and run nav -> main."""

    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.project_dir = Path(getattr(app, "project_dir", Path(__file__).resolve().parent))
        self.running = False
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.process: subprocess.Popen[str] | None = None

        self.location_var = tk.StringVar()
        self.floor_var = tk.StringVar()
        self.profile_var = tk.StringVar(value=getattr(app, "profile_var", tk.StringVar(value="")).get())
        self.status_var = tk.StringVar(value="Готовность: ✗")

        self.chain_vars = {
            "base_to_locations": tk.StringVar(value="✗ Вход в локации"),
            "location_icon": tk.StringVar(value="✗ Иконка локации"),
            "floor": tk.StringVar(value="✗ Этаж"),
            "enter_dungeon": tk.StringVar(value="✗ В данж"),
        }

        self._build()
        self._reload_profiles()
        self._reload_locations()
        self._refresh_ready_state()

    def _build(self) -> None:
        ttk.Label(self, text="Фарм", font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
        ttk.Label(
            self,
            text=(
                "Локации и этажи берутся из data/base/nav.json. "
                "Если чего-то нет, сначала обучите это на вкладке «Навигация»."
            ),
            wraplength=950,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(4, 10))

        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=(0, 10))

        ttk.Label(controls, text="Локация").grid(row=0, column=0, sticky="w")
        self.location_combo = ttk.Combobox(
            controls,
            textvariable=self.location_var,
            state="readonly",
            width=30,
        )
        self.location_combo.grid(row=0, column=1, sticky="w", padx=(6, 16))
        self.location_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_location_changed())

        ttk.Label(controls, text="Этаж").grid(row=0, column=2, sticky="w")
        self.floor_combo = ttk.Combobox(
            controls,
            textvariable=self.floor_var,
            state="readonly",
            width=26,
        )
        self.floor_combo.grid(row=0, column=3, sticky="w", padx=(6, 16))
        self.floor_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_ready_state())

        ttk.Label(controls, text="Профиль").grid(row=0, column=4, sticky="w")
        self.profile_combo = ttk.Combobox(
            controls,
            textvariable=self.profile_var,
            state="readonly",
            width=24,
        )
        self.profile_combo.grid(row=0, column=5, sticky="w", padx=(6, 0))

        status_box = ttk.LabelFrame(self, text="Готовность цепочки", padding=8)
        status_box.pack(fill="x", pady=(0, 10))
        for key in ("base_to_locations", "location_icon", "floor", "enter_dungeon"):
            ttk.Label(status_box, textvariable=self.chain_vars[key]).pack(anchor="w", pady=1)

        ttk.Label(self, textvariable=self.status_var).pack(anchor="w", pady=(0, 10))

        floors_box = ttk.LabelFrame(self, text="Сохранённые этажи выбранной локации", padding=8)
        floors_box.pack(fill="both", expand=True, pady=(0, 10))
        self.floor_list = tk.Listbox(floors_box, height=8, exportselection=False)
        self.floor_list.pack(fill="both", expand=True)
        self.floor_list.bind("<<ListboxSelect>>", self._select_floor_from_list)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x")
        self.start_button = ttk.Button(buttons, text="Старт", command=self._start, state="disabled")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Стоп", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Обновить", command=self._full_reload).pack(side="left", padx=(8, 0))

    def _nav_data(self) -> dict[str, Any]:
        try:
            import json

            path = self.project_dir / "data" / "base" / "nav.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _locations(self) -> list[dict[str, Any]]:
        data = self._nav_data()
        value = data.get("locations", [])
        return [item for item in value if isinstance(item, dict) and str(item.get("name", "")).strip()]

    def _selected_location(self) -> dict[str, Any] | None:
        name = self.location_var.get().strip()
        for item in self._locations():
            if str(item.get("name", "")).strip() == name:
                return item
        return None

    def _reload_profiles(self) -> None:
        profiles_root = self.project_dir / "data" / "profiles"
        profiles = sorted(
            item.name
            for item in profiles_root.iterdir()
            if item.is_dir() and item.name != "base"
        ) if profiles_root.exists() else []
        if not profiles:
            profiles = ["loc1"]
        self.profile_combo["values"] = profiles
        if self.profile_var.get().strip() not in profiles:
            self.profile_var.set(profiles[0])

    def _reload_locations(self) -> None:
        names = [str(item.get("name", "")).strip() for item in self._locations()]
        self.location_combo["values"] = names
        if self.location_var.get().strip() not in names:
            self.location_var.set(names[0] if names else "")
        self._reload_floors()

    def _reload_floors(self) -> None:
        location = self._selected_location()
        floors = location.get("floors", []) if isinstance(location, dict) else []
        names = [str(item.get("name", "")).strip() for item in floors if isinstance(item, dict) and str(item.get("name", "")).strip()]
        self.floor_combo["values"] = names
        if self.floor_var.get().strip() not in names:
            self.floor_var.set(names[0] if names else "")

        self.floor_list.delete(0, tk.END)
        for name in names:
            self.floor_list.insert(tk.END, name)
        if self.floor_var.get().strip() in names:
            idx = names.index(self.floor_var.get().strip())
            self.floor_list.selection_clear(0, tk.END)
            self.floor_list.selection_set(idx)
        self._refresh_ready_state()

    def _on_location_changed(self) -> None:
        self._reload_floors()

    def _select_floor_from_list(self, _event=None) -> None:
        selection = self.floor_list.curselection()
        if selection:
            self.floor_var.set(self.floor_list.get(selection[0]))
            self._refresh_ready_state()

    def _refresh_ready_state(self) -> None:
        data = self._nav_data()
        location = self._selected_location()
        floor = None
        if isinstance(location, dict):
            for item in location.get("floors", []):
                if isinstance(item, dict) and str(item.get("name", "")).strip() == self.floor_var.get().strip():
                    floor = item
                    break

        base = data.get("base_to_locations") if isinstance(data.get("base_to_locations"), dict) else {}
        enter = data.get("enter_dungeon") if isinstance(data.get("enter_dungeon"), dict) else {}

        checks = {
            "base_to_locations": bool(str(base.get("png", "")).strip()),
            "location_icon": bool(location and str(location.get("png", "")).strip()),
            "floor": bool(floor and str(floor.get("png", "")).strip()),
            "enter_dungeon": bool(str(enter.get("png", "")).strip()),
        }
        titles = {
            "base_to_locations": "Вход в локации",
            "location_icon": "Иконка локации",
            "floor": "Этаж",
            "enter_dungeon": "В данж",
        }
        for key, ok in checks.items():
            self.chain_vars[key].set(f"{'✓' if ok else '✗'} {titles[key]}")

        ready = all(checks.values()) and bool(self.location_var.get().strip()) and bool(self.floor_var.get().strip())
        self.status_var.set(
            "Готовность: ✓ цепочка настроена"
            if ready
            else "Готовность: ✗ сначала обучите цепочку на вкладке «Навигация»"
        )

        self.start_button.configure(state="normal" if ready and not self.running else "disabled")
        self.stop_button.configure(state="normal" if self.running else "disabled")
        self.location_combo.configure(state="disabled" if self.running else "readonly")
        self.floor_combo.configure(state="disabled" if self.running else "readonly")
        self.profile_combo.configure(state="disabled" if self.running else "readonly")

    def _full_reload(self) -> None:
        self._reload_profiles()
        self._reload_locations()
        self._refresh_ready_state()

    def _start(self) -> None:
        self._refresh_ready_state()
        if self.start_button.cget("state") == "disabled":
            messagebox.showwarning(
                "Фарм",
                "Старт недоступен, пока не настроена вся цепочка: вход в локации → иконка локации → этаж → «В данж».",
                parent=self,
            )
            return
        try:
            import nav
        except Exception as exc:
            messagebox.showerror("Фарм", f"Не удалось импортировать nav.py: {exc}", parent=self)
            return
        if not hasattr(nav, "go_to_dungeon"):
            messagebox.showinfo(
                "Фарм",
                "В nav.py отсутствует функция go_to_dungeon(project_dir, location_name, floor_name, log, stop_event).",
                parent=self,
            )
            return

        self.running = True
        self.stop_event.clear()
        self.status_var.set("Фарм: переход база → локация → этаж → «В данж»...")
        self._refresh_ready_state()
        self.worker = threading.Thread(target=self._run_worker, daemon=True)
        self.worker.start()

    def _run_worker(self) -> None:
        message = "Остановлено"
        try:
            import nav

            ok, reason = nav.go_to_dungeon(
                self.project_dir,
                self.location_var.get().strip(),
                self.floor_var.get().strip(),
                log=self._log,
                stop_event=self.stop_event,
            )
            if not ok:
                message = f"Фарм не запущен: {reason}"
            elif self.stop_event.is_set():
                message = "Остановлено"
            else:
                code, out = self._run_main_process()
                message = "Фарм завершён" if code == 0 else f"main.py завершился с кодом {code}: {out}"
        except Exception as exc:
            message = f"Ошибка: {exc}"
        self.after(0, lambda: self._finish(message))

    def _run_main_process(self) -> tuple[int, str]:
        command = [
            sys.executable,
            "main.py",
            "--profile",
            self.profile_var.get().strip(),
            "--floor-from",
            "1",
            "--floor-to",
            "1",
        ]
        self._log(f"[FARM] cmd={' '.join(command)}")
        self.process = subprocess.Popen(
            command,
            cwd=str(self.project_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        last_line = ""
        assert self.process.stdout is not None
        for line in self.process.stdout:
            last_line = line.rstrip("\n")
            self._log(last_line)
            if self.stop_event.is_set() and self.process.poll() is None:
                self.process.terminate()
        code = self.process.wait()
        self.process = None
        return code, last_line

    def _stop(self) -> None:
        self.stop_event.set()
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass
        self.running = False
        self.status_var.set("Фарм остановлен")
        self._refresh_ready_state()

    def _finish(self, message: str) -> None:
        self.running = False
        self.process = None
        self.status_var.set(message)
        self._refresh_ready_state()

    def _log(self, *parts: Any) -> None:
        text = " ".join(str(part) for part in parts)
        if hasattr(self.app, "log"):
            try:
                self.app.log(text)
                return
            except Exception:
                pass
        print(text)


FarmPanel = FarmTab


def render_hero_menu(parent, app):
    return FarmTab(parent, app)


__all__ = ["FarmTab", "FarmPanel", "render_hero_menu"]
