from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from gui_base import SettingsTab
from gui_farm import ControlTab
from gui_memory import MemoryTab


BOT_ACTIONS: tuple[tuple[str, str, bool], ...] = (
    ("Проверка", "--check", True),
    ("Скриншот", "--capture", True),
    ("Обучение", "--teach", True),
    ("Обучение без кликов", "--teach-only", True),
    ("Список профилей", "--list-profiles", False),
    ("Сброс памяти", "--reset-knowledge", True),
)


class BotTab(ttk.Frame):
    """Tab for direct main.py actions using only real CLI flags from main.py."""

    def __init__(self, parent: tk.Misc, app: "GuiApp") -> None:
        super().__init__(parent, padding=12)
        self.app = app
        self._build()

    def _build(self) -> None:
        controls = ttk.LabelFrame(self, text="Команды", padding=10)
        controls.pack(fill=tk.X)

        profile_row = ttk.Frame(controls)
        profile_row.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(profile_row, text="Профиль:").pack(side=tk.LEFT)
        self.app.profile_combo = ttk.Combobox(
            profile_row,
            textvariable=self.app.profile_var,
            state="readonly",
            width=22,
        )
        self.app.profile_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.app.profile_combo.bind("<<ComboboxSelected>>", self.app.on_profile_selected)

        ttk.Button(
            profile_row,
            text="Обновить",
            command=self.app.reload_profiles,
        ).pack(side=tk.LEFT, padx=(10, 0))

        buttons_row = ttk.Frame(controls)
        buttons_row.pack(fill=tk.X)

        for title, flag, needs_profile in BOT_ACTIONS:
            button = ttk.Button(
                buttons_row,
                text=title,
                command=lambda current_flag=flag, current_needs_profile=needs_profile: self.app.run_main(
                    [current_flag],
                    with_profile=current_needs_profile,
                ),
            )
            button.pack(side=tk.LEFT, padx=(0, 8))
            self.app.start_buttons.append(button)

        self.app.stop_button = ttk.Button(
            buttons_row,
            text="Остановить процесс",
            command=self.app.stop_process,
            state=tk.DISABLED,
        )
        self.app.stop_button.pack(side=tk.LEFT)

        ttk.Label(self, textvariable=self.app.status_var).pack(anchor=tk.W, pady=(10, 0))


