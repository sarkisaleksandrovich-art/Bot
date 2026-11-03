# FIX_PLAN

Lane summary:
- Lane A — 13 fixes; files: `bot/floor.py`, `nav.py`, `bot/safety.py`, `main.py`, `bot/logging_setup.py`
- Lane B — 12 fixes; files: `bot/adb_controller.py`, `bot/explorer.py`, `bot/knowledge.py`, `bot/profiles.py`, `bot/config.py`
- Lane C — 11 fixes; files: `gui.py`, `gui_base.py`, `gui_farm.py`

| Fix ID | Source IDs | Severity | File:Line | Concrete change | Risk | Dependencies | Lane |
|---|---|---|---|---|---|---|---|
| FIX-001 | NAV-001, FLR-003 | critical | `bot/floor.py:141-153` | Replace battle-end inference based on missing `auto_battle` with a positive post-battle confirmation path: wait for explicit room-exit/loot/passage/event templates or require consecutive non-battle frames before returning. Keep the existing hard timeout only as fallback. | Medium | None | A |
| FIX-002 | SAF-001 | critical | `nav.py:471-485` | After tapping `enter_dungeon`, poll for a verifiable combat-state indicator and return failure if battle entry is not confirmed within timeout instead of returning success after fixed sleep. | Medium | None | A |
| FIX-003 | SAF-002, ADB-005, SAF-005 | critical | `nav.py:477-485` | Remove the blind center-tap fallback; add a distinct verified autobattle-enable lookup/action and confirm the enabled state after tap. If no verified control is found, fail safely with a clear reason. | Medium | FIX-002 | A |
| FIX-004 | FLR-002 | high | `bot/floor.py:145-148` | After traversing a passage, update floor-state graph traversal explicitly: switch/create the target node or mark the passage consumed for the current node so `choose_next_target()` cannot reselect the same passage indefinitely. | High | None | A |
| FIX-005 | NAV-006 | high | `bot/floor.py:201-204` | Route floor pause labeling through the same stable-frame capture path used by explorer pause handling, retrying `wait_until_stable()` before opening teacher UI. | Low | None | A |
| FIX-006 | ERR-014 | high | `bot/floor.py:193-195` | After unstable-screen labeling, restart stable capture and return only a fresh validated frame instead of continuing on the labeled last unstable frame. | Medium | FIX-005 | A |
| FIX-007 | NAV-004 | high | `nav.py:193-198` | Guard screenshot capture in `take_screenshot()` with explicit `None`/failure validation and raise a navigation-specific error before attempting save. | Low | None | A |
| FIX-008 | ERR-002 | critical | `nav.py:213-220` | Narrow `find_template()` exception handling to expected vision/template failures with contextual logging; re-raise unexpected exceptions so corrupted runtime state is not reported as ordinary “not found”. | Medium | None | A |
| FIX-009 | ERR-003 | high | `nav.py:243-249` | Replace silent per-template `except Exception: continue` with narrow exception handling plus template/category logging, and fail fast on programmer errors such as invalid frames. | Medium | FIX-008 | A |
| FIX-010 | ERR-004, ADB-004, CON-006 | high | `nav.py:286-289, 317-358, 346-358, 388-419` | Refactor hold/release ownership to per-session state tied to the runtime/device used for DOWN/UP, and surface release/input failures instead of suppressing them. Keep all hold-state mutation inside one controller object rather than module globals. | High | None | A |
| FIX-011 | ERR-005, SAF-006 | high | `nav.py:406-410, 568-594` | Replace broad exception flattening and fixed-sleep path progression with verified step transitions and structured operational errors; unexpected exceptions should propagate, while expected transition failures return explicit reasons. | Medium | FIX-008 | A |
| FIX-012 | SAF-003, SAF-004 | high | `main.py:104-124; nav.py:429-485, 551-589; bot/safety.py:87-116` | Thread `SafetyManager` through navigation/runtime APIs and require pre-tap policy/screen checks for nav actions. Extend safety action mapping to cover `loot`, `mob`, `event_choice`, `event_skip`, and `skip` as explicit operational actions. | High | FIX-003, FIX-011 | A |
| FIX-013 | CFG-002 | critical | `main.py:104-109, 259-271; bot/logging_setup.py:20-27` | Make logging setup idempotent: configure once in `main()`, pass the logger into `_run_navigation()`, and stop removing arbitrary root handlers on repeated setup. | Medium | None | A |
| FIX-014 | ADB-001 | critical | `bot/adb_controller.py:113-123; nav.py:147-156, 380-419` | Introduce a reusable capture path for navigation loops so hot-path frame reads do not spawn a fresh `adb exec-out screencap -p` process per query; at minimum reuse the most recent frame within polling windows and centralize throttling/caching. | High | None | B |
| FIX-015 | ADB-002 | high | `nav.py:284-289; bot/adb_controller.py:83-94, 61-72` | Replace repeated short swipe-command spawning for hold fallback with a persistent or longer-lived movement input strategy, reducing adb process churn during continuous movement. | High | FIX-010, FIX-014 | B |
| FIX-016 | ADB-003, ERR-001, ERR-018 | high | `bot/adb_controller.py:21-34, 240-253, 258-271; main.py:89-97; gui_base.py:257-260` | Remove post-definition monkey patches by implementing explicit class-native compatibility (`from_config()` or supported overloaded init) and update callers to use the real API. | High | None | B |
| FIX-017 | NAV-002 | high | `nav.py:111-119, 223-236, 250-273, 215-216` | Cache parsed `nav.json`/`actions.json` and decoded template PNGs by resolved path, with invalidation on file change or explicit reload, so repeated helper calls stop rereading disk in tight loops. | Medium | None | B |
| FIX-018 | NAV-003, FLR-001 | high | `bot/explorer.py:223-228, 349-352` | Make pause requests edge-triggered (`consume_pause_request()` or equivalent) and replace process-randomized `hash()` frame signatures with a deterministic digest for stall detection. | Medium | None | B |
| FIX-019 | CFG-005 | high | `bot/profiles.py:345-366; bot/config.py:260-338` | Move the exact profile-count/shape validation into config loading so malformed `profiles.items` fail early, while keeping runtime checks as defensive assertions. | Low | None | B |
| FIX-020 | CFG-006, CON-007 | high | `bot/knowledge.py:138-145, 275-306; bot/profiles.py:116-126` | Define one canonical profile knowledge layout and make knowledge writes atomic under a profile-scoped file lock; write PNG/JSON through temp-file+replace and generate collision-resistant template IDs. | High | FIX-019 | B |
| FIX-021 | CON-005 | high | `nav.py:47, 167-189, 364-379` | Add explicit runtime-cache lifecycle management with invalidation on stop/restart and ADB failure, and protect cache access with a lock instead of keeping live runtime objects forever. | Medium | FIX-016 | B |
| FIX-022 | CFG-001 | high | `main.py:130-141` | Remove the duplicated eager checklist prints in `--check` and compute adb status once before emitting the numbered output. | Low | None | B |
| FIX-023 | CFG-003 | high | `main.py:106-117` | Stop constructing unused screenshot/vision objects in navigation startup; instantiate only the dependencies actually required by the path. | Low | FIX-016 | B |
| FIX-024 | GUI-003, GUI-007 | high | `gui.py:25-26, 55-61, 96-102; gui_base.py:728-731` | Move app-log draining to `GuiApp` and provide one always-on log sink/panel owned by the root window so log consumption no longer depends on Settings-tab polling. | Medium | None | C |
| FIX-025 | GUI-004 | high | `gui.py:73-83` | Either implement Bot-tab subprocess launch with the same supervision model used elsewhere or disable/remove the dead buttons until supported. | Medium | FIX-024 | C |
| FIX-026 | GUI-005, ERR-017, GUI-006 | high | `gui_base.py:183-189, 214-218, 345-372` | Make GUI settings paths derive from `project_dir`, distinguish missing `nav.json` from malformed/corrupt content with visible user feedback, and wrap the Settings form in a scrollable container so lower controls remain reachable. | Medium | None | C |
| FIX-027 | GUI-001, CON-003 | high | `gui_farm.py:121-128, 134-135` | Replace direct Tk calls from the global hotkey listener with a thread-safe event queue polled by the Tk main thread. | Medium | None | C |
| FIX-028 | CON-001, CON-002, CON-004, ERR-021 | critical | `gui_farm.py:143-151, 172-220, 195-217, 234-245, 248-274, 457-485` | Centralize farm-process supervision and shutdown: surface malformed `nav.json`, avoid blocking stdout iteration, start child in its own process group, and implement terminate→wait→kill→reap with one serialized stop/full-stop state machine. | High | FIX-027 | C |
| FIX-029 | GUI-002 | critical | `gui_farm.py:180-193, 253-274` | Replace worker-thread `self.after(...)` calls with queue-based result handoff so only the Tk main thread touches widgets. | Medium | FIX-027 | C |
| FIX-030 | CON-006 | high | `nav.py:43-47, 317-358` | Fold GUI-triggered hold/release actions over the same per-session hold controller used by nav so manual actions, stop snapshots, and farm navigation cannot release each other’s globals. | High | FIX-010 | C |
| FIX-031 | CON-005 | high | `nav.py:167-189, 364-379; gui_farm.py:248-274, 457-485` | In stop/full-stop paths, explicitly invalidate nav runtime cache and release device/session resources after supervised shutdown so later GUI runs do not reuse stale live objects. | Medium | FIX-021, FIX-028, FIX-030 | C |
| FIX-032 | B-018/GUI-006 | high | `gui_base.py:345-372` | Implement a scrollable Settings content frame with scrollbar and wheel bindings as suggested in the GUI area file; keep console scrolling independent. | Low | FIX-026 | C |
| FIX-033 | B-009/ERR-018 | critical | `gui_base.py:257-260; bot/adb_controller.py:21-34, 258-271` | Update GUI provider construction to use the explicit controller API introduced for constructor compatibility, eliminating reliance on monkey-patched signatures. | Medium | FIX-016 | C |
| FIX-034 | B-009/ERR-018 | critical | `main.py:89-97; bot/adb_controller.py:21-34, 258-271` | Update CLI/runtime controller construction to the same explicit controller API so GUI and CLI no longer depend on monkey-patched `ADBController(config, logger)`. | Medium | FIX-016 | B |
| FIX-035 | B-015/GUI-003,GUI-007 | high | `gui.py:25-26, 55-61; gui_base.py:728-731, gui.py:96-102` | Consolidate all app logging behind the root-owned log consumer and remove Settings-tab-only queue ownership, matching the area-file suggested architecture. | Low | FIX-024 | C |
| FIX-036 | B-033/CON-003,GUI-001 | high | `gui_farm.py:121-128, 134-135` | Use one reviewed queue/poller implementation for hotkey events and remove direct thread-to-Tk scheduling. | Low | FIX-027 | C |

## Not planned: not verified
- `B-013` (`NAV-005`)
- `B-043` (`NAV-007`)
- `B-044` (`NAV-008`, `ADB-010`)
- `B-045` (`NAV-009`)
- `B-046` (`NAV-010`, `NAV-011`, `CON-009`)
- `B-047` (`NAV-012`)
- `B-048` (`NAV-013`)
- `B-049` (`NAV-014`, `SAF-007`)
- `B-050` (`NAV-015`, `CFG-010`)
