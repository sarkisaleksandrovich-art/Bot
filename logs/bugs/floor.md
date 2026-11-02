# Floor exploration audit

## FLR-001 — High
**File:** `bot/explorer.py:127-130`, `bot/teacher_ui.py:79-81`, `bot/teacher_ui.py:123-129`

**Bug:** The scenario can stop at the "ready"/pause state and never start farming because a latched pause flag is reused as a level-trigger instead of an edge-trigger. Once Space was pressed once, every later checkpoint sees pause as still requested until `show_and_label()` returns via Space or `q`.

**Evidence:**
> `if not self.teacher_ui.pause_requested():`
> `    return`
> `frame = self._capture_stable_frame_for_pause(note=note)`
> `self._last_frame = frame`
> `self.teacher_ui.notify(note or "Пауза")`
> `self.teacher_ui.show_and_label(frame, self.knowledge)`

> `if key == 32:`
> `    self._pause_event.set()`
> `    return True`
> `return self._pause_event.is_set()`

> `if key == 32:`
> `    save_fn = getattr(knowledge, "save", None)`
> `    if callable(save_fn):`
> `        save_fn()`
> `    self._pause_event.clear()`
> `    self.notify("Продолжение работы")`
> `    return list(added)`

At every checkpoint, `pause_requested()` returns `self._pause_event.is_set()` even when no new keypress happened, so the explorer keeps re-entering pause whenever that flag remained set.

**Fix:** Make pause handling edge-triggered: `pause_requested()` should report only a fresh Space press, or add a separate `consume_pause_request()` that clears the flag before returning control to the state machine. Also avoid using `cv2.waitKey()` as both global input poll and latch source.

## FLR-002 — High
**File:** `bot/floor.py:79-96`, `bot/floor_state.py:239-267`, `bot/floor_state.py:646-663`

**Bug:** Passage doors are terminal for the floor loop. After entering a passage, the door is marked `passage` and control returns to the top-level map loop without updating node identity or consuming that passage. `choose_next_target()` is allowed to select `passage` again forever once no `unvisited` doors remain.

**Evidence:**
> `if screen_name == "passage_screen":`
> `    state = self.floor_state.mark_door_status(state, door_id, "passage", note="passage_screen")`
> `    self.floor_state.save(state)`
> `    return state`

> `if status == "unvisited":`
> `    candidates_unvisited.append((distance, door))`
> `elif status == "passage":`
> `    candidates_passage.append((distance, door))`
...
> `pool = candidates_unvisited or candidates_passage`

> `if current_status == "passage" and new_status == "in_progress":`
> `    return`
...
> `if current_status == "passage" and new_status == "unvisited":`
> `    return`

The state machine never calls `set_current_node()` after moving through a passage, so it stays in the same node and can repeatedly pick the same passage door. The transition table explicitly allows re-entering passage doors (`passage -> in_progress`).

**Fix:** After a passage is taken, create/switch to the target node and associate the traversed door with that node, or mark the passage as consumed for the current node. If backtracking is intended, model graph traversal explicitly instead of leaving `current_node_id` unchanged.

## FLR-003 — Medium
**File:** `bot/floor.py:141-151`

**Bug:** Battle-end detection can declare battle finished only because the auto-battle button is not detected on a stable frame, even if combat is still ongoing.

**Evidence:**
> `auto_matches = self.vision.find_category(stable_frame, templates, "auto_battle")`
> `room_name = self._classify_room_or_passage(stable_frame, templates)`
> `if room_name in {"room_screen", "event_screen", "passage_screen"} and not auto_matches:`
> `    return stable_frame`
> `if time.monotonic() - started >= self._battle_finish_timeout_s:`
> `    return stable_frame`

This treats `room_screen` + missing `auto_battle` as sufficient proof that combat ended. If the auto-battle control disappears, is occluded, or was never labeled, the function exits early and the bot proceeds as if the room were clear.

**Fix:** Require a positive post-battle signal, such as detecting `exit`, `loot`, or a known room/passage UI that is specifically outside battle; or require the absence of battle-only markers across several consecutive stable frames before concluding the fight ended.

## FLR-004 — Medium
**File:** `bot/floor.py:153-160`, `bot/floor.py:161-169`, `bot/floor_state.py:348-351`

**Bug:** Open-room markers are sticky forever inside the saved floor state. Doors that once matched `room_open_big_icon` are excluded from selection on every later loop because the code only adds the note and never removes it when the icon disappears.

**Evidence:**
> `state = self.floor_state.upsert_visible_doors(state, [match.rect for match in map_info["door_matches"]])`
> `state = self._mark_open_big_icons(state, map_info["open_room_matches"])`
> `self.floor_state.save(state)`

> `for match in matches:`
> `    door = self._closest_door_by_rect(state, match.rect)`
> `    if door is not None:`
> `        try:`
> `            state = self.floor_state.mark_open_room_icon(state, door.door_id, present=True)`

> `if self._is_marked_open_room(door):`
> `    continue`

There is no pass that clears `present=False` for doors no longer covered by current `open_room_matches`, so one transient match permanently blocks that door from `choose_next_target()`.

**Fix:** Recompute open-room flags from the current frame each loop: first clear the token for all visible/current-node doors, then set it only for matched doors. Alternatively store this as ephemeral frame data instead of persistent notes.

## FLR-005 — Medium
**File:** `bot/floor.py:84-91`, `bot/floor_state.py:173-181`, `bot/floor_state.py:337-344`

**Bug:** The code performs an immediate save for `in_progress`, then often performs another save for the final state moments later, creating avoidable double-save churn on every successful door entry.

**Evidence:**
> `state = self.floor_state.mark_in_progress_and_flush(state, target.door_id, note="entered")`
> `state = self._handle_entered_door(state, target.door_id, entered_frame)`

> `state = self.mark_door_status(state, door_id, "in_progress", note=note)`
> `self.save(state)`
> `return state`

> `state = self.floor_state.mark_door_status(state, door_id, final_status, note=f"{final_status}_after_exit")`
> `self.floor_state.save(state)`
> `return state`

The first flush is justified for crash recovery, but the implementation then always writes again for `passage`, `failed`, `skipped`, or `cleared`, even when both writes happen back-to-back in the same control path.

**Fix:** Keep the mandatory recovery flush for `in_progress`, but batch later noncritical updates where possible or mark dirty state and flush once per completed branch. At minimum, document that this is intentional because it materially increases write frequency.
