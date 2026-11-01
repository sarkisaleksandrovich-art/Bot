from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

from bot.floor import FloorExploreError, FloorExplorer
from bot.safety import SafetyStop

if TYPE_CHECKING:
    from bot.config import Config
    from bot.knowledge import KnowledgeBase


class ExplorerError(Exception):
    ...


RunStatus = Literal["completed", "stopped", "failed"]


@dataclass(frozen=True)
class ExploreResult:
    status: RunStatus
    floors_completed: int
    rooms_cleared: int
    rooms_skipped: int
    rooms_failed: int
    reason: str


class Explorer:
    def __init__(
        self,
        floor_explorer: "FloorExplorer",
        teacher_ui,
        config: "Config",
        knowledge: "KnowledgeBase",
    ) -> None:
        if not hasattr(teacher_ui, "pause_requested"):
            raise ExplorerError("teacher_ui.pause_requested is required")
        self.floor_explorer = floor_explorer
        self.teacher_ui = teacher_ui
        self.config = config
        self.knowledge = knowledge

    def run(self, floor_from: int, floor_to: int) -> ExploreResult:
        if isinstance(floor_from, bool) or isinstance(floor_to, bool):
            raise ExplorerError("floor_from/floor_to must be int")
        if floor_from > floor_to:
            raise ExplorerError("floor_from must be <= floor_to")
        floors_completed = 0
        rooms_cleared = 0
        rooms_skipped = 0
        rooms_failed = 0
        try:
            for floor_number in range(floor_from, floor_to + 1):
                if self.teacher_ui.pause_requested():
                    return ExploreResult("stopped", floors_completed, rooms_cleared, rooms_skipped, rooms_failed, "pause requested")
                result = self.floor_explorer.run(floor_number)
                rooms_cleared += result.rooms_cleared
                rooms_skipped += result.rooms_skipped
                rooms_failed += result.rooms_failed
                if result.status == "completed":
                    floors_completed += 1
                    continue
                if result.status == "stopped":
                    return ExploreResult("stopped", floors_completed, rooms_cleared, rooms_skipped, rooms_failed, result.reason)
                return ExploreResult("failed", floors_completed, rooms_cleared, rooms_skipped, rooms_failed, result.reason)
            return ExploreResult("completed", floors_completed, rooms_cleared, rooms_skipped, rooms_failed, "range completed")
        except SafetyStop:
            raise
        except FloorExploreError:
            raise
        except Exception as exc:
            raise ExplorerError(str(exc)) from exc
