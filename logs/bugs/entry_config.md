# Entry point, config, logging and packaging audit

Repository: `sarkisaleksandrovich-art/Bot`
Source ref audited: `main`
Log branch: `bot-run-logs`
Date: 2026-11-02

## Summary
- Critical: 1
- High: 5
- Medium: 3
- Low: 1
- Total: 10

## Findings

### CFG-001
- Severity: High
- File: `main.py:130-141`
- Description: `--check` prints steps `[1/8]` and `[2/8]` twice.
- Evidence:
  - `print("[1/8] adb.exe: " + ("OK" if _adb_executable_status(config)[0] else _adb_executable_status(config)[1]))`
  - `print(f"[2/8] serial: {get_path(config, 'adb', 'device_serial')}")`
  - Later in the same function the same two numbered lines are printed again:
    - `print(f"[1/8] adb.exe: {'OK' if adb_exists else adb_path_msg}")`
    - `print(f"[2/8] serial: {get_path(config, 'adb', 'device_serial')}")`
- Suggested fix: Remove the first two eager `print(...)` calls and compute `adb_exists, adb_path_msg` once before emitting the numbered checklist.

### CFG-002
- Severity: Critical
- File: `main.py:104-109`, `main.py:259-271`, `bot/logging_setup.py:8-44`
- Description: Logging is initialized multiple times and `setup_logging()` forcibly removes every root handler on each call. Running `--navigate` first initializes logging in `main()`, then calls `setup_logging(config)` again inside `_run_navigation()`, which tears down existing handlers and recreates them. This is unsafe for embedding, tests, GUI integrations, and any code that already attached root handlers.
- Evidence:
  - Global startup: `config = load_config(args.config); logger = setup_logging(config)` in `main.py:263`.
  - Repeated setup in navigation path: `logger = setup_logging(config)` in `main.py:109`.
  - Destructive handler reset:
    - `root_logger = logging.getLogger()`
    - `for handler in list(root_logger.handlers):`
    - `    root_logger.removeHandler(handler)`
    - `    handler.close()`
- Suggested fix: Make logging setup idempotent. Configure once in `main()`, pass the logger into `_run_navigation()`, and only add handlers if an equivalent application-owned handler is missing instead of clearing the entire root logger.

### CFG-003
- Severity: High
- File: `main.py:113-117`
- Description: `_run_navigation()` creates heavy objects (`ScreenshotProvider`, `VisionEngine`) that are never used. This needlessly imports OpenCV/vision dependencies and can make `--navigate` fail even when navigation itself only needs ADB and `nav.go_to_dungeon`.
- Evidence:
  - `objects = _make_core_objects(config, logger, "base")`
  - Only `adb = objects["adb"]` is later used; `screenshot` and `vision` are assigned but never referenced for navigation output.
- Suggested fix: Create only the ADB/logger objects needed for navigation, or pass dependencies required by `nav.go_to_dungeon` explicitly.

### CFG-004
- Severity: High
- File: `config.yaml:99-110`, `bot/config.py:282-288`
- Description: The shipped config fails validation because required `safety` keys do not match the actual YAML keys. Validator requires `block_shop`, `block_ads`, `block_pvp`, `stop_on_danger`, but the file also contains unrelated `allow_ads`, `allow_purchase`, `allow_premium_currency`, `allow_pvp`; any missing required key would hard-fail startup, and the schema does not validate or reject the extra incompatible safety flags.
- Evidence:
  - Validator requires: `_require_bool(data, "safety", "block_shop")`, `_require_bool(..., "block_ads")`, `_require_bool(..., "block_pvp")`, `_require_bool(..., "stop_on_danger")`.
  - YAML additionally defines unused contradictory knobs: `allow_ads`, `allow_purchase`, `allow_premium_currency`, `allow_pvp`.
- Suggested fix: Define one authoritative safety schema, validate all supported keys, and remove/rename stale fields so config semantics are unambiguous.

### CFG-005
- Severity: High
- File: `bot/profiles.py:321-366`, `config.yaml:57-91`
- Description: The profile contract enforces exactly 7 profiles, but config validation in `bot/config.py` does not validate `profiles.items` at all. Invalid configs therefore pass `load_config()` and only fail later when `ProfileManager` is instantiated.
- Evidence:
  - Runtime-only enforcement: `if len(entries) != 7: raise ProfileError("profiles.items must contain exactly 7 profiles")`.
  - No matching checks exist in `validate_config()` for `profiles.items`, item shape, IDs, uniqueness, or `loc1`-`loc7` membership.
- Suggested fix: Move profile list validation into `validate_config()` so malformed profile configs fail early at config load, and also verify the exact expected IDs `loc1`..`loc7` if that is the product requirement.

