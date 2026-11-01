from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

LOCATION_IDS = tuple(f"loc{i}" for i in range(1, 8))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _base_dir(config: Any = None) -> Path:
    value = getattr(config, "base_dir", None)
    return Path(value).resolve() if value else _project_dir()


def _profiles_root(config: Any = None) -> Path:
    raw = None
    if config is not None:
        try:
            raw = config.raw.get("profiles", {}).get("root_dir")
        except AttributeError:
            raw = None
    root = Path(raw or "data/profiles")
    return (root if root.is_absolute() else _base_dir(config) / root).resolve()


def floor_template_dir(location_id: str, floor_number: int, config: Any = None) -> Path:
    if location_id not in LOCATION_IDS:
        raise ValueError(f"location_id must be one of {LOCATION_IDS}")
    if isinstance(floor_number, bool) or not isinstance(floor_number, int) or floor_number < 1:
        raise ValueError("floor_number must be an integer >= 1")
    return _profiles_root(config) / location_id / "floors" / f"floor_{floor_number:03d}"


def floor_manifest_path(config: Any, location_id: str, floor_number: int) -> Path:
    return floor_template_dir(location_id, floor_number, config) / "floor_templates.json"


def load_floor_templates(config: Any, location_id: str, floor_number: int) -> dict[str, Any]:
    path = floor_manifest_path(config, location_id, floor_number)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 1, "location_id": location_id, "floor_number": floor_number, "templates": []}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load floor manifest {path}: {exc}") from exc
    validate_floor_templates(data, location_id, floor_number)
    return data


def validate_floor_templates(data: dict[str, Any], location_id: str, floor_number: int) -> None:
    if not isinstance(data, dict):
        raise ValueError("floor manifest must be an object")
    if data.get("location_id", location_id) != location_id:
        raise ValueError("floor manifest location_id does not match selected location")
    if int(data.get("floor_number", floor_number)) != floor_number:
        raise ValueError("floor manifest floor_number does not match selected floor")
    if not isinstance(data.get("templates", []), list):
        raise ValueError("floor manifest templates must be a list")


def build_farm_target(location_id: str, floor_number: int) -> dict[str, Any]:
    if location_id not in LOCATION_IDS or floor_number < 1:
        raise ValueError("invalid farm target")
    return {"profile": location_id, "floor_from": floor_number, "floor_to": floor_number}


def start_farm(config: Any, logger: Any, *, location_id: str, floor_number: int) -> int:
    """Run the existing CLI farm path; no new explorer logic is introduced."""
    target = build_farm_target(location_id, floor_number)
    command = [sys.executable, "main.py", "--profile", target["profile"], "--floor-from", str(target["floor_from"]), "--floor-to", str(target["floor_to"])]
    config_path = getattr(config, "path", None) or getattr(config, "config_path", None)
    if config_path:
        command.extend(["--config", str(config_path)])
    try:
        return subprocess.call(command, cwd=str(_base_dir(config)))
    except OSError as exc:
        if logger and hasattr(logger, "error"):
            logger.error("farm process failed: %s", exc)
        return 1


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _copy_screenshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


