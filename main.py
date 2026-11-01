from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from bot.adb_controller import ADBController, ADBError
from bot.config import get_profile, load_config
from bot.explorer import GameExplorer
from bot.knowledge import KnowledgeBase, KnowledgeError
from bot.logging_setup import setup_logging
from bot.profiles import list_profiles, resolve_profile
from bot.safety import SafetyStop, SafetyService
from bot.screenshot import ScreenshotError, ScreenshotService
from bot.teacher_ui import TeacherUI, TeacherUIError
from bot.vision import VisionError, VisionService


def build_arg_parser():
    parser = argparse.ArgumentParser(prog="main.py")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--teach", action="store_true")
    parser.add_argument("--teach-only", action="store_true")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--floor-from", type=int, default=None)
    parser.add_argument("--floor-to", type=int, default=None)
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--reset-knowledge", action="store_true")
    return parser


def _ensure_dirs(config) -> None:
    for path in [
        config.data_dir,
        config.captures_dir,
        config.temp_dir,
        config.knowledge_dir,
        config.unknown_dir,
        config.state_dir,
        str(Path(config.progress_file).parent),
        str(Path(config.rules_file).parent),
        str(Path(config.features_file).parent),
    ]:
        Path(path).mkdir(parents=True, exist_ok=True)


def _create_services(config_path: str, profile_name: str | None):
    config = load_config(config_path)
    setup_logging(config.log_level)
    profile = resolve_profile(config, profile_name)
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
    teacher_ui = TeacherUI(pause_hotkey=config.teacher_pause_hotkey)
    return config, profile, adb, screenshot, vision, knowledge, safety, teacher_ui


def run_check(config_path: str, profile_name: str | None) -> int:
    try:
        config, _profile, adb, screenshot, _vision, knowledge, _safety, _teacher_ui = _create_services(config_path, profile_name)
        _ensure_dirs(config)
        adb.connect()
        frame = screenshot.capture(adb)
        height, width = frame.shape[:2]
        if width != config.expected_width or height != config.expected_height:
            return 1
        try:
            knowledge.load()
        except FileNotFoundError:
            knowledge.reset()
            knowledge.save()
        return 0
    except Exception:
        return 1


def run_capture(config_path: str, profile_name: str | None) -> int:
    try:
        config, _profile, adb, screenshot, _vision, _knowledge, _safety, _teacher_ui = _create_services(config_path, profile_name)
        _ensure_dirs(config)
        adb.connect()
        frame = screenshot.capture(adb)
        path = Path(config.captures_dir) / f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        screenshot.save(frame, str(path))
        return 0
    except Exception:
        return 1


def run_teach(config_path: str, profile_name: str | None, floor_from: int | None, floor_to: int | None) -> int:
    try:
        config, profile, adb, screenshot, vision, knowledge, safety, teacher_ui = _create_services(config_path, profile_name)
        _ensure_dirs(config)
        try:
            knowledge.load()
        except FileNotFoundError:
            knowledge.reset()
            knowledge.save()
        adb.connect()
        explorer = GameExplorer(adb, screenshot, vision, knowledge, safety, teacher_ui, config, profile)
        explorer.run(
            floor_from if floor_from is not None else config.default_floor_from,
            floor_to if floor_to is not None else config.default_floor_to,
        )
        return 0
    except Exception:
        return 1


def run_teach_only(config_path: str, profile_name: str | None) -> int:
    try:
        config, _profile, adb, screenshot, _vision, knowledge, _safety, teacher_ui = _create_services(config_path, profile_name)
        _ensure_dirs(config)
        try:
            knowledge.load()
        except FileNotFoundError:
            knowledge.reset()
            knowledge.save()
        adb.connect()
        teacher_ui.teach_only(adb, screenshot, knowledge)
        return 0
    except Exception:
        return 1


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.list_profiles:
        try:
            config = load_config(args.config)
            for name in list_profiles(config):
                print(name)
            return 0
        except Exception:
            return 1

    if args.reset_knowledge:
        try:
            config = load_config(args.config)
            knowledge = KnowledgeBase(config.rules_file, config.features_file, config.knowledge_dir)
            knowledge.reset()
            knowledge.save()
            return 0
        except Exception:
            return 1

    if args.check:
        return run_check(args.config, args.profile)
    if args.capture:
        return run_capture(args.config, args.profile)
    if args.teach_only:
        return run_teach_only(args.config, args.profile)
    return run_teach(args.config, args.profile, args.floor_from, args.floor_to)


if __name__ == "__main__":
    raise SystemExit(main())
