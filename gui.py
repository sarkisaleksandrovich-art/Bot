from __future__ import annotations

import importlib
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from bot.config import BotConfig, get_path, load_config


ALLOWED_LOCATION_IDS: tuple[str, ...] = (
    "loc1",
    "loc2",
    "loc3",
    "loc4",
    "loc5",
    "loc6",
    "loc7",
)


def run_gui(
    *,
    config: dict[str, Any] | BotConfig,
    profile: str | None = None,
    location_id: str | None = None,
    floor_number: int | None = None,
    floor_to: int | None = None,
) -> int:
    """Точка входа GUI-режима. Возвращает код завершения процесса."""
    app = BotGUI(
        config=config,
        profile=profile,
        location_id=location_id,
        floor_number=floor_number,
        floor_to=floor_to,
    )
    app.run()
    return 0


class BotGUI:
    def __init__(
        self,
        *,
        config: dict[str, Any] | BotConfig,
        profile: str | None,
        location_id: str | None,
        floor_number: int | None,
        floor_to: int | None,
    ) -> None:
        self.config = config if isinstance(config, BotConfig) else BotConfig(raw=dict(config))
        self.project_dir = self.config.base_dir.resolve()
        self.config_path = self._detect_config_path(self.config)

        self.root = tk.Tk()
        self.root.title("LDPlayer Bot GUI")
        self.root.geometry("1100x760")
        self.root.minsize(980, 680)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.current_process: subprocess.Popen[str] | None = None
        self.current_thread: threading.Thread | None = None
        self.stop_requested = False

        self.profile_var = tk.StringVar(value=profile or self._default_profile())
        self.location_var = tk.StringVar(value=self._normalize_location(location_id or profile or self._default_profile()))
        self.floor_var = tk.StringVar(value="" if floor_number is None else str(floor_number))
        self.floor_to_var = tk.StringVar(value="" if floor_to is None else str(floor_to))
        self.status_var = tk.StringVar(value="Готово")

        self.module_status: dict[str, str] = {}
        self.profile_combo: ttk.Combobox | None = None
        self.location_combo: ttk.Combobox | None = None
        self.log_text: tk.Text | None = None
        self.stop_button: ttk.Button | None = None
        self.start_buttons: list[ttk.Button] = []
        self.module_notebook: ttk.Notebook | None = None

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._populate_profiles()
        self._wire_profile_location_sync()
        self._attach_modules()
        self._poll_log_queue()

    def run(self) -> None:
        self.root.mainloop()

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        controls = ttk.LabelFrame(main, text="Управление", padding=10)
        controls.pack(fill=tk.X)

        row1 = ttk.Frame(controls)
        row1.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(row1, text="Профиль фарма:").pack(side=tk.LEFT)
        self.profile_combo = ttk.Combobox(row1, textvariable=self.profile_var, state="readonly", width=18)
        self.profile_combo.pack(side=tk.LEFT, padx=(6, 18))

        ttk.Label(row1, text="GUI-локация:").pack(side=tk.LEFT)
        self.location_combo = ttk.Combobox(
            row1,
            textvariable=self.location_var,
            state="readonly",
            width=10,
            values=list(ALLOWED_LOCATION_IDS),
        )
        self.location_combo.pack(side=tk.LEFT, padx=(6, 18))

        ttk.Label(row1, text="Этаж с:").pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self.floor_var, width=8).pack(side=tk.LEFT, padx=(6, 12))

        ttk.Label(row1, text="по:").pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self.floor_to_var, width=8).pack(side=tk.LEFT, padx=(6, 12))

        ttk.Button(row1, text="Список профилей", command=self._refresh_profiles).pack(side=tk.LEFT, padx=(10, 0))

        row2 = ttk.Frame(controls)
        row2.pack(fill=tk.X)

        self._add_action_button(row2, "Проверка", lambda: self._run_main_flags(["--check"]))
        self._add_action_button(row2, "Скриншот", lambda: self._run_main_flags(["--capture"]))
        self._add_action_button(row2, "Обучение", lambda: self._run_main_flags(["--teach"]))
        self._add_action_button(row2, "Обучение без кликов", lambda: self._run_main_flags(["--teach-only"]))

        self.stop_button = ttk.Button(row2, text="Остановить процесс", command=self._stop_process, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(main, textvariable=self.status_var).pack(anchor=tk.W, pady=(8, 6))

        self.module_notebook = ttk.Notebook(main)
        self.module_notebook.pack(fill=tk.BOTH, expand=False, pady=(0, 10))

        log_frame = ttk.LabelFrame(main, text="Живой лог", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_frame, wrap=tk.WORD, height=24, state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _add_action_button(self, parent: ttk.Frame, text: str, command: Any) -> None:
        button = ttk.Button(parent, text=text, command=command)
        button.pack(side=tk.LEFT, padx=(0, 8))
        self.start_buttons.append(button)

    def _populate_profiles(self) -> None:
        profiles = self._list_profiles()
        if not profiles:
            profiles = [p for p in ALLOWED_LOCATION_IDS]
        if self.profile_var.get() not in profiles:
            self.profile_var.set(profiles[0])
        if self.profile_combo is not None:
            self.profile_combo["values"] = profiles

    def _refresh_profiles(self) -> None:
        self._populate_profiles()
        self._log_local("Список профилей обновлён")

    def _wire_profile_location_sync(self) -> None:
        if self.profile_combo is not None:
            self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_changed)

    def _on_profile_changed(self, event: object | None = None) -> None:
        del event
        profile = self.profile_var.get().strip()
        if profile in ALLOWED_LOCATION_IDS:
            self.location_var.set(profile)

    def _attach_modules(self) -> None:
        specs = [
            ("gui_base", "Базовые знания"),
            ("gui_memory", "Редактирование памяти"),
            ("gui_farm", "Фарм"),
        ]
        for module_name, title in specs:
            self._attach_one_module(module_name, title)

    def _attach_one_module(self, module_name: str, title: str) -> None:
        parent = self.module_notebook
        if parent is None:
            return

        frame = ttk.Frame(parent, padding=10)
        parent.add(frame, text=title)

        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            self.module_status[module_name] = "missing"
            ttk.Label(
                frame,
                text=f"Модуль {module_name}.py не найден. Подключение недоступно.",
                foreground="red",
            ).pack(anchor=tk.W)
            return
        except Exception as exc:
            self.module_status[module_name] = "error"
            ttk.Label(
                frame,
                text=f"Ошибка импорта {module_name}: {exc}",
                foreground="red",
            ).pack(anchor=tk.W)
            return

        self.module_status[module_name] = "imported"
        ttk.Label(frame, text=f"Модуль {module_name} импортирован.").pack(anchor=tk.W, pady=(0, 8))

        builder_names = (
            "build_tab",
            "build_gui",
            "build_frame",
            "create_tab",
            "create_frame",
            "mount_tab",
        )
        for name in builder_names:
            builder = getattr(module, name, None)
            if callable(builder):
                try:
                    builder(frame, self)
                    ttk.Label(frame, text=f"Использован хук {module_name}.{name}(...)").pack(anchor=tk.W)
                    self.module_status[module_name] = f"hook:{name}"
                    return
                except TypeError:
                    try:
                        builder(frame)
                        ttk.Label(frame, text=f"Использован хук {module_name}.{name}(frame)").pack(anchor=tk.W)
                        self.module_status[module_name] = f"hook:{name}"
                        return
                    except Exception as exc:
                        ttk.Label(frame, text=f"Хук {name} завершился с ошибкой: {exc}", foreground="red").pack(anchor=tk.W)
                        return
                except Exception as exc:
                    ttk.Label(frame, text=f"Хук {name} завершился с ошибкой: {exc}", foreground="red").pack(anchor=tk.W)
                    return

        ttk.Label(
            frame,
            text=(
                "Модуль найден, но в нём нет известного tkinter-хука. "
                "Ожидается одна из функций: build_tab/build_gui/build_frame/create_tab/create_frame/mount_tab."
            ),
            wraplength=900,
        ).pack(anchor=tk.W)

    def _run_main_flags(self, extra_flags: list[str]) -> None:
        if self.current_process is not None:
            messagebox.showwarning("Процесс уже запущен", "Сначала остановите текущий процесс.")
            return

        profile = self.profile_var.get().strip()
        if not profile:
            messagebox.showerror("Ошибка", "Выберите профиль.")
            return
        if profile == "base":
            messagebox.showerror("Ошибка", "Профиль base нельзя использовать как профиль фарма.")
            return

        command = [sys.executable, "main.py", *extra_flags, "--profile", profile]
        floor_from = self.floor_var.get().strip()
        floor_to = self.floor_to_var.get().strip()

        if floor_from:
            command.extend(["--floor-from", floor_from])
        if floor_to:
            command.extend(["--floor-to", floor_to])
        if self.config_path is not None:
            command.extend(["--config", self.config_path])

        self._start_process(command, title=" ".join(extra_flags) or "main")

    def _start_process(self, command: list[str], *, title: str) -> None:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        self.stop_requested = False
        self._set_running_state(True)
        self.status_var.set(f"Запуск: {title}")
        self._log_local(f"[GUI] cwd={self.project_dir}")
        self._log_local(f"[GUI] cmd={' '.join(command)}")

        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.project_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
        except OSError as exc:
            self._set_running_state(False)
            self.status_var.set("Ошибка запуска")
            self._log_local(f"[GUI] Не удалось запустить процесс: {exc}")
            return

        self.current_process = process
        thread = threading.Thread(target=self._reader_thread, args=(process,), daemon=True)
        self.current_thread = thread
        thread.start()

    def _reader_thread(self, process: subprocess.Popen[str]) -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    self.log_queue.put(line.rstrip("\n"))
            return_code = process.wait()
            self.log_queue.put(f"[GUI] Процесс завершён с кодом {return_code}")
        except Exception as exc:
            self.log_queue.put(f"[GUI] Ошибка чтения вывода: {exc}")
        finally:
            self.log_queue.put("__PROCESS_DONE__")

    def _poll_log_queue(self) -> None:
        try:
            while True:
                item = self.log_queue.get_nowait()
                if item == "__PROCESS_DONE__":
                    self.current_process = None
                    self.current_thread = None
                    self._set_running_state(False)
                    if self.stop_requested:
                        self.status_var.set("Процесс остановлен")
                    else:
                        self.status_var.set("Готово")
                    continue
                self._append_log(item)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)

    def _append_log(self, text: str) -> None:
        if self.log_text is None:
            return
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _log_local(self, text: str) -> None:
        self._append_log(text)

    def _stop_process(self) -> None:
        process = self.current_process
        if process is None:
            return
        self.stop_requested = True
        self.status_var.set("Остановка процесса...")
        self._log_local("[GUI] Остановка процесса...")
        try:
            process.terminate()
        except Exception as exc:
            self._log_local(f"[GUI] Не удалось terminate(): {exc}")
            try:
                process.kill()
            except Exception as kill_exc:
                self._log_local(f"[GUI] Не удалось kill(): {kill_exc}")

    def _set_running_state(self, running: bool) -> None:
        state_buttons = tk.DISABLED if running else tk.NORMAL
        for button in self.start_buttons:
            button.configure(state=state_buttons)
        if self.stop_button is not None:
            self.stop_button.configure(state=(tk.NORMAL if running else tk.DISABLED))
        if self.profile_combo is not None:
            self.profile_combo.configure(state=(tk.DISABLED if running else "readonly"))
        if self.location_combo is not None:
            self.location_combo.configure(state=(tk.DISABLED if running else "readonly"))

    def _on_close(self) -> None:
        if self.current_process is not None:
            if not messagebox.askyesno("Выход", "Есть активный процесс. Остановить и выйти?"):
                return
            self._stop_process()
        self.root.after(200, self.root.destroy)

    def _list_profiles(self) -> list[str]:
        root_dir = self._profiles_root_dir()
        if not root_dir.exists() or not root_dir.is_dir():
            return []
        result: list[str] = []
        for item in sorted(root_dir.iterdir()):
            if item.is_dir() and item.name != "base":
                result.append(item.name)
        return result

    def _profiles_root_dir(self) -> Path:
        raw = get_path(self.config, "profiles", "root_dir")
        path = Path(str(raw))
        if not path.is_absolute():
            path = self.project_dir / path
        return path.resolve()

    def _default_profile(self) -> str:
        try:
            return str(get_path(self.config, "profiles", "default_profile"))
        except Exception:
            return "loc1"

    def _normalize_location(self, value: str | None) -> str:
        if value in ALLOWED_LOCATION_IDS:
            return str(value)
        return "loc1"

    def _detect_config_path(self, config: BotConfig) -> str | None:
        source_path = config.raw.get("__source_path")
        if isinstance(source_path, str) and source_path.strip():
            return source_path
        candidate = self.project_dir / "config.yaml"
        if candidate.exists():
            return str(candidate)
        return None


if __name__ == "__main__":
    loaded_config = load_config("config.yaml")
    raise SystemExit(run_gui(config=loaded_config))
