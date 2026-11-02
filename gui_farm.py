from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import ttk

from gui_base import capture_region

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None

try:
    from pynput import keyboard as pynput_keyboard  # type: ignore
except Exception:  # pragma: no cover
    pynput_keyboard = None

ACTION_LABELS = {
    "loot": "Лут",
    "mob": "Моб",
    "event_choice": "Событие-выбор",
    "event_skip": "Событие-пропустить",
    "skip": "Пропустить объект",
}
VALID_ACTIONS = {"loot", "mob", "event_choice", "event_skip", "skip"}


class ControlTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.project_dir = Path(getattr(app, "project_dir", ".")).resolve()
        self.stop_event = threading.Event()
        self.nav_thread: threading.Thread | None = None
        self.main_process: subprocess.Popen[str] | None = None
        self.listener_thread: threading.Thread | None = None
        self.hotkey_listener = None
        self.listener_started = False
        self.running = False
        self._stop_snapshot_busy = False
        self._manual_dialog_open = False
        self._matched_action: str | None = None
        self._matched_score: float | None = None
        self._matched_name: str | None = None
        self._matched_png: str | None = None

        self.location_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Готово")
        self.location_names: list[str] = []

        self._build_ui()
        self._bind_focus_hotkeys()
        self._start_hotkey_listener_once()
        self.reload_locations()
        self.refresh_ready_state()

    def _build_ui(self) -> None:
        ttk.Label(self, text="Управление", font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        ttk.Label(
            self,
            text="Простой запуск фарма без ввода этажей вручную. Локации берутся из data/base/nav.json.",
            wraplength=1000,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(4, 8))

        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 8))

        ttk.Label(top, text="Локация:").grid(row=0, column=0, sticky="w")
        self.location_combo = ttk.Combobox(top, textvariable=self.location_var, state="readonly", width=36)
        self.location_combo.grid(row=0, column=1, sticky="w", padx=(8, 10))
        self.location_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_ready_state())
        ttk.Button(top, text="Обновить", command=self._on_reload).grid(row=0, column=2, sticky="w")

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(actions, text="Старт (Num1)", command=self.do_start, state=tk.DISABLED)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Стоп (Num2)", command=self.do_stop, state=tk.DISABLED)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.event_button = ttk.Button(actions, text="Событие (Num3)", command=self.do_event)
        self.event_button.pack(side="left", padx=(16, 0))
        self.loot_button = ttk.Button(actions, text="Лут", command=self.on_loot)
        self.loot_button.pack(side="left", padx=(8, 0))
        self.mob_button = ttk.Button(actions, text="Моб", command=self.on_mob)
        self.mob_button.pack(side="left", padx=(8, 0))

        ttk.Label(self, textvariable=self.status_var).pack(anchor="w", pady=(0, 8))

        if not getattr(self.app, "full_stop_button", None):
            container = getattr(self.app, "root", self.winfo_toplevel())
            bottom = ttk.Frame(container)
            bottom.pack(side="bottom", fill="x", padx=10, pady=(0, 10))
            button = tk.Button(
                bottom,
                text="Полная остановка (Num0)",
                command=self.do_full_stop,
                bg="#b71c1c",
                fg="white",
                activebackground="#d32f2f",
                activeforeground="white",
                relief="raised",
                padx=10,
                pady=6,
            )
            button.pack(fill="x")
            self.app.full_stop_button = button

    def _bind_focus_hotkeys(self) -> None:
        root = getattr(self.app, "root", self.winfo_toplevel())
        root.bind("<KP_1>", lambda _e: self.do_start())
        root.bind("<KP_2>", lambda _e: self.do_stop())
        root.bind("<KP_3>", lambda _e: self.do_event())
        root.bind("<KP_0>", lambda _e: self.do_full_stop())

    def _start_hotkey_listener_once(self) -> None:
        if self.listener_started:
            return
        self.listener_started = True
        if pynput_keyboard is None:
            self.log("pynput не установлен: глобальные Num-клавиши работают только при фокусе окна бота. Подсказка: pip install pynput")
            return

        def worker() -> None:
            def on_press(key) -> None:
                vk = getattr(key, "vk", None)
                if vk == 97:
                    self.after(0, self.do_start)
                elif vk == 98:
                    self.after(0, self.do_stop)
                elif vk == 99:
                    self.after(0, self.do_event)
                elif vk == 96:
                    self.after(0, self.do_full_stop)

            self.hotkey_listener = pynput_keyboard.Listener(on_press=on_press)
            self.hotkey_listener.daemon = True
            self.hotkey_listener.start()
            self.hotkey_listener.join()

        self.listener_thread = threading.Thread(target=worker, daemon=True)
        self.listener_thread.start()

    def _read_nav_json(self) -> dict[str, Any]:
        path = self.project_dir / "data" / "base" / "nav.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def reload_locations(self) -> None:
        data = self._read_nav_json()
        raw_locations = data.get("locations", []) if isinstance(data.get("locations"), list) else []
        names = []
        for item in raw_locations:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                if name:
                    names.append(name)
        self.location_names = names[:7]
        self.location_combo["values"] = self.location_names
        if self.location_var.get().strip() not in self.location_names:
            self.location_var.set(self.location_names[0] if self.location_names else "")

    def _on_reload(self) -> None:
        self.reload_locations()
        self.refresh_ready_state()

    def refresh_ready_state(self) -> None:
        try:
            import nav
            status = nav.nav_status(self.project_dir)
        except Exception as exc:
            self.start_button.configure(state=tk.DISABLED)
            self.status_var.set(f"Ошибка nav.py: {exc}")
            return
        nav_ok = bool(status.get("ready")) and bool(status.get("nav_json_present")) and int(status.get("locations_count") or 0) > 0
        ready = nav_ok and bool(self.location_var.get().strip())
        self.start_button.configure(state=(tk.NORMAL if ready and not self.running else tk.DISABLED))
        self.stop_button.configure(state=(tk.NORMAL if self.running else tk.DISABLED))
        if not ready:
            self.status_var.set("Старт заблокирован: заполните цепочку в Настройках (nav.nav_status).")
        elif not self.running:
            self.status_var.set("Готово")

    def log(self, text: str) -> None:
        if hasattr(self.app, "log"):
            self.app.log(str(text))
        else:
            print(text)

    def do_start(self) -> None:
        self.refresh_ready_state()
        if self.running or self.start_button.cget("state") == tk.DISABLED:
            return
        location_name = self.location_var.get().strip()
        if not location_name:
            return
        self.stop_event.clear()
        self.running = True
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.status_var.set("Иду в локацию...")

        def worker() -> None:
            message = "Остановлено"
            try:
                import nav
                ok, reason = nav.go_to_dungeon(self.project_dir, location_name, log=self.log, stop_event=self.stop_event)
                if self.stop_event.is_set():
                    message = "Остановлено"
                elif not ok:
                    message = f"Навигация не выполнена: {reason}"
                else:
                    code, last = self._run_main_process()
                    if self.stop_event.is_set():
                        message = "Остановлено"
                    else:
                        message = "Фарм завершён" if code == 0 else f"main.py завершился с кодом {code}: {last}"
            except Exception as exc:
                message = f"Ошибка: {exc}"
            self.after(0, lambda: self._finish_run(message))

        self.nav_thread = threading.Thread(target=worker, daemon=True)
        self.nav_thread.start()

    def _run_main_process(self) -> tuple[int, str]:
        command = [sys.executable, "main.py", "--profile", self._selected_profile()]
        self.log(f"[FARM] {' '.join(command)}")
        self.main_process = subprocess.Popen(
            command,
            cwd=str(self.project_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        last_line = ""
        assert self.main_process.stdout is not None
        for line in self.main_process.stdout:
            last_line = line.rstrip("\n")
            self.log(last_line)
            if self.stop_event.is_set() and self.main_process.poll() is None:
                try:
                    self.main_process.terminate()
                except Exception:
                    pass
        code = self.main_process.wait()
        self.main_process = None
        return code, last_line

    def _selected_profile(self) -> str:
        value = getattr(getattr(self.app, "profile_var", None), "get", lambda: "")()
        value = str(value).strip()
        return value or "loc1"

    def _finish_run(self, message: str) -> None:
        self.running = False
        self.main_process = None
        self.status_var.set(message)
        self.refresh_ready_state()

    def do_stop(self) -> None:
        self.stop_event.set()
        self.status_var.set("Остановка...")
        self._terminate_main_process()
        self._release_and_snapshot_async()

    def _terminate_main_process(self) -> None:
        if self.main_process is not None and self.main_process.poll() is None:
            try:
                self.main_process.terminate()
            except Exception:
                pass

    def _release_and_snapshot_async(self) -> None:
        if self._stop_snapshot_busy:
            return
        self._stop_snapshot_busy = True

        def worker() -> None:
            try:
                import nav
                if self.nav_thread is not None and self.nav_thread.is_alive():
                    self.nav_thread.join(timeout=5.0)
                nav.release(self.project_dir, log=self.log)
                shot_path, frame = nav.take_screenshot(self.project_dir)
                found = nav.find_known_objects(self.project_dir, frame)
                annotated = nav.annotate_known(frame, found)
                annotated_path = str(Path(shot_path).with_name(Path(shot_path).stem + "_known.png"))
                if cv2 is not None and annotated is not None:
                    try:
                        cv2.imwrite(annotated_path, annotated)
                    except Exception:
                        annotated_path = shot_path
                summary = nav.memory_summary(self.project_dir)
                found_count = len(found)
                self.after(0, lambda: self._after_stop_snapshot(summary, found_count, shot_path, annotated_path))
            except Exception as exc:
                self.after(0, lambda: self._after_stop_snapshot(f"Ошибка стоп-сценария: {exc}", 0, "", ""))
            finally:
                self._stop_snapshot_busy = False

        threading.Thread(target=worker, daemon=True).start()

    def _after_stop_snapshot(self, summary: str, found_count: int, shot_path: str, annotated_path: str) -> None:
        self.log(summary)
        self.log(f"Найдено известных объектов: {found_count}")
        self.log("Объекты без рамки считаются новыми.")
        if shot_path:
            self.log(f"Скриншот: {shot_path}")
        if annotated_path:
            self.log(f"Известные объекты с рамками: {annotated_path}")
        self.status_var.set("Пауза после Стоп")
        self.running = False
        self.refresh_ready_state()
        self._open_manual_stop_dialog()

    def _open_manual_stop_dialog(self) -> None:
        if self._manual_dialog_open:
            return
        self._manual_dialog_open = True
        self._matched_action = None
        self._matched_score = None
        self._matched_name = None
        self._matched_png = None
        try:
            result = capture_region(self, "Выделите рамкой объект. Объекты без рамки на предыдущем скриншоте считаются новыми.")
            if not result:
                return
            crop, _bbox = result
            try:
                import nav
                matched = nav.match_known(self.project_dir, crop)
            except Exception as exc:
                self.log(f"match_known ошибка: {exc}")
                matched = None
            replace_known = False
            matched_action: str | None = None
            matched_score: float | None = None
            matched_name: str | None = None
            matched_png: str | None = None
            if isinstance(matched, tuple) and len(matched) >= 1:
                matched_action = str(matched[0]).strip() or None
                if len(matched) >= 2:
                    try:
                        matched_score = float(matched[1])
                    except Exception:
                        matched_score = None
                if len(matched) >= 3:
                    matched_name = str(matched[2]).strip() or None
                if len(matched) >= 4:
                    matched_png = str(matched[3]).strip() or None
            elif isinstance(matched, str):
                matched_action = matched.strip() or None
            if matched_action:
                self._matched_action = matched_action
                self._matched_score = matched_score
                self._matched_name = matched_name
                self._matched_png = matched_png
                decision = self._ask_known_match(matched_name or ACTION_LABELS.get(matched_action, matched_action), matched_score)
                if decision == "cancel":
                    return
                if decision == "keep":
                    if matched_score is None:
                        self.log(f"Это уже в памяти как {matched_name or ACTION_LABELS.get(matched_action, matched_action)}")
                    else:
                        self.log(f"Это уже в памяти как {matched_name or ACTION_LABELS.get(matched_action, matched_action)} (score={matched_score:.3f})")
                    return
                replace_known = decision == "replace"
            self._ask_action_dialog(crop, matched_action, replace_known)
        finally:
            self._manual_dialog_open = False

    def _ask_known_match(self, known_name: str, score: float | None) -> str:
        win = tk.Toplevel(self)
        win.title("Совпадение с памятью")
        win.transient(self.winfo_toplevel())
        win.grab_set()
        result = {"value": "cancel"}
        text = f"Это уже в памяти как {known_name}"
        if score is not None:
            text += f"\nscore={score:.3f}"
        ttk.Label(win, text=text, padding=12, justify=tk.LEFT).pack(anchor="w")
        row = ttk.Frame(win)
        row.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(row, text="Оставить", command=lambda: self._close_modal(win, result, "keep")).pack(side="left")
        ttk.Button(row, text="Заменить", command=lambda: self._close_modal(win, result, "replace")).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Отмена", command=lambda: self._close_modal(win, result, "cancel")).pack(side="right")
        self.wait_window(win)
        return result["value"]

    def _ask_action_dialog(self, crop: Any, remembered_action: str | None, replace_known: bool) -> None:
        choice = self._show_action_choice_dialog(remembered_action)
        if choice in {None, "cancel"}:
            return
        if choice == "full_stop":
            self.do_full_stop()
            return
        if choice == "continue":
            self.do_start()
            return
        if choice in VALID_ACTIONS:
            try:
                self._save_action_memory(crop, choice, replace=replace_known)
            except Exception as exc:
                self.log(f"Не удалось сохранить действие: {exc}")
            if choice != "skip":
                self._manual_execute_action(choice, crop)

    def _show_action_choice_dialog(self, remembered_action: str | None) -> str | None:
        win = tk.Toplevel(self)
        win.title("Что делать с этим?")
        win.transient(self.winfo_toplevel())
        win.grab_set()
        result = {"value": None}
        ttk.Label(win, text="Что делать с этим?", padding=(12, 12, 12, 6), font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        items = []
        if remembered_action:
            items.append((remembered_action, f"Сохранённое действие: {ACTION_LABELS.get(remembered_action, remembered_action)}"))
        for value, text in [
            ("loot", "Лут"),
            ("mob", "Моб"),
            ("event_choice", "Событие-выбор"),
            ("event_skip", "Событие-пропустить"),
            ("skip", "Пропустить объект"),
            ("continue", "Продолжить фарм"),
            ("full_stop", "Полная остановка"),
        ]:
            if value == remembered_action:
                continue
            items.append((value, text))
        for value, text in items:
            style = "Accent.TButton" if remembered_action and value == remembered_action else "TButton"
            ttk.Button(body, text=text, style=style, command=lambda v=value: self._close_modal(win, result, v)).pack(fill="x", pady=2)
        tk.Button(body, text="Отмена", command=lambda: self._close_modal(win, result, "cancel")).pack(fill="x", pady=(8, 0))
        self.wait_window(win)
        return result["value"]

    def _manual_execute_action(self, action: str, crop: Any) -> None:
        self.log("Подхожу к цели")

        def worker() -> None:
            try:
                import nav
                if action == "loot":
                    ok, reason = nav.click_loot(self.project_dir, crop, log=self.log, stop_event=self.stop_event)
                elif action == "mob":
                    ok, reason = nav.attack_mob(self.project_dir, crop, log=self.log, stop_event=self.stop_event)
                else:
                    ok, reason = nav.handle_event(self.project_dir, crop, log=self.log, stop_event=self.stop_event)
                self.log(f"Ручное действие: ok={ok}, reason={reason}")
            except Exception as exc:
                self.log(f"Ошибка ручного действия: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _load_actions_data(self) -> dict[str, Any]:
        actions_path = self.project_dir / "data" / "base" / "actions.json"
        try:
            data = json.loads(actions_path.read_text(encoding="utf-8")) if actions_path.exists() else {}
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        objects = data.get("objects", data.get("actions", data.get("templates", [])))
        if not isinstance(objects, list):
            objects = []
        data["objects"] = [item for item in objects if isinstance(item, dict)]
        return data

    def _save_action_memory(self, crop: Any, action: str, replace: bool = False) -> None:
        if action not in VALID_ACTIONS:
            raise ValueError(f"Недопустимое действие: {action}")
        base_dir = self.project_dir / "data" / "base"
        base_dir.mkdir(parents=True, exist_ok=True)
        actions_path = base_dir / "actions.json"
        data = self._load_actions_data()
        objects = data["objects"]
        png_name = f"manual_{int(time.time() * 1000)}.png"
        record_name = self._matched_name or f"manual_{action}_{int(time.time())}"

        if replace and self._matched_action:
            for index, item in enumerate(objects):
                item_action = str(item.get("action", "")).strip()
                item_name = str(item.get("name", "")).strip()
                item_png = str(item.get("png") or item.get("template") or item.get("file") or "").strip()
                if item_action != self._matched_action:
                    continue
                if self._matched_name and item_name == self._matched_name:
                    png_name = item_png or png_name
                    objects[index] = {"name": record_name, "png": png_name, "action": action}
                    if cv2 is None or not cv2.imwrite(str(base_dir / png_name), crop):
                        raise RuntimeError("Не удалось сохранить шаблон объекта")
                    actions_path.write_text(json.dumps({"objects": objects}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    return
                if self._matched_png and item_png == self._matched_png:
                    png_name = item_png or png_name
                    objects[index] = {"name": record_name, "png": png_name, "action": action}
                    if cv2 is None or not cv2.imwrite(str(base_dir / png_name), crop):
                        raise RuntimeError("Не удалось сохранить шаблон объекта")
                    actions_path.write_text(json.dumps({"objects": objects}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    return

        if cv2 is None or not cv2.imwrite(str(base_dir / png_name), crop):
            raise RuntimeError("Не удалось сохранить шаблон объекта")
        objects.append({"name": record_name, "png": png_name, "action": action})
        actions_path.write_text(json.dumps({"objects": objects}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def on_loot(self) -> None:
        self._manual_target_action("loot")

    def on_mob(self) -> None:
        self._manual_target_action("mob")

    def do_event(self) -> None:
        self._manual_target_action("event_choice")

    def _manual_target_action(self, action: str) -> None:
        if self._manual_dialog_open:
            return
        result = capture_region(self, "Выделите цель на экране")
        if not result:
            return
        crop, _bbox = result
        self._manual_execute_action(action, crop)

    def do_full_stop(self) -> None:
        self.stop_event.set()
        self.running = False
        self._terminate_main_process()
        if self.nav_thread is not None and self.nav_thread.is_alive():
            self.nav_thread.join(timeout=5.0)
        try:
            import nav
            nav.release(self.project_dir, log=self.log)
        except Exception as exc:
            self.log(f"release ошибка: {exc}")
        root = getattr(self.app, "root", self.winfo_toplevel())
        for child in list(root.winfo_children()):
            if isinstance(child, tk.Toplevel):
                try:
                    child.destroy()
                except Exception:
                    pass
        self.status_var.set("Полная остановка")
        self.refresh_ready_state()

    on_start = do_start
    on_stop = do_stop
    on_event = do_event
    on_full_stop = do_full_stop

    @staticmethod
    def _close_modal(win: tk.Toplevel, state: dict[str, Any], value: str) -> None:
        state["value"] = value
        win.destroy()


FarmTab = ControlTab

__all__ = ["ControlTab", "FarmTab"]
