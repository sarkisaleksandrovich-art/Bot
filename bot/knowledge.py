from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from bot.vision import Rect, Template


class KnowledgeError(Exception):
    pass


class UnknownCategoryError(KnowledgeError):
    pass


@dataclass
class _PendingAction:
    kind: str
    payload: dict[str, Any]


class KnowledgeBase:
    CATEGORIES: tuple[str, ...] = (
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

    _SCREEN_CATEGORIES: tuple[str, ...] = (
        "room_screen",
        "passage_screen",
        "event_screen",
    )

    def __init__(self, config: "Config", profile: str) -> None:
        self._config = config
        self.profile = profile
        self._rules_path = Path(self._config.get("storage.rules_file", "./data/rules.json"))
        self._features_path = Path(self._config.get("storage.features_file", "./data/features.json"))
        self._knowledge_dir = Path(self._config.get("storage.knowledge_dir", "./data/knowledge"))
        self._atomic_write = bool(self._config.get("storage.atomic_write", True))
        self._backups_enabled = bool(self._config.get("storage.backups_enabled", True))
        self._default_threshold = float(self._config.get("matching.default_threshold", 0.88))
        self._default_method = str(self._config.get("matching.method", "TM_CCOEFF_NORMED"))
        self._game_name = str(self._config.get("game.name", ""))

        self._profile_root = self._knowledge_dir / self.profile
        self._common_root = self._knowledge_dir / "common"

        self._rules_data: dict[str, Any] = self._default_rules()
        self._features_data: dict[str, Any] = self._default_features()
        self._templates_cache: dict[str, Template] = {}
        self._template_meta: dict[str, dict[str, Any]] = {}
        self._pending: list[_PendingAction] = []
        self._dirty_rules = False
        self._dirty_features = False

        try:
            self._ensure_directories()
            self.load()
        except FileNotFoundError:
            self._rules_data = self._default_rules()
            self._features_data = self._default_features()
            self._dirty_rules = True
            self._dirty_features = True
        except Exception as exc:  # pragma: no cover - defensive wrapper
            raise KnowledgeError(f"Failed to initialize knowledge base: {exc}") from exc

    def load(self) -> None:
        self._ensure_directories()
        self._rules_data = self._load_rules_data()
        self._features_data = self._load_features_data()
        self._normalize_profile_sections()
        self._rebuild_template_cache()
        self._dirty_rules = False
        self._dirty_features = False

    def save(self) -> None:
        self._apply_pending_actions()
        self._normalize_profile_sections()
        self._rebuild_features_from_rules()

        if self._backups_enabled:
            self._backup_file(self._rules_path)
            self._backup_file(self._features_path)

        self._write_json_atomic(self._rules_path, self._rules_data)
        self._write_json_atomic(self._features_path, self._features_data)

        self._dirty_rules = False
        self._dirty_features = False

    def reset(self) -> None:
        self._rules_data = self._default_rules()
        self._features_data = self._default_features()
        self._templates_cache.clear()
        self._template_meta.clear()
        self._pending.clear()

        profile_dir = self._profile_root
        if profile_dir.exists():
            shutil.rmtree(profile_dir)
        self._ensure_directories()
        self.save()

    def categories(self) -> tuple[str, ...]:
        return self.CATEGORIES

    def list_templates(self, category: str | None = None, enabled_only: bool = True) -> list[Template]:
        if category is not None:
            self._validate_category(category)

        result: list[Template] = []
        for template in self._templates_cache.values():
            if category is not None and template.category != category:
                continue
            if enabled_only and not template.enabled:
                continue
            result.append(template)

        result.sort(key=lambda item: item.id)
        return result

    def add_template(
        self,
        category: str,
        image: "Frame",
        roi: "Rect",
        threshold: float | None = None,
        tags: list[str] | None = None,
        screen_tag: str = "",
        note: str = "",
    ) -> str:
        self._validate_category(category)
        self._validate_image(image)
        self._validate_rect(roi)

        match_threshold = float(threshold if threshold is not None else self._default_threshold)
        if not (0.0 <= match_threshold <= 1.0):
            raise KnowledgeError("Template threshold must be in range [0.0, 1.0].")

        template_id = self._generate_template_id()
        category_dir = self._profile_root / category
        category_dir.mkdir(parents=True, exist_ok=True)
        image_path = category_dir / f"{template_id}.png"

        image_to_save = np.ascontiguousarray(image)
        if not cv2.imwrite(str(image_path), image_to_save):
            raise KnowledgeError(f"Failed to write template image: {image_path}")

        timestamp = self._utc_now_iso()
        template_entry = {
            "id": template_id,
            "category": category,
            "image_path": self._normalize_path(image_path),
            "created_at": timestamp,
            "updated_at": timestamp,
            "enabled": True,
            "source": {
                "kind": "user_labeled_crop",
                "screen_tag": str(screen_tag),
                "note": str(note),
            },
            "match": {
                "method": self._default_method,
                "threshold": match_threshold,
            },
            "roi": {
                "x": int(roi.x),
                "y": int(roi.y),
                "w": int(roi.w),
                "h": int(roi.h),
            },
            "profile": self.profile,
            "tags": list(tags or []),
            "stats": {
                "times_used": 0,
                "times_matched": 0,
                "times_failed": 0,
            },
        }

        profile_block = self._profile_rules_block(self.profile)
        profile_block["templates"].append(template_entry)
        if category in self._SCREEN_CATEGORIES:
            profile_block.setdefault("screen_classifiers", {})
            profile_block["screen_classifiers"].setdefault(category, [])
            profile_block["screen_classifiers"][category].append(template_id)

        self._dirty_rules = True
        self._dirty_features = True
        self._rebuild_template_cache()
        return template_id

    def get_templates_for_screen_classification(self) -> list[Template]:
        templates: list[Template] = []
        for category in self._SCREEN_CATEGORIES:
            templates.extend(self.get_templates_for_category(category))
        templates.sort(key=lambda item: item.id)
        return templates

    def get_templates_for_category(self, category: str) -> list[Template]:
        self._validate_category(category)
        return self.list_templates(category=category, enabled_only=True)

    def increment_match_stats(self, template_id: str, matched: bool) -> None:
        template_entry = self._find_template_entry(template_id)
        if template_entry is None:
            raise KnowledgeError(f"Template not found: {template_id}")

        stats = template_entry.setdefault("stats", {})
        stats["times_used"] = int(stats.get("times_used", 0)) + 1
        if matched:
            stats["times_matched"] = int(stats.get("times_matched", 0)) + 1
        else:
            stats["times_failed"] = int(stats.get("times_failed", 0)) + 1
        template_entry["updated_at"] = self._utc_now_iso()
        self._dirty_rules = True
        self._dirty_features = True

    def add_click_point(
        self,
        category: str,
        x: int,
        y: int,
        roi: Rect | None = None,
        note: str = "",
    ) -> None:
        self._validate_category(category)
        if x < 0 or y < 0:
            raise KnowledgeError("Click point coordinates must be non-negative.")

        payload = {
            "category": category,
            "x": int(x),
            "y": int(y),
            "note": str(note),
        }
        if roi is not None:
            self._validate_rect(roi)
            payload["roi"] = {"x": roi.x, "y": roi.y, "w": roi.w, "h": roi.h}
        self._pending.append(_PendingAction(kind="click_point", payload=payload))
        self._dirty_rules = True

    def add_template_from_frame(
        self,
        frame: "Frame",
        rect: Rect,
        category: str,
        threshold: float | None = None,
        tags: list[str] | None = None,
        screen_tag: str = "",
        note: str = "",
    ) -> str:
        self._validate_image(frame)
        self._validate_rect(rect)
        h, w = frame.shape[:2]
        if rect.x + rect.w > w or rect.y + rect.h > h:
            raise KnowledgeError("Template rectangle is outside frame bounds.")
        crop = frame[rect.y:rect.y + rect.h, rect.x:rect.x + rect.w].copy()
        return self.add_template(
            category=category,
            image=crop,
            roi=rect,
            threshold=threshold,
            tags=tags,
            screen_tag=screen_tag,
            note=note,
        )

    def undo_last_annotation(self) -> bool:
        if self._pending:
            self._pending.pop()
            self._dirty_rules = True
            return True

        profile_block = self._profile_rules_block(self.profile)
        templates = profile_block.get("templates", [])
        if templates:
            last = templates.pop()
            image_path = Path(str(last.get("image_path", "")))
            try:
                if image_path.exists():
                    image_path.unlink()
            except OSError as exc:
                raise KnowledgeError(f"Failed to remove template image: {image_path}") from exc

            category = str(last.get("category", ""))
            if category in self._SCREEN_CATEGORIES:
                classifier_ids = profile_block.get("screen_classifiers", {}).get(category, [])
                profile_block.get("screen_classifiers", {}).setdefault(category, [])
                profile_block["screen_classifiers"][category] = [tid for tid in classifier_ids if tid != last.get("id")]

            self._dirty_rules = True
            self._dirty_features = True
            self._rebuild_template_cache()
            return True

        return False

    def _default_rules(self) -> dict[str, Any]:
        return {
            "version": 1,
            "game": self._game_name,
            "profiles": {
                self.profile: {
                    "templates": [],
                    "screen_classifiers": {
                        "room_screen": [],
                        "passage_screen": [],
                        "event_screen": [],
                    },
                    "click_points": {},
                },
                "common": {
                    "templates": [],
                    "screen_classifiers": {
                        "room_screen": [],
                        "passage_screen": [],
                        "event_screen": [],
                    },
                    "click_points": {},
                },
            },
        }

    def _default_features(self) -> dict[str, Any]:
        return {
            "version": 1,
            "profile": self.profile,
            "generated_at": self._utc_now_iso(),
            "templates": {},
        }

    def _ensure_directories(self) -> None:
        self._rules_path.parent.mkdir(parents=True, exist_ok=True)
        self._features_path.parent.mkdir(parents=True, exist_ok=True)
        self._profile_root.mkdir(parents=True, exist_ok=True)
        self._common_root.mkdir(parents=True, exist_ok=True)
        for category in self.CATEGORIES:
            (self._profile_root / category).mkdir(parents=True, exist_ok=True)
            (self._common_root / category).mkdir(parents=True, exist_ok=True)

    def _load_rules_data(self) -> dict[str, Any]:
        if not self._rules_path.exists():
            return self._default_rules()
        try:
            with self._rules_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise KnowledgeError(f"Invalid rules.json: {exc}") from exc
        if not isinstance(data, dict):
            raise KnowledgeError("rules.json root must be an object.")
        return data

    def _load_features_data(self) -> dict[str, Any]:
        if not self._features_path.exists():
            return self._default_features()
        try:
            with self._features_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise KnowledgeError(f"Invalid features.json: {exc}") from exc
        if not isinstance(data, dict):
            raise KnowledgeError("features.json root must be an object.")
        return data

    def _normalize_profile_sections(self) -> None:
        self._rules_data.setdefault("version", 1)
        self._rules_data.setdefault("game", self._game_name)
        profiles = self._rules_data.setdefault("profiles", {})
        if not isinstance(profiles, dict):
            raise KnowledgeError("rules.json field 'profiles' must be an object.")

        for profile_name in (self.profile, "common"):
            block = profiles.setdefault(profile_name, {})
            if not isinstance(block, dict):
                raise KnowledgeError(f"Profile block must be an object: {profile_name}")
            block.setdefault("templates", [])
            block.setdefault("screen_classifiers", {})
            block.setdefault("click_points", {})
            for category in self._SCREEN_CATEGORIES:
                block["screen_classifiers"].setdefault(category, [])

        self._features_data.setdefault("version", 1)
        self._features_data["profile"] = self.profile
        self._features_data.setdefault("generated_at", self._utc_now_iso())
        self._features_data.setdefault("templates", {})

    def _profile_rules_block(self, profile_name: str) -> dict[str, Any]:
        profiles = self._rules_data.setdefault("profiles", {})
        block = profiles.setdefault(profile_name, {})
        block.setdefault("templates", [])
        block.setdefault("screen_classifiers", {})
        block.setdefault("click_points", {})
        for category in self._SCREEN_CATEGORIES:
            block["screen_classifiers"].setdefault(category, [])
        return block

    def _rebuild_template_cache(self) -> None:
        self._templates_cache.clear()
        self._template_meta.clear()

        for profile_name in ("common", self.profile):
            block = self._profile_rules_block(profile_name)
            templates = block.get("templates", [])
            if not isinstance(templates, list):
                raise KnowledgeError(f"templates for profile '{profile_name}' must be a list.")
            for entry in templates:
                template = self._template_from_entry(entry)
                self._templates_cache[template.id] = template
                self._template_meta[template.id] = entry

        self._rebuild_features_from_rules()

    def _rebuild_features_from_rules(self) -> None:
        templates_section: dict[str, Any] = {}
        for template_id, template in self._templates_cache.items():
            image_path = Path(self._template_meta[template_id]["image_path"])
            checksum = self._compute_checksum(image_path)
            templates_section[template_id] = {
                "image_path": self._normalize_path(image_path),
                "category": template.category,
                "shape": list(template.image.shape),
                "roi": {
                    "x": template.roi.x,
                    "y": template.roi.y,
                    "w": template.roi.w,
                    "h": template.roi.h,
                },
                "match": {
                    "method": self._default_method,
                    "threshold": float(template.threshold),
                },
                "checksum": checksum,
                "enabled": bool(template.enabled),
                "updated_at": str(self._template_meta[template_id].get("updated_at", self._utc_now_iso())),
            }
        self._features_data = {
            "version": 1,
            "profile": self.profile,
            "generated_at": self._utc_now_iso(),
            "templates": templates_section,
        }

    def _template_from_entry(self, entry: dict[str, Any]) -> Template:
        if not isinstance(entry, dict):
            raise KnowledgeError("Template entry must be an object.")
        category = str(entry.get("category", ""))
        self._validate_category(category)
        image_path = Path(str(entry.get("image_path", "")))
        if not image_path.exists():
            raise KnowledgeError(f"Template image file not found: {image_path}")

        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            raise KnowledgeError(f"Failed to load template image: {image_path}")

        roi_data = entry.get("roi", {})
        rect = Rect(
            x=int(roi_data.get("x", 0)),
            y=int(roi_data.get("y", 0)),
            w=int(roi_data.get("w", image.shape[1])),
            h=int(roi_data.get("h", image.shape[0])),
        )
        self._validate_rect(rect)

        match_data = entry.get("match", {})
        threshold = float(match_data.get("threshold", self._default_threshold))

        return Template(
            id=str(entry.get("id", "")),
            category=category,
            image=image,
            roi=rect,
            threshold=threshold,
            enabled=bool(entry.get("enabled", True)),
        )

    def _find_template_entry(self, template_id: str) -> dict[str, Any] | None:
        for profile_name in (self.profile, "common"):
            block = self._profile_rules_block(profile_name)
            for entry in block.get("templates", []):
                if str(entry.get("id", "")) == template_id:
                    return entry
        return None

    def _apply_pending_actions(self) -> None:
        if not self._pending:
            return
        block = self._profile_rules_block(self.profile)
        click_points = block.setdefault("click_points", {})
        for action in self._pending:
            if action.kind == "click_point":
                payload = dict(action.payload)
                category = str(payload.pop("category"))
                click_points.setdefault(category, [])
                click_points[category].append(payload)
            else:
                raise KnowledgeError(f"Unsupported pending action kind: {action.kind}")
        self._pending.clear()

    def _write_json_atomic(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if self._atomic_write:
            fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_name, path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        else:
            with path.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())

    def _backup_file(self, path: Path) -> None:
        if not path.exists():
            return
        backup_path = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup_path)

    def _compute_checksum(self, path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                hasher.update(chunk)
        return "sha256:" + hasher.hexdigest()

    def _generate_template_id(self) -> str:
        base = datetime.now(timezone.utc).strftime("tpl_%Y%m%d_%H%M%S")
        index = 1
        while True:
            template_id = f"{base}_{index:03d}"
            if template_id not in self._templates_cache:
                return template_id
            index += 1

    def _validate_category(self, category: str) -> None:
        if category not in self.CATEGORIES:
            raise UnknownCategoryError(f"Unknown category: {category}")

    def _validate_rect(self, rect: Rect) -> None:
        if rect.w <= 0 or rect.h <= 0:
            raise KnowledgeError("Rect width and height must be positive.")
        if rect.x < 0 or rect.y < 0:
            raise KnowledgeError("Rect coordinates must be non-negative.")

    def _validate_image(self, image: "Frame") -> None:
        if not isinstance(image, np.ndarray):
            raise KnowledgeError("Template image must be a numpy array.")
        if image.size == 0:
            raise KnowledgeError("Template image must not be empty.")
        if image.ndim not in (2, 3):
            raise KnowledgeError("Template image must be 2D or 3D numpy array.")

    def _normalize_path(self, path: Path) -> str:
        return path.as_posix()

    def _utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
