# Navigation and Vision Audit

Source branch audited: `main`
Log branch: `bot-run-logs`
Files reviewed fully in raw-content chunks: `nav.py`, `bot/vision.py`, `bot/explorer.py`, `bot/screenshot.py`, `bot/knowledge.py`, `bot/profiles.py`, `bot/floor.py`, `bot/floor_state.py`, `bot/adb_controller.py`, `bot/safety.py`

## Findings

### NAV-001
- **Severity:** critical
- **File:** `bot/floor.py:286-291`
- **Description:** The floor loop can terminate a battle early purely because the screen becomes stable and `auto_battle` is no longer detected, even if the bot is still on the same `room_screen` and combat is not actually finished.
- **Evidence:** `_wait_battle_end()` returns when `room_name in {"room_screen", "event_screen", "passage_screen"} and not auto_matches`. In `_clear_room()` the bot taps `auto_battle`, then `_wait_after_motion("auto_battle")` and later `_wait_battle_end()`. A room can remain classified as `room_screen` throughout combat, and `auto_battle` may disappear after being enabled, so the condition is satisfied before any exit/loot/end-of-room signal exists.
- **Suggested fix:** Wait for a positive end-of-battle signal instead of the absence of `auto_battle`. For example, require `exit`, `loot`, `passage_screen`, `event_screen`, or another explicit post-combat template, and keep a hard timeout as fallback.

### NAV-002
- **Severity:** high
- **File:** `nav.py:100-107`, `nav.py:127-134`, `nav.py:250-273`, `nav.py:305-322`
- **Description:** `nav.json`, `actions.json`, and template PNGs are re-read from disk on every helper call instead of being cached, creating avoidable latency in tight navigation and matching loops.
- **Evidence:** `_settings()` calls `_read_nav()` every time; `_load_actions_list()` calls `_read_actions()` every time; `find_known_objects()` and `match_known()` iterate all actions and call `_load_image(png)` inside the loop; `find_template()` loads the template file with `_load_image(template_path)` every call. `approach_target()` repeatedly calls `find_template()` inside its movement loop.
- **Suggested fix:** Cache parsed JSON and decoded template images by resolved path and invalidate only on file change or explicit reload.

### NAV-003
- **Severity:** high
- **File:** `bot/explorer.py:343-360`
- **Description:** The stall detector uses Python's process-randomized `hash()` on raw frame bytes, so the same screen can hash differently across interpreter runs and collision behavior is uncontrolled.
- **Evidence:** `_frame_signature()` returns `str(hash(np.ascontiguousarray(small).tobytes()))`. Python’s built-in hash is intentionally salted per process and is not stable across runs.
- **Suggested fix:** Use a deterministic digest such as `hashlib.sha1`/`md5` on the resized grayscale bytes, or compare frames directly with a stable metric.

### NAV-004
- **Severity:** high
- **File:** `nav.py:120-125`
- **Description:** `take_screenshot()` does not handle a failed capture before saving, so screenshot acquisition errors propagate without context and `save()` may be called with an invalid frame in future regressions.
- **Evidence:** `frame = runtime.screenshot.capture()` is used immediately by `runtime.screenshot.save(frame, str(path))` with no validation or fallback.
- **Suggested fix:** Guard capture failures, validate `frame is not None`, and raise a navigation-specific error message before attempting save.

### NAV-005
- **Severity:** high
- **File:** `bot/vision.py:381-401`
- **Description:** ROI normalization clamps coordinates but does not reorder inverted `(x1, y1, x2, y2)` inputs, so reversed coordinates become invalid instead of being normalized.
- **Evidence:** `_normalize_roi()` casts and clamps `x1, y1, x2, y2`, then immediately raises when `x2 <= x1 or y2 <= y1`. In contrast, `bot/knowledge.py:_normalize_rect()` swaps endpoints when `x2 < x1` or `y2 < y1`.
- **Suggested fix:** Normalize ROI coordinate order before or after clamping by swapping endpoints when necessary, or reject earlier with a clearer caller contract and enforce it consistently across modules.

### NAV-006
- **Severity:** high
- **File:** `bot/explorer.py:260-276`, `bot/floor.py:304-308`
- **Description:** Pause handling captures a raw screenshot instead of a stable frame, so labeling can be opened on a transient animation frame exactly when the comments require checks during waits.
- **Evidence:** `DungeonExplorer._pause_checkpoint()` calls `_capture_stable_frame_for_pause()`, but `FloorExplorer._handle_pause_on_step()` directly does `frame = self.screenshot.capture()` and `_pause_and_label(frame)`. `_wait_after_motion()` also delegates to `_stable_capture_or_teach()`, but asynchronous pause requests between steps still use unstable raw captures.
- **Suggested fix:** Reuse the stable-frame path for pause-triggered labeling in floor logic, or at least attempt `wait_until_stable()` before showing the teacher UI.

### NAV-007
- **Severity:** medium
- **File:** `nav.py:336-396`
- **Description:** `approach_target()` performs two waits per control iteration: a full `wait_until_stable()` call and then an additional fixed sleep, making approach responsiveness slow and compounding timeout risk.
- **Evidence:** Each loop calls `_stable_wait(runtime, timeout_s=DEFAULT_STABLE_TIMEOUT_S, ...)`, which itself polls/sleeps until stable or timeout, and then after movement decisions the code still does `time.sleep(max(0.05, frame_interval_ms / 1000.0))` or `time.sleep(max(0.05, frame_interval_ms / 2000.0))`.
- **Suggested fix:** Replace the stable wait with a lighter periodic capture during active movement, or skip the extra fixed sleep when `wait_until_stable()` already consumed the polling delay budget.

