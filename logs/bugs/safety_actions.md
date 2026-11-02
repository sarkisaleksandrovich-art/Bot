# Safety and Action Audit Findings

Source branch audited: `main`
Log branch: `bot-run-logs`
Files reviewed in full: `bot/safety.py`, `nav.py`, `main.py`, `logs/REPO_MAP.md`

## Summary
- Critical: 2
- High: 4
- Medium: 2
- Low: 1
- Total: 9

## Findings

### SAF-001
- Severity: Critical
- File: `nav.py:444-460`
- Description: `start_battle()` starts combat by tapping `enter_dungeon`, then waits a fixed 1.0 second and returns success without verifying that the game actually entered battle or that the post-tap screen changed to the expected state.
- Evidence:
  - `nav.py:452-455` taps the matched `enter_dungeon` template via `_click_center(...)`.
  - `nav.py:458` does `time.sleep(1.0)`.
  - `nav.py:460-469` only optionally looks for `autobattle_on`; if that check is skipped or inconclusive, the function still reaches `return True, "battle started"` at `nav.py:470`.
- Suggested fix: After tapping `enter_dungeon`, poll for a verifiable battle-state indicator with timeout, such as a battle HUD, autobattle button, movement lock, or a known combat template. Return failure if the battle state is not confirmed.

### SAF-002
- Severity: Critical
- File: `nav.py:463-468`
- Description: If the `autobattle_on` template is configured but not found after battle start, the bot performs a blind fallback tap at the exact screen center. This is an unsafe tap that can hit arbitrary UI, including ads, popups, purchases, or unrelated controls.
- Evidence:
  - `nav.py:463-464` logs that autobattle was not found.
  - `nav.py:465-467` computes `tx, ty = width // 2, height // 2` and executes `runtime.adb.tap(int(tx), int(ty))`.
  - No template verification is performed before or after the center tap.
- Suggested fix: Remove the blind center tap. Instead detect a specific `autobattle_enable`/toggle-off control and tap only that verified control, then confirm the enabled state appears.

### SAF-003
- Severity: High
- File: `nav.py:444-446, 534-544, 551-589`; `main.py:106-124`; `bot/safety.py:100-116`
- Description: Navigation and action execution never consult `SafetyManager` before taps and flow transitions. As a result, the bot can navigate into or tap screens containing ads, purchases, premium-currency prompts, or PvP without policy enforcement.
- Evidence:
  - `main.py:106-124` creates a `SafetyManager`, but `_run_navigation()` at `main.py:106-124` only passes `adb`, `screenshot`, and `vision` into navigation flow indirectly and never supplies or invokes safety checks there.
  - `nav.py` functions `start_battle`, `_go_path`, `_run_known_objects`, `click_loot`, `attack_mob`, and `handle_event` execute taps directly with `runtime.adb.tap(...)` and no `SafetyManager.ensure_action_allowed(...)`, `ensure_rule_action_allowed(...)`, or `ensure_safe_frame(...)` calls.
  - `bot/safety.py:100-116` provides `ensure_action_allowed`, but there are no call sites in the audited navigation path.
- Suggested fix: Thread a `SafetyManager` into nav/runtime APIs and require pre-tap checks for screen safety and action policy before every navigation tap, especially around dungeon entry, event handling, and any UI that may contain monetization or PvP actions.

### SAF-004
- Severity: High
- File: `bot/safety.py:78-98`
- Description: `SafetyManager.is_action_allowed()` denies every action name except `shop`, `ads`, `pvp`, `premium_currency`, `explore`, and `teach`. The in-game actions used by navigation (`loot`, `mob`, `event_choice`, `event_skip`, `skip`) are unsupported and would all evaluate to forbidden if SafetyManager were integrated.
- Evidence:
  - `bot/safety.py:87-98` returns `True` only for `shop`, `ads`, `pvp`, `premium_currency`, `explore`, and `teach`; all other actions fall through to `return False`.
  - `nav.py:15-21` and `nav.py:383-406` explicitly use action kinds `loot`, `mob`, `event_choice`, `event_skip`, and `skip`.
