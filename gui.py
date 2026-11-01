from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable

from bot.config import BotConfig, ConfigError, load_config

ALLOWED_LOCATION_IDS: tuple[str, ...] = (
    "loc1",
    "loc2",
    "loc3",
    "loc4",
    "loc5",
    "loc6",
    "loc7",
)

MAIN_ACTIONS: tuple[tuple[str, str], ...] = (
    ("Проверка", "--check"),
    ("Скриншот", "--capture"),
    ("Обучение", "--teach"),
    ("Обучение без кликов", "--teach-only"),
    ("Список профилей", "--list-profiles"),
)


class GuiApp:
    def __init__(
        self,
        *,
        config_path: str = "config.yaml",
        profile: str | None = None,
        floor_from: int | None = None,
        floor_to: int | None = None,
    ) -> None:
        self.config_path = config_path
        self.project_dir = Path(__file__).resolve().parent
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.current_process: subprocess.Popen[str] | None = None
        self.current_thread: threading.Thread | None = None
        self.stop_requested = False
        self.exit_code = 0

        self.root = tk.Tk()
        self.root.title("LDPlayer Bot")
        self.root.geometry("1180x780")
        self.root.minsize(980, 700)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.status_var = tk.StringVar(value="Готово")
        self.profile_var = tk.StringVar(value=self._normalize_profile(profile))
        self.location_var = tk.StringVar(value=self._normalize_location(profile))
        self.floor_var = tk.StringVar(value="" if floor_from is None else str(floor_from))
        self.floor_to_var = tk.StringVar(value="" if floor_to is None else str(floor_to))

        self.profile_combo: ttk.Combobox | None = None
        self.location_combo: ttk.Combobox | None = None
        self.stop_button: ttk.Button | None = None
        self.log_text: tk.Text | None = None
        self.notebook: ttk.Notebook | None = None
        self.start_buttons: list[ttk.Button] = []

        self._build_ui()
        self._load_profiles_from_disk()
        self._bind_profile_sync()
        self._attach_module_tabs()
        self._poll_log_queue()

    def run(self) -> int:
        self.root.mainloop()
        return self.exit_code

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        controls = ttk.LabelFrame(main, text="Управление ботом", padding=10)
        controls.pack(fill=tk.X)

        top_row = ttk.Frame(controls)
        top_row.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(top_row, text="Профиль:").pack(side=tk.LEFT)
        self.profile_combo = ttk.Combobox(top_row, textvariable=self.profile_var, state="readonly", width=18)
        self.profile_combo.pack(side=tk.LEFT, padx=(6, 18))

        ttk.Label(top_row, text="GUI-локация:").pack(side=tk.LEFT)
        self.location_combo = ttk.Combobox(
            top_row,
            textvariable=self.location_var,
            state="readonly",
            width=10,
            values=list(ALLOWED_LOCATION_IDS),
        )
        self.location_combo.pack(side=tk.LEFT, padx=(6, 18))

        ttk.Label(top_row, text="Этаж от:").pack(side=tk.LEFT)
        ttk.Entry(top_row, textvariable=self.floor_var, width=8).pack(side=tk.LEFT, padx=(6, 12))

        ttk.Label(top_row, text="по:").pack(side=tk.LEFT)
        ttk.Entry(top_row, textvariable=self.floor_to_var, width=8).pack(side=tk.LEFT, padx=(6, 12))

        ttk.Button(top_row, text="Обновить профили", command=self._load_profiles_from_disk).pack(side=tk.LEFT, padx=(10, 0))

        action_row = ttk.Frame(controls)
        action_row.pack(fill=tk.X)

        for title, flag in MAIN_ACTIONS:
            button = ttk.Button(action_row, text=title, command=lambda f=flag: self._run_main_flags([f]))
            button.pack(side=tk.LEFT, padx=(0, 8))
            self.start_buttons.append(button)

        self.stop_button = ttk.Button(action_row, text="Остановить процесс", command=self._stop_process, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(main, textvariable=self.status_var).pack(anchor=tk.W, pady=(8, 6))

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill=tk.BOTH, expand=False, pady=(0, 10))

        log_frame = ttk.LabelFrame(main, text="Живой лог", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_frame, wrap=tk.WORD, height=24, state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _load_profiles_from_disk(self) -> None:
        profiles = self._list_profiles()
        if not profiles:
            profiles = list(ALLOWED_LOCATION_IDS)
        if self.profile_var.get() not in profiles:
            self.profile_var.set(profiles[0])
        if self.profile_combo is not None:
            self.profile_combo["values"] = profiles
        self._append_log("[GUI] Список профилей обновлён")

    def _bind_profile_sync(self) -> None:
        if self.profile_combo is not None:
            self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)

    def _on_profile_selected(self, event: object | None = None) -> None:
        del event
        profile = self.profile_var.get().strip()
        if profile in ALLOWED_LOCATION_IDS:
            self.location_var.set(profile)

    def _attach_module_tabs(self) -> None:
        if self.notebook is None:
            return
        self._attach_module_tab("gui_base", "Базовые знания")
        self._attach_module_tab("gui_memory", "Редактирование памяти")
        self._attach_module_tab("gui_farm", "Фарм")

    def _attach_module_tab(self, module_name: str, title: str) -> None:
        assert self.notebook is not None
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text=title)

        try:
            module = __import__(module_name)
        except ModuleNotFoundError:
            ttk.Label(
                frame,
                text=f"Модуль {module_name}.py не найден. Подключение недоступно.",
                foreground="red",
            ).pack(anchor=tk.W)
            return
        except Exception as exc:
            ttk.Label(frame, text=f"Ошибка импорта {module_name}: {exc}", foreground="red").pack(anchor=tk.W)
            return

        ttk.Label(frame, text=f"Модуль {module_name} импортирован.").pack(anchor=tk.W, pady=(0, 8))

        hook_names: tuple[str, ...] = (
            "build_tab",
            "build_gui",
            "build_frame",
            "create_tab",
            "create_frame",
            "mount_tab",
        )
        for hook_name in hook_names:
            hook = getattr(module, hook_name, None)
            if not callable(hook):
                continue
            if self._try_mount_hook(frame, hook):
                ttk.Label(frame, text=f"Использован хук {module_name}.{hook_name}(...)" ).pack(anchor=tk.W)
                return

        ttk.Label(
            frame,
            text=(
                "Модуль найден, но в нём нет известного tkinter-хука. "
                "Ожидается одна из функций: build_tab/build_gui/build_frame/"
                "create_tab/create_frame/mount_tab."
            ),
            wraplength=900,
        ).pack(anchor=tk.W)

    def _try_mount_hook(self, frame: ttk.Frame, hook: Callable[..., Any]) -> bool:
        try:
            hook(frame, self)
            return True
        except TypeError:
            try:
                hook(frame)
                return True
            except Exception as exc:
                ttk.Label(frame, text=f"Хук завершился с ошибкой: {exc}", foreground="red").pack(anchor=tk.W)
                return True
        except Exception as exc:
            ttk.Label(frame, text=f"Хук завершился с ошибкой: {exc}", foreground="red").pack(anchor=tk.W)
            return True

    def _run_main_flags(self, extra_flags: list[str]) -> None:
        if self.current_process is not None:
            messagebox.showwarning("Процесс уже запущен", "Сначала остановите текущий процесс.")
            return

        profile = self.profile_var.get().strip()
        if not profile:
            messagebox.showerror("Ошибка", "Выберите профиль.")
            return
        if profile == "base":
            messagebox.showerror("Ошибка", "Профиль base нельзя использовать как игровой профиль фарма.")
            return

        command = [sys.executable, "main.py", *extra_flags]
        if "--list-profiles" not in extra_flags:
            command.extend(["--profile", profile])
            floor_from = self.floor_var.get().strip()
            floor_to = self.floor_to_var.get().strip()
            if floor_from:
                command.extend(["--floor-from", floor_from])
            if floor_to:
                command.extend(["--floor-to", floor_to])

        if self.config_path:
            command.extend(["--config", self.config_path])

        self._start_process(command, title=" ".join(extra_flags) or "main")

    def _start_process(self, command: list[str], *, title: str) -> None:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        self.stop_requested = False
        self._set_running_state(True)
        self.status_var.set(f"Запуск: {title}")
        self._append_log(f"[GUI] cwd={self.project_dir}")
        self._append_log(f"[GUI] cmd={' '.join(command)}")

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
            self._append_log(f"[GUI] Не удалось запустить процесс: {exc}")
            return

        self.current_process = process
        self.current_thread = threading.Thread(target=self._reader_thread, args=(process,), daemon=True)
        self.current_thread.start()

    def _reader_thread(self, process: subprocess.Popen[str]) -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    self.log_queue.put(line.rstrip("\n"))
            code = process.wait()
            self.log_queue.put(f"[GUI] Процесс завершён с кодом {code}")
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
                    self.status_var.set("Процесс остановлен" if self.stop_requested else "Готово")
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

    def _stop_process(self) -> None:
        process = self.current_process
        if process is None:
            return
        self.stop_requested = True
        self.status_var.set("Остановка процесса...")
        self._append_log("[GUI] Остановка процесса...")
        try:
            process.terminate()
        except Exception as exc:
            self._append_log(f"[GUI] Не удалось terminate(): {exc}")
            try:
                process.kill()
            except Exception as kill_exc:
                self._append_log(f"[GUI] Не удалось kill(): {kill_exc}")

    def _set_running_state(self, running: bool) -> None:
        state = tk.DISABLED if running else tk.NORMAL
        for button in self.start_buttons:
            button.configure(state=state)
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
        try:
            config = load_config(self.config_path)
            root_dir_value = config.raw.get("profiles", {}).get("root_dir", "data/profiles")
            profiles_root = Path(str(root_dir_value))
            if not profiles_root.is_absolute():
                profiles_root = (config.base_dir / profiles_root).resolve()
        except (ConfigError, OSError, ValueError, TypeError):
            profiles_root = (self.project_dir / "data" / "profiles").resolve()

        if not profiles_root.exists() or not profiles_root.is_dir():
            return []

        result: list[str] = []
        for item in sorted(profiles_root.iterdir()):
            if item.is_dir() and item.name != "base":
                result.append(item.name)
        return result

    @staticmethod
    def _normalize_profile(profile: str | None) -> str:
        if profile and profile != "base":
            return profile
        return "loc1"

    @staticmethod
    def _normalize_location(profile: str | None) -> str:
        if profile in ALLOWED_LOCATION_IDS:
            return profile
        return "loc1"


def launch_gui(
    config_path: str = "config.yaml",
    profile: str | None = None,
    floor_from: int | None = None,
    floor_to: int | None = None,
) -> int:
    if floor_from is not None and floor_to is not None and floor_from != floor_to:
        print("GUI navigation requires one floor: --floor-from == --floor-to", file=sys.stderr)
        return 1

    app = GuiApp(
        config_path=config_path,
        profile=profile,
        floor_from=floor_from,
        floor_to=floor_to,
    )
    return app.run()


if __name__ == "__main__":
    raise SystemExit(launch_gui())