### NAV-008
- **Severity:** medium
- **File:** `nav.py:284-289`
- **Description:** The swipe-hold fallback can spin indefinitely without any stop budget or backoff growth if ADB swipes keep succeeding but the game ignores them.
- **Evidence:** `_hold_swipe_worker()` loops `while not stop_event.is_set(): runtime.adb.swipe(...)`; on success there is no sleep except the swipe duration itself, and there is no max runtime independent of the caller.
- **Suggested fix:** Add a worker-side max duration or periodic health/stop callback, and include a small pacing sleep or state check even on success.

### NAV-009
- **Severity:** medium
- **File:** `nav.py:420-433`
- **Description:** After approaching a target, the click helpers capture a new frame and click based on a fresh match without any timeout or stability check, so they can lose the target during movement transition and fail spuriously.
- **Evidence:** `click_loot()`, `attack_mob()`, and `handle_event()` do `frame = runtime.screenshot.capture()` immediately after `approach_target()` and then call `find_template(..., frame=frame)` once.
- **Suggested fix:** Wait briefly for a stable post-approach frame or retry matching for a short bounded timeout before giving up.

### NAV-010
- **Severity:** medium
- **File:** `bot/vision.py:287-315`
- **Description:** `wait_for()` has no stop/pause hook, so callers cannot interrupt long polling except by waiting for the timeout.
- **Evidence:** The loop only checks for a match or `time.monotonic() - started >= timeout_s`, then sleeps `poll_ms / 1000.0`; there is no injected stop predicate analogous to `approach_target(..., stop_event=...)`.
- **Suggested fix:** Add an optional `stop_fn`/`stop_event` parameter and check it on every iteration before sleeping.

### NAV-011
- **Severity:** medium
- **File:** `bot/vision.py:72-132`
- **Description:** `wait_until_stable()` has no stop/pause hook either, which forces higher-level callers to wait until the current stable timeout completes before reacting unless they wrap multiple retries outside the function.
- **Evidence:** The loop continuously captures, compares, and sleeps until `stable_count >= target_stable_frames` or timeout, with no external cancellation check.
- **Suggested fix:** Accept an optional stop predicate/event and abort promptly when requested.

### NAV-012
- **Severity:** medium
- **File:** `bot/floor.py:228-246`
- **Description:** Unstable-screen recovery returns the last unstable frame after labeling instead of retrying for a newly stabilized frame, allowing downstream safety/classification logic to continue on a known unstable image.
- **Evidence:** `_stable_capture_or_teach()` retries `wait_until_stable()` up to `_max_stable_retries + 1`; if still unstable, it notifies, calls `_pause_and_label(last_frame)`, and then immediately `return last_frame`.
- **Suggested fix:** After labeling, restart the stable-capture loop and only return once a fresh stable frame is obtained or an explicit abort condition is hit.

### NAV-013
- **Severity:** medium
- **File:** `bot/explorer.py:229-258`
- **Description:** Unknown stable screens trigger teacher UI and then the function returns without revalidating the newly labeled knowledge against the current frame.
- **Evidence:** `_ensure_safe_known_frame()` increments `_unknown_stable_count`, notifies, and calls `self.teacher_ui.show_and_label(frame, self.knowledge)`, but does not rerun `classify_screen()`/`has_any()` before returning to the caller.
- **Suggested fix:** Re-run screen classification/presence checks after labeling and only proceed if the frame becomes known, otherwise force another capture or raise a bounded error.

### NAV-014
- **Severity:** medium
- **File:** `bot/floor.py:173-190`
- **Description:** Event rooms are always marked `skipped` without checking for any event-choice vs event-skip logic, making one state effectively unreachable despite navigation code elsewhere distinguishing event actions.
- **Evidence:** `_handle_entered_door()` returns `_exit_room_with_status(..., final_status="skipped")` whenever `screen_name == "event_screen"`. Meanwhile `nav.py` distinguishes `event_choice` and `event_skip` in `ACTION_KIND_LABELS` and `run_event(action=...)`.
- **Suggested fix:** Add explicit event interaction logic and map event outcomes to separate statuses/actions before exiting the room.

### NAV-015
- **Severity:** low
- **File:** `bot/vision.py:51-52`, `bot/vision.py:344-351`
- **Description:** Default thresholds are inconsistent between navigation and vision subsystems, which can produce surprising match behavior depending on entry point.
- **Evidence:** `VisionEngine` defaults `_default_threshold` to `vision.default_match_threshold` or `0.88`, while `nav.py` uses `DEFAULT_MATCH_THRESHOLD = 0.78` and `_settings()` returns that default when `nav.json` lacks a value.
- **Suggested fix:** Centralize the default threshold in one config location and have both modules read the same fallback.

### NAV-016
- **Severity:** low
- **File:** `nav.py:442-459`
- **Description:** `start_battle()` relies on fixed sleeps instead of bounded state waits, slowing happy-path navigation and making timing fragile across devices.
- **Evidence:** After clicking `enter_dungeon`, the code does `time.sleep(1.0)`, and after the fallback autobattle tap it does `time.sleep(0.5)` rather than waiting for a screen or button state change.
- **Suggested fix:** Replace fixed sleeps with `vision.wait_for()`/stable-screen waits tied to explicit templates and short timeouts.

## Severity counts
- critical: 1
- high: 5
- medium: 8
- low: 2