### CFG-006
- Severity: High
- File: `bot/knowledge.py:131-145`, `config.yaml:40-55`, `bot/profiles.py:96-126`
- Description: Knowledge/profile path layout is inconsistent. `KnowledgeBase` stores `rules.json` and `features.json` under `profiles.root_dir/<profile>/`, while templates are stored under `knowledge.templates_dir/<profile>/`. Meanwhile `ProfileManager.ensure_profile()` also creates an additional nested tree under `profiles.root_dir/knowledge/<profile>/` with `location_path.json`, `floor_selection.json`, and `rules.json`. This creates three knowledge locations with overlapping semantics and makes missing files likely.
- Evidence:
  - `KnowledgeBase` paths:
    - `self._profile_dir = profiles_root / self.profile`
    - `self._rules_path = self._profile_dir / self._rules_filename`
    - `self._features_path = self._profile_dir / self._features_filename`
  - Templates path separate from profile dir:
    - `templates_root_cfg = Path(str(self._knowledge_cfg.get("templates_dir", "./data/templates")))`
    - `self._profile_templates_dir = ...`
  - Additional parallel tree in `ProfileManager.ensure_profile()`:
    - `knowledge_dir = Path(self.knowledge_dir(profile))`
    - `self._ensure_json_file(knowledge_dir / "rules.json", ...)`
    - `self._ensure_json_file(knowledge_dir / "location_path.json", ...)`
- Suggested fix: Choose a single canonical on-disk layout for profile data, templates, and per-location knowledge, then make all components resolve paths from that shared contract.

### CFG-007
- Severity: Medium
- File: `main.py:223-230`, `bot/knowledge.py:131-145`, `bot/profiles.py:96-126`
- Description: Reset/backup only preserves profile-root `rules.json` and `features.json`, but does not back up or reset the additional `profiles_root/knowledge/<profile>/location_path.json` and `floor_selection.json` files created by `ProfileManager.ensure_profile()`. Users can lose or retain stale navigation teaching unexpectedly.
- Evidence:
  - Backup copies only filenames from `app.rules_file` and `app.features_file`, plus `templates/`.
  - `ProfileManager.ensure_profile()` additionally creates `knowledge/<profile>/location_path.json` and `knowledge/<profile>/floor_selection.json` under the profiles root, yet `_backup_profile_knowledge()` ignores them.
- Suggested fix: Extend backup/reset logic to include every persisted knowledge artifact for the selected profile, including location path and floor selection data.

### CFG-008
- Severity: Medium
- File: `pyproject.toml:1-28`, `requirements.txt:1-6`
- Description: Dependency declarations are split and inconsistent. `pyproject.toml` declares only `crewai` and `exa-py`, while the actual entry point imports YAML, OpenCV, NumPy, Pillow, keyboard, pynput and many local modules. Installing from PEP 621 metadata alone will produce a broken CLI environment.
- Evidence:
  - `pyproject.toml` dependencies: only `crewai[...]` and `exa-py`.
  - Runtime imports from audited entry/config stack require at least `yaml` (`PyYAML`), `cv2` (`opencv-python`), `numpy`, and likely GUI/input packages from `requirements.txt`.
- Suggested fix: Make one source of truth for runtime dependencies. Add all CLI/runtime packages to `pyproject.toml` (or generate requirements from it) and keep `requirements.txt` synchronized or clearly dev-only.

### CFG-009
- Severity: Medium
- File: `main.py:274-278`, `pyproject.toml:11-16`
- Description: Packaged script entry points target `src/ldplayer_dungeon_explorer_bot_with_audit/main.py`, but the audited operational CLI is root `main.py`. Installing the package will not expose the real bot CLI modes (`--check`, `--teach`, `--capture`, etc.).
- Evidence:
  - Real CLI dispatch lives in repository root `main.py` with parser and modes.
  - `pyproject.toml` maps scripts to `ldplayer_dungeon_explorer_bot_with_audit.main:run`, `:train`, `:replay`, etc., not to root `main.py`.
- Suggested fix: Either package the actual bot CLI module and point `[project.scripts]` to it, or clearly separate the crewAI sample package from the executable bot repository.

### CFG-010
- Severity: Low
- File: `config.yaml:27`, `main.py:160-162`
- Description: Default vision threshold is configured as `0.88`, but the audit requirement expects `0.78`; if the rest of the system or trained templates assume `0.78`, `--check` and runtime matching will behave more strictly than intended.
- Evidence:
  - `config.yaml`: `vision.default_match_threshold: 0.88`
  - `main.py`: `vision.find_all(... threshold=config.default_match_threshold)`
- Suggested fix: Align the configured default threshold with the intended operating threshold and document/validate acceptable ranges per environment.

## Notes
- I did not edit source files.
- `logs/REPO_MAP.md` was referenced from task context, but it is not present on `main`; the audit used the provided map and the source files on `main`.
