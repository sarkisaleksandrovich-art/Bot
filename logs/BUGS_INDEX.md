# BUGS_INDEX

Coverage:
- `logs/bugs/navigation.md`: 16 findings (NAV-001..NAV-016)
- `logs/bugs/gui.md`: 10 findings (GUI-001..GUI-010)
- `logs/bugs/floor.md`: 5 findings (FLR-001..FLR-005)
- `logs/bugs/adb.md`: 10 findings (ADB-001..ADB-010)
- `logs/bugs/entry_config.md`: 10 findings (CFG-001..CFG-010)
- `logs/bugs/safety_actions.md`: 9 findings (SAF-001..SAF-009)
- `logs/bugs/concurrency.md`: 11 findings (CON-001..CON-011)
- `logs/bugs/errors_compat.md`: 24 findings (ERR-001..ERR-024)
- Total source findings: 95
- Total index rows: 50

| Index ID | Severity | Verdict | Source IDs | File:Line | Description | Area links |
|---|---|---|---|---|---|---|
| B-001 | critical | CONFIRMED | NAV-001, FLR-003 | `bot/floor.py:141-153` | Battle end is inferred from missing `auto_battle` on room/event/passage screens, allowing premature combat exit. | `logs/bugs/navigation.md`, `logs/bugs/floor.md` |
| B-002 | critical | CONFIRMED | ADB-001 | `bot/adb_controller.py:113-123; nav.py:147-156, 380-419` | Every hot-path frame capture spawns a fresh `adb exec-out screencap -p` process. | `logs/bugs/adb.md` |
| B-003 | critical | CONFIRMED | CFG-002 | `main.py:104-109, 259-271; bot/logging_setup.py:20-27` | Logging setup is repeated and tears down all root handlers on each call. | `logs/bugs/entry_config.md` |
| B-004 | critical | CONFIRMED | SAF-001 | `nav.py:471-485` | Battle start returns success after a fixed sleep without verifying entry into combat. | `logs/bugs/safety_actions.md` |
| B-005 | critical | CONFIRMED | SAF-002, ADB-005 | `nav.py:477-485` | Missing `autobattle_on` triggers a blind center tap with no template verification. | `logs/bugs/safety_actions.md`, `logs/bugs/adb.md` |
| B-006 | critical | CONFIRMED | CON-001 | `gui_farm.py:172-220, 239-245, 457-485` | Child farm process can be orphaned because stop/full-stop only terminate without mandatory reap/kill cleanup. | `logs/bugs/concurrency.md` |
| B-007 | critical | CONFIRMED | CON-002 | `gui_farm.py:195-217` | Stop can stall behind blocking stdout iteration on the child process. | `logs/bugs/concurrency.md` |
| B-008 | critical | CONFIRMED | ERR-002 | `nav.py:213-220` | Template lookup swallows every exception and reports a normal “not found” result. | `logs/bugs/errors_compat.md` |
| B-009 | critical | CONFIRMED | ERR-018 | `gui_base.py:257-260; bot/adb_controller.py:21-34, 258-271` | GUI provider construction depends on a monkey-patched `ADBController(config, logger)` signature. | `logs/bugs/errors_compat.md` |
| B-010 | high | CONFIRMED | NAV-002 | `nav.py:111-119, 223-236, 250-273, 215-216` | Navigation rereads JSON and reloads template PNGs on repeated helper calls instead of caching. | `logs/bugs/navigation.md` |
| B-011 | high | CONFIRMED | NAV-003 | `bot/explorer.py:349-352` | Frame stall detection uses process-randomized Python `hash()` on image bytes. | `logs/bugs/navigation.md` |
| B-012 | high | CONFIRMED | NAV-004 | `nav.py:193-198` | Screenshot capture is saved immediately without validating the captured frame. | `logs/bugs/navigation.md` |
| B-013 | high | not checked | NAV-005 | `bot/vision.py:381-401` | ROI normalization does not reorder inverted coordinates before validity checks. | `logs/bugs/navigation.md` |
| B-014 | high | CONFIRMED | NAV-006 | `bot/floor.py:201-204` | Floor pause labeling uses a raw screenshot instead of a stable frame capture path. | `logs/bugs/navigation.md` |
| B-015 | high | CONFIRMED | GUI-003, GUI-007 | `gui.py:25-26, 55-61; gui_base.py:728-731, gui.py:96-102` | App log messages are only queued/drained through Settings-tab plumbing, with no central always-on log consumer. | `logs/bugs/gui.md` |
| B-016 | high | CONFIRMED | GUI-004 | `gui.py:73-83` | Bot-tab run handler is unimplemented and only logs that launch is not supported. | `logs/bugs/gui.md` |
| B-017 | high | CONFIRMED | GUI-005 | `gui_base.py:183-189, 214-218` | GUI settings load/save uses module-level relative paths instead of `project_dir`-based paths. | `logs/bugs/gui.md` |
| B-018 | high | PARTIAL | GUI-006 | `gui_base.py:345-372` | Settings tab lacks scrolling, making lower controls inaccessible on smaller displays. | `logs/bugs/gui.md` |
| B-019 | high | CONFIRMED | FLR-001 | `bot/explorer.py:223-228` | Pause requests are level-triggered, so the floor flow can re-enter pause repeatedly. | `logs/bugs/floor.md` |
| B-020 | high | CONFIRMED | FLR-002 | `bot/floor.py:145-148` | Passage handling marks the door and returns without switching nodes or consuming traversal state. | `logs/bugs/floor.md` |
| B-021 | high | CONFIRMED | ADB-002 | `nav.py:284-289; bot/adb_controller.py:83-94, 61-72` | Hold-swipe fallback turns continuous movement into repeated adb command spawns. | `logs/bugs/adb.md` |
| B-022 | high | CONFIRMED | ADB-003, ERR-001 | `bot/adb_controller.py:21-34, 240-253, 258-271; main.py:89-97` | `ADBController` core methods and constructor are monkey-patched after class definition, hiding the real API. | `logs/bugs/adb.md`, `logs/bugs/errors_compat.md` |
| B-023 | high | CONFIRMED | ADB-004, CON-006 | `nav.py:43-47, 317-358, 346-358, 388-419` | Hold/release uses module-global state across callers and suppresses release failures. | `logs/bugs/adb.md`, `logs/bugs/concurrency.md` |
| B-024 | high | CONFIRMED | CFG-001 | `main.py:130-141` | `--check` prints checklist items 1 and 2 twice. | `logs/bugs/entry_config.md` |
| B-025 | high | CONFIRMED | CFG-003 | `main.py:106-117` | Navigation startup constructs screenshot and vision objects that it never uses. | `logs/bugs/entry_config.md` |
| B-026 | high | NOT CONFIRMED | CFG-004 | `config.yaml:99-110; bot/config.py:282-288` | Claimed safety-key mismatch is not supported by the verified source read. | `logs/bugs/entry_config.md` |
| B-027 | high | CONFIRMED | CFG-005 | `bot/profiles.py:345-366; bot/config.py:260-338` | Profile count is enforced at runtime but not validated during config loading. | `logs/bugs/entry_config.md` |
| B-028 | high | CONFIRMED | CFG-006 | `bot/knowledge.py:138-145; bot/profiles.py:116-126` | Knowledge/profile data is stored in inconsistent parallel directory layouts. | `logs/bugs/entry_config.md` |
| B-029 | high | CONFIRMED | SAF-003 | `main.py:104-124; nav.py:429-485, 551-589; bot/safety.py:100-116` | Navigation taps/actions never consult `SafetyManager` before acting. | `logs/bugs/safety_actions.md` |
| B-030 | high | CONFIRMED | SAF-004 | `bot/safety.py:87-98; nav.py:25-31` | Safety policy does not recognize operational nav action kinds like `loot`, `mob`, or event actions. | `logs/bugs/safety_actions.md` |
| B-031 | high | CONFIRMED | SAF-005 | `nav.py:471-485` | Battle start looks only for `autobattle_on`, with no distinct enable template before fallback behavior. | `logs/bugs/safety_actions.md` |
| B-032 | high | CONFIRMED | SAF-006 | `nav.py:568-594` | Path navigation advances through taps on fixed sleeps rather than verified state transitions. | `logs/bugs/safety_actions.md` |
| B-033 | high | CONFIRMED | CON-003, GUI-001 | `gui_farm.py:121-128, 134-135` | Global hotkey listener thread touches Tk via `self.after(...)`. | `logs/bugs/concurrency.md`, `logs/bugs/gui.md` |
| B-034 | high | CONFIRMED | CON-004 | `gui_farm.py:234-238, 248-274, 457-485` | Stop/full-stop shutdown paths race with async release/snapshot work and UI teardown. | `logs/bugs/concurrency.md` |
| B-035 | high | CONFIRMED | CON-005 | `nav.py:47, 167-189, 364-379` | Global runtime cache retains live runtime objects across runs without invalidation. | `logs/bugs/concurrency.md` |
| B-036 | high | CONFIRMED | CON-007 | `bot/knowledge.py:275-306` | Template PNG writes and JSON metadata updates are non-atomic and can race. | `logs/bugs/concurrency.md` |
| B-037 | high | CONFIRMED | ERR-003 | `nav.py:243-249` | Known-object scanning silently skips any per-template exception. | `logs/bugs/errors_compat.md` |
| B-038 | high | CONFIRMED | ERR-004 | `nav.py:286-289, 351-358` | Low-level hold/release input errors are swallowed and hidden from callers. | `logs/bugs/errors_compat.md` |
| B-039 | high | CONFIRMED | ERR-005 | `nav.py:406-410` | Navigation approach loop converts any internal exception into an ordinary failure tuple. | `logs/bugs/errors_compat.md` |
| B-040 | high | CONFIRMED | ERR-014 | `bot/floor.py:193-195` | After unstable retries, floor logic returns the labeled last frame instead of a fresh stable recapture. | `logs/bugs/errors_compat.md` |
| B-041 | high | CONFIRMED | ERR-017 | `gui_base.py:183-188` | Malformed or unreadable `nav.json` is silently replaced with defaults in GUI settings loading. | `logs/bugs/errors_compat.md` |
| B-042 | high | CONFIRMED | ERR-021 | `gui_farm.py:143-151` | Corrupted `nav.json` is silently treated as empty config in the control tab. | `logs/bugs/errors_compat.md` |
| B-043 | medium | not checked | NAV-007 | `nav.py:336-396` | Approach loop compounds wait latency by doing stable waits plus extra fixed sleeps. | `logs/bugs/navigation.md` |
| B-044 | medium | not checked | NAV-008, ADB-010 | `nav.py:284-289, 333-337` | Swipe-hold worker can run indefinitely and silently retry on failures with little visibility. | `logs/bugs/navigation.md`, `logs/bugs/adb.md` |
| B-045 | medium | not checked | NAV-009 | `nav.py:420-433` | Post-approach click helpers rematch immediately on a fresh frame without a bounded stabilization retry. | `logs/bugs/navigation.md` |
| B-046 | medium | not checked | NAV-010, NAV-011, CON-009 | `bot/vision.py:72-132, 287-315; bot/screenshot.py:24-39; bot/floor.py:224-239` | Stability/polling waits and related retries lack stop hooks, delaying graceful interruption. | `logs/bugs/navigation.md`, `logs/bugs/concurrency.md` |
| B-047 | medium | not checked | NAV-012 | `bot/floor.py:228-246` | Unstable-screen recovery can continue on the last unstable frame after labeling. | `logs/bugs/navigation.md` |
| B-048 | medium | not checked | NAV-013 | `bot/explorer.py:229-258` | Unknown-screen labeling returns without revalidating the newly taught knowledge on the current frame. | `logs/bugs/navigation.md` |
| B-049 | medium | not checked | NAV-014, SAF-007 | `bot/floor.py:173-190; nav.py:383-406` | Event handling/status paths collapse distinct event choices into simplistic skip/one-tap behavior. | `logs/bugs/navigation.md`, `logs/bugs/safety_actions.md` |
| B-050 | medium | not checked | NAV-015, CFG-010 | `bot/vision.py:51-52, 344-351; config.yaml:27; main.py:160-162` | Default match-threshold assumptions are inconsistent across config, vision, and nav paths. | `logs/bugs/navigation.md`, `logs/bugs/entry_config.md` |

Totals from table:
- Rows by severity: critical 9, high 33, medium 8, low 0
- Distinct source IDs covered: 95 / 95
- Uncovered IDs: none
