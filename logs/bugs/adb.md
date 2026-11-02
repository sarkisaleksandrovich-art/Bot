# ADB / Screenshot / Input Audit

Source branch audited: `main`
Log branch: `bot-run-logs`
Files reviewed fully in 300-line chunks: `bot/adb_controller.py`, `bot/screenshot.py`, `nav.py`, plus call sites in `main.py` and `bot/floor.py`.

## Severity Summary
- Critical: 1
- High: 4
- Medium: 4
- Low: 1

---

## ADB-001 — Critical
**Severity:** Critical  
**File:** `nav.py:346-351`, `nav.py:380-419`, `bot/adb_controller.py:113-123`  
**Description:** Screenshot-driven navigation creates a brand new `adb exec-out screencap -p` process for every frame, including repeated polling loops. This is a major throughput and latency bug for LDPlayer/ADB automation because each frame pays full process-spawn cost, USB/socket setup cost, and PNG encode/decode cost.  
**Evidence:**
- `ADBController.screencap_png()` runs `self._run_adb(["exec-out", "screencap", "-p"], text=False)` on every call (`bot/adb_controller.py:113-123`).
- `ScreenshotProvider.capture()` always calls `self._adb.screencap_png()` (`bot/screenshot.py:24-30`).
- Navigation loops repeatedly call capture through `_stable_wait(runtime, ...)` and `runtime.screenshot.capture()` inside `approach_target()` (`nav.py:380-419`), `click_loot()` (`nav.py:429-436`), `attack_mob()` (`nav.py:439-446`), `handle_event()` (`nav.py:449-456`), and `start_battle()` (`nav.py:465-487`).
- `_stable_wait()` itself delegates to `runtime.vision.wait_until_stable(runtime.screenshot.capture, ...)` (`nav.py:147-156`), so one alignment attempt can spawn many screencap subprocesses.
**Why this is a bug:** On emulator automation, frame polling is hot-path code. Launching a fresh adb process and a fresh remote `screencap` command per frame drastically reduces FPS, increases latency variance, and makes movement logic overshoot or stall because decisions are based on old frames.  
**Suggested fix:** Replace per-frame `adb exec-out screencap -p` spawning with a persistent capture channel or emulator-native capture path. At minimum, centralize throttling/caching so a control loop can reuse the same recent frame instead of spawning one adb process per query.

---

## ADB-002 — High
**Severity:** High  
**File:** `nav.py:333-337`  
**Description:** Joystick hold fallback uses one separate adb swipe command per hold chunk, forever, in a worker thread.  
**Evidence:**
- `_hold_swipe_worker()` loops until stop and repeatedly executes `runtime.adb.swipe(state.x, state.y, state.x, state.y, state.chunk_ms)` (`nav.py:333-337`).
- `ADBController.swipe()` calls `self.shell(...)` (`bot/adb_controller.py:83-94`), and `shell()` calls `_run_adb(...)` (`bot/adb_controller.py:61-72`), so every hold chunk is a full adb subprocess.
- Default chunk is 250 ms (`nav.py:21`, `nav.py:117-118`), meaning ~4 adb processes/sec for one held direction, plus screenshot polling processes.
**Why this is a bug:** Continuous movement is implemented as process spam. Under load, movement timing becomes uneven, CPU usage rises, and ADB contention competes with screenshot capture. On slower hosts this can make navigation nondeterministic.  
**Suggested fix:** Prefer a true persistent pointer-down mechanism. If fallback is needed, use a long-running input stream/session rather than discrete swipes every chunk, or increase chunk duration and serialize with capture logic to reduce process churn.

---

## ADB-003 — High
**Severity:** High  
**File:** `bot/adb_controller.py:240-271`  
**Description:** `ADBController._run_adb` and `ADBController.__init__` are monkey-patched after class definition, changing core behavior outside the class body.  
**Evidence:**
- `_original_run_adb = ADBController._run_adb`, wrapper `_run_adb_checked(...)`, then `ADBController._run_adb = _run_adb_checked` (`bot/adb_controller.py:240-253`).
- `_orig_init = ADBController.__init__`, wrapper `_init_compat(...)`, then `ADBController.__init__ = _init_compat` (`bot/adb_controller.py:258-271`).
**Why this is a bug:** This hides the real constructor/runtime behavior from readers, type checkers, and subclasses. Any subclass overriding `_run_adb` or `__init__` can be silently bypassed or wrapped unexpectedly. It also makes import order significant and complicates debugging because stack traces and object signatures no longer match the class definition.  
**Suggested fix:** Move validation and compatibility logic into explicit methods inside the class. If backward compatibility is needed, provide a classmethod/factory or support both constructor signatures directly in `__init__` without post-definition reassignment.

---

## ADB-004 — High
**Severity:** High  
**File:** `nav.py:357-358`, `nav.py:388-419`  
**Description:** Hold/release state can become globally inconsistent across runtimes because `_HOLD_*` state is process-global while runtime/device selection is per project, and release errors are suppressed.  
**Evidence:**
- Global state: `_HOLD_THREAD`, `_HOLD_STOP_EVENT`, `_HOLD_ACTIVE_SIDE`, `_RUNTIME_CACHE` (`nav.py:43-47`).
- `release()` derives coordinates from the current `project_dir` runtime, then suppresses failures from `input motionevent UP ...` with `except Exception: pass` (`nav.py:357-358`).
- `approach_target()` repeatedly calls `release(project_dir=project_dir, ...)` in multiple branches and in `finally` (`nav.py:388-419`).
**Why this is a bug:** If the active hold was started for one runtime/device/project and `release()` runs against another, the UP event can be sent to the wrong device or with wrong coordinates. Because exceptions are swallowed and global flags are still cleared, the code can believe the joystick was released while the device remains logically held.  
**Suggested fix:** Bind hold state to a specific runtime/device serial, store the exact pressed coordinates/device used for DOWN, and only clear global state after a successful matching release or an explicit forced-reset path that is logged.

