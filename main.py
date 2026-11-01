from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple


def _import_or_exit():
    try:
        from bot.config import get_profile, load_config
        from bot.logging_setup import setup_logging
        from bot.adb_controller import (
            ADBCommandError,
            ADBConnectionError,
            ADBController,
            ADBError,
        )
        from bot.screenshot import ScreenshotError, ScreenshotService
        from bot.vision import VisionError, VisionService
        from bot.safety import SafetyService, SafetyStop, SafetyViolation
        from bot.knowledge import KnowledgeBase, KnowledgeError
        from bot.teacher_ui import TeacherUI, TeacherUIError
        from bot.profiles import list_profiles, resolve_profile
        from bot.explorer import ExplorerError, GameExplorer, run_explore, run_teach_only
        return {
            "get_profile": get_profile,
            "load_config": load_config,
            "setup_logging": setup_logging,
            "ADBCommandError": ADBCommandError,
            "ADBConnectionError": ADBConnectionError,
            "ADBController": ADBController,
            "ADBError": ADBError,
            "ScreenshotError": ScreenshotError,
            "ScreenshotService": ScreenshotService,
            "VisionError": VisionError,
            "VisionService": VisionService,
            "SafetyService": SafetyService,
            "SafetyStop": SafetyStop,
            "SafetyViolation": SafetyViolation,
            "KnowledgeBase": KnowledgeBase,
            "KnowledgeError": KnowledgeError,
            "TeacherUI": TeacherUI,
            "TeacherUIError": TeacherUIError,
            "list_profiles": list_profiles,
            "resolve_profile": resolve_profile,
            "ExplorerError": ExplorerError,
            "GameExplorer": GameExplorer,
            "run_explore": run_explore,
            "run_teach_only": run_teach_only,
        }
    except Exception as exc:
        print("Ошибка запуска: не удалось импортировать модули проекта.", file=sys.stderr)
        print(f"Подробности: {exc}", file=sys.stderr)
        print("Проверьте, что рядом есть папка bot/ и все её файлы.", file=sys.stderr)
        sys.exit(2)


_IMPORTED = _import_or_exit()


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "CLI для обучаемого бота-исследователя. "
            "Если режим не указан, по умолчанию запускается --teach."
        ),
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Путь к config.yaml. По умолчанию: config.yaml",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Проверить окружение: ADB, устройство, Android, экран, knowledge, профили.",
    )
    parser.add_argument(
        "--capture",
        action="store_true",
        help="Сделать один скриншот и сохранить его.",
    )
    parser.add_argument(
        "--teach",
        action="store_true",
        help="Основной режим исследования с обучением по Пробелу.",
    )
    parser.add_argument(
        "--teach-only",
        action="store_true",
        help="Только разметка: бот ничего не нажимает, пользователь играет сам.",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Имя профиля из config.yaml, например loc1.",
    )
    parser.add_argument(
        "--floor-from",
        type=int,
        default=None,
        help="Начальный этаж диапазона.",
    )
    parser.add_argument(
        "--floor-to",
        type=int,
        default=None,
        help="Конечный этаж диапазона.",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="Показать профили и число выученных эталонов.",
    )
    parser.add_argument(
        "--reset-knowledge",
        action="store_true",
        help="Сбросить knowledge только для выбранного профиля, с резервной копией и подтверждением.",
    )
    return parser


