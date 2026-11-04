from __future__ import annotations

import queue
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from gui_base import SettingsTab
from gui_farm import ControlTab
from gui_memory import MemoryTab, list_profiles


BOT_ACTIONS: tuple[tuple[str, str, bool], ...] = (
    ("Проверка", "--check", True),
    ("Скриншот", "--capture", True),
    ("Обучение", "--teach", True),
    ("Обучение без кликов", "--teach-only", True),
    ("Сброс памяти", "--reset-knowledge", True),
)


class BotTab(ttk.Frame):
    """Tab for direct main.py actions using only real CLI flags from main.py."""

    def __init__(self, parent: tk.Misc, app: GuiApp) -> None:
        super().__init__(parent, padding=12)
        self.app = app
        self._build()

    def _build(self) -> None:
        ttk.Label(self, text="Бот", font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        ttk.Label(
            self,
            text="Запуск отдельных режимов main.py. Используются только реально существующие флаги CLI.",
            wraplength=1000,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(4, 10))

        profile_row = ttk.Frame(self)
        profile_row.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(profile_row, text="Профиль:").pack(side=tk.LEFT)
        self.app.profile_combo = ttk.Combobox(
            profile_row,
            textvariable=self.app.profile_var,
            state="readonly",
            width=24,
        )
        self.app.profile_combo.pack(side=tk.LEFT, padx=(8, 0))
        self.app.profile_combo.bind("<<ComboboxSelected>>", self.app.on_profile_selected)

        ttk.Button(profile_row, text="Обновить", command=self.app.reload_profiles).pack(side=tk.LEFT, padx=(10, 0))

        actions = ttk.LabelFrame(self, text="Команды", padding=10)
        actions.pack(fill=tk.X)

        row = ttk.Frame(actions)
        row.pack(fill=tk.X)
        for title, flag, needs_profile in BOT_ACTIONS:
            button = ttk.Button(
                row,
                text=title,
                command=lambda current_flag=flag, current_needs_profile=needs_profile: self.app.run_main(
                    [current_flag],
                    with_profile=current_needs_profile,
                ),
            )
            button.pack(side=tk.LEFT, padx=(0, 8), pady=2)
            self.app.start_buttons.append(button)

        self.app.stop_button = ttk.Button(
            self,
            text="Остановить процесс",
            command=self.app.stop_process,
            state=tk.DISABLED,
        )
        self.app.stop_button.pack(anchor="w", pady=(10, 0))

        ttk.Label(self, textvariable=self.app.status_var).pack(anchor="w", pady=(10, 0))


class GuiApp:
    def __init__(self, profile: str | None = None) -> None:
        self.project_dir = Path(__file__).resolve().parent
        self.data_root = self.project_dir / "data" / "profiles"

        # Явный AppUserModelID до создания окна: иначе панель задач показывает
        # иконку pythonw.exe вместо иконки окна
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LDPlayerBot.GUI.1")
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.title("LDPlayer Bot")
        self.root.geometry("1180x780")
        self.root.minsize(980, 680)
        icon_path = self.project_dir / "icon.ico"
        if icon_path.exists():
            try:
                self.root.iconbitmap(str(icon_path))
            except Exception:
                pass
            # iconphoto дублирует иконку для панели задач (надёжнее при pythonw)
            try:
                from PIL import Image, ImageTk
                self._icon_photo = ImageTk.PhotoImage(Image.open(icon_path))
                self.root.iconphoto(True, self._icon_photo)
            except Exception:
                pass
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.status_var = tk.StringVar(value="Готово")
        self.profile_var = tk.StringVar(value=profile or "")

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.app_log = self.log

        self.profile_combo: ttk.Combobox | None = None
        self.stop_button: ttk.Button | None = None
        self.start_buttons: list[ttk.Button] = []
        self.control_tab: ControlTab | None = None
        self.settings_tab: SettingsTab | None = None
        self.memory_tab: MemoryTab | None = None
        self.bot_tab: BotTab | None = None
        self.full_stop_button = None

        self.current_process = None
        self.current_thread = None
        self.stop_requested = False
        self.exit_code = 0
        self._closing = False

        self._build_ui()
        self._mount_tabs()
        self.reload_profiles()

    def log(self, text: str) -> None:
        self.log_queue.put(str(text))

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=10)
        container.pack(fill=tk.BOTH, expand=True)
        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill=tk.BOTH, expand=True)

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
        profiles = list_profiles(self.data_root)
        if not profiles:
            profiles = ["loc1"]
        if self.profile_combo is not None:
            self.profile_combo["values"] = profiles
        current = self.profile_var.get().strip()
        if current not in profiles:
            self.profile_var.set(profiles[0])
        self.log(f"[GUI] Профили обновлены: {', '.join(profiles)}")

    def on_profile_selected(self, _event=None) -> None:
        self.log(f"[GUI] Выбран профиль: {self.profile_var.get().strip()}")
        if self.memory_tab is not None:
            try:
                self.memory_tab.reload()
            except Exception:
                pass

    def run_main(self, args: list[str], *, with_profile: bool = True) -> None:
        if self.control_tab is not None and getattr(self.control_tab, "running", False):
            messagebox.showwarning("Бот занят", "Сначала остановите фарм во вкладке Управление.")
            return
        if hasattr(self.settings_tab, "_log_console"):
            title = " ".join(args) or "main"
            profile_suffix = f" --profile {self.profile_var.get().strip()}" if with_profile else ""
            self.settings_tab._log_console(f"[GUI] Команда из вкладки Бот: main.py {' '.join(args)}{profile_suffix}")
            self.settings_tab._log_console("[GUI] Запуск из вкладки Бот не реализован в этом окне; используйте вкладку Управление для фарма.")
        self.status_var.set("Команда передана в консоль Настроек")

    def stop_process(self) -> None:
        if self.control_tab is not None:
            self.control_tab.do_stop()

    def is_bot_running(self) -> bool:
        return bool(self.control_tab is not None and getattr(self.control_tab, "running", False))

    bot_running = is_bot_running

    def on_close(self) -> None:
        if self.control_tab is not None:
            listener = getattr(self.control_tab, "hotkey_listener", None)
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass
        if self.is_bot_running():
            if not messagebox.askyesno("Выход", "Бот ещё работает. Остановить и выйти?"):
                return
            try:
                self.control_tab.do_full_stop()  # type: ignore[union-attr]
            except Exception:
                pass
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
