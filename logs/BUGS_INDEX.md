# Bugs Index

De-duplicated backlog from per-area audits on branch `bot-run-logs`.

| ID | Severity | File:Line | Description | Area |
|---|---|---|---|---|
| BUG-001 | critical | nav.py:463-468 | Blind center tap when autobattle state is not detected can hit arbitrary UI. | [safety_actions](logs/bugs/safety_actions.md) |
| BUG-002 | critical | bot/floor.py:286-291 | Battle can be marked finished early from stable screen plus missing autobattle signal. | [navigation](logs/bugs/navigation.md) |
| BUG-003 | critical | gui_farm.py:122-130 | Hotkey listener thread calls Tk methods from a non-main thread. | [gui](logs/bugs/gui.md) |
| BUG-004 | critical | gui_farm.py:216-219 | Background worker threads call Tk via `self.after(...)` off the main thread. | [gui](logs/bugs/gui.md) |
| BUG-005 | critical | gui_farm.py:129-136,172-217 | GUI stop/full-stop can leave orphan or blocked farm subprocesses. | [concurrency](logs/bugs/concurrency.md) |
| BUG-006 | critical | nav.py:217-220 | `find_template()` swallows all exceptions and reports false “not found”. | [errors_compat](logs/bugs/errors_compat.md) |
| BUG-007 | critical | main.py:104-109,259-271; bot/logging_setup.py:8-44 | Logging setup is destructive and reinitialised multiple times. | [entry_config](logs/bugs/entry_config.md) |
| BUG-008 | high | nav.py:444-460 | `start_battle()` returns success without verifying battle actually started. | [safety_actions](logs/bugs/safety_actions.md) |
| BUG-009 | high | nav.py:444-446,534-589; bot/safety.py:100-116 | Navigation taps never consult `SafetyManager` before acting. | [safety_actions](logs/bugs/safety_actions.md) |
| BUG-010 | high | nav.py:445,463-468,489-507 | Autobattle handling checks only `autobattle_on` and falls back unsafely instead of locating an enable control. | [adb](logs/bugs/adb.md) |
| BUG-011 | high | bot/adb_controller.py:240-271; gui_base.py:257-260; main.py:89 | `ADBController` relies on runtime monkey-patched constructor/API. | [errors_compat](logs/bugs/errors_compat.md) |
| BUG-012 | high | bot/adb_controller.py:113-123; bot/screenshot.py:24-30; nav.py:380-419 | Every frame capture spawns a fresh `adb exec-out screencap -p` process. | [adb](logs/bugs/adb.md) |
| BUG-013 | high | nav.py:333-337 | Hold fallback spams separate ADB swipe subprocesses continuously. | [adb](logs/bugs/adb.md) |
| BUG-014 | high | nav.py:357-358,388-419,442-497 | Global hold/release state can desync across runtimes and callers. | [concurrency](logs/bugs/concurrency.md) |
| BUG-015 | high | nav.py:156-176 | Global runtime cache reuses mutable live runtime objects without invalidation. | [concurrency](logs/bugs/concurrency.md) |
| BUG-016 | high | gui_base.py:76-78,170-179,903-906 | GUI mixes CWD-relative globals with project-relative paths. | [gui](logs/bugs/gui.md) |
| BUG-017 | high | gui_base.py:345-372 | Settings tab has no scrolling, making controls unreachable on small displays. | [gui](logs/bugs/gui.md) |
| BUG-018 | high | gui.py:105-114; gui_base.py:728-807 | App log queue has no always-on consumer or global log panel. | [gui](logs/bugs/gui.md) |
| BUG-019 | high | bot/floor.py:67-104; bot/floor_state.py:57-92 | Floor explorer lacks an explicit deterministic terminal completion transition. | [floor](logs/bugs/floor.md) |
| BUG-020 | high | bot/floor.py:287-290 | After unstable/manual labeling flow, code continues with stale unstable frame. | [errors_compat](logs/bugs/errors_compat.md) |
| BUG-021 | high | bot/floor.py:304-308; bot/explorer.py:260-276 | Pause-triggered labeling can use a transient raw frame instead of a stable one. | [navigation](logs/bugs/navigation.md) |
| BUG-022 | high | bot/knowledge.py:275-306,349-380 | Knowledge PNG/JSON writes are non-atomic and race across processes. | [concurrency](logs/bugs/concurrency.md) |
| BUG-023 | high | bot/explorer.py:343-360 | Stall detector uses process-randomized Python `hash()` on frame bytes. | [navigation](logs/bugs/navigation.md) |
| BUG-024 | high | nav.py:243-249 | `find_known_objects()` silently skips all template/vision errors. | [errors_compat](logs/bugs/errors_compat.md) |
| BUG-025 | high | nav.py:355-413 | `approach_target()` converts unexpected internal failures into normal misses. | [errors_compat](logs/bugs/errors_compat.md) |
| BUG-026 | high | nav.py:551-589 | Path navigation advances via fixed sleeps without verifying each transition. | [safety_actions](logs/bugs/safety_actions.md) |
| BUG-027 | high | config.yaml:99-110; bot/config.py:282-288 | Shipped safety config/schema is inconsistent and can validate ambiguously. | [entry_config](logs/bugs/entry_config.md) |
| BUG-028 | high | bot/profiles.py:321-366 | Exact-7 profile contract is enforced late instead of at config validation. | [entry_config](logs/bugs/entry_config.md) |
| BUG-029 | high | bot/knowledge.py:131-145; bot/profiles.py:96-126 | Profile/knowledge storage layout is split across inconsistent directories. | [entry_config](logs/bugs/entry_config.md) |
| BUG-030 | high | gui.py:142-152 | Bot tab action buttons are dead UI and do not launch `main.py`. | [gui](logs/bugs/gui.md) |
| BUG-031 | medium | bot/vision.py:72-132,287-315 | Stability and wait polling APIs lack stop/cancel hooks. | [navigation](logs/bugs/navigation.md) |
| BUG-032 | medium | bot/screenshot.py:22-38 | Screenshot retries catch every exception and blur transport vs decode failures. | [adb](logs/bugs/adb.md) |
| BUG-033 | medium | bot/adb_controller.py:24-34,260-266 | `serial="auto"` works only via compatibility patch, not the real constructor. | [adb](logs/bugs/adb.md) |
| BUG-034 | medium | bot/adb_controller.py:61-72,196-218 | `adb shell` API builds fragile single-string commands. | [adb](logs/bugs/adb.md) |
| BUG-035 | medium | nav.py:346-370 | `hold()`/`release()` rebuild runtime during cleanup paths. | [adb](logs/bugs/adb.md) |
| BUG-036 | medium | bot/floor.py:173-190, nav.py:383-406 | Event handling/status logic collapses choice vs skip into the same behavior. | [safety_actions](logs/bugs/safety_actions.md) |
| BUG-037 | medium | nav.py:245-249 | `actions.json` contents are loaded without validating allowed action values. | [safety_actions](logs/bugs/safety_actions.md) |
| BUG-038 | medium | nav.py:336-396 | `approach_target()` waits twice per control iteration, hurting responsiveness. | [navigation](logs/bugs/navigation.md) |
| BUG-039 | medium | nav.py:284-289 | Swipe-hold worker can run indefinitely without its own stop budget. | [navigation](logs/bugs/navigation.md) |
| BUG-040 | medium | nav.py:420-433 | Post-approach click helpers retry on only one fresh unstable frame. | [navigation](logs/bugs/navigation.md) |
| BUG-041 | medium | bot/explorer.py:229-258 | Teacher labeling returns without revalidating the just-labeled frame. | [navigation](logs/bugs/navigation.md) |
| BUG-042 | medium | bot/floor.py:118-149,181-197 | Floor logic persists state multiple times per single logical transition. | [floor](logs/bugs/floor.md) |
| BUG-043 | medium | bot/floor.py:84-85,204-225 | Floor logic reloads templates repeatedly within one exploration step. | [floor](logs/bugs/floor.md) |
| BUG-044 | medium | bot/teacher_ui.py:79-154 | Teacher UI loops can block indefinitely without timeout/step budget. | [floor](logs/bugs/floor.md) |
| BUG-045 | medium | gui_base.py:816-818; gui_farm.py:175-176 | GUI command gating checks stale `app.current_process` instead of real run state. | [gui](logs/bugs/gui.md) |
| BUG-046 | medium | gui_base.py:157-167 | GUI exposes `match_level` abstraction but persists only `match_threshold`. | [gui](logs/bugs/gui.md) |
| BUG-047 | medium | gui_base.py:219-247 | Region-capture worker thread is fire-and-forget and not shutdown-coordinated. | [concurrency](logs/bugs/concurrency.md) |
| BUG-048 | medium | bot/screenshot.py:24-39; bot/floor.py:224-239 | Several long waits and retries are not interruptible by stop events. | [concurrency](logs/bugs/concurrency.md) |
| BUG-049 | medium | gui_farm.py:283-337 | Manual action workers are detached and can outlive UI/session shutdown. | [concurrency](logs/bugs/concurrency.md) |
| BUG-050 | medium | main.py:91-103,124-126,204-248 | Multiple CLI paths flatten broad exceptions into generic results. | [errors_compat](logs/bugs/errors_compat.md) |
| BUG-051 | medium | bot/explorer.py:313-316,365-369 | Explorer silently degrades on template/capture errors to empty or stale state. | [errors_compat](logs/bugs/errors_compat.md) |
| BUG-052 | medium | bot/safety.py:70-73 | `ensure_safe_frame()` wraps every vision failure as generic `SafetyError`. | [errors_compat](logs/bugs/errors_compat.md) |
| BUG-053 | medium | gui_base.py:183-188; gui_farm.py:147-151 | GUI silently treats invalid/corrupt `nav.json` as defaults or empty config. | [errors_compat](logs/bugs/errors_compat.md) |
| BUG-054 | medium | gui.py:170-185; gui_farm.py:249-305 | GUI shutdown/process/fsync cleanup swallows termination errors. | [errors_compat](logs/bugs/errors_compat.md) |
| BUG-055 | medium | main.py:223-230; bot/profiles.py:96-126 | Backup/reset omits some profile knowledge artifacts. | [entry_config](logs/bugs/entry_config.md) |
| BUG-056 | medium | pyproject.toml:1-28; requirements.txt:1-6 | Packaging metadata omits runtime dependencies used by the real CLI. | [entry_config](logs/bugs/entry_config.md) |
| BUG-057 | medium | main.py:274-278; pyproject.toml:11-16 | Packaged entry points do not target the operational root CLI. | [entry_config](logs/bugs/entry_config.md) |
| BUG-058 | low | bot/vision.py:51-52,344-351; config.yaml:27 | Default match thresholds are inconsistent across config, nav, and vision. | [navigation](logs/bugs/navigation.md) |
| BUG-059 | low | nav.py:111-119 | Unknown movement/action config aliases are ignored silently. | [safety_actions](logs/bugs/safety_actions.md) |
| BUG-060 | low | gui_farm.py:107-110,122-130 | Hotkeys are brittle across keypad/layout/platform variants. | [gui](logs/bugs/gui.md) |
| BUG-061 | low | gui.py:122-129,139-143 | GUI profile/memory refresh paths swallow exceptions silently. | [concurrency](logs/bugs/concurrency.md) |

## Totals

### By severity
- Critical: 7
- High: 23
- Medium: 27
- Low: 4
- Total: 61

### By area file
- logs/bugs/navigation.md: 14
- logs/bugs/gui.md: 10
- logs/bugs/floor.md: 5
- logs/bugs/adb.md: 8
- logs/bugs/entry_config.md: 9
- logs/bugs/safety_actions.md: 8
- logs/bugs/concurrency.md: 10
- logs/bugs/errors_compat.md: 14
