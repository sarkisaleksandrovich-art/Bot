from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from bot.adb_controller import ADBController, ADBError, ADBTimeoutError, DeviceNotFoundError
from bot.config import Config, ConfigError, ensure_directories, load_config
from bot.explorer import ExploreResult, ExplorerError, run_explore, run_teach_only
from bot.knowledge import KnowledgeBase, KnowledgeError
from bot.logging_setup import setup_logging
from bot.profiles import ProfileError, get_active_profile_name, list_profiles, load_profile
from bot.safety import SafetyStop, SafetyService
from bot.screenshot import ScreenshotError, ScreenshotService
from bot import teacher_ui
from bot.vision import VisionError, VisionService


def build_arg_parser() -> argparse.ArgumentParser:
    """
    Возвращает настроенный CLI parser.
    """
    parser = argparse.ArgumentParser(
        description=(
            "CLI для обучаемого бота-исследователя. "
            "Если режим не указан, по умолчанию используется --teach."
        )
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Путь к config.yaml (по умолчанию: ./config.yaml)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Проверить конфиг, ADB, профиль и базу знаний",
    )
    parser.add_argument(
        "--capture",
        action="store_true",
        help="Сделать один скриншот и сохранить его в screenshots_dir",
    )
    parser.add_argument(
        "--teach",
        action="store_true",
        help="Режим исследования с окном обучения и паузой по Пробелу",
    )
    parser.add_argument(
        "--teach-only",
        action="store_true",
        help="Только разметка: бот сам ничего не нажимает",
    )
    parser.add_argument(
        "--profile",
        help="Профиль, например loc1",
    )
    parser.add_argument(
        "--floor-from",
        type=int,
        help="Начальный этаж диапазона",
    )
    parser.add_argument(
        "--floor-to",
        type=int,
        help="Конечный этаж диапазона",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="Показать профили и число выученных эталонов",
    )
    parser.add_argument(
        "--reset-knowledge",
        action="store_true",
        help="Сбросить знания выбранного профиля после подтверждения",
    )
    return parser


def _print_error(message: str) -> None:
    print(f"Ошибка: {message}", file=sys.stderr)


