"""Tkinter window for teaching the shared GUI navigation profile ``base``.

The module deliberately keeps ADB/template matching out of the GUI.  It only
presents a guided, click-free teaching checklist and delegates every lesson
to the existing ``main.py --teach-only`` path with ``--profile base``.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

LOCATION_IDS = tuple(f"loc{i}" for i in range(1, 8))
LESSONS = (
    ("main_base", "Главная база", "Откройте в LDPlayer главный экран базы."),
    ("location_select", "Экран выбора локаций", "Откройте в LDPlayer экран выбора локаций."),
    *tuple(
        (f"location_{i}", f"Иконка локации loc{i}", f"Откройте в LDPlayer экран с иконкой локации loc{i}.")
        for i in range(1, 8)
    ),
    ("hero_menu_start", "Меню героев и кнопка старта", "Откройте в LDPlayer меню героев с видимой кнопкой старта."),
)


def base_profile_dir(config: Any = None) -> Path:
    """Return ``data/profiles/base`` without requiring BotConfig at import time."""
    if config is not None:
        root = getattr(config, "base_dir", None)
        if root is not None:
            return Path(root) / "data" / "profiles" / "base"
        raw = getattr(config, "raw", {}) or {}
        configured = raw.get("profiles", {}).get("root_dir", "data/profiles")
        path = Path(str(configured))
        if not path.is_absolute():
            path = Path.cwd() / path
        return path / "base"
    return Path(__file__).resolve().parent / "data" / "profiles" / "base"


def validate_base_knowledge(data: dict) -> None:
    if data.get("schema_version") != 1:
        raise ValueError("base knowledge: schema_version must be 1")
    if data.get("profile") != "base":
        raise ValueError("base knowledge: profile must be 'base'")
    screens = data.get("screen_templates")
    if not isinstance(screens, dict):
        raise ValueError("base knowledge: screen_templates is missing")
    for key in ("main_base", "location_select", "hero_menu_start"):
        if not isinstance(screens.get(key), dict):
            raise ValueError(f"base knowledge: missing screen template {key}")
    icons = data.get("location_icons")
    if not isinstance(icons, list) or len(icons) != 7:
        raise ValueError("base knowledge: location_icons must contain exactly 7 items")
    ids = [item.get("location_id") for item in icons if isinstance(item, dict)]
    if set(ids) != set(LOCATION_IDS) or len(ids) != len(set(ids)):
        raise ValueError("base knowledge: location_ids must be loc1..loc7 and unique")


def load_base_knowledge(config: Any = None) -> dict:
    path = base_profile_dir(config) / "knowledge.json"
    with path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    validate_base_knowledge(data)
    return data


def get_location_icon(knowledge: dict, location_id: str) -> dict:
    if location_id not in LOCATION_IDS:
        raise ValueError(f"unknown location: {location_id}")
    for item in knowledge.get("location_icons", []):
        if item.get("location_id") == location_id:
            return item
    raise KeyError(location_id)


def resolve_template_path(base_dir: Path, record: dict) -> Path:
    return (Path(base_dir) / str(record["file"])).resolve()


def _state_path(config: Any = None) -> Path:
    return base_profile_dir(config) / "training_state.json"


def _load_state(config: Any = None) -> dict[str, bool]:
    try:
        with _state_path(config).open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        return {str(k): bool(v) for k, v in data.items()}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict[str, bool], config: Any = None) -> None:
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)


class BaseKnowledgeWindow:
    def __init__(self, master: tk.Misc, config_path: str = "config.yaml") -> None:
        self.master = master
        self.config_path = config_path
        self.window = tk.Toplevel(master)
        self.window.title("Базовые знания")
        self.window.geometry("760x560")
        self.window.minsize(650, 440)
        self.state = _load_state()
        self.config = None
        try:
            from bot.config import load_config
            self.config = load_config(config_path)
            load_base_knowledge(self.config)
        except Exception as exc:
            self._knowledge_error = str(exc)
        else:
            self._knowledge_error = ""

        container = ttk.Frame(self.window, padding=16)
        container.pack(fill=tk.BOTH, expand=True)
        ttk.Label(container, text="Базовые знания", font=("TkDefaultFont", 16, "bold")).pack(anchor=tk.W)
        ttk.Label(container, text="Обучение выполняется без кликов: откройте нужный экран в LDPlayer и нажмите «Обучить».", wraplength=700).pack(anchor=tk.W, pady=(5, 12))
        if self._knowledge_error:
            ttk.Label(container, text=f"Профиль base пока не готов: {self._knowledge_error}", foreground="darkorange", wraplength=700).pack(anchor=tk.W, pady=(0, 8))

        self.tree = ttk.Treeview(container, columns=("status", "instruction"), show="headings", selectmode="browse")
        self.tree.heading("status", text="Состояние")
        self.tree.heading("instruction", text="Что открыть в LDPlayer")
        self.tree.column("status", width=130, anchor=tk.CENTER)
        self.tree.column("instruction", width=540)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        for key, title, instruction in LESSONS:
            self.tree.insert("", tk.END, iid=key, values=(self._status(key), f"{title}: {instruction}"))

        self.selected = tk.StringVar()
        ttk.Label(container, textvariable=self.selected, wraplength=700).pack(anchor=tk.W, pady=(10, 6))
        buttons = ttk.Frame(container)
        buttons.pack(fill=tk.X)
        self.teach_button = ttk.Button(buttons, text="Обучить выбранный пункт", command=self._teach_selected)
        self.teach_button.pack(side=tk.LEFT)
        ttk.Button(buttons, text="Обновить состояние", command=self._refresh).pack(side=tk.LEFT, padx=8)
        ttk.Button(buttons, text="Закрыть", command=self.window.destroy).pack(side=tk.RIGHT)

    def _status(self, key: str) -> str:
        return "Обучено" if self.state.get(key) else "Не обучено"

    def _on_select(self, _event: object = None) -> None:
        selection = self.tree.selection()
        if not selection:
            self.selected.set("Выберите пункт обучения.")
            return
        key = selection[0]
        item = next(item for item in LESSONS if item[0] == key)
        self.selected.set(f"{item[1]}\n{item[2]}")

    def _refresh(self) -> None:
        self.state = _load_state(self.config)
        for key, _, _ in LESSONS:
            self.tree.set(key, "status", self._status(key))

    def _teach_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Базовые знания", "Сначала выберите пункт обучения.", parent=self.window)
            return
        key = selection[0]
        if self.state.get(key) and not messagebox.askyesno("Повторное обучение", "Этот пункт уже обучен. Повторить обучение?", parent=self.window):
            return
        if self._knowledge_error:
            messagebox.showerror("Базовые знания", "Нельзя начать обучение: профиль base не найден или повреждён.", parent=self.window)
            return
        command = [sys.executable, "main.py", "--teach-only", "--profile", "base", "--config", self.config_path, "--base-lesson", key]
        try:
            result = subprocess.run(command, cwd=Path(__file__).resolve().parent, check=False)
        except OSError as exc:
            messagebox.showerror("Обучение", f"Не удалось запустить main.py: {exc}", parent=self.window)
            return
        if result.returncode == 0:
            self.state[key] = True
            _save_state(self.state, self.config)
            self.tree.set(key, "status", "Обучено")
            messagebox.showinfo("Обучение", "Пункт успешно обучен в общем профиле base.", parent=self.window)
        else:
            messagebox.showerror("Обучение", f"main.py завершился с кодом {result.returncode}.", parent=self.window)


def open_base_window(master: tk.Misc | None = None, config_path: str = "config.yaml") -> BaseKnowledgeWindow:
    """Open the window expected by ``gui.py`` and return its controller."""
    if master is None:
        master = tk._get_default_root() or tk.Tk()
    return BaseKnowledgeWindow(master, config_path=config_path)


# Compatibility aliases for callers that use a verb-oriented name.
launch_base_window = open_base_window

__all__ = [
    "BaseKnowledgeWindow", "base_profile_dir", "load_base_knowledge",
    "validate_base_knowledge", "get_location_icon", "resolve_template_path",
    "open_base_window", "launch_base_window",
]
