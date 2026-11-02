# Concurrency and resource audit

Source ref audited: `main`
Log branch: `bot-run-logs`
Scope: Python files listed in `logs/REPO_MAP.md`, prioritising modules that use `threading`, `subprocess`, `queue`, Tkinter worker threads, and persistent file/state writes.

## Summary
- Critical: 2
- High: 5
- Medium: 3
- Low: 1

---

## CON-001
- Severity: Critical
- File: `gui_farm.py:129-136, 172-193`
- Description: `ControlTab.do_start()` starts navigation in a daemon thread and, after `nav.go_to_dungeon()`, launches `main.py --profile ...` as a subprocess, but stop/full-stop only sends `terminate()` to the child without escalation, wait timeout, or process-group cleanup. If the child ignores SIGTERM or is blocked in ADB/OpenCV, the GUI can leave a live orphan farm process behind after the UI reports stopped or exits.
- Evidence:
  - `gui_farm.py:172-193` creates `self.nav_thread = threading.Thread(target=worker, daemon=True)` and inside worker calls `nav.go_to_dungeon(...)` followed by `code, last = self._run_main_process()`.
  - `gui_farm.py:195-220` launches `self.main_process = subprocess.Popen(...)` and only reacts to stop by `self.main_process.terminate()` inside the stdout-reading loop.
  - `gui_farm.py:239-245` `_terminate_main_process()` only calls `terminate()` and never `wait()`, `kill()`, or `communicate()` cleanup.
  - `gui_farm.py:457-485` `do_full_stop()` again only calls `_terminate_main_process()`, then joins nav thread for 5s and proceeds to UI cleanup.
- Suggested fix: Start the child in its own process group/session; on stop, perform `terminate()`, bounded `wait(timeout=...)`, then `kill()`/process-group kill on timeout; always reap the child in all exit paths. Consider a dedicated supervisor thread and store explicit process state so GUI shutdown cannot outpace child cleanup.

## CON-002
- Severity: Critical
- File: `gui_farm.py:195-217`
- Description: The GUI can deadlock stop handling on subprocess output because `_run_main_process()` iterates `for line in self.main_process.stdout:`. If the child is alive but not producing a newline-terminated line, the loop blocks in `readline()`. `do_stop()` only calls `terminate()` and returns; the worker thread may remain stuck until the child flushes/exits, so state transitions, release, and shutdown become non-deterministic.
- Evidence:
  - `gui_farm.py:204-214` reads child output with `for line in self.main_process.stdout:`.
  - Stop path only sets `self.stop_event` and calls `_terminate_main_process()` (`gui_farm.py:234-238`), which does not close the pipe or wait with timeout.
  - Full stop waits only `self.nav_thread.join(timeout=5.0)` (`gui_farm.py:461-463`), so a blocked stdout reader can outlive the join and continue accessing shared state after GUI teardown.
- Suggested fix: Replace line-iterator blocking reads with a pump thread or `communicate(timeout=...)`; on stop close/abandon stdout, wait with timeout, then force-kill. Avoid having the navigation worker both supervise and block on child stdio.

## CON-003
- Severity: High
- File: `gui_farm.py:118-136`
- Description: The global hotkey listener thread calls Tk methods from a non-main thread. Tkinter is not thread-safe; invoking `self.after(...)` from `pynput`'s listener thread can intermittently crash Tcl, hang the app, or enqueue UI actions during teardown.
- Evidence:
  - `gui_farm.py:118-128` defines `on_press()` inside the listener worker.
  - That callback directly executes `self.after(0, self.do_start)` / `self.after(0, self.do_stop)` / etc. from the listener thread.
  - The listener itself runs in `self.listener_thread = threading.Thread(target=worker, daemon=True)` (`gui_farm.py:134-135`).
- Suggested fix: Never touch Tk objects from foreign threads. Use a `queue.Queue` for hotkey events and poll it from the Tk main loop via `root.after(...)`, or use only root-bound key events on the main thread.

## CON-004
- Severity: High
- File: `gui_farm.py:248-274, 457-485`
- Description: `do_stop()` triggers asynchronous snapshot/release work while `do_full_stop()` can run concurrently and call `nav.release()` again, destroy windows, and change shared flags without synchronization. This races ADB hold/release state, background screenshotting, and modal dialogs.
- Evidence:
  - `gui_farm.py:234-238` calls `_release_and_snapshot_async()` after stop.
  - `_release_and_snapshot_async()` spawns another daemon thread that may `join()` nav thread, call `nav.release(...)`, take a screenshot, search known objects, and schedule `_after_stop_snapshot(...)` (`gui_farm.py:248-274`).
  - `do_full_stop()` concurrently sets `self.stop_event`, calls `_terminate_main_process()`, may `join()` nav thread, calls `nav.release(...)`, and destroys `Toplevel` windows (`gui_farm.py:457-485`).
  - `_stop_snapshot_busy` is a plain boolean, not protected by a lock; there is no mutual exclusion around shutdown phases.
- Suggested fix: Centralise stop/full-stop into one serialized shutdown state machine protected by a lock. Make snapshot optional after the process is fully reaped, and skip snapshot entirely during full application close.

## CON-005
- Severity: High
- File: `nav.py:156-176, 386-440`
- Description: The navigation runtime cache stores live `ADBController`, `ScreenshotProvider`, and `VisionEngine` instances in a global `_RUNTIME_CACHE` without invalidation or close semantics. Subsequent GUI runs, stop/start cycles, and threads reuse the same mutable runtime indefinitely, including after errors or device reconnects.
- Evidence:
  - `nav.py:156-176` `_build_runtime()` returns cached runtime from `_RUNTIME_CACHE[key]` if present; otherwise creates and stores live ADB/screenshot/vision objects.
  - No function removes entries from `_RUNTIME_CACHE` or reconnects/rebuilds on failure.
  - `release()` only handles hold state; it does not close cached runtime objects or clear `_RUNTIME_CACHE` (`nav.py:499-519`).
