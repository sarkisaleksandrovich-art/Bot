# GUI bug audit

Source ref audited: `main`
Files read fully in 300-line chunks: `gui.py`, `gui_base.py`, `gui_farm.py`, `gui_memory.py`, `bot/teacher_ui.py`, `GUI_README.md`

## Summary
- Critical: 2
- High: 4
- Medium: 3
- Low: 1

## Findings

### GUI-001
- Severity: Critical
- File: `gui_farm.py:122-130`
- Description: Global hotkey callback calls Tkinter from a non-main thread via `self.after(...)`.
- Evidence: `_start_hotkey_listener_once()` starts `listener_thread`, then inside nested `worker()` it creates a `pynput_keyboard.Listener`. Its `on_press` callback directly executes `self.after(0, self.do_start)` / `self.after(0, self.do_stop)` / `self.after(0, self.do_event)` / `self.after(0, self.do_full_stop)`. Those callbacks run on the listener’s thread, not the Tk mainloop thread.
- Why it is a bug: Tkinter is not thread-safe. Even calling `.after()` on a widget from a worker thread is unsafe and can crash intermittently with `RuntimeError: main thread is not in main loop` or corrupt UI state.
- Suggested fix: Never touch Tk from the pynput callback. Push an enum/message into a thread-safe queue from `on_press`, and have the main Tk thread poll that queue with `root.after(...)`.

### GUI-002
- Severity: Critical
- File: `gui_farm.py:216-219`, `gui_farm.py:329-332`
- Description: Background worker threads call `self.after(...)` directly to re-enter Tk.
- Evidence: In `do_start()`, the navigation worker ends with `self.after(0, lambda: self._finish_run(message))`. In `_release_and_snapshot_async()`, the worker calls `self.after(0, lambda: self._after_stop_snapshot(...))` both in success and exception paths.
- Why it is a bug: These lambdas are scheduled from non-main threads. Tkinter thread safety rules still apply; `.after()` itself is a Tk call. This is exactly the thread-safety violation the audit was asked to find.
- Suggested fix: Replace all worker-to-Tk calls with queue-based handoff. The worker should put structured results into a `queue.Queue`; the main thread should own one polling `after()` loop that drains it and updates widgets.

### GUI-003
- Severity: High
- File: `gui.py:105-114`, `gui_base.py:792-807`
- Description: App logs are enqueued but no log panel exists until the Settings tab is created and active polling starts there.
- Evidence: `GuiApp.log()` only does `self.log_queue.put(...)`. The only reader is `SettingsTab._poll_app_log_queue()`, which appends messages to the console in the Settings tab. `gui.py` builds no global log widget, and other tabs have no direct log display.
- Why it is a bug: Logging is effectively hidden behind one tab instead of being a guaranteed application log panel. If Settings fails to construct, is removed, or its polling stops, the queue grows unread. The requested audit specifically asked to flag the missing log panel / unread queue issue.
- Suggested fix: Move log consumption to `GuiApp` and provide a dedicated always-visible log panel or status console owned by the root window. Tabs can write to it, but one central component should drain `log_queue` regardless of tab state.

### GUI-004
- Severity: High
- File: `gui.py:142-152`
- Description: `run_main()` is an unimplemented handler; Bot tab buttons do not launch `main.py`.
- Evidence: The method only writes two console lines: `Команда ...` and `Запуск ... не реализован в этом окне`, then sets status text to `Команда передана в консоль Настроек`.
- Why it is a bug: The Bot tab renders actionable buttons for CLI modes, but pressing them does not execute anything. This is dead UI behavior / misleading controls.
- Suggested fix: Either implement actual subprocess launch with proper state management, or disable/remove the buttons until supported and clearly label them as unavailable.

### GUI-005
- Severity: High
- File: `gui_base.py:76-78`, `gui_base.py:170-179`, `gui_base.py:903-906`
- Description: `NAV_PATH`/`BASE_DIR`/`LIVE_LOG_PATH` are global relative paths, while the rest of the app uses `project_dir`-relative paths.
- Evidence: `BASE_DIR = Path("data/base")`, `NAV_PATH = BASE_DIR / "nav.json"`, `LIVE_LOG_PATH = Path("logs") / "bot_live.log"`. `_load_nav()` and `_save_nav()` use those globals directly. Meanwhile `gui_farm.py` reads nav via `self.project_dir / "data" / "base" / "nav.json"` and console status reports `NAV_PATH.exists()`.
- Why it is a bug: Launching the GUI from any current working directory other than the repo root makes Settings read/write different files than ControlTab. It also makes `tail`/status/log output point at process-CWD logs rather than project logs.
- Suggested fix: Eliminate module-level relative paths. Derive all GUI file paths from `app.project_dir` (or inject a path provider) so every tab reads/writes the same repository-local files regardless of process CWD.

