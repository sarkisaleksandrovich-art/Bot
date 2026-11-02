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


DEFAULT_DATA_ROOT = Path("data/profiles")
DEFAULT_FEATURES_FILE = "features.json"


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
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _validate_json(temporary)
        temporary.replace(path)
        _validate_json(path)
    finally:
        temporary.unlink(missing_ok=True)


def _backup(path: Path) -> Path:
    _validate_json(path)
    backup = Path(f"{path}.bak")
    shutil.copy2(path, backup)
    _validate_json(backup)
    return backup


def _profiles_root(data_root: Path | str = DEFAULT_DATA_ROOT) -> Path:
    return Path(data_root)


def _profile_dir(profile: str, data_root: Path | str = DEFAULT_DATA_ROOT) -> Path:
    profile_name = str(profile).strip()
    if not profile_name:
        raise ValueError("Profile name is required")
    return _profiles_root(data_root) / profile_name


def _features_filename(app: Any | None = None) -> str:
    config = getattr(app, "config", None)
    if isinstance(config, dict):
        app_config = config.get("app")
        if isinstance(app_config, dict):
            value = app_config.get("features_file")
            if isinstance(value, str) and value.strip():
                return Path(value).name
    return DEFAULT_FEATURES_FILE


def features_path(
    profile: str,
    data_root: Path | str = DEFAULT_DATA_ROOT,
    *,
    features_file: str = DEFAULT_FEATURES_FILE,
) -> Path:
    filename = Path(features_file).name
    if not filename:
        raise ValueError("features_file is required")
    return _profile_dir(profile, data_root) / filename


def load_features(
    profile: str,
    data_root: Path | str = DEFAULT_DATA_ROOT,
    *,
    features_file: str = DEFAULT_FEATURES_FILE,
) -> dict[str, Any]:
    return _validate_json(features_path(profile, data_root, features_file=features_file))


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


def list_profiles(data_root: Path | str = DEFAULT_DATA_ROOT) -> list[str]:
    root = _profiles_root(data_root)
    if not root.exists():
        return []
    return sorted(item.name for item in root.iterdir() if item.is_dir() and item.name != "base")


def list_feature_ids(
    profile: str,
    data_root: Path | str = DEFAULT_DATA_ROOT,
    *,
    features_file: str = DEFAULT_FEATURES_FILE,
) -> list[str]:
    return [str(record["id"]) for record in _records(load_features(profile, data_root, features_file=features_file))]


def delete_one_feature(
    profile: str,
    feature_id: str,
    data_root: Path | str = DEFAULT_DATA_ROOT,
    *,
    bot_running: Callable[[], bool] | bool = False,
    features_file: str = DEFAULT_FEATURES_FILE,
) -> None:
    running = bot_running() if callable(bot_running) else bool(bot_running)
    if running:
        raise RuntimeError("Bot is running; memory editing is locked")
    if not isinstance(feature_id, str) or not feature_id.strip():
        raise ValueError("A single feature id is required")

    path = features_path(profile, data_root, features_file=features_file)
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

    updated = copy.deepcopy(original)
    updated["templates"] = [record for record in records if record.get("id") != feature_id]
    updated["updated_at"] = _utc_now()

    backup = _backup(path)
    try:
        png.unlink()
        _atomic_write_json(path, updated)
        _validate_json(path)
    except Exception:
        try:
            _validate_json(backup)
            shutil.copy2(backup, path)
            _validate_json(path)
        except Exception as restore_error:
            raise RuntimeError(f"Deletion failed and JSON rollback failed: {restore_error}") from restore_error
        raise


class MemoryTab(ttk.Frame):
    """Tab for deleting exactly one selected icon from the chosen profile."""

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
        selected_text = str(selected).strip() if selected is not None else ""
        if selected_text:
            return selected_text
        profiles = list_profiles(self._data_root())
        return profiles[0] if profiles else ""

    def _data_root(self) -> Path:
        root = getattr(self.app, "data_root", None)
        if root:
            return Path(root)
        project_dir = getattr(self.app, "project_dir", None)
        if project_dir:
            return Path(project_dir) / DEFAULT_DATA_ROOT
        return DEFAULT_DATA_ROOT

    def _features_file(self) -> str:
        return _features_filename(self.app)

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
        combo = ttk.Combobox(self, textvariable=self.profile_var, state="readonly")
        combo.pack(fill=tk.X, pady=(0, 8))
        combo.bind("<<ComboboxSelected>>", lambda _event: self.reload())
        self.profile_combo = combo

        ttk.Label(self, text="Иконки профиля:").pack(anchor=tk.W)
        self.listbox.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        ttk.Button(self, text="Удалить выбранную", command=self.delete_selected).pack(anchor=tk.E)
        ttk.Label(self, textvariable=self.status_var).pack(anchor=tk.W, pady=(8, 0))

    def reload(self) -> None:
        profiles = list_profiles(self._data_root())
        self.profile_combo["values"] = profiles

        current = self.profile_var.get().strip()
        app_profile_var = getattr(self.app, "profile_var", None)
        app_current = app_profile_var.get().strip() if hasattr(app_profile_var, "get") else ""
        if app_current and app_current in profiles and current != app_current:
            self.profile_var.set(app_current)
            current = app_current
        elif profiles and current not in profiles:
            self.profile_var.set(profiles[0])
            current = profiles[0]

        self.listbox.delete(0, tk.END)
        if not current:
            self.status_var.set("Нет профилей")
            return

        try:
            records = _records(load_features(current, self._data_root(), features_file=self._features_file()))
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
            messagebox.showwarning(
                "Удаление заблокировано",
                "Нельзя изменять память при работающем боте.",
                parent=self.winfo_toplevel(),
            )
            return

        if not messagebox.askyesno(
            "Подтверждение",
            f"Удалить иконку {feature_id} и её PNG?\nБудет создан {self._features_file()}.bak.",
            parent=self.winfo_toplevel(),
        ):
            return

        try:
            delete_one_feature(
                self.profile_var.get(),
                feature_id,
                self._data_root(),
                bot_running=self._bot_running,
                features_file=self._features_file(),
            )
        except Exception as exc:
            messagebox.showerror("Удаление", str(exc), parent=self.winfo_toplevel())
            return

        self.reload()
        self.status_var.set(f"Удалено: {feature_id}; JSON проверен, создан .bak")


def open_memory_editor(parent: tk.Misc, app: Any) -> MemoryTab:
    tab = MemoryTab(parent, app)
    tab.pack(fill=tk.BOTH, expand=True)
    return tab


__all__ = [
    "MemoryTab",
    "delete_one_feature",
    "features_path",
    "list_feature_ids",
    "list_profiles",
    "load_features",
    "open_memory_editor",
]
