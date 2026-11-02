# Error-handling and Python 3.14 compatibility audit

Scanned ref: `main`
Generated: 2026-11-02

## Findings

### ERR-001
- Severity: high
- File: `bot/adb_controller.py:57-59, 122-127`
- Description: `shell()` always calls `check_connection()`, and `check_connection()` itself calls `_run_adb(["get-state"], ...)`, which validates exit status. After that, `shell()` runs a second ADB command. This is safe, but the main compatibility bug is elsewhere: `main.py` and other callers use `ADBController(config, logger)`, while the compatibility shim in `bot/adb_controller.py` treats `args[0]` as config and silently ignores the second positional `logger`. That works only because `_init_compat` accepts `*args`, but it also means the signature seen by type checkers and readers is wrong and can hide bad calls.
- Evidence: `bot/adb_controller.py:21-22` defines `__init__(self, adb_path: str, serial: str | None = None, exec_timeout_s: float = 15.0)`, while `bot/adb_controller.py:242-251` monkey-patches `ADBController.__init__` to accept a config object. `main.py:89`, `nav.py:195`, `gui_base.py:259` call `ADBController(config, logger)`.
- Suggested fix: Replace the runtime monkey-patch with a real overloaded or explicit constructor/classmethod (for example `ADBController.from_config(config, logger)`), and update call sites accordingly so static checking matches runtime behavior.

### ERR-002
- Severity: critical
- File: `nav.py:217-220`
- Description: `find_template()` swallows every exception from `runtime.vision.find(...)` and returns a normal “not found” result. This hides programming errors, invalid frame/template state, ROI failures, and OpenCV errors, causing navigation logic to proceed on corrupted state instead of failing loudly.
- Evidence: `except Exception: return False, 0, 0, 0, 0.0` in `nav.py:217-220`.
- Suggested fix: Catch only expected lookup failures (for example `VisionError` for invalid ROI/template) and log them with context; re-raise unexpected exceptions.

### ERR-003
- Severity: high
- File: `nav.py:243-249`
- Description: `find_known_objects()` catches all exceptions per template and silently skips the object. If `frame` is `None`, a template is unreadable, or the vision engine is broken, the function reports partial or empty results with no signal to the caller.
- Evidence: `except Exception: continue` in `nav.py:243-249`.
- Suggested fix: Narrow the exception list, emit a log entry with template path/category, and fail fast on programmer errors such as `frame is None`.

### ERR-004
- Severity: high
- File: `nav.py:322-324, 333-341`
- Description: low-level input errors in joystick hold/release are silently ignored, which can leave the bot believing a hold/release succeeded when the emulator rejected the input. This can wedge movement state and create hard-to-debug navigation hangs.
- Evidence: `_hold_swipe_worker()` catches `Exception` and only sleeps (`nav.py:322-324`); `release()` catches all exceptions from `motionevent UP` and does nothing (`nav.py:333-341`).
- Suggested fix: Log failures, track hold-state health, and surface a failure to callers when release/input cannot be delivered.

### ERR-005
- Severity: high
- File: `nav.py:355-413`
- Description: `approach_target()` wraps the whole navigation loop in `except Exception` and converts every failure into `(False, str(exc))`. This swallows coding bugs and makes callers treat internal errors as ordinary navigation misses.
- Evidence: `except Exception as exc: ... return False, str(exc)` in `nav.py:407-410`.
- Suggested fix: Catch only expected operational exceptions, log and re-raise unexpected ones, or return structured error categories.

### ERR-006
- Severity: medium
- File: `main.py:91-97, 99-103, 273-274`
- Description: several import blocks catch broad `Exception`, converting any bug during module import into a generic runtime message. This masks import-time programming errors, circular imports, and syntax/runtime initialization failures.
- Evidence: `_make_core_objects()` catches `Exception` around `from bot.vision import VisionEngine` and `from bot.safety import SafetyManager` (`main.py:91-103`); top-level runner catches `(..., Exception)` in `main.py:273-274`.
- Suggested fix: Catch `ImportError`/`ModuleNotFoundError` only for missing dependencies, and let unexpected import/runtime errors propagate with traceback or be logged explicitly.

