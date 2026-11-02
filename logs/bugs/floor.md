# Floor exploration / state-machine audit findings

## FLR-001 — Critical — bot/floor.py:67-104, bot/floor_state.py:57-92
**Description:** `FloorExplorer.run()` never exits after the floor is exhausted unless `NoAvailableDoorError` is raised *and* `next_floor` is detected. In the common completion path it returns only from the `NoAvailableDoorError` branch, but there is no explicit end-of-floor state transition check after every door completion, so the outer `while True` can continue re-entering the loop on stale state if the target selection keeps finding doors due to state not advancing the current node/phase. This is a state-machine liveness bug: completion depends on side effects outside the loop contract.
**Evidence:** `run()` begins with `while True:` at `bot/floor.py:67` and only returns on `teach_only`, `NoAvailableDoorError + next_floor`, or downstream exceptions. After entering a door it calls `_handle_entered_door(...)` and then the loop simply iterates again without an explicit loop exit or floor-complete guard. The state store does not update `phase` or `current_node_id` in `mark_in_progress_and_flush()` (`bot/floor_state.py:231-248`) or `mark_door_status()`, so completion depends entirely on external transitions.
**Suggested fix:** Add an explicit terminal floor-complete condition in `run()` based on state/phase, and update state transitions to set a terminal phase or current node when the floor is finished. Return a `FloorResult` from every terminal branch.

## FLR-002 — High — bot/floor_state.py:57-92
**Description:** `save()` performs two `os.replace()` calls: temp file -> `tmp_path`, then `tmp_path` -> final path. This creates an unnecessary double-write / double-replace sequence that increases the chance of partial recovery artifacts and makes concurrent readers observe transient states. It also leaves an extra rename target (`tmp_path`) that is then manually cleaned up.
**Evidence:** `save()` writes to `named_tmp_path`, then `os.replace(named_tmp_path, tmp_path)`, then `os.replace(tmp_path, self._path)` at `bot/floor_state.py:77-83`. The function itself documents atomic save, but the two-step replace is not atomic from the perspective of a single durable write.
**Suggested fix:** Replace the double-rename with a single atomic move from the temp file directly to `self._path`, followed by directory fsync.

## FLR-003 — High — bot/floor.py:118-149, bot/floor.py:181-197
**Description:** Multiple paths can write the same floor state more than once per logical transition. `run()` saves state before target selection, then `_handle_entered_door()` may save again on the same door transition, and `_exit_room_with_status()` also saves after updating status. This makes repeated disk writes likely on a single step and increases contention and crash windows.
**Evidence:** `run()` calls `self.floor_state.save(state)` immediately after `upsert_visible_doors()` / `mark_open_big_icons()` at `bot/floor.py:85-89`. Then `mark_in_progress_and_flush()` already saves inside `bot/floor_state.py:231-248`, and later `_handle_entered_door()` / `_exit_room_with_status()` each call `self.floor_state.save(state)` after status changes (`bot/floor.py:118-149`, `bot/floor.py:181-197`).
**Suggested fix:** Centralize persistence at one point per transition; avoid saving both inside state mutators and again in the caller unless the save is intentionally idempotent and documented.

## FLR-004 — High — bot/floor.py:84-85, 204-225
**Description:** Templates are reloaded on every outer loop iteration and again inside wait helpers, causing repeated full reloads even when nothing changed. This can degrade performance and destabilize matching if template sources are being mutated by teacher actions mid-step.
**Evidence:** `run()` calls `templates = self._reload_templates()` before the loop and again at the start of every `while True` iteration (`bot/floor.py:67-73`, `84-85`). `_stable_capture_or_teach()` also receives templates but `_wait_after_motion()` always throws them away and calls `_reload_templates()` again (`bot/floor.py:204-225`). `_handle_entered_door()` and `_clear_room()` reload templates several more times in the same flow.
**Suggested fix:** Cache template snapshots per step or per state transition and only refresh after a confirmed label/save or explicit invalidation event.