---

## ADB-005 — High
**Severity:** High  
**File:** `nav.py:489-507`  
**Description:** Battle-start fallback assumes the correct autobattle button is always at screen center.  
**Evidence:**
- In `start_battle()`, when the autobattle template is not found, code computes `tx, ty = width // 2, height // 2` and taps the center: `runtime.adb.tap(int(tx), int(ty))` (`nav.py:481-486`).
**Why this is a bug:** This is a hard-coded screen-position assumption unrelated to UI detection. On different resolutions, aspect ratios, camera states, or localized UI layouts, center tap may trigger movement, attack, modal dismissal, or another unintended action. This is especially dangerous because it runs exactly when detection has already failed.  
**Suggested fix:** Do not substitute a blind center tap for a missing template. Either fail safely, or use a config-driven normalized coordinate explicitly tied to the target UI and scaled using device resolution from `adb.connect()`.

---

## ADB-006 — Medium
**Severity:** Medium  
**File:** `bot/screenshot.py:22-38`  
**Description:** Screenshot retry logic catches every exception type and converts it into generic retry/failure handling, obscuring root cause and mixing decode failures with transport failures.  
**Evidence:**
- `capture()` wraps the whole operation in `except Exception as exc:` (`bot/screenshot.py:31-37`).
- It logs a warning and retries even for programmer errors or persistent configuration problems.
**Why this is a bug:** Broad exception handling makes operational diagnosis difficult. For ADB/device-state issues, callers lose detail about whether the problem was timeout, no device, corrupted bytes, OpenCV decode failure, or something unexpected. It also risks retrying non-retriable logic errors.  
**Suggested fix:** Catch and classify expected exceptions separately (`ADBTimeoutError`, `DeviceNotFoundError`, `ADBError`, decode errors), preserve context in the final exception, and avoid retrying obviously non-retriable failures.

---

## ADB-007 — Medium
**Severity:** Medium  
**File:** `bot/adb_controller.py:24-34`, `bot/adb_controller.py:260-266`, `main.py:147`, `main.py:157`  
**Description:** `serial=auto` handling exists only in the monkey-patched compatibility constructor and not in the main constructor/API.  
**Evidence:**
- Base `__init__` only strips empty serials; any non-empty string, including `"auto"`, is kept literally (`bot/adb_controller.py:24-34`).
- Compatibility patch converts `"auto"` to `None` (`bot/adb_controller.py:260-266`).
- `main.py` displays serial from config directly and constructs `ADBController(config, logger)` through the patched path (`main.py:147`, `main.py:157`).
**Why this is a bug:** Behavior depends on how the controller is instantiated. Code using the documented-looking raw constructor `ADBController(adb_path, serial="auto")` will target literal serial `auto` and fail, while config-based construction works. This inconsistency is a device-state bug waiting to surface in tests, tools, or future refactors.  
**Suggested fix:** Normalize `"auto"` in the real constructor or in a dedicated serial-normalization helper used by all entry points.

---

## ADB-008 — Medium
**Severity:** Medium  
**File:** `bot/adb_controller.py:61-72`, `bot/adb_controller.py:196-218`  
**Description:** `adb shell` commands are built as a single shell string argument, which is fragile for quoting/escaping and can break with special characters.  
**Evidence:**
- `shell(command)` passes `["shell", command]` to `_run_adb(...)` (`bot/adb_controller.py:61-72`).
- Callers interpolate strings like `input swipe ...`, `input tap ...`, `getprop ...` into that single command string.
**Why this is a bug:** While current commands are simple, this API encourages unsafe composition and makes future commands containing spaces, quotes, redirects, or user-derived values error-prone. It also differs from the safer argv-style construction used for the outer adb process.  
**Suggested fix:** Provide `shell_args(*args)` or accept a sequence for shell commands, joining/escaping only in one well-defined place.

---

## ADB-009 — Medium
**Severity:** Medium  
**File:** `nav.py:346-370`  
**Description:** `hold()`/`release()` call `_build_runtime()` every time, which can reconnect/create runtime on release paths and couples cleanup to runtime initialization.  
**Evidence:**
- `hold()` starts with `runtime = _build_runtime(project_dir, log=log)` (`nav.py:346`).
- `release()` also starts with `runtime = _build_runtime(project_dir, log=log)` (`nav.py:370`).
- `_build_runtime()` may `adb.connect()` when cache is cold (`nav.py:166-189`).
**Why this is a bug:** Cleanup code should not depend on successfully creating a new runtime. If release is called during startup failure or after partial teardown, attempting to build/connect a runtime can raise a fresh exception or target a different cached runtime than the one that owns the hold.  
**Suggested fix:** Store the runtime/device used by the active hold and release against that object directly. Make release a best-effort cleanup that does not require a fresh connect path.

---

## ADB-010 — Low
**Severity:** Low  
**File:** `nav.py:333-337`  
**Description:** Swipe-loop worker silently swallows all exceptions and only sleeps, leaving no signal that movement input is failing.  
**Evidence:**
- `_hold_swipe_worker()` catches `Exception` and only `time.sleep(...)` (`nav.py:335-337`).
**Why this is a bug:** If ADB starts failing mid-hold, navigation logic keeps believing it is moving, but the worker has degraded into a hidden retry loop with no logging or state transition.  
**Suggested fix:** Log repeated failures, set an error flag visible to the main loop, and terminate/force release after a threshold.
