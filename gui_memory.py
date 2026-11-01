from __future__ import annotations

"""Safe, single-record GUI memory editor."""

import copy
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
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


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        _validate_json(temporary_path)
        os.replace(temporary_path, path)
        _validate_json(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _backup(path: Path) -> Path:
    backup = Path(f"{path}.bak")
    if path.exists():
        _validate_json(path)
        shutil.copy2(path, backup)
        _validate_json(backup)
    return backup


def _profile_dir(
    profile: str,
    data_root: Path | str = Path("data/profiles"),
) -> Path:
    if profile not in PROFILE_IDS:
        raise ValueError(f"Unknown profile: {profile!r}")
    return Path(data_root) / profile


def features_path(
    profile: str,
    data_root: Path | str = Path("data/profiles"),
) -> Path:
    return _profile_dir(profile, data_root) / "features.json"


def delete_one_feature(
    profile: str,
    feature_id: str,
    data_root: Path | str = Path("data/profiles"),
    *,
    bot_running: Callable[[], bool] | bool = False,
) -> None:
    """Delete exactly one feature and its PNG, preserving a validated .bak."""

    if bot_running() if callable(bot_running) else bool(bot_running):
        raise RuntimeError("Bot is running; memory editing is locked")

    if not isinstance(feature_id, str) or not feature_id.strip():
        raise ValueError("A single feature id is required")

    path = features_path(profile, data_root)
    original = _validate_json(path)
    records = _records(original)

    matches = [item for item in records if item.get("id") == feature_id]

    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one feature named {feature_id!r}, "
            f"found {len(matches)}"
        )

    filename = matches[0].get("file")
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError(f"Feature {feature_id!r} has no valid PNG path")

    png = Path(filename)
    if not png.is_absolute():
        png = path.parent / png
    if png.exists() and png.suffix.lower() != ".png":
        raise ValueError(f"Refusing to delete non-PNG file: {png}")

    backup = _backup(path)

    updated = copy.deepcopy(original)
    updated["templates"] = [item for item in records if item.get("id") != feature_id]
    updated["updated_at"] = _utc_now()

    try:
        _atomic_json_write(path, updated)
        if png.exists():
            png.unlink()
    except Exception:
        shutil.copy2(backup, path)
        _validate_json(path)
        raise

    _validate_json(path)


def _records(data: dict[str, Any]) -> list[dict[str, Any]]:
    records = data.get("templates", [])
    if not isinstance(records, list):
        raise ValueError("features.json 'templates' must be a list")

    result: list[dict[str, Any]] = []
    for item in records:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not item["id"].strip()
        ):
            raise ValueError("Each feature record must have a non-empty string id")
        result.append(item)
    return result


def load_features(
    profile: str,
    data_root: Path | str = Path("data/profiles"),
) -> dict[str, Any]:
    return _validate_json(features_path(profile, data_root))


def _default_data_root(config: Any = None) -> Path:
    raw = getattr(config, "raw", None)

    if isinstance(raw, dict):
        root = raw.get("profiles", {}).get("root_dir")
        if isinstance(root, str) and root.strip():
            return Path(root)

    return Path("data/profiles")


@dataclass
class MemoryEditor:
    parent: tk.Misc
    data_root: Path
    bot_running: Callable[[], bool] | bool = False

    def __post_init__(self) -> None:
        self.window = tk.Toplevel(self.parent)
        self.window.title("Редактирование памяти")
        self.window.geometry("760x480")

        self.profile_var = tk.StringVar(value=PROFILE_IDS[0])
        self.status_var = tk.StringVar(value="Выберите одну иконку")

        top = ttk.Frame(self.window, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Профиль:").pack(side=tk.LEFT)

        combo = ttk.Combobox(
            top,
            textvariable=self.profile_var,
            values=PROFILE_IDS,
            state="readonly",
            width=12,
        )
        combo.pack(side=tk.LEFT, padx=8)
        combo.bind("<<ComboboxSelected>>", lambda _event: self.reload())

        ttk.Button(top, text="Обновить", command=self.reload).pack(side=tk.LEFT)

        body = ttk.Frame(self.window, padding=(10, 0, 10, 10))
        body.pack(fill=tk.BOTH, expand=True)

        self.listbox = tk.Listbox(body, selectmode=tk.SINGLE, exportselection=False)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.listbox.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.configure(yscrollcommand=scroll.set)

        bottom = ttk.Frame(self.window, padding=10)
        bottom.pack(fill=tk.X)

        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(bottom, text="Удалить выбранную", command=self.delete_selected).pack(side=tk.RIGHT)

        self.reload()

    def reload(self) -> None:
        self.listbox.delete(0, tk.END)

        try:
            data = load_features(self.profile_var.get(), self.data_root)
            for record in _records(data):
                self.listbox.insert(tk.END, record["id"])
            self.status_var.set(f"Загружено записей: {self.listbox.size()}")
        except Exception as exc:
            self.status_var.set(f"Ошибка JSON: {exc}")
            messagebox.showerror("Память", str(exc), parent=self.window)

    def delete_selected(self) -> None:
        selected = self.listbox.curselection()

        if len(selected) != 1:
            messagebox.showwarning("Память", "Нужно выбрать строго одну иконку.", parent=self.window)
            return

        feature_id = self.listbox.get(selected[0])
        confirmed = messagebox.askyesno(
            "Подтверждение удаления",
            f"Удалить иконку «{feature_id}» и её PNG?\nБудет создана копия .bak.",
            parent=self.window,
        )
        if not confirmed:
            return

        try:
            delete_one_feature(
                self.profile_var.get(),
                feature_id,
                self.data_root,
                bot_running=self.bot_running,
            )
        except Exception as exc:
            messagebox.showerror("Удаление не выполнено", str(exc), parent=self.window)
            return

        self.reload()
        self.status_var.set(f"Удалено: {feature_id}; JSON проверен до и после")


def open_memory_editor(
    parent: tk.Misc,
    config: Any = None,
    bot_running: Callable[[], bool] | bool = False,
) -> MemoryEditor:
    return MemoryEditor(parent, _default_data_root(config), bot_running)


def open_memory_window(
    parent: tk.Misc,
    config: Any = None,
    bot_running: Callable[[], bool] | bool = False,
) -> MemoryEditor:
    return open_memory_editor(parent, config, bot_running)


__all__ = [
    "MemoryEditor",
    "delete_one_feature",
    "features_path",
    "load_features",
    "open_memory_editor",
    "open_memory_window",
]