## FLR-005 — Medium — bot/floor.py:93-104, bot/teacher_ui.py:79-130
**Description:** The exploration loop can stop at a “ready / pause requested” teaching state without a dedicated confirmation step. When `_stable_capture_or_teach()` decides the screen is unknown, it calls `_pause_and_label(frame)` and then continues; in `TeacherUI.show_and_label()`, pressing Space returns immediately after saving and clearing pause, without any explicit “confirm start / resume” gate. This can strand the scenario in a ready-to-continue state or resume too early after partial labeling.
**Evidence:** `run()` simply continues after `_pause_and_label(frame)` when `screen_name is None` or unstable (`bot/floor.py:93-104`, `204-225`). In `TeacherUI.show_and_label()`, Space calls `save_fn()` then `self._pause_event.clear()` and returns (`bot/teacher_ui.py:108-130`), while `pause_requested()` only sets the pause flag on Space (`bot/teacher_ui.py:63-69`). There is no separate confirmation action distinguishing “labeling done” from “resume exploration”.
**Suggested fix:** Add an explicit confirm/resume step after annotation, e.g. a dedicated key or dialog that returns only after the user confirms the bot may continue.

## FLR-006 — Medium — bot/teacher_ui.py:79-130, bot/teacher_ui.py:142-154
**Description:** `TeacherUI.show_and_label()` and `teach_only()` both rely on unbounded `while True` loops with no step limit or timeout. If the window remains open and the user never presses a terminating key, the bot can remain blocked indefinitely.
**Evidence:** `show_and_label()` has `while True:` at `bot/teacher_ui.py:79-130`; `teach_only()` has another `while True:` at `bot/teacher_ui.py:142-154`. Neither loop contains an iteration cap or elapsed-time cutoff.
**Suggested fix:** Add a configurable timeout/step budget and fail closed with a clear error when the UI is idle too long.

## FLR-007 — Medium — bot/floor_state.py:260-286
**Description:** The state machine allows `passage -> failed` directly, which may be valid for error handling but is not treated as a terminal progression check anywhere else. Because `failed` is terminal and non-selectable, a door can become permanently dead without a recovery path, and the loop has no structured fallback other than skipping it.
**Evidence:** `_ALLOWED_STATUSES` includes `failed` and `_TERMINAL_STATUSES` marks it terminal (`bot/floor_state.py:24-33`). `mark_door_status()` accepts any allowed transition through `_ensure_transition()`, and `increment_attempts()` forcibly marks `failed` after max attempts (`bot/floor_state.py:260-286`). `choose_next_target()` excludes failed doors, so a door can silently disappear from consideration.
**Suggested fix:** Make failure transitions explicit and audited, with a reasoned retry/recovery policy or a separate quarantine state if the door can be retried later.

## FLR-008 — Low — bot/floor.py:210-225, bot/teacher_ui.py:63-69
**Description:** Pause handling is edge-triggered by Space in `TeacherUI.pause_requested()`, but the same key is also used to clear and resume in `show_and_label()`. This conflates “request pause” with “resume after annotation” and can cause surprising control flow, especially when `_handle_pause_on_step()` is called before capture.
**Evidence:** `pause_requested()` sets `_pause_event` on Space and returns True (`bot/teacher_ui.py:63-69`). `show_and_label()` also treats Space as “save and continue” (`bot/teacher_ui.py:108-130`). `FloorExplorer` invokes `_handle_pause_on_step()` at the top of each iteration and inside stable waits (`bot/floor.py:210-225`), so the same key has two meanings in two different call sites.
**Suggested fix:** Split pause and resume controls into distinct inputs or explicit UI states so the same key cannot mean both “pause” and “continue”.

## Severity counts
- Critical: 1
- High: 3
- Medium: 3
- Low: 1

## Most serious issue
FLR-001: the main floor loop has no explicit terminal exit/phase transition, so the explorer can keep cycling on stale state instead of completing the floor deterministically.
