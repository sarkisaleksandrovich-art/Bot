from __future__ import annotations

import argparse
import builtins
import json
import os
import shutil
import subprocess
import sys
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
    raise RuntimeError(
        "Не найден модуль bot.screenshot. Установите/восстановите bot/screenshot.py."
    ) from exc


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "CLI для управления LDPlayer. "
            "Подключение к Android: --teach."
        ),
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--teach", action="store_true")
    parser.add_argument("--teach-only", dest="teach_only", action="store_true")
    parser.add_argument("--profile", help="Имя профиля, например loc1")
    parser.add_argument("--floor-from", dest="floor_from", type=int)
    parser.add_argument("--floor-to", dest="floor_to", type=int)
    parser.add_argument("--list-profiles", dest="list_profiles", action="store_true")
    parser.add_argument("--reset-knowledge", dest="reset_knowledge", action="store_true")
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Запустить графический интерфейс навигации и фарма",
    )
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


def _resolve_profile_and_range(
    config: BotConfig,
    logger: Any,
    cli_profile: str | None,
    cli_floor_from: int | None,
    cli_floor_to: int | None,
) -> tuple[ProfileManager, str, int, int]:
    profile_manager = ProfileManager(config, logger)
    selected_profile = cli_profile or _safe_get_default_profile(config)
    try:
        profile_manager.ensure_profile(selected_profile)
    except ProfileError as exc:
        raise ProfileError(f"Не удалось выбрать профиль '{selected_profile}'. {exc}") from exc

    floor_from = cli_floor_from if cli_floor_from is not None else 1
    if floor_from < 1:
        raise ProfileError("--floor-from должен быть >= 1")

    if cli_floor_to is not None:
        floor_to = cli_floor_to
    else:
        try:
            floor_to = int(get_path(config, "game", "floors_per_level"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProfileError("Не найден game.floors_per_level в config.yaml") from exc

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
        raise RuntimeError(
            "Не удалось импортировать bot.vision.VisionEngine. "
            "Проверьте зависимости и исходники."
        ) from exc
    try:
        from bot.safety import SafetyManager
    except Exception as exc:
        raise RuntimeError(
            "Не удалось импортировать bot.safety.SafetyManager."
        ) from exc

    vision = VisionEngine(config, logger)
    knowledge = KnowledgeBase(config, profile, logger)
    safety = SafetyManager(config, logger)
    teacher_ui = TeacherUI(config, logger)
    return {
        "adb": adb,
        "screenshot": screenshot,
        "vision": vision,
        "knowledge": knowledge,
        "safety": safety,
        "teacher_ui": teacher_ui,
    }


def _adb_executable_status(config: BotConfig | None = None) -> tuple[bool, str]:
    candidates: list[str] = []
    if config is not None:
        configured = get_path(config, "adb", "adb_path")
        if configured:
            candidates.append(str(configured))
    for name in ("adb.exe", "adb"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for base in (r"C:\LDPlayer\LDPlayer9", r"C:\LDPlayer\LDPlayer4.0", r"C:\Program Files\LDPlayer\LDPlayer9"):
        candidates.append(base + r"\adb.exe")
    for path in candidates:
        if Path(path).is_file():
            return True, path
    return False, "adb.exe не найден"


def _adb_version_text() -> str:
    for candidate in ("adb.exe", "adb"):
        executable = shutil.which(candidate)
        if not executable:
            continue
        try:
            result = subprocess.run(
                [executable, "version"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10, check=False,
            )
        except OSError:
            continue
        text = (result.stdout or "").strip() or (result.stderr or "").strip()
        if text:
            return text.splitlines()[0]
    return "adb.exe не найден"


def _android_version(adb: ADBController) -> str:
    for command in ("getprop ro.build.version.release", "getprop ro.system.build.version.release"):
        try:
            value = adb.shell(command).strip()
        except ADBCommandError:
            continue
        if value:
            return value
    return "неизвестно"


def _count_templates(knowledge: KnowledgeBase) -> int:
    try:
        return len(knowledge.get_templates(enabled_only=False))
    except Exception:
        return -1


def _knowledge_paths(config: BotConfig, profile: str) -> tuple[Path, Path, Path]:
    base_dir = config.base_dir
    profiles_root = Path(str(get_path(config, "profiles", "root_dir")))
    if not profiles_root.is_absolute():
        profiles_root = (base_dir / profiles_root).resolve()
    profile_dir = (profiles_root / profile).resolve()
    templates_dir = Path(str(get_path(config, "knowledge", "templates_dir")))
    if not templates_dir.is_absolute():
        templates_dir = (base_dir / templates_dir / profile).resolve()
    return profiles_root, profile_dir, templates_dir


def _tap_match_center(adb: ADBController, match: Any) -> tuple[int, int]:
    x1, y1, x2, y2 = match.rect
    x = (x1 + x2) // 2
    y = (y1 + y2) // 2
    adb.tap(x, y)
    return x, y


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
        adb.connect()
        adb_ok = True
    except ADBConnectionError as exc:
        errors.append(f"ADB подключение: {exc}")
    frame = None
    if adb_ok:
        try:
            print(f"[3/8] Android: {_android_version(adb)}")
            width, height = adb.screen_size()
            print(f"[4/8] размер экрана: {width}x{height}")
            frame = screenshot.capture()
            shot_ok = True
            print("[5/8] скриншот: OK")
        except (ADBCommandError, ScreenshotError) as exc:
            errors.append(f"скриншот: {exc}")
            print("[5/8] скриншот: ошибка")
    else:
        print("[3/8] Android: пропуск")
        print("[4/8] размер экрана: пропуск")
        print("[5/8] скриншот: пропуск")
    try:
        profiles = ProfileManager(config, logger).list_profiles()
        _, profile_dir, profile_templates_dir = _knowledge_paths(config, profile)
        print(f"[6/8] knowledge/profile: {profile_dir}")
        print(f"      templates/{profile}: {profile_templates_dir}")
        print(f"[7/8] профили: {', '.join(profiles) if profiles else 'не найдены'}")
        templates = knowledge.get_templates(enabled_only=False)
        knowledge_ok = True
        print(f"[8/8] templates loaded: {len(templates)}")
    except (ProfileError, KnowledgeError, KeyError) as exc:
        errors.append(f"knowledge: {exc}")
        print("[8/8] templates loaded: ошибка")
    if shot_ok and knowledge_ok and frame is not None:
        try:
            matches = vision.find_all(frame=frame, templates=templates, threshold=config.default_match_threshold)
            vision_ok = True
            print(f"vision matches: {len(matches)}")
        except Exception as exc:
            errors.append(f"vision: {exc}")
    if adb_exists and adb_ok and shot_ok and knowledge_ok and vision_ok:
        print("Итог: OK")
        return 0
    print("Итог: есть ошибки")
    for item in errors:
        print(f"- {item}")
    return 2 if (adb_exists and not adb_ok) or not adb_exists else 1


def _run_capture(config: BotConfig, logger: Any, profile: str) -> int:
    objects = _make_core_objects(config, logger, profile)
    adb = objects["adb"]
    screenshot = objects["screenshot"]
    adb.connect()
    frame = screenshot.capture()
    screenshots_dir = (config.base_dir / "screenshots").resolve()
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    output_path = screenshots_dir / f"capture_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.png"
    screenshot.save(frame, str(output_path))
    print(str(output_path))
    return 0


def _run_list_profiles(config: BotConfig, logger: Any) -> int:
    profile_manager = ProfileManager(config, logger)
    profiles = profile_manager.list_profiles()
    if not profiles:
        print("Профили не найдены.")
        return 0
    print("Профили:")
    for profile in profiles:
        try:
            profile_manager.ensure_profile(profile)
            knowledge = KnowledgeBase(config, profile, logger)
            count = _count_templates(knowledge)
            count_text = str(count) if count >= 0 else "ошибка"
            print(f"- {profile}: шаблонов {count_text}")
        except Exception as exc:
            print(f"- {profile}: ошибка загрузки ({exc})")
    return 0


def _backup_profile_knowledge(config: BotConfig, profile: str) -> Path:
    base_dir = config.base_dir
    profiles_root = Path(str(get_path(config, "profiles", "root_dir")))
    if not profiles_root.is_absolute():
        profiles_root = (base_dir / profiles_root).resolve()
    profile_dir = (profiles_root / profile).resolve()
    backup_root = (base_dir / "backups").resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = backup_root / f"knowledge_{profile}_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    app_rules = str(get_path(config, "app", "rules_file"))
    app_features = str(get_path(config, "app", "features_file"))
    for file_name in (app_rules, app_features):
        source = profile_dir / file_name
        if source.exists():
            shutil.copy2(source, backup_dir / file_name)
    templates_dir = profile_dir / "templates"
    if templates_dir.exists() and templates_dir.is_dir():
        shutil.copytree(templates_dir, backup_dir / "templates", dirs_exist_ok=True)
    return backup_dir


def _run_reset_knowledge(config: BotConfig, logger: Any, profile: str) -> int:
    print(f"Сброс знаний профиля '{profile}'.")
    print("rules.json и features.json будут заменены. Продолжить? (YES/NO): ", end="")
    answer = builtins.input().strip()
    if answer != "YES":
        print("Отменено.")
        return 0
    profile_manager = ProfileManager(config, logger)
    profile_manager.ensure_profile(profile)
    backup_dir = _backup_profile_knowledge(config, profile)
    knowledge = KnowledgeBase(config, profile, logger)
    knowledge.reset()
    print(f"Профиль '{profile}' сброшен.")
    print(f"Резервная копия: {backup_dir}")
    return 0


def _run_teach(config: BotConfig, logger: Any, profile: str, floor_from: int, floor_to: int) -> int:
    objects = _make_core_objects(config, logger, profile)
    adb = objects["adb"]
    screenshot = objects["screenshot"]
    vision = objects["vision"]
    knowledge = objects["knowledge"]
    safety = objects["safety"]
    teacher_ui = objects["teacher_ui"]
    adb.connect()
    explorer = DungeonExplorer(
        adb=adb, screenshot=screenshot, vision=vision, knowledge=knowledge,
        safety=safety, teacher_ui=teacher_ui, config=config, profile=profile, logger=logger,
    )
    if not hasattr(explorer, "run_explore"):
        raise ExplorerError("DungeonExplorer.run_explore(...) не найден.")
    results = explorer.run_explore(floor_from=floor_from, floor_to=floor_to)
    print(f"Исследование завершено. Обработано этажей: {len(results)}")
    return 0


def _run_teach_only(config: BotConfig, logger: Any, profile: str) -> int:
    objects = _make_core_objects(config, logger, profile)
    adb = objects["adb"]
    screenshot = objects["screenshot"]
    knowledge = objects["knowledge"]
    teacher_ui = objects["teacher_ui"]
    adb.connect()
    if not hasattr(teacher_ui, "teach_only"):
        raise TeacherUIError("TeacherUI.teach_only(adb, screenshot, knowledge) не найден.")
    added = teacher_ui.teach_only(adb, screenshot, knowledge)
    print(f"Добавлено шаблонов: {added}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        logger = setup_logging(config)
    except FileNotFoundError:
        _print_error(f"Не найден файл конфигурации: {args.config}. Укажите --config.")
        return 1
    except ConfigError as exc:
        _print_error(f"Ошибка конфигурации: {exc}")
        return 1
    except OSError as exc:
        _print_error(f"Ошибка инициализации логирования: {exc}")
        return 1

    try:
        profile_manager, profile, floor_from, floor_to = _resolve_profile_and_range(
            config=config, logger=logger, cli_profile=args.profile,
            cli_floor_from=args.floor_from, cli_floor_to=args.floor_to,
        )
        del profile_manager
    except ProfileError as exc:
        _print_error(str(exc))
        return 1

    mode_count = sum([
        args.check, args.capture, args.teach_only, args.list_profiles,
        args.reset_knowledge, args.gui, args.teach,
    ])
    if mode_count > 1:
        _print_error(
            "Укажите только один режим: --check, --capture, --teach, "
            "--teach-only, --list-profiles, --reset-knowledge или --gui."
        )
        return 1

    try:
        if args.gui:
            from gui import launch_gui
            return launch_gui(profile=args.profile)
        if args.list_profiles:
            return _run_list_profiles(config, logger)
        if args.reset_knowledge:
            return _run_reset_knowledge(config, logger, profile)
        if args.check:
            return _run_check(config, logger, profile)
        if args.capture:
            return _run_capture(config, logger, profile)
        if args.teach_only:
            return _run_teach_only(config, logger, profile)
        return _run_teach(config, logger, profile, floor_from, floor_to)
    except (ADBConnectionError, ADBCommandError, ADBError, ScreenshotError) as exc:
        _print_error(f"ADB/устройство: {exc}")
        return 2
    except Exception as exc:
        if exc.__class__.__name__ == "DangerDetectedError":
            _print_error(f"Обнаружен danger: {exc}")
            return 3
        if isinstance(exc, (TeacherUIError, LabelingCancelledError)):
            _print_error(f"Обучение отменено/ошибка: {exc}")
            return 4
        if isinstance(exc, (ExplorerError, KnowledgeError)):
            _print_error(f"Ошибка исследования: {exc}")
            return 5
        _print_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
