from __future__ import annotations

from typing import Optional, Sequence


class SafetyStop(RuntimeError):
    pass


class SafetyViolation(RuntimeError):
    pass


class SafetyService:
    def __init__(
        self,
        block_purchases: bool = True,
        block_ads: bool = True,
        block_pvp: bool = True,
        stop_on_danger: bool = True,
        enforce_safe_actions: bool = False,
    ) -> None:
        self.block_purchases = bool(block_purchases)
        self.block_ads = bool(block_ads)
        self.block_pvp = bool(block_pvp)
        self.stop_on_danger = bool(stop_on_danger)
        self.enforce_safe_actions = bool(enforce_safe_actions)

    def check_danger(self, frame, vision: "VisionService", templates: Sequence["Template"]) -> bool:
        match = vision.best_match_by_category(frame=frame, templates=templates, category="danger")
        found = match is not None
        if found and self.stop_on_danger:
            raise SafetyStop("danger detected")
        return found

    def validate_tap(self, x: int, y: int, allowed_rois: Optional[list] = None) -> None:
        if not self.enforce_safe_actions:
            return
        if not allowed_rois:
            raise SafetyViolation("tap is not allowed: no allowed_rois configured")
        for roi in allowed_rois:
            if not isinstance(roi, (list, tuple)) or len(roi) != 4:
                continue
            rx, ry, rw, rh = [int(v) for v in roi]
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return
        raise SafetyViolation(f"tap is outside allowed_rois: ({x}, {y})")

    def assert_action_allowed(self, action_name: str) -> None:
        normalized = (action_name or "").strip().lower()
        if normalized == "purchase" and self.block_purchases:
            raise SafetyViolation("purchase is blocked")
        if normalized == "ad" and self.block_ads:
            raise SafetyViolation("ad is blocked")
        if normalized == "pvp" and self.block_pvp:
            raise SafetyViolation("pvp is blocked")
