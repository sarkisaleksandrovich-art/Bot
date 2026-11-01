from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

import cv2
import numpy as np


Frame = np.ndarray


class KnowledgeError(Exception):
    pass


class UnknownCategoryError(KnowledgeError):
    pass


_ALLOWED_CATEGORIES: tuple[str, ...] = (
    "door_marker",
    "party_marker",
    "room_open_big_icon",
    "room_screen",
    "passage_screen",
    "event_screen",
    "mob",
    "boss",
    "loot",
    "exit",
    "auto_battle",
    "next_floor",
    "danger",
)


@dataclass(frozen=True)
class TemplateRecord:
    id: str
    category: str
    file: str
    width: int
    height: int
    source_screen_size: tuple[int, int]
    selected_rect: tuple[int, int, int, int]
    roi_hint: tuple[int, int, int, int]
    created_at: str
    enabled: bool
    tags: tuple[str, ...]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_rect(rect: tuple[int, int, int, int] | list[int]) -> tuple[int, int, int, int]:
    if len(rect) != 4:
        raise KnowledgeError("Rect must contain exactly 4 integers.")
    x1, y1, x2, y2 = (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return x1, y1, x2, y2


def _validate_positive_size(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise KnowledgeError(f"Invalid size: width={width}, height={height}.")


def _ensure_frame(frame: Frame) -> None:
    if not isinstance(frame, np.ndarray):
        raise KnowledgeError("Frame must be numpy.ndarray.")
    if frame.ndim not in (2, 3):
        raise KnowledgeError("Frame must be 2D or 3D numpy.ndarray.")
    if frame.shape[0] <= 0 or frame.shape[1] <= 0:
        raise KnowledgeError("Frame must have positive width and height.")


def _frame_size(frame: Frame) -> tuple[int, int]:
    _ensure_frame(frame)
    height, width = frame.shape[:2]
    return int(width), int(height)


def _rect_inside_screen(rect: tuple[int, int, int, int], width: int, height: int) -> bool:
    x1, y1, x2, y2 = rect
    return 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height


def _clamp_rect(rect: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = rect
    x1 = max(0, min(x1, width))
    x2 = max(0, min(x2, width))
    y1 = max(0, min(y1, height))
    y2 = max(0, min(y2, height))
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return x1, y1, x2, y2


def _atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        except OSError:
            pass
        raise


class KnowledgeBase:
    def __init__(self, config: "BotConfig", profile: str, logger: "logging.Logger") -> None:
        self.config = config
        self.profile = str(profile)
        self.logger = logger
        self._lock = RLock()

        raw = getattr(config, "raw", None)
        if not isinstance(raw, dict):
            raise KnowledgeError("config.raw must be a dict.")

        self._knowledge_cfg = raw.get("knowledge", {})
        self._app_cfg = raw.get("app", {})
        self._profiles_cfg = raw.get("profiles", {})

        self._rules_filename = str(self._app_cfg.get("rules_file", "rules.json"))
        self._features_filename = str(self._app_cfg.get("features_file", "features.json"))

        profiles_root = Path(str(self._profiles_cfg.get("root_dir", "./data/profiles")))
        self._profile_dir = profiles_root / self.profile

        templates_root_cfg = Path(str(self._knowledge_cfg.get("templates_dir", "./data/templates")))
        self._profile_templates_dir = self._resolve_profile_templates_dir(templates_root_cfg, self.profile)
        self._common_templates_dir = self._resolve_common_templates_dir(templates_root_cfg)

        self._rules_path = self._profile_dir / self._rules_filename
        self._features_path = self._profile_dir / self._features_filename

        min_size = self._knowledge_cfg.get("min_template_size", [8, 8])
        max_size = self._knowledge_cfg.get("max_template_size", [800, 800])

        if not isinstance(min_size, (list, tuple)) or len(min_size) != 2:
            raise KnowledgeError("knowledge.min_template_size must be [width, height].")
        if not isinstance(max_size, (list, tuple)) or len(max_size) != 2:
            raise KnowledgeError("knowledge.max_template_size must be [width, height].")

        self._min_template_size = (int(min_size[0]), int(min_size[1]))
        self._max_template_size = (int(max_size[0]), int(max_size[1]))

        self._staged_templates: list[TemplateRecord] = []
        self._staged_clicks: list[dict[str, Any]] = []
        self._staged_actions: list[dict[str, Any]] = []

        self._ensure_directories()

    def categories(self) -> list[str]:
        return list(_ALLOWED_CATEGORIES)

    def load_rules(self) -> dict:
        with self._lock:
            if not self._rules_path.exists():
                raise FileNotFoundError(str(self._rules_path))
            try:
                with self._rules_path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except json.JSONDecodeError as exc:
                raise KnowledgeError(f"rules.json is corrupted: {exc}") from exc
            except OSError as exc:
                raise KnowledgeError(f"Failed to read rules.json: {exc}") from exc
            if not isinstance(data, dict):
                raise KnowledgeError("rules.json root must be an object.")
            return data

    def save_rules(self, data: dict) -> None:
        with self._lock:
            if not isinstance(data, dict):
                raise KnowledgeError("Rules data must be dict.")
            payload = dict(data)
            payload["version"] = int(payload.get("version", 1))
            payload["profile"] = self.profile
            payload["updated_at"] = _utc_now_iso()
            payload["categories"] = self.categories()
            try:
                content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
                _atomic_write_text(self._rules_path, content)
            except (TypeError, ValueError, OSError) as exc:
                raise KnowledgeError(f"Failed to save rules.json: {exc}") from exc

    def load_features(self) -> list[TemplateRecord]:
        with self._lock:
            if not self._features_path.exists():
                raise FileNotFoundError(str(self._features_path))
            return self._load_templates_from_features_path(self._features_path)

    def save_features(self, templates: list[TemplateRecord]) -> None:
        with self._lock:
            normalized = [self._normalize_template_record(item) for item in templates]
            payload = {
                "version": 1,
                "profile": self.profile,
                "updated_at": _utc_now_iso(),
                "categories": self.categories(),
                "templates": [self._template_to_dict(item) for item in normalized],
            }
            try:
                content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
                _atomic_write_text(self._features_path, content)
            except (TypeError, ValueError, OSError) as exc:
                raise KnowledgeError(f"Failed to save features.json: {exc}") from exc

    def get_templates(
        self,
        categories: list[str] | None = None,
        enabled_only: bool = True
    ) -> list[TemplateRecord]:
        with self._lock:
            category_filter: set[str] | None = None
            if categories is not None:
                category_filter = set()
                for category in categories:
                    self._validate_category(category)
                    category_filter.add(category)

            result: list[TemplateRecord] = []
            for item in self._load_combined_templates(include_staged=True):
                if enabled_only and not item.enabled:
                    continue
                if category_filter is not None and item.category not in category_filter:
                    continue
                result.append(item)
            result.sort(key=lambda t: (t.category, t.id))
            return result

    def load_template_image(self, template_id: str) -> Frame:
        with self._lock:
            for item in self._load_combined_templates(include_staged=True):
                if item.id == template_id:
                    image = cv2.imread(str(Path(item.file)), cv2.IMREAD_COLOR)
                    if image is None:
                        raise KnowledgeError(f"Failed to read template image: {item.file}")
                    return image
            raise KeyError(template_id)

    def add_template(
        self,
        frame: Frame,
        rect: tuple[int, int, int, int],
        category: str,
        roi_hint: tuple[int, int, int, int] | None = None,
        tags: list[str] | None = None
    ) -> TemplateRecord:
        with self._lock:
            self._validate_category(category)
            _ensure_frame(frame)

            screen_w, screen_h = _frame_size(frame)
            selected_rect = _normalize_rect(rect)
            if not _rect_inside_screen(selected_rect, screen_w, screen_h):
                raise KnowledgeError("Selected rect is outside source frame.")

            x1, y1, x2, y2 = selected_rect
            cropped = frame[y1:y2, x1:x2].copy()
            tpl_w, tpl_h = _frame_size(cropped)
            self._validate_template_size(tpl_w, tpl_h)

            if roi_hint is None:
                roi_rect = (0, 0, screen_w, screen_h)
            else:
                roi_rect = _clamp_rect(_normalize_rect(roi_hint), screen_w, screen_h)
                if roi_rect[0] >= roi_rect[2] or roi_rect[1] >= roi_rect[3]:
                    raise KnowledgeError("roi_hint is invalid after clamping.")

            template_id = self._next_template_id()
            created_at = _utc_now_iso()
            png_path = self._profile_templates_dir / f"{template_id}.png"

            if not cv2.imwrite(str(png_path), cropped):
                raise KnowledgeError(f"Failed to write template PNG: {png_path}")

            record = TemplateRecord(
                id=template_id,
                category=category,
                file=str(png_path.as_posix()),
                width=tpl_w,
                height=tpl_h,
                source_screen_size=(screen_w, screen_h),
                selected_rect=selected_rect,
                roi_hint=roi_rect,
                created_at=created_at,
                enabled=True,
                tags=tuple(tags or ["manual"]),
            )

            existing = self.load_features()
            existing.append(record)
            self.save_features(existing)
            self._staged_templates.append(record)
            self._staged_actions.append({"type": "template", "id": record.id})
            return record

    def reset(self) -> None:
        with self._lock:
            self._ensure_directories()
            self.save_rules(self._default_rules())
            self.save_features([])
            if self._profile_templates_dir.exists():
                for file_path in self._profile_templates_dir.iterdir():
                    if file_path.is_file() and file_path.suffix.lower() == ".png":
                        file_path.unlink()
            self._staged_templates.clear()
            self._staged_clicks.clear()
            self._staged_actions.clear()

    def add_click_point(
        self,
        x: int,
        y: int,
        category: str,
        roi_hint: tuple[int, int, int, int] | None = None,
        tags: list[str] | None = None
    ) -> dict[str, Any]:
        with self._lock:
            self._validate_category(category)
            record = {
                "type": "click_point",
                "x": int(x),
                "y": int(y),
                "category": category,
                "roi_hint": list(_normalize_rect(roi_hint)) if roi_hint is not None else None,
                "tags": list(tags or ["manual"]),
                "created_at": _utc_now_iso(),
            }
            self._staged_clicks.append(record)
            self._staged_actions.append({"type": "click_point", "index": len(self._staged_clicks) - 1})
            return dict(record)

    def undo_last_annotation(self) -> bool:
        with self._lock:
            if not self._staged_actions:
                return False
            last = self._staged_actions.pop()
            if last.get("type") == "template":
                template_id = str(last["id"])
                templates = self.load_features()
                to_remove = None
                remaining: list[TemplateRecord] = []
                for item in templates:
                    if item.id == template_id and to_remove is None:
                        to_remove = item
                    else:
                        remaining.append(item)
                if to_remove is not None:
                    image_path = Path(to_remove.file)
                    if image_path.exists():
                        image_path.unlink()
                    self.save_features(remaining)
                self._staged_templates = [item for item in self._staged_templates if item.id != template_id]
                return True
            if last.get("type") == "click_point":
                index = int(last["index"])
                if 0 <= index < len(self._staged_clicks):
                    self._staged_clicks.pop(index)
                    rebuilt: list[dict[str, Any]] = []
                    click_index = 0
                    for action in self._staged_actions:
                        if action.get("type") == "click_point":
                            rebuilt.append({"type": "click_point", "index": click_index})
                            click_index += 1
                        else:
                            rebuilt.append(action)
                    self._staged_actions = rebuilt
                    return True
            return False

    def save(self) -> None:
        with self._lock:
            templates = self.load_features() if self._features_path.exists() else []
            self.save_features(templates)
            if not self._rules_path.exists():
                self.save_rules(self._default_rules())

    def profile_dir(self) -> str:
        return str(self._profile_dir.as_posix())

    def common_dir(self) -> str:
        return str(self._common_templates_dir.as_posix())

    def _ensure_directories(self) -> None:
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        self._profile_templates_dir.mkdir(parents=True, exist_ok=True)
        self._common_templates_dir.mkdir(parents=True, exist_ok=True)
        if not self._rules_path.exists():
            self.save_rules(self._default_rules())
        if not self._features_path.exists():
            self.save_features([])

    def _resolve_profile_templates_dir(self, templates_root: Path, profile: str) -> Path:
        parts = [part for part in templates_root.parts if part not in ("", ".")]
        if len(parts) >= 2 and parts[-2] == "templates" and parts[-1] == profile:
            return templates_root
        if parts and parts[-1] == profile:
            return templates_root
        return templates_root / profile

    def _resolve_common_templates_dir(self, templates_root: Path) -> Path:
        parts = [part for part in templates_root.parts if part not in ("", ".")]
        if parts and parts[-1] == "templates":
            return templates_root / "common"
        if len(parts) >= 2 and parts[-2] == "templates":
            return templates_root.parent / "common"
        return templates_root / "common"

    def _default_rules(self) -> dict[str, Any]:
        safety = self.config.raw.get("safety", {}) if isinstance(self.config.raw, dict) else {}
        game = self.config.raw.get("game", {}) if isinstance(self.config.raw, dict) else {}
        return {
            "version": 1,
            "profile": self.profile,
            "updated_at": "",
            "categories": self.categories(),
            "policy": {
                "block_shop": bool(safety.get("block_shop", True)),
                "block_ads": bool(safety.get("block_ads", True)),
                "block_pvp": bool(safety.get("block_pvp", True)),
                "stop_on_danger": bool(safety.get("stop_on_danger", True)),
                "clear_room_immediately": bool(game.get("clear_room_immediately", True)),
                "skip_events_by_exit": bool(game.get("skip_events_by_exit", True)),
                "go_all_doors_before_next_floor": bool(game.get("go_all_doors_before_next_floor", True)),
                "revisit_open_rooms": bool(game.get("revisit_open_rooms", False)),
            },
            "screen_rules": [],
            "notes": "",
        }

    def _load_templates_from_features_path(self, path: Path) -> list[TemplateRecord]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise KnowledgeError(f"features.json is corrupted: {exc}") from exc
        except OSError as exc:
            raise KnowledgeError(f"Failed to read features.json: {exc}") from exc

        if not isinstance(data, dict):
            raise KnowledgeError("features.json root must be an object.")
        templates_data = data.get("templates", [])
        if not isinstance(templates_data, list):
            raise KnowledgeError("features.json templates must be a list.")

        result: list[TemplateRecord] = []
        for item in templates_data:
            if not isinstance(item, dict):
                raise KnowledgeError("Template record must be object.")
            category = str(item["category"])
            self._validate_category(category)
            source_screen_size = item.get("source_screen_size", [0, 0])
            selected_rect = item.get("selected_rect", [0, 0, 0, 0])
            roi_hint = item.get("roi_hint", [0, 0, 0, 0])
            tags = item.get("tags", [])
            if not isinstance(source_screen_size, (list, tuple)) or len(source_screen_size) != 2:
                raise KnowledgeError("Template source_screen_size must be [width, height].")
            if not isinstance(tags, list):
                raise KnowledgeError("Template tags must be list.")
            result.append(
                TemplateRecord(
                    id=str(item["id"]),
                    category=category,
                    file=str(item["file"]),
                    width=int(item["width"]),
                    height=int(item["height"]),
                    source_screen_size=(int(source_screen_size[0]), int(source_screen_size[1])),
                    selected_rect=_normalize_rect(selected_rect),
                    roi_hint=_normalize_rect(roi_hint),
                    created_at=str(item.get("created_at", "")),
                    enabled=bool(item.get("enabled", True)),
                    tags=tuple(str(tag) for tag in tags),
                )
            )
        return result

    def _load_combined_templates(self, include_staged: bool) -> list[TemplateRecord]:
        result: list[TemplateRecord] = []
        seen: set[str] = set()

        common_features = self._common_templates_dir / self._features_filename
        if common_features.exists():
            for item in self._load_templates_from_features_path(common_features):
                if item.id not in seen:
                    seen.add(item.id)
                    result.append(item)

        if self._features_path.exists():
            for item in self._load_templates_from_features_path(self._features_path):
                if item.id not in seen:
                    seen.add(item.id)
                    result.append(item)

        if include_staged:
            for item in self._staged_templates:
                if item.id not in seen:
                    seen.add(item.id)
                    result.append(item)

        return result

    def _template_to_dict(self, item: TemplateRecord) -> dict[str, Any]:
        return {
            "id": item.id,
            "category": item.category,
            "file": item.file,
            "width": int(item.width),
            "height": int(item.height),
            "source_screen_size": [int(item.source_screen_size[0]), int(item.source_screen_size[1])],
            "selected_rect": [int(v) for v in item.selected_rect],
            "roi_hint": [int(v) for v in item.roi_hint],
            "created_at": item.created_at,
            "enabled": bool(item.enabled),
            "tags": [str(tag) for tag in item.tags],
        }

    def _validate_category(self, category: str) -> None:
        if category not in _ALLOWED_CATEGORIES:
            raise UnknownCategoryError(f"Unknown category: {category}")

    def _validate_template_size(self, width: int, height: int) -> None:
        _validate_positive_size(width, height)
        min_w, min_h = self._min_template_size
        max_w, max_h = self._max_template_size
        if width < min_w or height < min_h:
            raise KnowledgeError(f"Template is too small: {(width, height)}, minimum is {(min_w, min_h)}.")
        if width > max_w or height > max_h:
            raise KnowledgeError(f"Template is too large: {(width, height)}, maximum is {(max_w, max_h)}.")

    def _next_template_id(self) -> str:
        used_ids = {item.id for item in self._load_combined_templates(include_staged=True)}
        number = 1
        while True:
            candidate = f"tpl_{number:06d}"
            if candidate not in used_ids:
                return candidate
            number += 1

    def _normalize_template_record(self, item: TemplateRecord) -> TemplateRecord:
        if not isinstance(item, TemplateRecord):
            raise KnowledgeError("All features must be TemplateRecord instances.")
        self._validate_category(item.category)
        _validate_positive_size(item.width, item.height)
        return TemplateRecord(
            id=str(item.id),
            category=str(item.category),
            file=str(item.file),
            width=int(item.width),
            height=int(item.height),
            source_screen_size=(int(item.source_screen_size[0]), int(item.source_screen_size[1])),
            selected_rect=_normalize_rect(item.selected_rect),
            roi_hint=_normalize_rect(item.roi_hint),
            created_at=str(item.created_at),
            enabled=bool(item.enabled),
            tags=tuple(str(tag) for tag in item.tags),
        )