- Suggested fix: Extend safety policy mapping to cover operational in-game actions explicitly, e.g. allow `loot`, `mob`, `event_choice`, `event_skip`, and `skip` by default or classify them under an exploration-safe category.

### SAF-005
- Severity: High
- File: `nav.py:445, 370-378`
- Description: The navigation code uses the key `autobattle_on` as the lookup target after starting battle. This checks only for the already-enabled state and gives no way to locate the actual control that enables autobattle. That design directly causes the unsafe fallback tap path.
- Evidence:
  - `nav.py:445` loads `autobattle_png = _resolve_path(project_dir, _extract_png(nav.get("autobattle_on")))`.
  - `nav.py:463-468` treats absence of `autobattle_on` as meaning autobattle is off and then center-taps, instead of searching for an enable/toggle control.
  - `nav.py:370-378` exposes `autobattle_on` in status output; there is no companion `autobattle_enable` or equivalent handling.
- Suggested fix: Support distinct keys for state and action, e.g. `autobattle_on` and `autobattle_enable`, or a generalized `autobattle_toggle`, and confirm state transition after tapping the correct control.

### SAF-006
- Severity: High
- File: `nav.py:551-589`
- Description: `_go_path()` performs a sequence of taps through base, location, floor, and enter-dungeon screens with only pre-tap template finds and fixed sleeps. It does not verify that each tap led to the expected next screen before continuing, making silent misnavigation likely.
- Evidence:
  - `nav.py:568-571` taps `base_to_locations` and sleeps 1 second.
  - `nav.py:580-582` taps `location_icon` and sleeps 1 second.
  - `nav.py:589-594` taps `farm_floor_button` and sleeps 1 second.
  - The function does not confirm each transition succeeded before advancing to the next step; it only looks for the next template after a fixed wait.
- Suggested fix: After each tap, verify arrival at the expected next screen/state with a bounded retry loop and explicit failure reasons when the transition is not observed.

### SAF-007
- Severity: Medium
- File: `nav.py:383-406`
- Description: `event_skip` and `event_choice` are treated identically by `run_event()`/`handle_event()`. The action label is logged, but the code always just approaches and taps the event template once, without separate logic for skip-vs-choice outcomes.
- Evidence:
  - `nav.py:397-400` routes both `event_choice` and `event_skip` into `run_event(project_dir, action_template, action=kind, ...)`.
  - `nav.py:472-477` `run_event()` only logs `action={action}` and returns `"event handled: {action}"`; it does not implement any distinct skip or choice behavior.
- Suggested fix: Add separate post-open handlers for event choices versus skip actions, driven by explicit templates or action metadata.

### SAF-008
- Severity: Medium
- File: `nav.py:28-33, 245-249`
- Description: `actions.json` loading accepts multiple root keys (`objects`, `actions`, `templates`) but does not validate allowed action values. Unsupported or misspelled actions are silently loaded and later ignored or misprocessed.
- Evidence:
  - `nav.py:245-249` returns all dict items from whichever list key exists, with no schema validation.
  - `nav.py:383-406` later branches only on `loot`, `mob`, `event_choice`, `event_skip`, and `skip`; unknown values merely fall into `Пропущен неизвестный объект`.
- Suggested fix: Validate `actions.json` on load and reject or report entries whose `action` is not in the allowed set `{loot, mob, event_choice, event_skip, skip}`.

### SAF-009
- Severity: Low
- File: `nav.py:111-119`
- Description: `_settings()` only recognizes `click_radius_px`, `approach_timeout_s`, `hold_chunk_ms`, `frame_interval_ms`, and `match_threshold`. If configuration or documentation uses alternative action/movement keys such as `speed` or `movement_speed`, they are ignored silently.
- Evidence:
  - `nav.py:111-119` hardcodes the accepted settings names and does not report unknown aliases.
  - No handling exists for `speed` or `movement_speed` despite the audit criteria highlighting that mismatch risk.
- Suggested fix: Either support documented aliases explicitly or emit warnings when unrecognized navigation/action setting keys are present.
