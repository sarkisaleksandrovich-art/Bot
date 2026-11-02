from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

# Existing implementation retained; reviewed changes are applied to the navigation helpers.

DEFAULT_FLOOR_NUMBER = 1


def _extract_png(config_value: Any) -> Any:
    if isinstance(config_value, dict): return config_value.get("png")
    return config_value


def _nav_chain_templates(project_dir: str | Path, location_name: str, floor_number: int | None = None) -> dict[str, Optional[str]]:
    nav, _ = _read_nav(project_dir); location_item = None
    for item in nav.get("locations", []) if isinstance(nav.get("locations"), list) else []:
        if isinstance(item, dict) and str(item.get("name", "")).strip().lower() == str(location_name).strip().lower(): location_item = item; break
    farm_floor = nav.get("farm_floor_button")
    if isinstance(farm_floor, dict) and floor_number is not None:
        floor_specific = next((farm_floor.get(key) for key in (str(floor_number), f"floor_{floor_number}") if key in farm_floor), None); farm_floor = floor_specific if floor_specific is not None else _extract_png(farm_floor)
    return {"base": str(_resolve_path(project_dir, nav.get("base_screenshot")) or "") or None, "base_to_locations": str(_resolve_path(project_dir, _extract_png(nav.get("base_to_locations"))) or "") or None, "locations_map": str(_resolve_path(project_dir, nav.get("locations_map")) or "") or None, "location_icon": str(_resolve_path(project_dir, (location_item or {}).get("png")) or "") or None, "farm_floor_button": str(_resolve_path(project_dir, farm_floor) or "") or None, "enter_dungeon": str(_resolve_path(project_dir, _extract_png(nav.get("enter_dungeon"))) or "") or None, "confirm_start": str(_resolve_path(project_dir, _extract_png(nav.get("confirm_start"))) or "") or None}


def find_template(project_dir: str | Path, template: Any, frame: Any = None, roi: tuple[int, int, int, int] | None = None) -> tuple[bool, int, int, int, float]:
    runtime = _build_runtime(project_dir); settings = _settings(project_dir); threshold = settings["match_threshold"]; work_frame = frame if frame is not None else runtime.screenshot.capture(); template_path = _resolve_path(project_dir, template)
    if template_path is None: return False, 0, 0, 0, 0.0
    image = _load_image(template_path); width, height = _frame_shape(work_frame); effective_roi = roi if roi is not None else (0, 0, width, height)
    try: match = runtime.vision.find(work_frame, image, effective_roi, threshold)
    except Exception: return False, 0, 0, 0, 0.0
    if match is None: return False, 0, 0, 0, 0.0
    x, y = _center_from_rect(match.rect); return True, x, y, x - (width // 2), float(match.score)


def start_battle(project_dir: str | Path, log: Callable[..., Any] = print, stop_event: Any = None) -> tuple[bool, str]:
    runtime = _build_runtime(project_dir, log=log); nav, _ = _read_nav(project_dir); enter_png = _resolve_path(project_dir, _extract_png(nav.get("enter_dungeon"))); confirm_png = _resolve_path(project_dir, _extract_png(nav.get("confirm_start"))); autobattle_png = _resolve_path(project_dir, _extract_png(nav.get("autobattle_on")))
    if stop_event is not None and stop_event.is_set(): return False, "stopped"
    if enter_png is None: return False, "enter_dungeon template missing"
    frame, _ = _stable_wait(runtime, 2.0, log)
    if not _click_center(runtime, find_template(project_dir, str(enter_png), frame=frame), log): return False, "failed to start battle"
    if confirm_png is not None:
        deadline = time.monotonic() + 10.0; found_match = None
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set(): return False, "stopped"
            found, x, y, _dx, score = find_template(project_dir, str(confirm_png), frame=runtime.screenshot.capture())
            if found: found_match = (x, y, score); break
            time.sleep(0.2)
        if found_match is None: return False, "confirm_start not found"
        x, y, score = found_match; runtime.adb.tap(int(x), int(y)); _log(log, f"confirm_start tapped x={x} y={y} score={score:.3f}")
        changed_deadline = time.monotonic() + 10.0
        while time.monotonic() < changed_deadline:
            if stop_event is not None and stop_event.is_set(): return False, "stopped"
            still = find_template(project_dir, str(confirm_png), frame=runtime.screenshot.capture())[0]
            if not still: break
            time.sleep(0.2)
        else: return False, "confirm_start screen did not change"
    if autobattle_png is not None:
        battle_frame = runtime.screenshot.capture(); found, x, y, _dx, score = find_template(project_dir, str(autobattle_png), frame=battle_frame); _log(log, f"autobattle state found={found} score={score:.3f}")
        if not found:
            width, height = _frame_shape(battle_frame); runtime.adb.tap(width // 2, height // 2)
    return True, "battle started"
