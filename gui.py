from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any


BOT_ACTIONS: tuple[tuple[str, str], ...] = (
    ("Проверка", "--check"),
    ("Скриншот", "--capture"),
    ("Обучение", "--teach"),
    ("Обучение без кликов", "--teach-only"),
)


class MissingTab(ttk.Frame):
    """Fallback tab shown when an optional GUI module is unavailable."""

    def __init__(self, parent: tk.Misc, title: str, message: str) -> None:
        super().__init__(parent, padding=12)
        ttk.Label(self, text=title, font=("TkDefaultFont", 11, "bold")).pack(anchor=tk.W)
        ttk.Label(self, text=message, foreground="red", wraplength=900, justify=tk.LEFT).pack(
            anchor=tk.W,
            pady=(8, 0),
        )


class BotTab(ttk.Frame):
    """Main bot control tab: runs main.py with only real CLI flags."""

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
            text="Обновить список",
            command=self.app.reload_profiles,
        ).pack(side=tk.LEFT, padx=(10, 0))

        buttons_row = ttk.Frame(controls)
        buttons_row.pack(fill=tk.X)

        for title, flag in BOT_ACTIONS:
            button = ttk.Button(
                buttons_row,
                text=title,
                command=lambda current_flag=flag: self.app.run_main([current_flag]),
            )
            button.pack(side=tk.LEFT, padx=(0, 8))
            self.app.start_buttons.append(button)

        list_button = ttk.Button(
            buttons_row,
            text="Список профилей",
            command=lambda: self.app.run_main(["--list-profiles"], with_profile=False),
        )
        list_button.pack(side=tk.LEFT, padx=(0, 8))
        self.app.start_buttons.append(list_button)

        self.app.stop_button = ttk.Button(
            buttons_row,
            text="Остановить процесс",
            command=self.app.stop_process,
            state=tk.DISABLED,
        )
        self.app.stop_button.pack(side=tk.LEFT)

        ttk.Label(self, textvariable=self.app.status_var).pack(anchor=tk.W, pady=(10, 6))

        log_frame = ttk.LabelFrame(self, text="Живой лог", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.app.log_text = tk.Text(log_frame, wrap=tk.WORD, state=tk.DISABLED, height=24)
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.app.log_text.yview)
        self.app.log_text.configure(yscrollcommand=scrollbar.set)
        self.app.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)