- Suggested fix: Add explicit runtime lifecycle management: invalidate cache on stop/full-stop and on ADB failures, protect cache with a lock, and avoid sharing a mutable live runtime across unrelated threads unless it is designed to be thread-safe.

## CON-006
- Severity: High
- File: `nav.py:442-497`
- Description: Hold/release uses module-global `_HOLD_THREAD`, `_HOLD_STOP_EVENT`, `_HOLD_ACTIVE_SIDE`, and `_MOTIONEVENT_SUPPORTED` shared across all callers and windows. This makes movement state cross-cutting and non-reentrant; concurrent manual actions, stop snapshot, and navigation can release each other’s holds unexpectedly.
- Evidence:
  - Globals declared at `nav.py:42-46`.
  - `hold()` mutates all globals inside `_HOLD_LOCK` and may call `release()` as part of activation (`nav.py:442-477`).
  - `release()` also mutates the same globals and joins `_HOLD_THREAD` (`nav.py:480-519`).
  - Higher-level APIs (`approach_target`, `click_loot`, `attack_mob`, manual GUI actions) all route through this single shared hold state.
- Suggested fix: Move hold state into a per-runtime or per-session controller object. Pass it explicitly to callers instead of using module globals; keep stop_event and lifecycle local to the active navigation session.

## CON-007
- Severity: High
- File: `bot/knowledge.py:275-306, 349-380`
- Description: Knowledge writes PNG templates non-atomically and without any inter-process locking, while JSON metadata is updated separately. A crash or concurrent editor/bot instance can leave PNG/JSON out of sync, and `_next_template_id()` can race between writers to the same profile.
- Evidence:
  - `bot/knowledge.py:275-306` `add_template()` computes `template_id = self._next_template_id()`, writes `cv2.imwrite(str(png_path), cropped)`, then appends to features via `save_features(existing)`.
  - `_next_template_id()` scans current IDs and increments (`bot/knowledge.py:571-578`), which is race-prone across processes/instances.
  - There is only an in-process `RLock`; no file lock protects the profile against another process or another GUI/main.py instance.
- Suggested fix: Add profile-scoped file locking for knowledge updates, write PNGs via temp-file+replace where feasible, and generate collision-resistant IDs (UUID/timestamp+pid) rather than scanning mutable state.

## CON-008
- Severity: Medium
- File: `gui_base.py:219-247`
- Description: `capture_region()` starts a background screenshot thread that constructs ADB/ScreenshotProvider objects and captures a frame, but the thread is daemonized and never joined/cancelled on window close. Closing the app while capture is in progress can leave background device operations running against a destroyed UI.
- Evidence:
  - `gui_base.py:231-238` worker performs `_make_provider().capture()` in a background thread.
  - `gui_base.py:239` starts it as `threading.Thread(target=worker, daemon=True).start()`.
  - The UI only polls `state['done']`; no cancellation token or join is used when owner windows are destroyed.
- Suggested fix: Track capture worker threads, add cancellation and shutdown coordination, and avoid daemon fire-and-forget threads for device I/O during GUI shutdown.

## CON-009
- Severity: Medium
- File: `bot/screenshot.py:24-39`, `bot/vision.py:123-129`, `bot/floor.py:224-239`
- Description: Several long waits are not interruptible by any stop event. GUI stop can kill `main.py`, but inside bot logic sleeps and stability waits do not observe external cancellation, so graceful shutdown latency depends on timeout lengths.
- Evidence:
  - `bot/screenshot.py:35-38` retries with `time.sleep(...)` between capture attempts.
  - `bot/vision.py:94-129` `wait_until_stable()` loops until timeout and sleeps `poll / 1000.0` without a cancellation hook.
  - `bot/floor.py:224-239` `_wait_battle_end()` loops until `_battle_finish_timeout_s`, sleeping on each iteration, but only checks teacher pause, not a bot stop event.
- Suggested fix: Thread a shared cancellation/stop token from top-level run orchestration through capture/vision/floor loops so waits can abort promptly during shutdown.

## CON-010
- Severity: Medium
- File: `gui_farm.py:283-337`
- Description: Manual action workers are spawned as detached daemon threads and are not tracked. They can continue using cached nav runtime and logging after the UI is stopped or closed.
- Evidence:
  - `gui_farm.py:315-337` `_manual_execute_action()` spawns `threading.Thread(target=worker, daemon=True).start()`.
  - The worker calls `nav.click_loot/attack_mob/handle_event(...)`, all of which may perform ADB actions and blocking waits.
  - No handle is stored, no join occurs on stop/close, and no dedicated stop_event is created for the worker beyond the shared control tab event.
- Suggested fix: Track these workers, serialize manual actions with main navigation, and join/cancel them during stop/full-stop before destroying UI state.

## CON-011
- Severity: Low
- File: `gui.py:122-129`
- Description: `on_profile_selected()` swallows all exceptions from `memory_tab.reload()`, hiding cache/refresh failures. This is not itself a race, but it obscures stale-state bugs and invalidation failures the GUI should surface.
- Evidence:
  - `gui.py:125-129`:
    - `try: self.memory_tab.reload()`
    - `except Exception: pass`
- Suggested fix: Log the exception to the GUI console/logger at minimum, and surface reload failures so stale memory/profile state is not silently retained.