class FarmPanel(ttk.Frame):
    def __init__(self, master: tk.Misc, config: Any = None, logger: Any = None, *, on_start: Callable[..., Any] | None = None) -> None:
        super().__init__(master, padding=10)
        self.config_obj = config
        self.logger = logger
        self.on_start = on_start
        self.location_var = tk.StringVar(value="loc1")
        self.status_var = tk.StringVar(value="Выберите локацию и этаж из списка")
        self.running = False
        self.process: subprocess.Popen[str] | None = None
        self._build()
        self._refresh_floors()

    def _build(self) -> None:
        ttk.Label(self, text="Фарм", font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(8, 4))
        ttk.Label(row, text="Локация:").pack(side="left")
        ttk.Combobox(row, textvariable=self.location_var, values=LOCATION_IDS, state="readonly", width=12).pack(side="left", padx=8)
        self.location_var.trace_add("write", lambda *_: self._refresh_floors())
        ttk.Button(row, text="Добавить этаж", command=self._add_floor).pack(side="left")
        ttk.Label(self, text="Сохранённые этажи:").pack(anchor="w", pady=(8, 2))
        self.floor_list = tk.Listbox(self, height=8, exportselection=False)
        self.floor_list.pack(fill="both", expand=True)
        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=(8, 0))
        self.start_button = ttk.Button(controls, text="Старт фарма", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="Стоп", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(self, textvariable=self.status_var, foreground="#555").pack(anchor="w", pady=(8, 0))

    def _floor_root(self) -> Path:
        return _profiles_root(self.config_obj) / self.location_var.get() / "floors"

    def _refresh_floors(self) -> None:
        self.floor_list.delete(0, tk.END)
        root = self._floor_root()
        if root.exists():
            for item in sorted(root.glob("floor_*")):
                if item.is_dir() and item.name[6:].isdigit() and (item / "floor_templates.json").exists():
                    self.floor_list.insert(tk.END, f"Этаж {int(item.name[6:])}")

    def _add_floor(self) -> None:
        source = filedialog.askopenfilename(title="Выберите PNG-скриншот этажа", filetypes=[("PNG", "*.png"), ("Все файлы", "*.*")])
        if not source:
            return
        name = simpledialog.askstring("Имя этажа", "Имя шаблона (например, комната):", parent=self)
        if not name:
            return
        floor_number = self._next_floor_number()
        destination = floor_template_dir(self.location_var.get(), floor_number, self.config_obj) / "templates" / f"floor_{floor_number:03d}_{name.strip()}.png"
        try:
            _copy_screenshot(Path(source), destination)
            manifest = {"schema_version": 1, "location_id": self.location_var.get(), "floor_number": floor_number, "updated_at": _utc_now(), "templates": [{"id": f"{self.location_var.get()}_floor{floor_number}_{name.strip()}", "category": "floor_screen", "file": f"templates/{destination.name}", "enabled": True, "threshold": 0.86, "roi": [0, 0, 1280, 720], "tags": [self.location_var.get(), f"floor{floor_number}"]}]}
            _write_json(destination.parent.parent / "floor_templates.json", manifest)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Ошибка", str(exc), parent=self)
            return
        self._refresh_floors()
        self.status_var.set(f"Сохранён этаж {floor_number} для {self.location_var.get()}")

    def _next_floor_number(self) -> int:
        numbers = [int(p.name[6:]) for p in self._floor_root().glob("floor_*") if p.is_dir() and p.name[6:].isdigit()]
        return max(numbers, default=0) + 1

    def _selected_floor(self) -> int | None:
        selected = self.floor_list.curselection()
        if not selected:
            return None
        text = self.floor_list.get(selected[0])
        return int(text.split()[-1])

    def _start(self) -> None:
        floor = self._selected_floor()
        if floor is None:
            messagebox.showwarning("Фарм", "Сначала добавьте или выберите этаж.", parent=self)
            return
        if self.running:
            return
        self.running = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("Запуск фарма…")
        threading.Thread(target=self._run, args=(self.location_var.get(), floor), daemon=True).start()

    def _run(self, location_id: str, floor: int) -> None:
        try:
            result = self.on_start(location_id, floor) if self.on_start else start_farm(self.config_obj, self.logger, location_id=location_id, floor_number=floor)
            self.after(0, lambda: self.status_var.set(f"Фарм завершён, код {result}"))
        except Exception as exc:
            self.after(0, lambda: self.status_var.set(f"Ошибка фарма: {exc}"))
        finally:
            self.after(0, self._finished)

    def _finished(self) -> None:
        self.running = False
        self.process = None
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def _stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        self.status_var.set("Остановка запрошена")
        self._finished()


def open_farm_window(parent: tk.Misc | None = None, config: Any = None, logger: Any = None, *, on_start: Callable[..., Any] | None = None) -> FarmPanel:
    """Open the Farm panel. Compatible with GUI orchestrators that embed a frame."""
    if parent is None:
        parent = tk.Tk()
    panel = FarmPanel(parent, config, logger, on_start=on_start)
    panel.pack(fill="both", expand=True)
    return panel


def launch_farm_window(parent: tk.Misc | None = None, config: Any = None, logger: Any = None) -> FarmPanel:
    return open_farm_window(parent, config, logger)


__all__ = ["FarmPanel", "open_farm_window", "launch_farm_window", "floor_manifest_path", "load_floor_templates", "validate_floor_templates", "build_farm_target", "start_farm"]