class GuiApp:
    def __init__(self, profile: str | None = None) -> None:
        self.project_dir = Path(__file__).resolve().parent
        self.root = tk.Tk()
        self.root.title("LDPlayer Bot")
        self.root.geometry("1180x780")
        self.root.minsize(980, 680)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.status_var = tk.StringVar(value="Готово")
        self.profile_var = tk.StringVar(value=profile or "")

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.current_process: subprocess.Popen[str] | None = None
        self.current_thread: threading.Thread | None = None
        self.stop_requested = False
        self.exit_code = 0

        self.profile_combo: ttk.Combobox | None = None
        self.stop_button: ttk.Button | None = None
        self.log_text: tk.Text | None = None
        self.start_buttons: list[ttk.Button] = []

        self._build_ui()
        self.reload_profiles()
        self._mount_tabs()
        self._poll_log_queue()

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        notebook = ttk.Notebook(main)
        notebook.pack(fill=tk.BOTH, expand=True)
        self.notebook = notebook

    def _mount_tabs(self) -> None:
        self._add_navigation_tab()
        self._add_memory_tab()
        self._add_farm_tab()
        self._add_bot_tab()

    def _add_navigation_tab(self) -> None:
        try:
            from gui_base import NavigationTab  # type: ignore
        except ModuleNotFoundError:
            tab: ttk.Frame = MissingTab(
                self.notebook,
                "Навигация",
                "Модуль gui_base.py не найден. Вкладка навигации недоступна.",
            )
        except ImportError as exc:
            tab = MissingTab(
                self.notebook,
                "Навигация",
                f"Не удалось импортировать NavigationTab из gui_base.py: {exc}",
            )
        else:
            try:
                tab = NavigationTab(self.notebook, self)
            except Exception as exc:
                tab = MissingTab(
                    self.notebook,
                    "Навигация",
                    f"Ошибка создания NavigationTab(parent, app): {exc}",
                )
        self.notebook.add(tab, text="Навигация")

    def _add_memory_tab(self) -> None:
        try:
            from gui_memory import MemoryTab  # type: ignore
        except ModuleNotFoundError:
            tab: ttk.Frame = MissingTab(
                self.notebook,
                "Память",
                "Модуль gui_memory.py не найден. Вкладка памяти недоступна.",
            )
        except ImportError as exc:
            tab = MissingTab(
                self.notebook,
                "Память",
                f"Не удалось импортировать MemoryTab из gui_memory.py: {exc}",
            )
        else:
            try:
                tab = MemoryTab(self.notebook, self)
            except Exception as exc:
                tab = MissingTab(
                    self.notebook,
                    "Память",
                    f"Ошибка создания MemoryTab(parent, app): {exc}",
                )
        self.notebook.add(tab, text="Память")

    def _add_farm_tab(self) -> None:
        try:
            from gui_farm import FarmTab  # type: ignore
        except ModuleNotFoundError:
            tab: ttk.Frame = MissingTab(
                self.notebook,
                "Фарм",
                "Модуль gui_farm.py не найден. Вкладка фарма недоступна.",
            )
        except ImportError as exc:
            tab = MissingTab(
                self.notebook,
                "Фарм",
                f"Не удалось импортировать FarmTab из gui_farm.py: {exc}",
            )
        else:
            try:
                tab = FarmTab(self.notebook, self)
            except Exception as exc:
                tab = MissingTab(
                    self.notebook,
                    "Фарм",
                    f"Ошибка создания FarmTab(parent, app): {exc}",
                )
        self.notebook.add(tab, text="Фарм")

    def _add_bot_tab(self) -> None:
        tab = BotTab(self.notebook, self)
        self.notebook.add(tab, text="Бот")

    def reload_profiles(self) -> None:
        profiles_root = self.project_dir / "data" / "profiles"
        profiles = sorted(
            item.name
            for item in profiles_root.iterdir()
            if item.is_dir() and item.name != "base"
        ) if profiles_root.exists() else []

        if not profiles:
            profiles = ["loc1"]

        if self.profile_combo is not None:
            self.profile_combo["values"] = profiles

        current = self.profile_var.get().strip()
        if current not in profiles:
            self.profile_var.set(profiles[0])

        self.log(f"[GUI] Профили обновлены: {', '.join(profiles)}")

    def on_profile_selected(self, _event: object | None = None) -> None:
        del _event
        self.log(f"[GUI] Выбран профиль: {self.profile_var.get().strip()}")

    def log(self, text: str) -> None:
        self._append_log(text)

    def _append_log(self, text: str) -> None:
        if self.log_text is None:
            return
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

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
        self.log(f"[GUI] cwd={self.project_dir}")
        self.log(f"[GUI] cmd={' '.join(command)}")

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
            self.log(f"[GUI] Не удалось запустить процесс: {exc}")
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

    def stop_process(self) -> None:
        process = self.current_process
        if process is None:
            return
        self.stop_requested = True
        self.status_var.set("Остановка процесса...")
        self.log("[GUI] Остановка процесса...")
        try:
            process.terminate()
        except Exception as exc:
            self.log(f"[GUI] terminate() не удался: {exc}")
            try:
                process.kill()
            except Exception as kill_exc:
                self.log(f"[GUI] kill() не удался: {kill_exc}")

    def _set_running_state(self, running: bool) -> None:
        state = tk.DISABLED if running else tk.NORMAL
        for button in self.start_buttons:
            button.configure(state=state)
        if self.stop_button is not None:
            self.stop_button.configure(state=(tk.NORMAL if running else tk.DISABLED))
        if self.profile_combo is not None:
            self.profile_combo.configure(state=(tk.DISABLED if running else "readonly"))

    def on_close(self) -> None:
        if self.current_process is not None:
            if not messagebox.askyesno(
                "Выход",
                "Есть активный процесс. Остановить его и выйти?",
            ):
                return
            self.stop_process()
        self.root.after(200, self.root.destroy)

    def run(self) -> int:
        self.root.mainloop()
        return self.exit_code


def launch_gui(profile: str | None = None) -> int:
    app = GuiApp(profile=profile)
    return app.run()


def main() -> int:
    profile = sys.argv[1] if len(sys.argv) > 1 else None
    return launch_gui(profile=profile)


if __name__ == "__main__":
    raise SystemExit(main())
