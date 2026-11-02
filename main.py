from __future__ import annotations

import argparse
import builtins
import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import BotConfig, ConfigError, get_path, load_config
from bot.logging_setup import setup_logging
from bot.adb_controller import ADBCommandError, ADBConnectionError, ADBController, ADBError
from bot.knowledge import KnowledgeBase, KnowledgeError
from bot.teacher_ui import LabelingCancelledError, TeacherUI, TeacherUIError
from bot.profiles import ProfileError, ProfileManager
from bot.explorer import DungeonExplorer, ExplorerError

try:
    from bot.screenshot import ScreenshotError, ScreenshotProvider
except ModuleNotFoundError as exc:
    raise RuntimeError("Модуль bot.screenshot отсутствует. Проверьте наличие bot/screenshot.py.") from exc


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description=("CLI для LDPlayer. " "Обучение Android: --teach."))
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--teach", action="store_true")
    parser.add_argument("--teach-only", dest="teach_only", action="store_true")
    parser.add_argument("--profile", help="Имя профиля, по умолчанию loc1")
    parser.add_argument("--floor-from", dest="floor_from", type=int)
    parser.add_argument("--floor-to", dest="floor_to", type=int)
    parser.add_argument("--list-profiles", dest="list_profiles", action="store_true")
    parser.add_argument("--reset-knowledge", dest="reset_knowledge", action="store_true")
    parser.add_argument("--gui", action="store_true", help="Запустить GUI с выбором профиля и режимов")
    parser.add_argument("--navigate", action="store_true", help="Navigate from current screen to farm start")
    parser.add_argument("--location", type=str, help="Location for --navigate")
    parser.add_argument("--floor", type=int, help="Floor for --navigate")
    return parser


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _print_error(message: str) -> None:
    print(f"Ошибка: {message}", file=sys.stderr)


def _safe_get_default_profile(config: BotConfig) -> str:
    try:
        value = get_path(config, "profiles", "default_profile")
    except KeyError:
        return "default"
    return str(value)


def _resolve_profile_and_range(config: BotConfig, logger: Any, cli_profile: str | None, cli_floor_from: int | None, cli_floor_to: int | None) -> tuple[ProfileManager, str, int, int]:
    profile_manager = ProfileManager(config, logger)
    selected_profile = cli_profile or _safe_get_default_profile(config)
    try:
        profile_manager.ensure_profile(selected_profile)
    except ProfileError as exc:
        raise ProfileError(f"Профиль '{selected_profile}' не найден или повреждён: {exc}") from exc
    floor_from = cli_floor_from if cli_floor_from is not None else 1
    if floor_from < 1:
        raise ProfileError("--floor-from должен быть >= 1")
    if cli_floor_to is not None:
        floor_to = cli_floor_to
    else:
        try:
            floor_to = int(get_path(config, "game", "floors_per_level"))
        except (KeyError, TypeError, ValueError):
            raise ProfileError("Не удалось прочитать game.floors_per_level из config.yaml")
    if floor_to < 1:
        raise ProfileError("--floor-to должен быть >= 1")
    if floor_from > floor_to:
        raise ProfileError("--floor-from не может быть больше --floor-to")
    return profile_manager, selected_profile, floor_from, floor_to


def _make_core_objects(config: BotConfig, logger: Any, profile: str) -> dict[str, Any]:
    adb = ADBController(config, logger)
    screenshot = ScreenshotProvider(adb, config, logger)
    try:
        from bot.vision import VisionEngine
    except Exception as exc:
        raise RuntimeError("Не удалось импортировать bot.vision.VisionEngine. Проверьте OpenCV и bot/vision.py.") from exc
    try:
        from bot.safety import SafetyManager
    except Exception as exc:
        raise RuntimeError("Не удалось импортировать bot.safety.SafetyManager.") from exc
    vision = VisionEngine(config, logger)
    knowledge = KnowledgeBase(config, profile, logger)
    safety = SafetyManager(config, logger)
    teacher_ui = TeacherUI(config, logger)
    return {"adb": adb, "screenshot": screenshot, "vision": vision, "knowledge": knowledge, "safety": safety, "teacher_ui": teacher_ui}


def _run_navigation(config: BotConfig, location_id: str, floor_number: int) -> int:
    if not location_id or floor_number is None or floor_number < 1:
        return 2
    try:
        logger = setup_logging(config)
        objects = _make_core_objects(config, logger, "base")
        adb = objects["adb"]
        screenshot = objects["screenshot"]
        vision = objects["vision"]
        from nav import go_to_dungeon
        adb.connect()
        stop_event = threading.Event()
        ok, reason = go_to_dungeon(
            config.base_dir,
            location_id,
            log=print,
            stop_event=stop_event,
        )
        print(reason)
        return 0 if ok else 1
    except (ADBConnectionError, ADBCommandError, ADBError, ScreenshotError, Exception) as exc:
        _print_error(str(exc))
        return 1


