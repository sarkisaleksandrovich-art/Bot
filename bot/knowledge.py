from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple, Any

import cv2
import numpy as np


TEMPLATE_CATEGORIES: Tuple[str, ...] = (
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


class KnowledgeError(RuntimeError):
    pass


class KnowledgeBase:
    def __init__(
        self,
        rules_file: str,
        features_file: str,
        knowledge_dir: str,
    ) -> None:
        self.rules_file = os.path.abspath(rules_file)
        self.features_file = os.path.abspath(features_file)
        self.knowledge_dir = os.path.abspath(knowledge_dir)

        self.templates_root = os.path.join(self.knowledge_dir, "templates")
        self.common_dir = os.path.join(self.knowledge_dir, "common")
        self.common_templates_root = os.path.join(self.common_dir, "templates")

        self._rules: Dict[str, Any] = {}
        self._features: Dict[str, Any] = {}
        self._pending_annotations: List[Dict[str, Any]] = []
        self._loaded = False

        self.reset()

    def load(self) -> None:
        if not os.path.exists(self.rules_file):
            raise FileNotFoundError(self.rules_file)
        if not os.path.exists(self.features_file):
            raise FileNotFoundError(self.features_file)

        try:
            with open(self.rules_file, "r", encoding="utf-8") as f:
                rules = json.load(f)
            with open(self.features_file, "r", encoding="utf-8") as f:
                features = json.load(f)
        except json.JSONDecodeError as exc:
            raise KnowledgeError(f"Failed to parse knowledge files: {exc}") from exc
        except OSError as exc:
            raise KnowledgeError(f"Failed to read knowledge files: {exc}") from exc

        self._validate_rules(rules)
        self._validate_features(features)

        self._rules = rules
        self._features = features
        self._pending_annotations = []
        self._loaded = True
        self._ensure_directories()

    def save(self) -> None:
        try:
            self._ensure_directories()
            self._rules["updated_at"] = self._now_iso()
            self._features["updated_at"] = self._now_iso()
            self._atomic_write_json(self.rules_file, self._rules)
            self._atomic_write_json(self.features_file, self._features)
            self._pending_annotations = []
            self._loaded = True
        except Exception as exc:
            if isinstance(exc, KnowledgeError):
                raise
            raise KnowledgeError(f"Failed to save knowledge: {exc}") from exc

    def reset(self) -> None:
        try:
            self._rules = {
                "version": 1,
                "categories": list(TEMPLATE_CATEGORIES),
                "templates": [],
                "screen_rules": {
                    "room_screen": {
                        "match_any_of_categories": ["room_screen"],
                        "threshold": 0.90,
                    },
                    "passage_screen": {
                        "match_any_of_categories": ["passage_screen"],
                        "threshold": 0.90,
                    },
                    "event_screen": {
                        "match_any_of_categories": ["event_screen"],
                        "threshold": 0.90,
                    },
                },
                "updated_at": self._now_iso(),
            }
            self._features = {
                "version": 1,
                "templates": {},
                "updated_at": self._now_iso(),
            }
            self._pending_annotations = []
            self._loaded = True
            self._ensure_directories()
        except Exception as exc:
            raise KnowledgeError(f"Failed to reset knowledge: {exc}") from exc

    def get_templates(
        self,
        profile: Optional[str] = None,
        categories: Optional[Sequence[str]] = None,
        enabled_only: bool = True,
    ) -> List["Template"]:
        self._ensure_ready()

        category_filter = None
        if categories is not None:
            category_filter = set(categories)
            unknown = [c for c in category_filter if c not in TEMPLATE_CATEGORIES]
            if unknown:
                raise KnowledgeError(f"Unknown categories requested: {unknown}")

        result: List["Template"] = []
        for item in self._rules.get("templates", []):
            if enabled_only and not bool(item.get("enabled", True)):
                continue
            if category_filter is not None and item.get("category") not in category_filter:
                continue
            if not self._template_matches_profile(item, profile):
                continue

            image_path = item.get("image_path", "")
            full_image_path = self._resolve_image_path(image_path)
            if not os.path.exists(full_image_path):
                continue

            result.append(
                self._make_template_object(
                    template_id=str(item["id"]),
                    category=str(item["category"]),
                    image_path=full_image_path,
                    roi=self._normalize_roi_tuple(item["roi"]),
                    threshold=float(item["threshold"]),
                    enabled=bool(item.get("enabled", True)),
                    profile=str(item.get("profile", "common")),
                )
            )
        return result

    def add_template(
        self,
        frame: "np.ndarray",
        bbox: Tuple[int, int, int, int],
        category: str,
        profile: str,
        roi: Tuple[int, int, int, int],
        threshold: float,
        notes: str = "",
        source_frame_path: Optional[str] = None,
    ) -> str:
        self._ensure_ready()
        self._validate_category(category)
        if frame is None or not isinstance(frame, np.ndarray):
            raise KnowledgeError("frame must be a numpy.ndarray")
        if frame.ndim not in (2, 3):
            raise KnowledgeError("frame must be a 2D or 3D numpy.ndarray")

        x, y, w, h = self._validate_bbox(frame, bbox)
        roi_tuple = self._normalize_roi_tuple(roi)
        threshold_value = float(threshold)
        if threshold_value <= 0.0 or threshold_value > 1.0:
            raise ValueError("threshold must be in range (0.0, 1.0]")

        cropped = frame[y:y + h, x:x + w].copy()
        if cropped.size == 0:
            raise ValueError("bbox results in empty crop")

        template_id = self._next_template_id()
        profile_name = self._normalize_profile_name(profile)
        relative_image_path, absolute_image_path = self._build_template_paths(
            category=category,
            template_id=template_id,
            profile=profile_name,
        )

        self._ensure_parent_dir(absolute_image_path)
        ok = cv2.imwrite(absolute_image_path, cropped)
        if not ok:
            raise KnowledgeError(f"Failed to save template image: {absolute_image_path}")

        created_at = self._now_iso()
        rule_record = {
            "id": template_id,
            "category": category,
            "image_path": relative_image_path.replace("\\", "/"),
            "created_at": created_at,
            "profile": profile_name,
            "roi": [int(roi_tuple[0]), int(roi_tuple[1]), int(roi_tuple[2]), int(roi_tuple[3])],
            "threshold": threshold_value,
            "enabled": True,
            "notes": str(notes or ""),
        }

        feature_record = {
            "width": int(w),
            "height": int(h),
            "sha256": self._sha256_file(absolute_image_path),
            "profile": profile_name,
            "category": category,
            "source_frame_path": source_frame_path,
            "bbox_on_source": [int(x), int(y), int(w), int(h)],
            "tags": ["user_labeled"],
            "last_used_at": None,
            "use_count": 0,
        }

        self._rules.setdefault("templates", []).append(rule_record)
        self._features.setdefault("templates", {})[template_id] = feature_record
        self._rules["updated_at"] = self._now_iso()
        self._features["updated_at"] = self._now_iso()

        self._pending_annotations.append(
            {
                "kind": "template",
                "template_id": template_id,
                "category": category,
                "profile": profile_name,
                "bbox": [int(x), int(y), int(w), int(h)],
            }
        )
        return template_id

    def list_categories(self) -> Tuple[str, ...]:
        return TEMPLATE_CATEGORIES

    def has_any_template(self, category: str, profile: Optional[str] = None) -> bool:
        self._validate_category(category)
        self._ensure_ready()
        for item in self._rules.get("templates", []):
            if item.get("category") != category:
                continue
            if not bool(item.get("enabled", True)):
                continue
            if self._template_matches_profile(item, profile):
                return True
        return False

    def add_click_point(
        self,
        action_name: str,
        point: Tuple[int, int],
        profile: str,
        notes: str = "",
    ) -> None:
        x, y = self._normalize_point(point)
        if not action_name or not isinstance(action_name, str):
            raise ValueError("action_name must be a non-empty string")
        self._ensure_ready()
        self._pending_annotations.append(
            {
                "kind": "click_point",
                "action_name": action_name,
                "point": [x, y],
                "profile": self._normalize_profile_name(profile),
                "notes": str(notes or ""),
                "created_at": self._now_iso(),
            }
        )

    def undo_last_annotation(self) -> bool:
        self._ensure_ready()
        if self._pending_annotations:
            last = self._pending_annotations.pop()
            if last.get("kind") == "template":
                template_id = str(last["template_id"])
                template_record = self._find_rule_template(template_id)
                if template_record is not None:
                    image_path = self._resolve_image_path(str(template_record.get("image_path", "")))
                    self._rules["templates"] = [
                        item for item in self._rules.get("templates", []) if str(item.get("id")) != template_id
                    ]
                    self._features.get("templates", {}).pop(template_id, None)
                    try:
                        if image_path and os.path.exists(image_path):
                            os.remove(image_path)
                    except OSError:
                        pass
                    self._rules["updated_at"] = self._now_iso()
                    self._features["updated_at"] = self._now_iso()
                    return True
                return False
            return True
        return False

    def note_template_used(self, template_id: str, times_used: int = 1) -> None:
        self._ensure_ready()
        if times_used <= 0:
            return
        templates = self._features.setdefault("templates", {})
        if template_id not in templates:
            raise KnowledgeError(f"Unknown template id: {template_id}")
        record = templates[template_id]
        current = int(record.get("use_count", 0))
        record["use_count"] = current + int(times_used)
        record["last_used_at"] = self._now_iso()
        self._features["updated_at"] = self._now_iso()

    def mark_match_used(self, template_id: str, times_used: int = 1) -> None:
        self.note_template_used(template_id, times_used=times_used)

    def get_rules_data(self) -> Dict[str, Any]:
        self._ensure_ready()
        return json.loads(json.dumps(self._rules, ensure_ascii=False))

    def get_features_data(self) -> Dict[str, Any]:
        self._ensure_ready()
        return json.loads(json.dumps(self._features, ensure_ascii=False))

    def _ensure_ready(self) -> None:
        if not self._loaded:
            self.reset()

    def _validate_rules(self, rules: Dict[str, Any]) -> None:
        if not isinstance(rules, dict):
            raise KnowledgeError("rules.json must contain an object")
        if int(rules.get("version", 0)) != 1:
            raise KnowledgeError("Unsupported rules.json version")
        categories = rules.get("categories")
        if categories != list(TEMPLATE_CATEGORIES):
            raise KnowledgeError("rules.json categories do not match contract")
        templates = rules.get("templates")
        if not isinstance(templates, list):
            raise KnowledgeError("rules.json.templates must be a list")
        for item in templates:
            self._validate_rule_template(item)

    def _validate_features(self, features: Dict[str, Any]) -> None:
        if not isinstance(features, dict):
            raise KnowledgeError("features.json must contain an object")
        if int(features.get("version", 0)) != 1:
            raise KnowledgeError("Unsupported features.json version")
        if not isinstance(features.get("templates"), dict):
            raise KnowledgeError("features.json.templates must be an object")

    def _validate_rule_template(self, item: Dict[str, Any]) -> None:
        required = [
            "id",
            "category",
            "image_path",
            "created_at",
            "profile",
            "roi",
            "threshold",
            "enabled",
            "notes",
        ]
        for key in required:
            if key not in item:
                raise KnowledgeError(f"Template entry missing required field: {key}")
        self._validate_category(str(item["category"]))
        self._normalize_roi_tuple(item["roi"])
        threshold_value = float(item["threshold"])
        if threshold_value <= 0.0 or threshold_value > 1.0:
            raise KnowledgeError("Template threshold must be in range (0.0, 1.0]")

    def _validate_category(self, category: str) -> None:
        if category not in TEMPLATE_CATEGORIES:
            raise ValueError(f"Unknown category: {category}")

    def _validate_bbox(self, frame: np.ndarray, bbox: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        if len(bbox) != 4:
            raise ValueError("bbox must be (x, y, w, h)")
        x, y, w, h = [int(v) for v in bbox]
        if w <= 0 or h <= 0:
            raise ValueError("bbox width and height must be positive")
        frame_h, frame_w = frame.shape[:2]
        if x < 0 or y < 0 or x + w > frame_w or y + h > frame_h:
            raise ValueError("bbox is outside frame bounds")
        return x, y, w, h

    def _normalize_roi_tuple(self, roi: Sequence[int]) -> Tuple[int, int, int, int]:
        if len(roi) != 4:
            raise ValueError("roi must contain 4 integers")
        x, y, w, h = [int(v) for v in roi]
        if w <= 0 or h <= 0:
            raise ValueError("roi width and height must be positive")
        if x < 0 or y < 0:
            raise ValueError("roi x and y must be non-negative")
        return x, y, w, h

    def _normalize_point(self, point: Tuple[int, int]) -> Tuple[int, int]:
        if len(point) != 2:
            raise ValueError("point must be (x, y)")
        x, y = int(point[0]), int(point[1])
        if x < 0 or y < 0:
            raise ValueError("point coordinates must be non-negative")
        return x, y

    def _normalize_profile_name(self, profile: str) -> str:
        name = (profile or "").strip()
        return name if name else "common"

    def _template_matches_profile(self, item: Dict[str, Any], profile: Optional[str]) -> bool:
        item_profile = str(item.get("profile", "common") or "common")
        if profile is None:
            return True
        wanted = self._normalize_profile_name(profile)
        if item_profile == "common":
            return True
        return item_profile == wanted

    def _build_template_paths(self, category: str, template_id: str, profile: str) -> Tuple[str, str]:
        if profile == "common":
            relative = os.path.join("common", "templates", category, f"{template_id}.png")
            absolute = os.path.join(self.knowledge_dir, relative)
        else:
            relative = os.path.join("templates", category, f"{profile}__{template_id}.png")
            absolute = os.path.join(self.knowledge_dir, relative)
        return relative, absolute

    def _resolve_image_path(self, image_path: str) -> str:
        normalized = image_path.replace("/", os.sep)
        if os.path.isabs(normalized):
            return normalized
        return os.path.join(self.knowledge_dir, normalized)

    def _next_template_id(self) -> str:
        max_num = 0
        for item in self._rules.get("templates", []):
            template_id = str(item.get("id", ""))
            if template_id.startswith("tpl_"):
                tail = template_id[4:]
                if tail.isdigit():
                    max_num = max(max_num, int(tail))
        return f"tpl_{max_num + 1:06d}"

    def _find_rule_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        for item in self._rules.get("templates", []):
            if str(item.get("id")) == template_id:
                return item
        return None

    def _ensure_directories(self) -> None:
        os.makedirs(self.knowledge_dir, exist_ok=True)
        os.makedirs(self.templates_root, exist_ok=True)
        os.makedirs(self.common_templates_root, exist_ok=True)
        for category in TEMPLATE_CATEGORIES:
            os.makedirs(os.path.join(self.templates_root, category), exist_ok=True)
            os.makedirs(os.path.join(self.common_templates_root, category), exist_ok=True)
        self._ensure_parent_dir(self.rules_file)
        self._ensure_parent_dir(self.features_file)

    def _ensure_parent_dir(self, path: str) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _atomic_write_json(self, path: str, data: Dict[str, Any]) -> None:
        self._ensure_parent_dir(path)
        directory = os.path.dirname(path) or "."
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            finally:
                pass
            raise

    def _sha256_file(self, path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _make_template_object(
        self,
        template_id: str,
        category: str,
        image_path: str,
        roi: Tuple[int, int, int, int],
        threshold: float,
        enabled: bool,
        profile: str,
    ) -> "Template":
        try:
            from .vision import Template
        except Exception as exc:
            raise KnowledgeError(f"Failed to import vision.Template: {exc}") from exc
        return Template(
            id=template_id,
            category=category,
            image_path=image_path,
            roi=roi,
            threshold=threshold,
            enabled=enabled,
            profile=profile,
        )
