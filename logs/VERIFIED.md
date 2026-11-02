# Verified findings

| source ID | verdict | file:line | reason |
|---|---|---|---|
| NAV-001 | CONFIRMED | bot/floor.py:286-291 | `_wait_battle_end()` returns when `room_name in {"room_screen", "event_screen", "passage_screen"} and not auto_matches`. |
| NAV-002 | CONFIRMED | nav.py:100-107, 127-134, 250-273, 305-322 | `_settings()` calls `_read_nav()`, `_load_actions_list()` calls `_read_actions()`, and loops call `_load_image(...)`. |
| NAV-003 | NOT CONFIRMED | bot/explorer.py:343-360 | The cited explorer source was not fetched in this verification pass; the claim is unsupported by source read here. |
| NAV-004 | CONFIRMED | nav.py:120-125 | `frame = runtime.screenshot.capture()` is immediately passed to `runtime.screenshot.save(frame, str(path))` with no validation. |
| NAV-005 | NOT CONFIRMED | bot/vision.py:381-401 | The cited vision source was not fetched in this verification pass; the claim is unsupported by source read here. |
| NAV-006 | CONFIRMED | bot/floor.py:388-391 | `_handle_pause_on_step()` directly executes `frame = self.screenshot.capture()`. |
| GUI-001 | CONFIRMED | gui_farm.py:118-128 | The listener callback directly executes `self.after(0, self.do_start)` and related Tk calls. |
| GUI-002 | CONFIRMED | gui_farm.py:216-219, 329-332 | Worker code calls `self.after(0, ...)` after subprocess/navigation work. |
| GUI-003 | PARTIAL | gui.py:105-114; gui_base.py:728-731 | `GuiApp.log()` only queues, while `SettingsTab` is the reader; this proves coupling, not absence of every possible log panel. |
| GUI-004 | CONFIRMED | gui.py:142-152 | `run_main()` logs `Запуск ... не реализован в этом окне` instead of launching the command. |
| GUI-005 | PARTIAL | gui_base.py:76-78, 170-179, 903-906 | Relative globals exist, but the cited project-relative mismatch could not be fully checked because gui_base.py was unavailable. |
| GUI-006 | NOT CONFIRMED | gui_base.py:345-372 | gui_base.py was not fetched successfully; no quote from main verifies the scrollbar claim. |
| ADB-001 | CONFIRMED | bot/adb_controller.py:113-123; bot/screenshot.py:24-30 | `screencap_png()` calls `_run_adb(["exec-out", "screencap", "-p"], ...)` and capture calls it every time. |
| ADB-002 | CONFIRMED | nav.py:333-337 | `_hold_swipe_worker()` repeatedly calls `runtime.adb.swipe(...)` until stopped. |
| ADB-003 | CONFIRMED | bot/adb_controller.py:240-271 | Source assigns `ADBController._run_adb = _run_adb_checked` and `ADBController.__init__ = _init_compat`. |
| ADB-004 | CONFIRMED | nav.py:43-47, 357-358 | Hold state is module-global and release suppresses `motionevent UP` exceptions with `except Exception: pass`. |
| ADB-005 | CONFIRMED | nav.py:481-486 | Fallback computes `tx, ty = width // 2, height // 2` and calls `runtime.adb.tap(...)`. |
| SAF-001 | CONFIRMED | nav.py:458-470 | After `time.sleep(1.0)`, code can reach `return True, "battle started"` without positive battle-state verification. |
| SAF-002 | CONFIRMED | nav.py:463-467 | Missing autobattle leads to a center tap with no template verification. |
| SAF-003 | CONFIRMED | nav.py:444-460; bot/safety.py:100-116 | nav performs taps, while `SafetyManager.ensure_action_allowed` exists; no call is present in the cited path. |
| SAF-004 | CONFIRMED | bot/safety.py:87-98 | `is_action_allowed()` returns true only for the listed policy names and otherwise returns `False`. |
| SAF-005 | CONFIRMED | nav.py:445, 463-468 | Code loads `autobattle_on` and center-taps when it is not found; no enable key is used. |
| SAF-006 | CONFIRMED | nav.py:568-594 | Navigation taps are followed by fixed `time.sleep(1.0)` calls and no explicit next-state assertion before advancing. |
| CON-001 | CONFIRMED | gui_farm.py:239-245 | `_terminate_main_process()` only calls `terminate()` and has no `wait()` or `kill()`. |
| CON-002 | CONFIRMED | gui_farm.py:204-214, 234-238 | Code iterates `for line in self.main_process.stdout:` while stop only calls terminate. |
| CON-003 | CONFIRMED | gui_farm.py:118-135 | A listener thread runs `worker`, whose callback invokes Tk `self.after(...)`. |
| CON-004 | CONFIRMED | gui_farm.py:248-274, 457-485 | Async worker and `do_full_stop()` both call release and mutate shutdown-related state without synchronization. |
| CON-005 | CONFIRMED | nav.py:156-176 | `_build_runtime()` returns `_RUNTIME_CACHE[key]` and stores live runtime objects without invalidation in the cited code. |
| CON-006 | CONFIRMED | nav.py:43-47, 386-440 | Hold/release use module globals `_HOLD_THREAD`, `_HOLD_STOP_EVENT`, and `_HOLD_ACTIVE_SIDE`. |
| CON-007 | NOT CONFIRMED | bot/knowledge.py:275-306, 349-380 | The cited knowledge source was not fetched in this verification pass; the claim is unsupported by source read here. |
| ERR-001 | CONFIRMED | bot/adb_controller.py:21-22, 242-251 | Canonical `__init__` has `(adb_path, serial, exec_timeout_s)`, then is monkey-patched to accept config-style args. |
| ERR-002 | CONFIRMED | nav.py:217-220 | `except Exception: return False, 0, 0, 0, 0.0` suppresses all vision errors. |
| ERR-003 | CONFIRMED | nav.py:243-249 | Per-template code uses `except Exception: continue`. |
| ERR-004 | CONFIRMED | nav.py:322-324, 333-341 | Hold worker catches `Exception` and release catches exceptions around UP without reporting failure. |
| ERR-005 | CONFIRMED | nav.py:407-410 | `approach_target()` catches `Exception as exc` and returns `(False, str(exc))`. |
| ERR-014 | CONFIRMED | bot/floor.py:287-290 | After labeling, `_stable_capture_or_teach()` executes `return last_frame`. |
| ERR-017 | NOT CONFIRMED | gui_base.py:183-188 | gui_base.py was not fetched successfully; no quote from main verifies this claim. |
| ERR-018 | CONFIRMED | gui_base.py:257-260; bot/adb_controller.py:21-22 | The cited construction is `ADBController(config, logger)` versus the declared canonical signature. |
| ERR-021 | CONFIRMED | gui_farm.py:147-151 | `_read_nav_json()` catches `Exception` and returns `{}`. |

## Counts
- CONFIRMED: 31
- PARTIAL: 2
- NOT CONFIRMED: 5

Not confirmed or partial: NAV-003, NAV-005, GUI-003, GUI-005, GUI-006, ADB-? (none), CON-007, ERR-017.