def _run_check(config: BotConfig, logger: Any, profile: str) -> int:
    print("[1/8] adb.exe: " + ("OK" if _adb_executable_status(config)[0] else _adb_executable_status(config)[1]))
    print(f"[2/8] serial: {get_path(config, 'adb', 'device_serial')}")
    objects = _make_core_objects(config, logger, profile)
    adb = objects["adb"]
    screenshot = objects["screenshot"]
    vision = objects["vision"]
    knowledge = objects["knowledge"]
    adb_ok = shot_ok = knowledge_ok = vision_ok = False
    errors: list[str] = []
    adb_exists, adb_path_msg = _adb_executable_status(config)
    print(f"[1/8] adb.exe: {'OK' if adb_exists else adb_path_msg}")
    if not adb_exists:
        errors.append("adb.exe не найден")
    print(f"[2/8] serial: {get_path(config, 'adb', 'device_serial')}")
    try:
        adb.connect(); adb_ok = True
    except ADBConnectionError as exc:
        errors.append(f"ADB: {exc}")
    frame = None
    if adb_ok:
        try:
            print(f"[3/8] Android: {_android_version(adb)}")
            width, height = adb.screen_size(); print(f"[4/8] Размер экрана: {width}x{height}")
            frame = screenshot.capture(); shot_ok = True; print("[5/8] Скриншот: OK")
        except (ADBCommandError, ScreenshotError) as exc:
            errors.append(f"скриншот: {exc}"); print("[5/8] Скриншот: ошибка")
    else:
        print("[3/8] Android: пропущено"); print("[4/8] Размер экрана: пропущено"); print("[5/8] Скриншот: пропущено")
    try:
        templates = knowledge.get_templates(enabled_only=False); knowledge_ok = True; print(f"[6/8] templates loaded: {len(templates)}")
    except (KnowledgeError, KeyError) as exc:
        errors.append(f"knowledge: {exc}"); print("[6/8] templates loaded: ошибка")
    if frame is not None and knowledge_ok:
        try:
            matches = vision.find_all(frame=frame, templates=templates, threshold=config.default_match_threshold); vision_ok = True; print(f"vision matches: {len(matches)}")
        except Exception as exc:
            errors.append(f"vision: {exc}")
    else:
        print("vision matches: пропущено")
    if adb_ok and shot_ok and knowledge_ok and vision_ok:
        print("Готовность: OK"); return 0
    print("Готовность: ошибка")
    for item in errors: print(f"- {item}")
    return 2 if adb_exists and not adb_ok else 1


def _run_capture(config: BotConfig, logger: Any, profile: str) -> int:
    objects = _make_core_objects(config, logger, profile); adb = objects["adb"]; screenshot = objects["screenshot"]
    adb.connect(); frame = screenshot.capture(); screenshots_dir = (config.base_dir / "screenshots").resolve(); screenshots_dir.mkdir(parents=True, exist_ok=True)
    output_path = screenshots_dir / f"capture_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.png"; screenshot.save(frame, str(output_path)); print(str(output_path)); return 0


def _run_list_profiles(config: BotConfig, logger: Any) -> int:
    profile_manager = ProfileManager(config, logger); profiles = profile_manager.list_profiles()
    if not profiles: print("Профили отсутствуют."); return 0
    print("Профили:")
    for profile in profiles:
        try: profile_manager.ensure_profile(profile); count = len(KnowledgeBase(config, profile, logger).get_templates(enabled_only=False)); count_text = str(count) if count >= 0 else "неизвестно"; print(f"- {profile}: шаблонов {count_text}")
        except Exception as exc: print(f"- {profile}: ошибка ({exc})")
    return 0


def _backup_profile_knowledge(config: BotConfig, profile: str) -> Path:
    base_dir = config.base_dir; profiles_root = Path(str(get_path(config, "profiles", "root_dir"))); profiles_root = (base_dir / profiles_root).resolve() if not profiles_root.is_absolute() else profiles_root.resolve(); profile_dir = (profiles_root / profile).resolve(); backup_root = (base_dir / "backups").resolve(); backup_dir = backup_root / f"knowledge_{profile}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"; backup_dir.mkdir(parents=True, exist_ok=True)
    for file_name in (str(get_path(config, "app", "rules_file")), str(get_path(config, "app", "features_file"))):
        source = profile_dir / file_name
        if source.exists(): shutil.copy2(source, backup_dir / Path(file_name).name)
    templates_dir = profile_dir / "templates"
    if templates_dir.exists() and templates_dir.is_dir(): shutil.copytree(templates_dir, backup_dir / "templates", dirs_exist_ok=True)
    return backup_dir


def _run_reset_knowledge(config: BotConfig, logger: Any, profile: str) -> int:
    print(f"Сбросить rules.json и features.json профиля '{profile}'. YES/NO: ", end=""); answer = builtins.input().strip()
    if answer != "YES": print("Отмена."); return 0
    profile_manager = ProfileManager(config, logger); profile_manager.ensure_profile(profile); backup_dir = _backup_profile_knowledge(config, profile); knowledge = KnowledgeBase(config, profile, logger); knowledge.reset(); print(f"Профиль '{profile}' сброшен. Резервная копия: {backup_dir}"); return 0