def _resolve_path(config: Config, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (config.path.parent / path).resolve()
    return path


def _apply_cli_profile(config: Config, cli_profile: str | None) -> str:
    profile_name = get_active_profile_name(config, cli_profile)
    config.raw.setdefault("profiles", {})
    config.raw["profiles"]["active"] = profile_name
    return profile_name


def _build_services(config: Config) -> tuple[ADBController, ScreenshotService, VisionService, SafetyService]:
    adb = ADBController(
        adb_path=str(config.get("adb.adb_path")),
        serial=str(config.get("adb.serial") or "").strip() or None,
        exec_timeout_s=float(config.get("adb.exec_timeout_s", 15.0)),
    )
    screenshot = ScreenshotService(adb)
    screenshot.set_screenshots_dir(_resolve_path(config, str(config.get("app.screenshots_dir"))))
    vision = VisionService(config)
    safety = SafetyService(config)
    return adb, screenshot, vision, safety


def _connect_device(adb: ADBController, screenshot: ScreenshotService) -> Any:
    info = adb.connect()
    screenshot.set_expected_size(info.width, info.height)
    return info


def _run_check(config: Config, profile_name: str, logger: logging.Logger) -> int:
    logger.info("Запуск проверки окружения")
    ok = True

    print("[1/6] Конфиг: ОК")

    adb, screenshot, _vision, _safety = _build_services(config)

    device_info = None
    try:
        device_info = _connect_device(adb, screenshot)
        print(f"[2/6] ADB: ОК ({config.get('adb.adb_path')})")
        print(f"[3/6] Устройство: serial={device_info.serial}")
    except FileNotFoundError:
        ok = False
        print("[2/6] ADB: ошибка")
        _print_error("adb.exe не найден. Проверьте путь в adb.adb_path или установите Android Platform Tools.")
    except DeviceNotFoundError as exc:
        ok = False
        print("[2/6] ADB: ошибка")
        _print_error(f"Устройство не найдено: {exc}. Подключите LDPlayer/устройство и проверьте 'adb devices'.")
    except (ADBTimeoutError, ADBError) as exc:
        ok = False
        print("[2/6] ADB: ошибка")
        _print_error(f"Не удалось проверить ADB: {exc}")

    if device_info is not None:
        try:
            android_version = adb.shell("getprop ro.build.version.release").strip() or "не удалось определить"
        except (ADBError, ADBTimeoutError) as exc:
            android_version = f"не удалось определить ({exc})"
            ok = False
        print(f"[4/6] Android: {android_version}")
        print(f"[5/6] Экран: {device_info.width}x{device_info.height}")
    else:
        print("[4/6] Android: недоступно")
        print("[5/6] Экран: недоступно")

    try:
        profile = load_profile(config, profile_name)
        kb = KnowledgeBase(config, profile.name)
        templates_count = len(kb.list_templates(enabled_only=False))
        print(f"[6/6] Профиль: {profile.name}, эталонов: {templates_count}")
    except (ProfileError, KnowledgeError) as exc:
        ok = False
        print("[6/6] Профиль/знания: ошибка")
        _print_error(str(exc))

    knowledge_dir = _resolve_path(config, str(config.get("storage.knowledge_dir")))
    try:
        profiles_list = list_profiles(config)
    except ProfileError as exc:
        ok = False
        profiles_list = []
        _print_error(f"Не удалось получить список профилей: {exc}")

    print(f"Папка knowledge: {knowledge_dir}")
    print("Профили:", ", ".join(profiles_list) if profiles_list else "нет")

    if ok:
        print("Итог: ОК")
        return 0

    print("Итог: есть ошибки")
    return 1


def _run_capture(config: Config, logger: logging.Logger) -> int:
    adb, screenshot, _vision, _safety = _build_services(config)
    _connect_device(adb, screenshot)
    frame = screenshot.capture()
    path = screenshot.save_capture(frame=frame, prefix="capture")
    logger.info("Скриншот сохранён: %s", path)
    print(path)
    return 0


def _run_list_profiles(config: Config) -> int:
    profiles = list_profiles(config)
    if not profiles:
        print("Профили не найдены.")
        return 1

    for name in profiles:
        try:
            kb = KnowledgeBase(config, name)
            count = len(kb.list_templates(enabled_only=False))
            print(f"{name}\t{count}")
        except KnowledgeError as exc:
            print(f"{name}\tошибка чтения знаний: {exc}")
    return 0


def _run_reset_knowledge(config: Config, profile_name: str) -> int:
    answer = input(
        f"Вы действительно хотите сбросить знания профиля '{profile_name}'? "
        "Будет создана резервная копия. Для подтверждения введите YES: "
    ).strip()
    if answer != "YES":
        print("Сброс отменён.")
        return 1

    kb = KnowledgeBase(config, profile_name)
    knowledge_dir = _resolve_path(config, str(config.get("storage.knowledge_dir"))) / profile_name
    backup_root = knowledge_dir.parent / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_dir = backup_root / f"{profile_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    if knowledge_dir.exists():
        shutil.copytree(knowledge_dir, backup_dir, dirs_exist_ok=True)
        print(f"Резервная копия: {backup_dir}")
    else:
        print("Папка знаний профиля ещё не создана, резервировать нечего.")

    kb.reset()
    print(f"Знания профиля '{profile_name}' сброшены.")
    return 0


def _resolve_floor_range(args: argparse.Namespace, config: Config) -> tuple[int, int]:
    if (args.floor_from is None) != (args.floor_to is None):
        raise ValueError("Нужно указать сразу оба параметра: --floor-from и --floor-to.")

    if args.floor_from is not None and args.floor_to is not None:
        floor_from = int(args.floor_from)
        floor_to = int(args.floor_to)
    else:
        ranges = config.get("profiles.ranges", {})
        active = str(config.get("profiles.active"))
        profile_range = ranges.get(active, {}) if isinstance(ranges, dict) else {}
        floor_from = int(profile_range.get("floor_from", 1))
        floor_to = int(profile_range.get("floor_to", floor_from))

    if floor_from <= 0 or floor_to <= 0:
        raise ValueError("Номера этажей должны быть больше 0.")
    if floor_from > floor_to:
        raise ValueError("--floor-from не может быть больше --floor-to.")
    return floor_from, floor_to


def _run_teach(config: Config, profile_name: str, logger: logging.Logger, floor_from: int, floor_to: int) -> int:
    adb, screenshot, vision, safety = _build_services(config)
    _connect_device(adb, screenshot)
    knowledge = KnowledgeBase(config, profile_name)
    result: ExploreResult = run_explore(
        adb=adb,
        screenshot=screenshot,
        vision=vision,
        knowledge=knowledge,
        safety=safety,
        teacher_ui=teacher_ui,
        config=config,
        floor_from=floor_from,
        floor_to=floor_to,
    )
    logger.info("Режим teach завершён: %s", result)
    print(result.reason)
    return 0 if result.status == "completed" else 1


def _run_teach_only(config: Config, profile_name: str, logger: logging.Logger) -> int:
    adb, screenshot, vision, safety = _build_services(config)
    _connect_device(adb, screenshot)
    knowledge = KnowledgeBase(config, profile_name)
    result = run_teach_only(
        adb=adb,
        screenshot=screenshot,
        vision=vision,
        knowledge=knowledge,
        safety=safety,
        teacher_ui=teacher_ui,
        config=config,
    )
    logger.info("Режим teach-only завершён: %s", result)
    print(result.reason)
    return 0 if result.status == "completed" else 1


def main() -> int:
    """
    Основной сценарий запуска.

    Поведение режимов:
    --check
        Проверка конфига, ADB, загрузки профиля и базы знаний.
        Код возврата 0 при успехе, 1 при ошибке.

    --capture
        Делает один скриншот и сохраняет его в screenshots_dir.
        Код возврата 0/1.

    --teach
        Режим по умолчанию.
        Бот работает как исследователь, но пользователь может ставить паузу пробелом
        и размечать неизвестные стабильные экраны.

    --teach-only
        Бот ничего не нажимает, пользователь играет сам, бот только помогает размечать.

    --list-profiles
        Печатает доступные профили и завершает работу.

    --reset-knowledge
        Очищает знания активного профиля и завершает работу.

    Возвращает:
        int: exit code

    Исключения:
        Не должно пробрасывать необработанные исключения наружу;
        все переводятся в код возврата != 0 и лог.
    """
    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        ensure_directories(config)
        profile_name = _apply_cli_profile(config, args.profile)
        logger = setup_logging(config)

        selected_modes = [
            flag
            for flag, enabled in [
                ("check", args.check),
                ("capture", args.capture),
                ("teach", args.teach),
                ("teach-only", args.teach_only),
                ("list-profiles", args.list_profiles),
                ("reset-knowledge", args.reset_knowledge),
            ]
            if enabled
        ]

        if args.teach and args.teach_only:
            _print_error("Нельзя использовать одновременно --teach и --teach-only.")
            return 2

        if len(selected_modes) > 1 and not (
            args.teach is False and args.teach_only is False and len(selected_modes) == 1
        ):
            non_run_modes = sum(
                1
                for enabled in [args.check, args.capture, args.list_profiles, args.reset_knowledge]
                if enabled
            )
            if non_run_modes > 1 or ((args.teach or args.teach_only) and non_run_modes > 0):
                _print_error("Укажите только один основной режим за запуск.")
                return 2

        if args.list_profiles:
            return _run_list_profiles(config)

        if args.reset_knowledge:
            return _run_reset_knowledge(config, profile_name)

        if args.check:
            return _run_check(config, profile_name, logger)

        if args.capture:
            return _run_capture(config, logger)

        if args.teach_only:
            return _run_teach_only(config, profile_name, logger)

        floor_from, floor_to = _resolve_floor_range(args, config)
        return _run_teach(config, profile_name, logger, floor_from, floor_to)

    except KeyboardInterrupt:
        _print_error("Операция прервана пользователем.")
        return 130
    except FileNotFoundError as exc:
        _print_error(f"Файл не найден: {exc}")
        return 1
    except ConfigError as exc:
        _print_error(f"Проблема в конфиге: {exc}")
        return 1
    except (ProfileError, KnowledgeError, VisionError) as exc:
        _print_error(str(exc))
        return 1
    except (DeviceNotFoundError, ADBTimeoutError, ADBError) as exc:
        _print_error(f"Проблема с ADB или устройством: {exc}")
        return 1
    except ScreenshotError as exc:
        _print_error(f"Не удалось сделать скриншот: {exc}")
        return 1
    except SafetyStop as exc:
        _print_error(f"Бот остановлен по безопасности: {exc}")
        return 1
    except ExplorerError as exc:
        _print_error(f"Ошибка режима исследования: {exc}")
        return 1
    except ValueError as exc:
        _print_error(str(exc))
        return 2
    except Exception as exc:
        _print_error(f"Непредвиденная ошибка: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
