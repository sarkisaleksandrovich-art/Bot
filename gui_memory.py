from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from tkinter import messagebox, ttk

PROFILE_IDS = tuple(f"loc{i}" for i in range(1, 8))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _validate_json(path: Path) -> dict[str, Any]:
    return _read_json(path)


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _validate_json(temporary_path)
        temporary_path.replace(path)
        _validate_json(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _backup(path: Path) -> Path:
    _validate_json(path)
    backup = Path(f"{path}.bak")
    shutil.copy2(path, backup)
    _validate_json(backup)
    return backup


def _profile_dir(profile: str, data_root: Path | str = Path("data/profiles")) -> Path:
    if profile not in PROFILE_IDS:
        raise ValueError(f"Unknown profile: {profile}")
    return Path(data_root) / profile


def features_path(profile: str, data_root: Path | str = Path("data/profiles")) -> Path:
    return _profile_dir(profile, data_root) / "features.json"


def load_features(profile: str, data_root: Path | str = Path("data/profiles")) -> dict[str, Any]:
    return _validate_json(features_path(profile, data_root))


def _records(data: dict[str, Any]) -> list[dict[str, Any]]:
    records = data.get("templates", [])
    if not isinstance(records, list):
        raise ValueError("features.json 'templates' must be a list")
    result: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"].strip():
            raise ValueError("Each template must have a non-empty string id")
        result.append(record)
    return result


def delete_one_feature(
    profile: str,
    feature_id: str,
    data_root: Path | str = Path("data/profiles"),
    *,
    bot_running: Callable[[], bool] | bool = False,
) -> None:
    running = bot_running() if callable(bot_running) else bool(bot_running)
    if running:
        raise RuntimeError("Bot is running; memory editing is locked")
    if not isinstance(feature_id, str) or not feature_id.strip():
        raise ValueError("A single feature id is required")

    path = features_path(profile, data_root)
    original = _validate_json(path)
    records = _records(original)
    matches = [record for record in records if record.get("id") == feature_id]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one feature {feature_id!r}, found {len(matches)}")

    filename = matches[0].get("file")
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError(f"Feature {feature_id!r} has no PNG path")
    png = Path(filename)
    if not png.is_absolute():
        png = path.parent / png
    if png.suffix.lower() != ".png":
        raise ValueError(f"Refusing to delete non-PNG file: {png}")
    if not png.is_file():
        raise FileNotFoundError(f"PNG is missing; JSON was not changed: {png}")

    _backup(path)
    updated = copy.deepcopy(original)
    updated["templates"] = [record for record in records if record.get("id") != feature_id]
    updated["updated_at"] = _utc_now()
    _atomic_write_json(path, updated)
    png.unlink()
    _validate_json(path)


class MemoryTab(ttk.Frame):
    """In-place editor: exactly one selected icon may be deleted after confirmation."""

    def __init__(self, parent: tk.Misc, app: Any) -> None:
        super().__init__(parent, padding=12)
        self.app = app
        self.profile_var = tk.StringVar(value=self._initial_profile())
        self.status_var = tk.StringVar()
        self.listbox = tk.Listbox(self, selectmode=tk.SINGLE, exportselection=False)
        self._build()
        self.reload()

    def _initial_profile(self) -> str:
        value = getattr(self.app, "profile_var", None)
        selected = value.get() if hasattr(value, "get") else value
        return selected if selected in PROFILE_IDS else PROFILE_IDS[0]

    def _data_root(self) -> Path:
        root = getattr(self.app, "data_root", None)
        return Path(root) if root else Path("data/profiles")

    def _bot_running(self) -> bool:
        for name in ("is_bot_running", "bot_running"):
            value = getattr(self.app, name, False)
            if callable(value):
                return bool(value())
            if isinstance(value, bool):
                return value
        process = getattr(self.app, "current_process", None)
        return process is not None and process.poll() is None

    def _build(self) -> None:
        ttk.Label(self, text="Профиль:").pack(anchor=tk.W)
        combo = ttk.Combobox(self, textvariable=self.profile_var, values=PROFILE_IDS, state="readonly")
        combo.pack(fill=tk.X, pady=(0, 8))
        combo.bind("<<ComboboxSelected>>", lambda _event: self.reload())
        self.listbox.pack(fill=tk.BOTH, expand=True)
        ttk.Button(self, text="Удалить выбранную", command=self.delete_selected).pack(anchor=tk.E, pady=(8, 0))
        ttk.Label(self, textvariable=self.status_var).pack(anchor=tk.W, pady=(8, 0))

    def reload(self) -> None:
        self.listbox.delete(0, tk.END)
        try:
            records = _records(load_features(self.profile_var.get(), self._data_root()))
            for record in records:
                self.listbox.insert(tk.END, str(record["id"]))
            self.status_var.set(f"Иконок: {len(records)}")
        except Exception as exc:
            self.status_var.set(f"JSON: {exc}")

    def delete_selected(self) -> None:
        selection = self.listbox.curselection()
        if len(selection) != 1:
            messagebox.showwarning("Удаление", "Выберите ровно одну иконку.", parent=self.winfo_toplevel())
            return
        feature_id = self.listbox.get(selection[0])
        if self._bot_running():
            messagebox.showwarning("Удаление", "Бот работает: редактирование памяти заблокировано.", parent=self.winfo_toplevel())
            return
        if not messagebox.askyesno("Подтверждение", f"Удалить иконку {feature_id} и её PNG?\nБудет создан .bak.", parent=self.winfo_toplevel()):
            return
        try:
            delete_one_feature(self.profile_var.get(), feature_id, self._data_root(), bot_running=self._bot_running)
        except Exception as exc:
            messagebox.showerror("Удаление", str(exc), parent=self.winfo_toplevel())
            return
        self.reload()
        self.status_var.set(f"Удалено: {feature_id}; JSON проверен, .bak создан")


def open_memory_editor(parent: tk.Misc, app: Any) -> MemoryTab:
    tab = MemoryTab(parent, app)
    tab.pack(fill=tk.BOTH, expand=True)
    return tab


__all__ = ["MemoryTab", "delete_one_feature", "features_path", "load_features", "open_memory_editor"]
