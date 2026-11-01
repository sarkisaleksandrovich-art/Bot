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
        "Не удалось импортировать bot.screenshot. В репозитории отсутствует файл bot/screenshot.py. "
        "Проверьте, что модуль добавлен в проект или восстановите этот файл."
    ) from exc


def build_arg_parser() -> argparse.ArgumentParser:
    """
    Создаёт парсер CLI с обязательной поддержкой:
      --config
      --check
      --capture
      --teach
      --teach-only
      --profile
      --floor-from
      --floor-to
      --list-profiles
      --reset-knowledge

    :return: ArgumentParser
    """
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "CLI обучаемого бота-исследователя для LDPlayer. "
            "По умолчанию запускает режим --teach."
        ),
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Путь к config.yaml (по умолчанию: config.yaml)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Проверить конфиг, профиль, ADB, скриншот и знания без нажатий в игре",
    )
    parser.add_argument(
        "--capture",
        action="store_true",
        help="Сделать один скриншот, сохранить в screenshots/ и вывести путь",
    )
    parser.add_argument(
        "--teach",
        action="store_true",
        help="Режим исследования с окном обучения и паузой по Пробелу",
    )
    parser.add_argument(
        "--teach-only",
        dest="teach_only",
        action="store_true",
        help="Только разметка: бот сам ничего не нажимает, пользователь играет сам",
    )
    parser.add_argument(
        "--profile",
        help="Идентификатор профиля, например loc1",
    )
    parser.add_argument(
        "--floor-from",
        dest="floor_from",
        type=int,
        help="Стартовый этаж",
    )
    parser.add_argument(
        "--floor-to",
        dest="floor_to",
        type=int,
        help="Конечный этаж включительно",
    )
    parser.add_argument(
        "--list-profiles",
        dest="list_profiles",
        action="store_true",
        help="Показать профили и число выученных эталонов",
    )
    parser.add_argument(
        "--reset-knowledge",
        dest="reset_knowledge",
        action="store_true",
        help="Сбросить rules.json и features.json только выбранного профиля",
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
        raise ProfileError(
            f"Не удалось подготовить профиль '{selected_profile}'. {exc}"
        ) from exc

    floor_from = cli_floor_from if cli_floor_from is not None else 1
    if floor_from < 1:
        raise ProfileError("--floor-from должен быть целым числом >= 1")

    if cli_floor_to is not None:
        floor_to = cli_floor_to
    else:
        try:
            floor_to = int(get_path(config, "game", "floors_per_level"))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProfileError(
                "Не удалось определить game.floors_per_level из конфига"
            ) from exc

    if floor_to < 1:
        raise ProfileError("--floor-to должен быть целым числом >= 1")
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
            "Без него режимы --teach и --check работать не полностью."
        ) from exc

    try:
        from bot.safety import SafetyManager
    except Exception as exc:
        raise RuntimeError(
            "Не удалось импортировать bot.safety.SafetyManager. "
            "Без него режим --teach нельзя запустить безопасно."
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


def _adb_executable_status(config=None) -> tuple[bool, str]:
    candidates = []
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
    return False, "adb.exe не найден (ни в config.yaml, ни в PATH, ни в папках LDPlayer)"

def _adb_version_text() -> str:
    for candidate in ("adb.exe", "adb"):
        executable = shutil.which(candidate)
        if not executable:
            continue
        try:
            result = subprocess.run(
                [executable, "version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except OSError:
            continue
        text = (result.stdout or "").strip() or (result.stderr or "").strip()
        if text:
            return text.splitlines()[0]
    return "не удалось определить"


def _android_version(adb: ADBController) -> str:
    commands = [
        "getprop ro.build.version.release",
        "getprop ro.system.build.version.release",
    ]
    for command in commands:
        try:
            value = adb.shell(command).strip()
        except ADBCommandError:
            continue
        if value:
            return value
    return "не удалось определить"


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
        templates_dir = (base_dir / templates_dir).resolve()
    profile_templates_dir = (templates_dir / profile).resolve()
    return profiles_root, profile_dir, profile_templates_dir


def _tap_match_center(adb: ADBController, match: Any) -> tuple[int, int]:
    x1, y1, x2, y2 = match.rect
    x = (x1 + x2) // 2
    y = (y1 + y2) // 2
    adb.tap(x, y)
    return x, y


def _run_check(config: BotConfig, logger: Any, profile: str) -> int:
    print("Проверка окружения...")
    profile_manager = ProfileManager(config, logger)
    profile_manager.ensure_profile(profile)
    adb_ok = False
    shot_ok = False
    knowledge_ok = False
    vision_ok = False
    errors: list[str] = []

    adb_exists, adb_path_msg = _adb_executable_status(config)
    print(f"[1/8] adb.exe: {'OK' if adb_exists else 'ОШИБКА'} - {adb_path_msg}")
    if not adb_exists:
        errors.append("adb.exe не найден. Добавьте папку с adb в PATH или установите Android Platform Tools / LDPlayer adb.")

    print(f"[2/8] serial: {get_path(config, 'adb', 'device_serial')}")

    objects = _make_core_objects(config, logger, profile)
    adb = objects["adb"]
    screenshot = objects["screenshot"]
    vision = objects["vision"]
    knowledge = objects["knowledge"]

    try:
        adb.connect()
        adb_ok = True
    except ADBConnectionError as exc:
        errors.append(f"Не удалось подключиться к ADB-устройству: {exc}")

    frame = None
    if adb_ok:
        try:
            android_version = _android_version(adb)
            print(f"[3/8] версия Android: {android_version}")
        except ADBError as exc:
            errors.append(f"Не удалось получить версию Android: {exc}")
            print("[3/8] версия Android: ОШИБКА")

        try:
            width, height = adb.screen_size()
            print(f"[4/8] размер экрана: {width}x{height}")
        except ADBCommandError as exc:
            errors.append(f"Не удалось получить размер экрана: {exc}")
            print("[4/8] размер экрана: ОШИБКА")

        try:
            frame = screenshot.capture()
            shot_ok = True
            print("[5/8] скриншот: OK")
        except ScreenshotError as exc:
            errors.append(f"Не удалось сделать скриншот: {exc}")
            print("[5/8] скриншот: ОШИБКА")
    else:
        print("[3/8] версия Android: пропущено, потому что ADB не подключён")
        print("[4/8] размер экрана: пропущено, потому что ADB не подключён")
        print("[5/8] скриншот: пропущено, потому что ADB не подключён")

    try:
        profiles = profile_manager.list_profiles()
        _, profile_dir, profile_templates_dir = _knowledge_paths(config, profile)
        print(f"[6/8] папка knowledge/profile: {profile_dir}")
        print(f"      templates/{profile}: {profile_templates_dir}")
        print(f"[7/8] профили: {', '.join(profiles) if profiles else '(нет профилей)'}")
        templates = knowledge.get_templates(enabled_only=False)
        knowledge_ok = True
        print(f"[8/8] templates loaded: {len(templates)}")
    except (ProfileError, KnowledgeError, KeyError) as exc:
        templates = []
        errors.append(f"Не удалось прочитать знания или список профилей: {exc}")
        print("[8/8] templates loaded: ОШИБКА")

    if shot_ok and knowledge_ok:
        try:
            matches = vision.find_all(
                frame=frame,
                templates=templates,
                threshold=config.default_match_threshold,
            )
            vision_ok = True
            print(f"Найдено совпадений: {len(matches)}")
            for match in matches:
                print(
                    f"- {match.template_id}: score={match.score:.3f}, rect={match.rect}"
                )
        except Exception as exc:
            errors.append(f"Не удалось выполнить поиск шаблонов: {exc}")

    if adb_exists and adb_ok and shot_ok and knowledge_ok and vision_ok:
        print("Итог: OK")
        return 0

    print("Итог: ОШИБКА")
    for item in errors:
        print(f"- {item}")
    return 2 if (adb_exists and (not adb_ok or not shot_ok)) or not adb_exists else 1


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
            count_text = str(count) if count >= 0 else "не удалось посчитать"
            print(f"- {profile}: выученных эталонов {count_text}")
        except Exception as exc:
            print(f"- {profile}: ошибка чтения профиля ({exc})")
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
    print(f"ВНИМАНИЕ: будет сброшено только знание профиля '{profile}'.")
    print("Это очистит rules.json и features.json этого профиля.")
    answer = builtins.input("Введите YES для подтверждения: ").strip()
    if answer != "YES":
        print("Сброс отменён.")
        return 0

    profile_manager = ProfileManager(config, logger)
    profile_manager.ensure_profile(profile)
    backup_dir = _backup_profile_knowledge(config, profile)
    knowledge = KnowledgeBase(config, profile, logger)
    knowledge.reset()
    print(f"Знания профиля '{profile}' сброшены.")
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
        adb=adb,
        screenshot=screenshot,
        vision=vision,
        knowledge=knowledge,
        safety=safety,
        teacher_ui=teacher_ui,
        config=config,
        profile=profile,
        logger=logger,
    )

    if not hasattr(explorer, "run_explore"):
        raise ExplorerError(
            "В реальном коде нет DungeonExplorer.run_explore(...). "
            "Ожидался метод, но он отсутствует."
        )

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
        raise TeacherUIError(
            "В реальном коде нет TeacherUI.teach_only(adb, screenshot, knowledge)."
        )

    added = teacher_ui.teach_only(adb, screenshot, knowledge)
    print(f"Добавлено эталонов: {added}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """
    Точка входа приложения.

    Код возврата:
      0 - успех
      1 - ошибка конфигурации/инициализации
      2 - ошибка ADB/скриншота
      3 - остановка по danger
      4 - ошибка обучения/разметки
      5 - ошибка исследования

    :param argv: аргументы CLI или None
    :return: int
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        logger = setup_logging(config)
    except FileNotFoundError:
        _print_error(
            f"Файл конфига не найден: {args.config}. Укажите путь через --config."
        )
        return 1
    except ConfigError as exc:
        _print_error(f"Конфиг повреждён или неполон: {exc}")
        return 1
    except OSError as exc:
        _print_error(f"Не удалось подготовить логирование: {exc}")
        return 1

    try:
        profile_manager, profile, floor_from, floor_to = _resolve_profile_and_range(
            config=config,
            logger=logger,
            cli_profile=args.profile,
            cli_floor_from=args.floor_from,
            cli_floor_to=args.floor_to,
        )
        del profile_manager
    except ProfileError as exc:
        _print_error(str(exc))
        return 1

    mode_count = sum(
        1
        for flag in [args.check, args.capture, args.teach_only, args.list_profiles, args.reset_knowledge]
        if flag
    )
    if args.teach:
        mode_count += 1
    if mode_count > 1:
        _print_error(
            "Укажите только один основной режим: --check, --capture, --teach, --teach-only, --list-profiles или --reset-knowledge."
        )
        return 1

    try:
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
        _print_error(f"Проблема с ADB или скриншотом: {exc}")
        return 2
    except Exception as exc:
        if exc.__class__.__name__ == "DangerDetectedError":
            _print_error(f"Остановка по danger: {exc}")
            return 3
        if isinstance(exc, (TeacherUIError, LabelingCancelledError)):
            _print_error(f"Ошибка обучения/разметки: {exc}")
            return 4
        if isinstance(exc, (ExplorerError, KnowledgeError)):
            _print_error(f"Ошибка исследования: {exc}")
            return 5
        _print_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