### GUI-006
- Severity: High
- File: `gui_base.py:345-372`
- Description: Settings tab content has no scrollbar/canvas, so the long navigation + training form can become inaccessible on smaller displays.
- Evidence: `_build_ui()` packs `top = ttk.Frame(self)` and directly adds large `LabelFrame` sections plus the console. There is no `Canvas`, no vertical `Scrollbar`, and no mouse-wheel scroll binding for the settings form.
- Why it is a bug: The tab contains many rows and long help labels. On low-resolution laptops or scaled Windows desktops, users cannot reach lower controls reliably. This matches the requested “missing scroll in settings” audit target.
- Suggested fix: Wrap the settings content in a scrollable canvas/frame pattern (or a `ScrolledFrame`) and bind wheel scrolling. Keep the console separately scrollable.

### GUI-007
- Severity: Medium
- File: `gui_base.py:728-731`, `gui.py:96-102`
- Description: The app log queue is only drained by `SettingsTab`; messages produced before/without that poller are stranded, and queue ownership is in the wrong layer.
- Evidence: `GuiApp` owns `self.log_queue`, but never schedules its own drain loop. `SettingsTab.__init__()` alone calls `_poll_app_log_queue()`, which recursively reschedules itself with `self.after(100, ...)`.
- Why it is a bug: This couples core app logging to one optional tab instance. If that tab is ever recreated/destroyed, duplicate readers or no reader can occur. It is also inconsistent architecture for cross-tab logging.
- Suggested fix: Drain `GuiApp.log_queue` from `GuiApp` itself and fan logs out to registered sinks/widgets.

### GUI-008
- Severity: Medium
- File: `gui_base.py:816-818`, `gui_farm.py:175-176`, `gui.py:100-101`
- Description: Running-state checks use the wrong app field: dangerous console commands are gated by `app.current_process`, but the real running subprocess is stored in `ControlTab.main_process` and `ControlTab.running`.
- Evidence: `_dispatch_command()` blocks dangerous commands only if `getattr(self.app, "current_process", None) is not None`. `GuiApp.current_process` is initialized in `gui.py` but never assigned by `ControlTab`. Actual process state is kept in `self.main_process` and `self.running` in `gui_farm.py`.
- Why it is a bug: Console commands such as `screenshot`, `find`, `hold`, `loot`, `mob`, `event`, and `go` are not blocked while the farm is active, despite the warning message claiming they are. This can interfere with a running bot.
- Suggested fix: Gate on `self.app.is_bot_running()` or `self.app.control_tab.running/main_process`, and remove stale `GuiApp.current_process` if unused.

### GUI-009
- Severity: Medium
- File: `gui_base.py:157-167`
- Description: Settings normalization only recognizes `match_threshold`, while the console exposes both `match_threshold` and virtual `match_level`; there is no persisted `match_level` key and README/help suggest a level abstraction.
- Evidence: `_normalize_settings()` loads only `click_radius_px`, `approach_timeout_s`, `hold_chunk_ms`, `frame_interval_ms`, `match_threshold`. `_cmd_set()` accepts `match_level` but converts it immediately into `match_threshold` only.
- Why it is a bug: This is a settings-key mismatch at the GUI/API level: one part of the UI exposes a setting name that is not actually persisted. It is survivable, but confusing and easy to desync if other code or users expect `match_level` to exist in JSON.
- Suggested fix: Either standardize on `match_threshold` everywhere, or explicitly persist/read `match_level` as the canonical UI key and derive threshold from it.

### GUI-010
- Severity: Low
- File: `gui_farm.py:107-110`, `gui_farm.py:122-130`
- Description: Hotkey implementation is brittle and platform-dependent: focus hotkeys use `<KP_*>` only, and global hotkeys depend on raw virtual-key codes.
- Evidence: Focus bindings only register keypad events `<KP_1>`, `<KP_2>`, `<KP_3>`, `<KP_0>`. Global listener checks `vk == 97/98/99/96` directly.
- Why it is a bug: NumLock state, keyboard layout, laptop numpad emulation, or non-Windows behavior can break these bindings. Users may press top-row digits or different keypad scancodes and get no action.
- Suggested fix: Support both keypad and regular key bindings where appropriate, avoid hard-coded VK assumptions, and normalize keys through pynput’s `KeyCode`/character handling with explicit platform testing.