### ERR-007
- Severity: medium
- File: `main.py:124-126`
- Description: `_run_navigation()` catches `(..., Exception)` together with known ADB/screenshot errors, flattening unexpected failures into exit code 1 and stderr text only.
- Evidence: `except (ADBConnectionError, ADBCommandError, ADBError, ScreenshotError, Exception) as exc:` in `main.py:124-126`.
- Suggested fix: Remove the blanket `Exception` from grouped operational handlers; log known failures and let unexpected ones propagate or be logged with traceback.

### ERR-008
- Severity: medium
- File: `main.py:204-205`
- Description: `_run_list_profiles()` catches all exceptions per profile and continues. A corrupted profile or broken `KnowledgeBase` becomes a line of text instead of an actionable failure, which can hide repository-wide data issues.
- Evidence: `except Exception as exc: print(f"- {profile}: ошибка ({exc})")` in `main.py:204-205`.
- Suggested fix: Catch expected data errors only, and aggregate/report unexpected exceptions separately.

### ERR-009
- Severity: medium
- File: `main.py:247-248`
- Description: `_count_templates()` silently maps every exception to `-1`, losing the original cause and making caller behavior depend on a magic value.
- Evidence: `except Exception: return -1` in `main.py:247-248`.
- Suggested fix: Catch specific knowledge-loading exceptions and log them; otherwise propagate.

### ERR-010
- Severity: medium
- File: `bot/logging_setup.py:24-26`
- Description: handler-closing errors are silently swallowed. If a custom handler fails to close, resource leaks or locked log files on Windows can go unnoticed.
- Evidence: `except Exception: pass` in `bot/logging_setup.py:24-26`.
- Suggested fix: Catch `Exception` only to log a warning including handler identity.

### ERR-011
- Severity: medium
- File: `bot/explorer.py:313-316`
- Description: loading common templates uses unvalidated JSON and silently returns an empty list on any exception. Corrupt JSON, missing keys, or path errors are all interpreted as “no templates”, which can break screen recognition without diagnostics.
- Evidence: `except Exception: return []` around `json.load()` in `bot/explorer.py:313-316`.
- Suggested fix: Validate the JSON shape, catch `OSError`/`JSONDecodeError` explicitly, and log the failure path.

### ERR-012
- Severity: medium
- File: `bot/explorer.py:349-362`
- Description: error screenshot saving catches every exception and downgrades it to a warning, which can hide secondary failures such as invalid debug paths or screenshot provider regressions when debugging primary crashes.
- Evidence: `except Exception as exc: self.logger.warning(...)` in `bot/explorer.py:349-362`.
- Suggested fix: Keep the warning, but include traceback (`logger.exception`) and narrow catches where possible.

### ERR-013
- Severity: medium
- File: `bot/explorer.py:365-369`
- Description: `_safe_capture_last_frame()` suppresses all capture exceptions and returns a stale cached frame, potentially causing follow-up logic to reason about an old screen as if it were current.
- Evidence: `except Exception: return self._last_frame` in `bot/explorer.py:365-369`.
- Suggested fix: Catch only expected capture errors, log the fallback, and annotate stale-frame usage.

### ERR-014
- Severity: high
- File: `bot/floor.py:287-290`
- Description: when `vision.wait_until_stable()` never returns a frame, the code triggers manual labeling on `last_frame` and returns it after labeling, rather than forcing a fresh stable capture. This can continue with an unstable/old frame and propagate misclassification.
- Evidence: after retries, `self._pause_and_label(last_frame); return last_frame` in `bot/floor.py:287-290`.
- Suggested fix: After manual labeling, re-capture and re-validate a fresh frame before continuing.

### ERR-015
- Severity: medium
- File: `bot/floor.py:317-321`
- Description: `mark_open_room_icon()` failures due to missing doors are silently discarded. If door matching is systematically wrong, the bot loses state updates without visibility.
- Evidence: `except DoorNotFoundError: pass` in `bot/floor.py:317-321`.
- Suggested fix: Log a debug/warning message with the unmatched rectangle and node context.

