from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bot import teacher_ui
from bot.adb_controller import ADBController, ADBError, ADBTimeoutError, DeviceNotFoundError
from bot.config import ConfigError, ensure_directories, load_config
from bot.explorer import Explorer, ExplorerError
from bot.floor import FloorExplorer
from bot.knowledge import KnowledgeBase, KnowledgeError
from bot.logging_setup import setup_logging
from bot.profiles import ProfileError, get_active_profile_name, list_profiles, load_profile
from bot.safety import SafetyStop, SafetyService
from bot.screenshot import ScreenshotError, ScreenshotService
from bot.vision import VisionError, VisionService


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dungeon2 bot CLI")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--teach", action="store_true")
    parser.add_argument("--teach-only", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--floor-from", type=int)
    parser.add_argument("--floor-to", type=int)
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--reset-knowledge", action="store_true")
    return parser


def _resolve_path(config_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (config_path.parent / path).resolve()
    return path


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        ensure_directories(config)
        profile_name = get_active_profile_name(config, args.profile)
        config.raw["profiles"]["active"] = profile_name
        logger = setup_logging(config)

        if args.teach and args.teach_only:
            raise ValueError("--teach and --teach-only cannot be used together")
        if (args.floor_from is None) != (args.floor_to is None):
            raise ValueError("--floor-from and --floor-to must be used together")

        if args.list_profiles:
            for name in list_profiles(config):
                print(name)
            return 0

        if args.reset_knowledge:
            KnowledgeBase(config, profile_name).reset()
            print(profile_name)
            return 0

        adb = ADBController(
            adb_path=str(config.get("adb.adb_path")),
            serial=str(config.get("adb.serial") or "").strip() or None,
            exec_timeout_s=float(config.get("adb.exec_timeout_s", 15.0)),
        )
        screenshot = ScreenshotService(adb)
        screenshot.set_screenshots_dir(_resolve_path(config.path, str(config.get("app.screenshots_dir"))))
        vision = VisionService(config)
        safety = SafetyService(config)

        if args.check:
            info = adb.connect()
            screenshot.set_expected_size(info.width, info.height)
            load_profile(config, profile_name)
            KnowledgeBase(config, profile_name)
            print("OK")
            return 0

        if args.capture:
            info = adb.connect()
            screenshot.set_expected_size(info.width, info.height)
            path = screenshot.save_capture(prefix="capture")
            print(path)
            return 0

        knowledge = KnowledgeBase(config, profile_name)

        if args.teach_only:
            info = adb.connect()
            screenshot.set_expected_size(info.width, info.height)
            teacher_ui.teach_only(adb, screenshot, knowledge)
            return 0

        info = adb.connect()
        screenshot.set_expected_size(info.width, info.height)
        profile = load_profile(config, profile_name)
        floor_explorer = FloorExplorer(adb, screenshot, vision, knowledge, safety, teacher_ui, config, profile)
        explorer = Explorer(floor_explorer, teacher_ui, config, knowledge)
        if args.floor_from is not None and args.floor_to is not None:
            floor_from = args.floor_from
            floor_to = args.floor_to
        else:
            ranges = config.get("profiles.ranges", {})
            current = ranges.get(profile_name, {}) if isinstance(ranges, dict) else {}
            floor_from = int(current.get("floor_from", 1))
            floor_to = int(current.get("floor_to", floor_from))
        result = explorer.run(floor_from, floor_to)
        logger.info("explore result: %s", result)
        print(result.reason)
        return 0 if result.status == "completed" else 1
    except KeyboardInterrupt:
        return 130
    except (
        FileNotFoundError,
        ConfigError,
        ProfileError,
        KnowledgeError,
        VisionError,
        DeviceNotFoundError,
        ADBTimeoutError,
        ADBError,
        ScreenshotError,
        SafetyStop,
        ExplorerError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