def _run_teach(config: BotConfig, logger: Any, profile: str, floor_from: int, floor_to: int) -> int:
    objects = _make_core_objects(config, logger, profile); adb = objects["adb"]; screenshot = objects["screenshot"]; vision = objects["vision"]; knowledge = objects["knowledge"]; safety = objects["safety"]; teacher_ui = objects["teacher_ui"]
    adb.connect(); explorer = DungeonExplorer(adb=adb, screenshot=screenshot, vision=vision, knowledge=knowledge, safety=safety, teacher_ui=teacher_ui, config=config, profile=profile, logger=logger)
    if not hasattr(explorer, "run_explore"): raise ExplorerError("DungeonExplorer.run_explore(...) не найден")
    results = explorer.run_explore(floor_from=floor_from, floor_to=floor_to); print(f"Обход завершён: {len(results)}"); return 0


def _run_teach_only(config: BotConfig, logger: Any, profile: str) -> int:
    objects = _make_core_objects(config, logger, profile); adb = objects["adb"]; screenshot = objects["screenshot"]; knowledge = objects["knowledge"]; teacher_ui = objects["teacher_ui"]; adb.connect()
    if not hasattr(teacher_ui, "teach_only"): raise TeacherUIError("TeacherUI.teach_only(...) не найден")
    added = teacher_ui.teach_only(adb, screenshot, knowledge); print(f"Обучение без кликов: {added}"); return 0


def _adb_executable_status(config: BotConfig | None = None) -> tuple[bool, str]:
    candidates: list[str] = []
    if config is not None:
        configured = get_path(config, "adb", "adb_path")
        if configured: candidates.append(str(configured))
    for name in ("adb.exe", "adb"):
        found = shutil.which(name)
        if found: candidates.append(found)
    for base in (r"C:\LDPlayer\LDPlayer9", r"C:\LDPlayer\LDPlayer4.0", r"C:\Program Files\LDPlayer\LDPlayer9"): candidates.append(base + r"\adb.exe")
    for path in candidates:
        if Path(path).is_file(): return True, path
    return False, "adb.exe не найден"


def _adb_version_text() -> str:
    for command in ("adb.exe", "adb"):
        executable = shutil.which(command)
        if not executable: continue
        try:
            result = subprocess.run([executable, "version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False); text = (result.stdout or "").strip() or (result.stderr or "").strip()
            if text: return text.splitlines()[0]
        except OSError: continue
    return "adb.exe не найден"


def _android_version(adb: ADBController) -> str:
    for command in ("getprop ro.build.version.release", "getprop ro.system.build.version.release"):
        try:
            value = adb.shell(command).strip()
        except ADBCommandError:
            continue
        if value: return value
    return "неизвестно"


def _count_templates(knowledge: KnowledgeBase) -> int:
    try: return len(knowledge.get_templates(enabled_only=False))
    except Exception: return -1

def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser(); args = parser.parse_args(argv)
    if args.navigate:
        if not args.location: parser.error("--navigate requires --location")
        if args.floor is None: parser.error("--navigate requires --floor")
        if args.floor < 1: parser.error("--floor must be >= 1")
    try: config = load_config(args.config); logger = setup_logging(config)
    except (FileNotFoundError, ConfigError, OSError) as exc: _print_error(f"Не удалось загрузить конфигурацию {args.config}: {exc}"); return 1
    try: profile_manager, profile, floor_from, floor_to = _resolve_profile_and_range(config=config, logger=logger, cli_profile=args.profile, cli_floor_from=args.floor_from, cli_floor_to=args.floor_to)
    except ProfileError as exc: _print_error(str(exc)); return 1
    mode_count = sum([args.check, args.capture, args.teach_only, args.list_profiles, args.reset_knowledge, args.gui, args.teach, args.navigate])
    if mode_count > 1: _print_error("Выберите только один режим..."); return 1
    try:
        if args.navigate: return _run_navigation(config, args.location, args.floor)
        if args.gui:
            from gui import launch_gui
            return launch_gui(profile=args.profile)
        if args.list_profiles: return _run_list_profiles(config, logger)
        if args.reset_knowledge: return _run_reset_knowledge(config, logger, profile)
        if args.check: return _run_check(config, logger, profile)
        if args.capture: return _run_capture(config, logger, profile)
        if args.teach_only: return _run_teach_only(config, logger, profile)
        return _run_teach(config, logger, profile, floor_from, floor_to)
    except (ADBConnectionError, ADBCommandError, ADBError, ScreenshotError, ProfileError, KnowledgeError, TeacherUIError, LabelingCancelledError, ExplorerError, Exception) as exc: _print_error(str(exc)); return 1
if __name__ == "__main__": raise SystemExit(main())