def _create_services(config_path: str, profile_name: Optional[str]):
    load_config = _IMPORTED["load_config"]
    setup_logging = _IMPORTED["setup_logging"]
    resolve_profile = _IMPORTED["resolve_profile"]
    ADBController = _IMPORTED["ADBController"]
    ScreenshotService = _IMPORTED["ScreenshotService"]
    VisionService = _IMPORTED["VisionService"]
    SafetyService = _IMPORTED["SafetyService"]
    KnowledgeBase = _IMPORTED["KnowledgeBase"]
    TeacherUI = _IMPORTED["TeacherUI"]

    config = load_config(config_path)
    setup_logging(config.log_level)

    try:
        profile = resolve_profile(config, profile_name)
    except KeyError:
        profile = _IMPORTED["get_profile"](config, profile_name)

    adb = ADBController(
        device_serial=config.adb_device_serial,
        adb_path=config.adb_path,
        command_timeout_s=config.adb_command_timeout_s,
        connect_timeout_s=config.adb_connect_timeout_s,
        post_tap_delay_ms=config.post_tap_delay_ms,
        post_swipe_delay_ms=config.post_swipe_delay_ms,
        post_keyevent_delay_ms=config.post_keyevent_delay_ms,
    )
    screenshot = ScreenshotService(
        rotation=config.rotation,
        save_every_capture=config.save_every_capture,
        captures_dir=config.captures_dir,
    )
    vision = VisionService(
        default_threshold=config.matching_default_threshold,
        classify_threshold=config.matching_classify_threshold,
        method=config.matching_method,
        grayscale=config.matching_grayscale,
    )
    safety = SafetyService(
        block_purchases=config.block_purchases,
        block_ads=config.block_ads,
        block_pvp=config.block_pvp,
        stop_on_danger=config.stop_on_danger,
        enforce_safe_actions=config.enforce_safe_actions,
    )
    knowledge = KnowledgeBase(
        rules_file=config.rules_file,
        features_file=config.features_file,
        knowledge_dir=config.knowledge_dir,
    )
    teacher_ui = TeacherUI(
        pause_hotkey=config.teacher_pause_hotkey,
    )
    return config, profile, adb, screenshot, vision, safety, knowledge, teacher_ui


def _ensure_dirs(config) -> None:
    for path in [
        config.data_dir,
        config.captures_dir,
        config.temp_dir,
        config.knowledge_dir,
        config.unknown_dir,
        config.state_dir,
        os.path.dirname(config.progress_file) or ".",
        os.path.dirname(config.rules_file) or ".",
        os.path.dirname(config.features_file) or ".",
    ]:
        Path(path).mkdir(parents=True, exist_ok=True)


def _knowledge_templates_count(knowledge, profile_name: Optional[str]) -> int:
    try:
        return len(knowledge.get_templates(profile=profile_name, enabled_only=False))
    except TypeError:
        return len(knowledge.get_templates(profile=profile_name))
    except Exception:
        return 0