class GuiApp:
    def __init__(self, profile: str | None = None) -> None:
        self.project_dir = Path(__file__).resolve().parent
        self.data_root = self.project_dir / "data" / "profiles"
        self.root = tk.Tk()
        self.root.title("LDPlayer Bot")
        self.root.geometry("1180x780")
        self.root.minsize(980, 680)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.status_var = tk.StringVar(value="Готово")
        self.profile_var = tk.StringVar(value=profile or "")

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.app_log = self.log_queue
        self.current_process: subprocess.Popen[str] | None = None
        self.current_thread: threading.Thread | None = None
        self.stop_requested = False
        self.exit_code = 0
        self._closing = False

        self.profile_combo: ttk.Combobox | None = None
        self.stop_button: ttk.Button | None = None
        self.start_buttons: list[ttk.Button] = []
        self.control_tab: ControlTab | None = None
        self.settings_tab: SettingsTab | None = None
        self.memory_tab: MemoryTab | None = None
        self.bot_tab: BotTab | None = None
        self.full_stop_button = None

        self._build_ui()
        self.reload_profiles()
        self._mount_tabs()

    def app_log(self, text: str) -> None:
        self.log_queue.put(str(text))

    log = app_log

    def _build_ui(self) -> None:
        self._build_menu()
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        notebook = ttk.Notebook(main)
        notebook.pack(fill=tk.BOTH, expand=True)
        self.notebook = notebook

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root)
        profile_menu = tk.Menu(menu, tearoff=False)
        profile_menu.add_command(label="Выбор профиля", command=self.open_profile_picker)
        menu.add_cascade(label="Профиль", menu=profile_menu)
        self.root.config(menu=menu)

    def _mount_tabs(self) -> None:
        self.control_tab = ControlTab(self.notebook, self)
        self.settings_tab = SettingsTab(self.notebook, self)
        self.memory_tab = MemoryTab(self.notebook, self)
        self.bot_tab = BotTab(self.notebook, self)

        self.notebook.add(self.control_tab, text="Управление")
        self.notebook.add(self.settings_tab, text="Настройки")
        self.notebook.add(self.memory_tab, text="Память")
        self.notebook.add(self.bot_tab, text="Бот")

    def reload_profiles(self) -> None:
        profiles = sorted(
            item.name
            for item in self.data_root.iterdir()
            if item.is_dir() and item.name != "base"
        ) if self.data_root.exists() else []

        if not profiles:
            profiles = ["loc1"]

        if self.profile_combo is not None:
            self.profile_combo["values"] = profiles

        current = self.profile_var.get().strip()
        if current not in profiles:
            self.profile_var.set(profiles[0])

        self.app_log(f"[GUI] Профили обновлены: {', '.join(profiles)}")

    def open_profile_picker(self) -> None:
        if self.notebook.tabs():
            self.notebook.select(self.bot_tab)
        if self.profile_combo is not None:
            self.profile_combo.focus_set()
        self.app_log("[GUI] Открыт выбор профиля.")

    def on_profile_selected(self, _event: object | None = None) -> None:
        del _event
        self.app_log(f"[GUI] Выбран профиль: {self.profile_var.get().strip()}")
        if self.memory_tab is not None:
            try:
                self.memory_tab.reload()
            except Exception:
                pass

    def run_main(self, args: list[str], *, with_profile: bool = True) -> None:
        if self.current_process is not None:
            messagebox.showwarning(
                "Процесс уже запущен",
                "Сначала остановите текущий процесс.",
            )
            return

        command = [sys.executable, "main.py", *args]
        if with_profile:
            profile = self.profile_var.get().strip()
            if not profile:
                messagebox.showerror("Ошибка", "Выберите профиль.")
                return
            command.extend(["--profile", profile])

        self._start_process(command, title=" ".join(args) or "main")

    def _start_process(self, command: list[str], *, title: str) -> None:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        self.stop_requested = False
        self._set_running_state(True)
        self.status_var.set(f"Запуск: {title}")
        self.app_log(f"[GUI] cwd={self.project_dir}")
        self.app_log(f"[GUI] cmd={' '.join(command)}")

        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.project_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
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
            self.app_log(f"[GUI] Не удалось запустить процесс: {exc}")
            return

        self.current_process = process
        self.current_thread = threading.Thread(target=self._reader_thread, args=(process,), daemon=True)
        self.current_thread.start()
        self.root.after(100, self._poll_process_state)

    def _reader_thread(self, process: subprocess.Popen[str]) -> None:
        def pump(stream) -> None:
            if stream is None:
                return
            for line in stream:
                self.app_log(line.rstrip("\n"))

        threads: list[threading.Thread] = []
        for stream in (process.stdout, process.stderr):
            thread = threading.Thread(target=pump, args=(stream,), daemon=True)
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
        process.wait()

    def _poll_process_state(self) -> None:
        process = self.current_process
        if process is None:
            return
        if process.poll() is None:
            if not self._closing:
                self.root.after(100, self._poll_process_state)
            return
        code = process.returncode
        self.current_process = None
        self.current_thread = None
        self._set_running_state(False)
        self.status_var.set("Процесс остановлен" if self.stop_requested else "Готово")
        self.app_log(f"[GUI] Процесс завершён с кодом {code}")
        if self._closing:
            self.root.after(0, self.root.destroy)

    def stop_process(self) -> None:
        process = self.current_process
        if process is None:
            return
        self.stop_requested = True
        self.status_var.set("Остановка процесса...")
        self.app_log("[GUI] Остановка процесса...")
        try:
            process.terminate()
        except Exception as exc:
            self.app_log(f"[GUI] terminate() не удался: {exc}")
            try:
                process.kill()
            except Exception as kill_exc:
                self.app_log(f"[GUI] kill() не удался: {kill_exc}")

    def _set_running_state(self, running: bool) -> None:
        state = tk.DISABLED if running else tk.NORMAL
        for button in self.start_buttons:
            button.configure(state=state)
        if self.stop_button is not None:
            self.stop_button.configure(state=(tk.NORMAL if running else tk.DISABLED))
        if self.profile_combo is not None:
            self.profile_combo.configure(state=(tk.DISABLED if running else "readonly"))

    def is_bot_running(self) -> bool:
        control = self.control_tab
        if control is not None and getattr(control, "running", False):
            return True
        process = self.current_process
        return process is not None and process.poll() is None

    bot_running = is_bot_running

    def on_close(self) -> None:
        control = self.control_tab
        if control is not None:
            listener = getattr(control, "hotkey_listener", None)
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass
        if self.current_process is not None:
            if not messagebox.askyesno(
                "Выход",
                "Есть активный процесс. Остановить его и выйти?",
            ):
                return
            self._closing = True
            self.stop_process()
            return
        self._closing = True
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return self.exit_code


def launch_gui(profile: str | None = None) -> int:
    app = GuiApp(profile=profile)
    return app.run()


def main() -> int:
    return launch_gui()


if __name__ == "__main__":
    raise SystemExit(main())