### ERR-016
- Severity: medium
- File: `bot/safety.py:70-73`
- Description: `ensure_safe_frame()` catches every exception from vision and rewraps it as `SafetyError`, obscuring whether the root cause was bad input, OpenCV failure, or a programming bug.
- Evidence: `except Exception as exc: raise SafetyError(...) from exc` in `bot/safety.py:70-73`.
- Suggested fix: Catch expected vision exceptions specifically; use `logger.exception` or let unexpected exceptions propagate.

### ERR-017
- Severity: high
- File: `gui_base.py:183-188`
- Description: the GUI settings loader accepts invalid `nav.json` by returning defaults on any `OSError` or `ValueError`, silently discarding corrupt user data and hiding JSON shape problems.
- Evidence: `_load_nav()` catches `(OSError, ValueError)` and returns `_empty_nav()` in `gui_base.py:183-188`.
- Suggested fix: Distinguish file-not-found from malformed JSON, notify the user on corruption, and preserve the bad file for repair.

### ERR-018
- Severity: critical
- File: `gui_base.py:257-260`
- Description: `_make_provider()` repeats the constructor mismatch from the CLI path: it calls `ADBController(config, logger)` even though the class signature is `(adb_path, serial=None, exec_timeout_s=15.0)` and only works because of runtime monkey-patching. This is brittle for type-checkers, IDEs, and future refactors.
- Evidence: `gui_base.py:257-260` constructs `ADBController(config, logger)`; canonical signature is declared in `bot/adb_controller.py:21-22`.
- Suggested fix: Replace monkey-patch compatibility with an explicit `from_config()` constructor and update GUI/CLI/nav callers.

### ERR-019
- Severity: medium
- File: `gui.py:139-143`
- Description: profile-change refresh swallows every exception, hiding reload bugs in the Memory tab.
- Evidence: `except Exception: pass` in `gui.py:139-143`.
- Suggested fix: Log the exception and show a non-blocking UI error state.

### ERR-020
- Severity: medium
- File: `gui.py:170-185`
- Description: shutdown path silently ignores errors from stopping the hotkey listener and full-stop sequence, which can leave background threads/listeners alive on exit.
- Evidence: `except Exception: pass` in `gui.py:174-176` and `gui.py:181-183`.
- Suggested fix: Log failures during shutdown so stuck listeners/processes can be diagnosed.

### ERR-021
- Severity: high
- File: `gui_farm.py:147-151`
- Description: invalid or corrupted `data/base/nav.json` is silently treated as `{}`. The control tab then behaves as if no locations are configured, masking user-data corruption.
- Evidence: `_read_nav_json()` catches `Exception` and returns `{}` in `gui_farm.py:147-151`.
- Suggested fix: Catch `FileNotFoundError` separately; surface malformed JSON or other exceptions to the UI log/status.

### ERR-022
- Severity: medium
- File: `gui_farm.py:249-252, 272-275, 300-305`
- Description: several subprocess termination/fsync blocks swallow all exceptions. Failures to terminate the farm process or fsync action memory edits can go unnoticed, especially on Windows where file locking is common.
- Evidence: blanket `except Exception: pass` in `_run_main_process()` stop path (`gui_farm.py:249-252`), `_terminate_main_process()` (`gui_farm.py:272-275`), and `_write_actions_data()` fsync (`gui_farm.py:300-305`).
- Suggested fix: Log warnings with context; catch narrower OS/process exceptions.

### ERR-023
- Severity: medium
- File: `bot/teacher_ui.py:203-210`
- Description: closing the OpenCV window suppresses all `cv2.error`s. This can hide GUI backend problems and leave the UI state inconsistent.
- Evidence: `except cv2.error: pass` around `cv2.destroyWindow()` and `cv2.waitKey(1)` in `bot/teacher_ui.py:203-210`.
- Suggested fix: Log debug/warning information when cleanup fails.

### ERR-024
- Severity: low
- File: `src/ldplayer_dungeon_explorer_bot_with_audit/main.py:26-47`
- Description: the helper CLI catches broad `Exception` and rethrows a new generic `Exception`, discarding the original exception type for callers and tests.
- Evidence: `except Exception as e: raise Exception(...)` in `train()`, `replay()`, and `test()`.
- Suggested fix: Re-raise the original exception type with context (`raise RuntimeError(...) from e`) or let it propagate.

## Severity counts
- Critical: 2
- High: 8
- Medium: 12
- Low: 1