def _profile_id_for_knowledge(profile) -> Optional[str]:
    for attr in ("id", "name"):
        value = getattr(profile, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def run_check(config_path: str, profile_name: str | None) -> int:
    ADBError = _IMPORTED["ADBError"]
    ADBConnectionError = _IMPORTED["ADBConnectionError"]
    ADBCommandError = _IMPORTED["ADBCommandError"]
    ScreenshotError = _IMPORTED["ScreenshotError"]
    KnowledgeError = _IMPORTED["KnowledgeError"]

    print("Проверка окружения...")
    try:
        config, profile, adb, screenshot, _vision, _safety, knowledge, _teacher_ui = _create_services(
            config_path, profile_name
        )
    except FileNotFoundError:
        print(f"Ошибка: файл конфига не найден: {config_path}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Ошибка в config.yaml: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Ошибка профиля: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Ошибка инициализации: {exc}", file=sys.stderr)
        return 1

    errors = []
    selected_profile_name = _profile_id_for_knowledge(profile)

    try:
        _ensure_dirs(config)
        print(f"[OK] Конфиг загружен: {config.path}")
        print(f"[OK] Профиль: {selected_profile_name or '<не определён>'}")
    except Exception as exc:
        errors.append(f"Не удалось подготовить каталоги данных: {exc}")

    adb_exec = config.adb_path.strip() or "adb"
    if Path(adb_exec).name.lower() in {"adb", "adb.exe"}:
        print(f"[OK] Путь к ADB задан: {adb_exec}")
    else:
        print(f"[OK] Пользовательский путь к ADB: {adb_exec}")

    try:
        adb.connect()
        print(f"[OK] ADB подключён к устройству: {config.adb_device_serial}")
    except ADBConnectionError as exc:
        errors.append(
            f"ADB не подключился к устройству {config.adb_device_serial}: {exc}. "
            f"Проверьте adb.exe, LDPlayer и serial."
        )
    except ADBError as exc:
        errors.append(f"Ошибка ADB при подключении: {exc}")

    android_version = None
    if not errors:
        try:
            android_version = adb.shell("getprop ro.build.version.release")
            print(f"[OK] Версия Android: {android_version or 'не удалось определить'}")
        except ADBCommandError as exc:
            errors.append(f"Не удалось получить версию Android: {exc}")

        try:
            width, height = _get_screen_size(adb, screenshot)
            print(f"[OK] Размер экрана: {width}x{height}")
            if width != config.expected_width or height != config.expected_height:
                errors.append(
                    f"Разрешение экрана не совпадает с config.yaml: "
                    f"фактически {width}x{height}, ожидается {config.expected_width}x{config.expected_height}"
                )
            else:
                print("[OK] Разрешение совпадает с config.yaml")
        except Exception as exc:
            errors.append(f"Не удалось определить размер экрана: {exc}")

    try:
        if Path(config.knowledge_dir).exists():
            print(f"[OK] Папка knowledge: {config.knowledge_dir}")
        else:
            errors.append(f"Папка knowledge не существует: {config.knowledge_dir}")
    except Exception as exc:
        errors.append(f"Ошибка проверки knowledge_dir: {exc}")

    try:
        try:
            knowledge.load()
            print("[OK] База знаний загружена")
        except FileNotFoundError:
            knowledge.reset()
            knowledge.save()
            print("[OK] База знаний не найдена, создана пустая структура")
        profile_list = _IMPORTED["list_profiles"](config)
        print("Профили:")
        for name in profile_list:
            count = _knowledge_templates_count(knowledge, name)
            print(f"  - {name}: эталонов {count}")
    except KnowledgeError as exc:
        errors.append(f"Ошибка базы знаний: {exc}")
    except Exception as exc:
        errors.append(f"Не удалось проверить профили или knowledge: {exc}")

    if errors:
        print("\nИтог: ЕСТЬ ОШИБКИ", file=sys.stderr)
        for item in errors:
            print(f"- {item}", file=sys.stderr)
        return 1

    print("\nИтог: ОК")
    return 0


def _get_screen_size(adb, screenshot) -> Tuple[int, int]:
    if hasattr(adb, "get_screen_size"):
        return adb.get_screen_size()
    frame = screenshot.capture(adb)
    h, w = frame.shape[:2]
    return w, h


def run_capture(config_path: str, profile_name: str | None) -> int:
    ScreenshotError = _IMPORTED["ScreenshotError"]
    ADBError = _IMPORTED["ADBError"]

    try:
        config, _profile, adb, screenshot, _vision, _safety, knowledge, _teacher_ui = _create_services(
            config_path, profile_name
        )
        _ensure_dirs(config)
        try:
            knowledge.load()
        except FileNotFoundError:
            knowledge.reset()
            knowledge.save()
        adb.connect()
        frame = screenshot.capture(adb)

        screenshots_dir = Path("screenshots")
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        filename = f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        target_path = screenshots_dir / filename
        saved_path = screenshot.save(frame, str(target_path))
        print(saved_path)
        return 0
    except FileNotFoundError:
        print(f"Ошибка: файл конфига не найден: {config_path}", file=sys.stderr)
        return 1
    except (ADBError, ScreenshotError) as exc:
        print(f"Ошибка capture: {exc}", file=sys.stderr)
        print("Проверьте ADB, запущен ли LDPlayer и доступен ли скриншот устройства.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Непредвиденная ошибка capture: {exc}", file=sys.stderr)
        return 1


def run_teach(config_path: str, profile_name: str | None, floor_from: int | None, floor_to: int | None) -> int:
    ExplorerError = _IMPORTED["ExplorerError"]
    ADBError = _IMPORTED["ADBError"]
    ScreenshotError = _IMPORTED["ScreenshotError"]
    VisionError = _IMPORTED["VisionError"]
    KnowledgeError = _IMPORTED["KnowledgeError"]
    TeacherUIError = _IMPORTED["TeacherUIError"]
    SafetyStop = _IMPORTED["SafetyStop"]
    run_explore = _IMPORTED["run_explore"]

    try:
        config, profile, adb, screenshot, vision, safety, knowledge, teacher_ui = _create_services(
            config_path, profile_name
        )
        _ensure_dirs(config)
        try:
            knowledge.load()
        except FileNotFoundError:
            knowledge.reset()
            knowledge.save()

        adb.connect()

        actual_floor_from = floor_from if floor_from is not None else config.default_floor_from
        actual_floor_to = floor_to if floor_to is not None else config.default_floor_to

        if actual_floor_from <= 0 or actual_floor_to <= 0:
            print("Ошибка: --floor-from и --floor-to должны быть больше 0.", file=sys.stderr)
            return 1
        if actual_floor_from > actual_floor_to:
            print("Ошибка: --floor-from не может быть больше --floor-to.", file=sys.stderr)
            return 1

        run_explore(
            adb=adb,
            screenshot=screenshot,
            vision=vision,
            knowledge=knowledge,
            safety=safety,
            teacher_ui=teacher_ui,
            config=config,
            profile=profile,
            floor_from=actual_floor_from,
            floor_to=actual_floor_to,
        )
        print("Режим --teach завершён успешно.")
        return 0
    except FileNotFoundError:
        print(f"Ошибка: файл конфига не найден: {config_path}", file=sys.stderr)
        return 1
    except SafetyStop as exc:
        print(f"Остановка по безопасности: {exc}", file=sys.stderr)
        return 1
    except (ExplorerError, ADBError, ScreenshotError, VisionError, KnowledgeError, TeacherUIError) as exc:
        print(f"Ошибка в режиме --teach: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Непредвиденная ошибка в режиме --teach: {exc}", file=sys.stderr)
        return 1


def run_teach_only(config_path: str, profile_name: str | None) -> int:
    ADBError = _IMPORTED["ADBError"]
    ScreenshotError = _IMPORTED["ScreenshotError"]
    KnowledgeError = _IMPORTED["KnowledgeError"]
    TeacherUIError = _IMPORTED["TeacherUIError"]
    run_teach_only_fn = _IMPORTED["run_teach_only"]

    try:
        config, profile, adb, screenshot, vision, safety, knowledge, teacher_ui = _create_services(
            config_path, profile_name
        )
        _ensure_dirs(config)
        try:
            knowledge.load()
        except FileNotFoundError:
            knowledge.reset()
            knowledge.save()

        adb.connect()
        run_teach_only_fn(
            adb=adb,
            screenshot=screenshot,
            vision=vision,
            knowledge=knowledge,
            safety=safety,
            teacher_ui=teacher_ui,
            config=config,
            profile=profile,
        )
        print("Режим --teach-only завершён.")
        return 0
    except FileNotFoundError:
        print(f"Ошибка: файл конфига не найден: {config_path}", file=sys.stderr)
        return 1
    except (ADBError, ScreenshotError, KnowledgeError, TeacherUIError) as exc:
        print(f"Ошибка в режиме --teach-only: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Непредвиденная ошибка в режиме --teach-only: {exc}", file=sys.stderr)
        return 1


def _run_list_profiles(config_path: str, profile_name: Optional[str]) -> int:
    try:
        config, _profile, _adb, _screenshot, _vision, _safety, knowledge, _teacher_ui = _create_services(
            config_path, profile_name
        )
        _ensure_dirs(config)
        try:
            knowledge.load()
        except FileNotFoundError:
            knowledge.reset()
            knowledge.save()

        names = _IMPORTED["list_profiles"](config)
        if not names:
            print("Профили не найдены.")
            return 1

        print("Профили и число выученных эталонов:")
        for name in names:
            count = _knowledge_templates_count(knowledge, name)
            print(f"- {name}: {count}")
        return 0
    except FileNotFoundError:
        print(f"Ошибка: файл конфига не найден: {config_path}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Ошибка списка профилей: {exc}", file=sys.stderr)
        return 1


def _run_reset_knowledge(config_path: str, profile_name: Optional[str]) -> int:
    try:
        config, profile, _adb, _screenshot, _vision, _safety, knowledge, _teacher_ui = _create_services(
            config_path, profile_name
        )
        _ensure_dirs(config)

        selected_profile = _profile_id_for_knowledge(profile)
        if not selected_profile:
            print("Ошибка: не удалось определить выбранный профиль для сброса.", file=sys.stderr)
            return 1

        print(f"Вы собираетесь сбросить knowledge только для профиля: {selected_profile}")
        print("Будет создана резервная копия текущих файлов rules.json и features.json.")
        confirm = input("Введите YES для подтверждения: ").strip()
        if confirm != "YES":
            print("Сброс отменён пользователем.")
            return 1

        backup_root = Path(config.knowledge_dir) / "_backups" / selected_profile / datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_root.mkdir(parents=True, exist_ok=True)

        for src in [config.rules_file, config.features_file]:
            src_path = Path(src)
            if src_path.exists():
                shutil.copy2(src_path, backup_root / src_path.name)

        try:
            knowledge.load()
        except FileNotFoundError:
            knowledge.reset()
            knowledge.save()

        templates = []
        try:
            data_rules = knowledge.get_rules_data()
            data_features = knowledge.get_features_data()
        except Exception as exc:
            print(
                f"Ошибка: в реальном коде KnowledgeBase нет надёжного API для профильного reset знаний без ручной правки файлов: {exc}",
                file=sys.stderr,
            )
            return 1

        kept_rules = []
        removed_template_ids = set()
        for item in data_rules.get("templates", []):
            item_profile = str(item.get("profile", "")).strip()
            if item_profile == selected_profile:
                removed_template_ids.add(str(item.get("id", "")))
            else:
                kept_rules.append(item)
        data_rules["templates"] = kept_rules

        features_templates = data_features.get("templates", {})
        if isinstance(features_templates, dict):
            for template_id in list(features_templates.keys()):
                feature_profile = str(features_templates[template_id].get("profile", "")).strip()
                if template_id in removed_template_ids or feature_profile == selected_profile:
                    features_templates.pop(template_id, None)

        for item in list(data_rules.get("templates", [])):
            pass

        with open(config.rules_file, "w", encoding="utf-8") as fh:
            import json
            json.dump(data_rules, fh, ensure_ascii=False, indent=2)
        with open(config.features_file, "w", encoding="utf-8") as fh:
            import json
            json.dump(data_features, fh, ensure_ascii=False, indent=2)

        print(f"Готово. Резервная копия: {backup_root}")
        return 0
    except FileNotFoundError:
        print(f"Ошибка: файл конфига не найден: {config_path}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Ошибка сброса knowledge: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    selected_modes = sum(
        1 for flag in [args.check, args.capture, args.teach, args.teach_only, args.list_profiles, args.reset_knowledge] if flag
    )
    if selected_modes > 1:
        print(
            "Ошибка: выберите только один основной режим: "
            "--check, --capture, --teach, --teach-only, --list-profiles или --reset-knowledge.",
            file=sys.stderr,
        )
        return 2

    if args.list_profiles:
        return _run_list_profiles(args.config, args.profile)

    if args.reset_knowledge:
        return _run_reset_knowledge(args.config, args.profile)

    if args.check:
        return run_check(args.config, args.profile)

    if args.capture:
        return run_capture(args.config, args.profile)

    if args.teach_only:
        return run_teach_only(args.config, args.profile)

    return run_teach(args.config, args.profile, args.floor_from, args.floor_to)


if __name__ == "__main__":
    raise SystemExit(main())
