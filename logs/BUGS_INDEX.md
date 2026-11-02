# BUGS_INDEX

Coverage:
- navigation.md: 16 findings
- gui.md: 10 findings
- floor.md: 0 findings
- adb.md: 10 findings
- entry_config.md: 10 findings
- safety_actions.md: 9 findings
- concurrency.md: 11 findings
- errors_compat.md: 24 findings
- distinct source findings covered: 90
- index rows: 51

| Index ID | Severity | Verdict | Source IDs | File:Line | Description | Area |
|---|---|---|---|---|---|---|
| B-001 | critical | CONFIRMED | NAV-001, ERR-014 | bot/floor.py:286-291 | Battle-end/stable-frame logic can exit or continue using an unstable room frame instead of a verified post-battle frame. | [navigation](logs/bugs/navigation.md), [errors_compat](logs/bugs/errors_compat.md) |
| B-002 | critical | CONFIRMED | GUI-001, CON-003 | gui_farm.py:118-136 | Global hotkey listener thread calls Tk methods from a non-main thread. | [gui](logs/bugs/gui.md), [concurrency](logs/bugs/concurrency.md) |
| B-003 | critical | CONFIRMED | GUI-002 | gui_farm.py:216-219,329-332 | Background worker threads call `self.after(...)` directly to re-enter Tk. | [gui](logs/bugs/gui.md) |
| B-004 | critical | CONFIRMED | ADB-001 | bot/adb_controller.py:113-123; bot/screenshot.py:24-30 | Every frame capture spawns a fresh `adb exec-out screencap -p` process, heavily degrading hot-path throughput. | [adb](logs/bugs/adb.md) |
| B-005 | critical | CONFIRMED | CFG-002 | main.py:104-109,259-271; bot/logging_setup.py:8-44 | Logging setup is repeated and forcibly resets root handlers, breaking embedding and integrations. | [entry_config](logs/bugs/entry_config.md) |
| B-006 | critical | CONFIRMED | SAF-001 | nav.py:444-460 | `start_battle()` returns success after fixed sleep without proving battle actually started. | [safety_actions](logs/bugs/safety_actions.md) |
| B-007 | critical | CONFIRMED | SAF-002, ADB-005 | nav.py:463-468 | Missing autobattle match triggers a blind center tap that can hit arbitrary UI. | [safety_actions](logs/bugs/safety_actions.md), [adb](logs/bugs/adb.md) |
| B-008 | critical | CONFIRMED | CON-001 | gui_farm.py:129-136,172-193 | GUI stop/full-stop can leave an orphan farm subprocess because termination has no reap/escalation path. | [concurrency](logs/bugs/concurrency.md) |
| B-009 | critical | CONFIRMED | CON-002 | gui_farm.py:195-217 | Subprocess stdout line iteration can block stop/shutdown handling indefinitely. | [concurrency](logs/bugs/concurrency.md) |
| B-010 | critical | CONFIRMED | ERR-002 | nav.py:217-220 | `find_template()` swallows all vision exceptions and reports ordinary “not found”. | [errors_compat](logs/bugs/errors_compat.md) |
| B-011 | critical | CONFIRMED | ERR-018 | gui_base.py:257-260; bot/adb_controller.py:21-22 | GUI relies on the monkey-patched `ADBController(config, logger)` constructor mismatch. | [errors_compat](logs/bugs/errors_compat.md) |
| B-012 | high | CONFIRMED | NAV-002 | nav.py:100-107,127-134,250-273,305-322 | Navigation reloads JSON/settings/templates from disk on hot-path helper calls instead of caching. | [navigation](logs/bugs/navigation.md) |
| B-013 | high | NOT CONFIRMED | NAV-003 | bot/explorer.py:343-360 | Stall detector reportedly uses process-randomized `hash()` on frame bytes. | [navigation](logs/bugs/navigation.md) |
| B-014 | high | CONFIRMED | NAV-004 | nav.py:120-125 | `take_screenshot()` saves capture output without validating capture success. | [navigation](logs/bugs/navigation.md) |
| B-015 | high | NOT CONFIRMED | NAV-005 | bot/vision.py:381-401 | ROI normalization reportedly clamps coordinates without reordering inverted endpoints. | [navigation](logs/bugs/navigation.md) |
| B-016 | high | CONFIRMED | NAV-006 | bot/explorer.py:260-276; bot/floor.py:304-308 | Pause-triggered labeling can use a raw transient screenshot instead of a stable frame. | [navigation](logs/bugs/navigation.md) |
| B-017 | high | PARTIAL | GUI-003, GUI-007 | gui.py:105-114; gui_base.py:728-731 | App log queue draining is coupled to SettingsTab instead of a root-owned always-on log sink. | [gui](logs/bugs/gui.md) |
| B-018 | high | CONFIRMED | GUI-004 | gui.py:142-152 | Bot tab `run_main()` controls are present but do not launch anything. | [gui](logs/bugs/gui.md) |
| B-019 | high | PARTIAL | GUI-005 | gui_base.py:76-78,170-179,903-906 | GUI path globals are relative and may diverge from project-dir-based paths. | [gui](logs/bugs/gui.md) |
| B-020 | high | NOT CONFIRMED | GUI-006 | gui_base.py:345-372 | Settings tab reportedly lacks scrolling for long forms. | [gui](logs/bugs/gui.md) |
| B-021 | high | CONFIRMED | ADB-002 | nav.py:333-337 | Joystick hold fallback spams one adb swipe subprocess per hold chunk. | [adb](logs/bugs/adb.md) |
| B-022 | high | CONFIRMED | ADB-003, ERR-001 | bot/adb_controller.py:240-271 | `ADBController` behavior is changed by post-definition monkey-patching of core methods/constructor. | [adb](logs/bugs/adb.md), [errors_compat](logs/bugs/errors_compat.md) |
| B-023 | high | CONFIRMED | ADB-004 | nav.py:357-358,388-419 | Global hold/release state can desynchronize across runtimes/devices while release errors are suppressed. | [adb](logs/bugs/adb.md) |
| B-024 | high | CONFIRMED | CFG-001 | main.py:130-141 | `--check` prints checklist items `[1/8]` and `[2/8]` twice. | [entry_config](logs/bugs/entry_config.md) |
| B-025 | high | CONFIRMED | CFG-003 | main.py:113-117 | `_run_navigation()` constructs heavy screenshot/vision objects it never uses. | [entry_config](logs/bugs/entry_config.md) |
| B-026 | high | CONFIRMED | CFG-004 | config.yaml:99-110; bot/config.py:282-288 | Shipped safety config keys do not match validation schema, leaving semantics inconsistent. | [entry_config](logs/bugs/entry_config.md) |
| B-027 | high | CONFIRMED | CFG-005 | bot/profiles.py:321-366; config.yaml:57-91 | Profile count/shape rules are enforced only at runtime, not during config validation. | [entry_config](logs/bugs/entry_config.md) |
| B-028 | high | CONFIRMED | CFG-006 | bot/knowledge.py:131-145; config.yaml:40-55; bot/profiles.py:96-126 | Knowledge/profile storage layout is split across multiple overlapping directories. | [entry_config](logs/bugs/entry_config.md) |
| B-029 | high | CONFIRMED | SAF-003 | nav.py:444-460,534-544,551-589; main.py:106-124; bot/safety.py:100-116 | Navigation/action code performs taps without consulting `SafetyManager` policy checks. | [safety_actions](logs/bugs/safety_actions.md) |
| B-030 | high | CONFIRMED | SAF-004 | bot/safety.py:78-98 | Safety policy denies most actual navigation action kinds because they are unmapped. | [safety_actions](logs/bugs/safety_actions.md) |
| B-031 | high | CONFIRMED | SAF-005 | nav.py:445,370-378 | Navigation looks for `autobattle_on` state but has no verified enable/toggle target. | [safety_actions](logs/bugs/safety_actions.md) |
| B-032 | high | CONFIRMED | SAF-006 | nav.py:551-589 | `_go_path()` chains taps with fixed sleeps and no explicit confirmation of each screen transition. | [safety_actions](logs/bugs/safety_actions.md) |
| B-033 | high | CONFIRMED | CON-004 | gui_farm.py:248-274,457-485 | Stop and full-stop can race each other while both release input, snapshot, and mutate shutdown state. | [concurrency](logs/bugs/concurrency.md) |
| B-034 | high | CONFIRMED | CON-005 | nav.py:156-176,386-440 | Global runtime cache keeps live mutable ADB/screenshot/vision objects without invalidation lifecycle. | [concurrency](logs/bugs/concurrency.md) |
| B-035 | high | CONFIRMED | CON-006 | nav.py:442-497 | Hold/release state is process-global and non-reentrant across sessions/windows. | [concurrency](logs/bugs/concurrency.md) |
| B-036 | high | NOT CONFIRMED | CON-007 | bot/knowledge.py:275-306,349-380 | Knowledge PNG/JSON writes reportedly lack atomicity and inter-process locking. | [concurrency](logs/bugs/concurrency.md) |
| B-037 | high | CONFIRMED | ERR-003 | nav.py:243-249 | `find_known_objects()` silently skips any per-template exception. | [errors_compat](logs/bugs/errors_compat.md) |
| B-038 | high | CONFIRMED | ERR-004 | nav.py:322-324,333-341; ADB-010 | Low-level movement input failures are swallowed, hiding broken hold/release delivery. | [errors_compat](logs/bugs/errors_compat.md), [adb](logs/bugs/adb.md) |
| B-039 | high | CONFIRMED | ERR-005 | nav.py:355-413 | `approach_target()` converts unexpected internal errors into ordinary `(False, message)` results. | [errors_compat](logs/bugs/errors_compat.md) |
| B-040 | high | NOT CONFIRMED | ERR-017 | gui_base.py:183-188 | GUI settings loader reportedly replaces invalid `nav.json` with defaults silently. | [errors_compat](logs/bugs/errors_compat.md) |
| B-041 | high | CONFIRMED | ERR-021 | gui_farm.py:147-151 | Control-tab nav JSON loader catches all exceptions and pretends the file is empty. | [errors_compat](logs/bugs/errors_compat.md) |
| B-042 | medium | not checked | NAV-007 | nav.py:336-396 | `approach_target()` adds extra fixed sleeps on top of stability waits, slowing response. | [navigation](logs/bugs/navigation.md) |
| B-043 | medium | not checked | NAV-008 | nav.py:284-289 | Swipe-hold fallback worker can loop indefinitely without its own runtime budget/backoff growth. | [navigation](logs/bugs/navigation.md) |
| B-044 | medium | not checked | NAV-009 | nav.py:420-433 | Post-approach click helpers re-capture once without bounded stabilization/retry. | [navigation](logs/bugs/navigation.md) |
| B-045 | medium | not checked | NAV-010, NAV-011, CON-009 | bot/vision.py:72-132,287-315; bot/screenshot.py:24-39; bot/floor.py:224-239 | Several wait/stability/capture retry loops are not externally interruptible by stop/pause hooks. | [navigation](logs/bugs/navigation.md), [concurrency](logs/bugs/concurrency.md) |
| B-046 | medium | not checked | NAV-012 | bot/floor.py:228-246 | After failed stability retries, floor recovery returns a known-unstable frame after labeling. | [navigation](logs/bugs/navigation.md) |
| B-047 | medium | not checked | NAV-013 | bot/explorer.py:229-258 | Teacher labeling for unknown stable screens does not revalidate knowledge against the current frame before continuing. | [navigation](logs/bugs/navigation.md) |
| B-048 | medium | not checked | NAV-014, SAF-007 | bot/floor.py:173-190; nav.py:383-406 | Event handling/status paths collapse distinct event outcomes instead of implementing separate skip vs choice logic. | [navigation](logs/bugs/navigation.md), [safety_actions](logs/bugs/safety_actions.md) |
| B-049 | medium | not checked | GUI-008, GUI-009, ADB-006, ADB-007, ADB-008, ADB-009, CFG-007, CFG-008, CFG-009, CON-008, CON-010, ERR-006, ERR-007, ERR-008, ERR-009, ERR-010, ERR-011, ERR-012, ERR-013, ERR-015, ERR-016, ERR-019, ERR-020, ERR-022, ERR-023 | multiple | Miscellaneous medium-severity issues: wrong running-state gate, settings-key mismatch, broad exception handling, fragile shell/constructor/runtime cleanup, incomplete backup/reset, packaging mismatch, daemon worker lifecycle, stale-frame fallbacks, and shutdown/logging suppression. | [gui](logs/bugs/gui.md), [adb](logs/bugs/adb.md), [entry_config](logs/bugs/entry_config.md), [concurrency](logs/bugs/concurrency.md), [errors_compat](logs/bugs/errors_compat.md) |
| B-050 | low | not checked | NAV-015, CFG-010, SAF-009 | bot/vision.py:51-52,344-351; config.yaml:27; main.py:160-162; nav.py:111-119 | Default thresholds/settings aliases are inconsistent across modules and docs/config. | [navigation](logs/bugs/navigation.md), [entry_config](logs/bugs/entry_config.md), [safety_actions](logs/bugs/safety_actions.md) |
| B-051 | low | not checked | NAV-016, GUI-010, ERR-024 | nav.py:442-459; gui_farm.py:107-130; src/ldplayer_dungeon_explorer_bot_with_audit/main.py:26-47 | Low-severity timing, hotkey portability, and helper CLI exception-wrapping issues. | [navigation](logs/bugs/navigation.md), [gui](logs/bugs/gui.md), [errors_compat](logs/bugs/errors_compat.md) |
