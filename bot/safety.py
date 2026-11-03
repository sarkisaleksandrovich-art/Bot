from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import BotConfig
    from .knowledge import TemplateRecord
    from .screenshot import Frame
    from .vision import VisionEngine


class SafetyError(Exception):
    """Базовое исключение модуля безопасности."""


class DangerDetectedError(SafetyError):
    """Выбрасывается, когда на кадре обнаружен danger и политика требует остановки."""


class ForbiddenActionError(SafetyError):
    """Выбрасывается, когда запрошено запрещённое политикой действие."""


class SafetyManager:
    """Менеджер правил безопасности для исследовательского бота."""

    _NETWORK_ERROR_NAMES: tuple[str, ...] = (
        "TimeoutError", "ConnectionError", "BrokenPipeError", "ConnectionResetError", "ConnectionAbortedError", "OSError", "ADBConnectionError", "ADBCommandError", "ScreenshotError",
    )

    def __init__(self, config: "BotConfig", logger: "logging.Logger") -> None:
        self.config = config; self.logger = logger
        safety_cfg = self._section("safety")
        self.allow_ads = self._read_bool(safety_cfg, "allow_ads", not self._read_bool(safety_cfg, "block_ads", True))
        self.allow_purchase = self._read_bool(safety_cfg, "allow_purchase", not self._read_bool(safety_cfg, "block_shop", True))
        self.allow_premium_currency = self._read_bool(safety_cfg, "allow_premium_currency", False)
        self.allow_pvp = self._read_bool(safety_cfg, "allow_pvp", not self._read_bool(safety_cfg, "block_pvp", True))
        self.stop_on_danger = self._read_bool(safety_cfg, "stop_on_danger", True)
        self.max_io_errors = self._read_int(safety_cfg, "max_io_errors", 5, minimum=1)
        self._consecutive_network_errors = 0

    def ensure_safe_frame(self, frame: "Frame", vision: "VisionEngine", templates: list["TemplateRecord"]) -> None:
        try:
            has_danger = vision.has_any(frame=frame, templates=templates, categories=["danger"])
        except Exception as exc:
            raise SafetyError(f"Не удалось проверить кадр на danger: {exc}") from exc
        if has_danger:
            self.logger.error("Обнаружена категория danger на кадре.")
            if self.stop_on_danger:
                raise DangerDetectedError("Обнаружена категория danger; выполнение остановлено политикой безопасности.")

    def is_action_allowed(self, action_name: str) -> bool:
        normalized = self._normalize_action_name(action_name)
        if normalized == "shop": return self.allow_purchase
        if normalized == "ads": return self.allow_ads
        if normalized == "pvp": return self.allow_pvp
        if normalized == "premium_currency": return self.allow_premium_currency
        if normalized in {"explore", "teach", "loot", "mob", "event_choice", "event_skip", "skip"}: return True
        return False

    def ensure_action_allowed(self, action_name: str) -> None:
        if not self.is_action_allowed(action_name):
            raise ForbiddenActionError(f"Действие '{action_name}' запрещено политикой безопасности.")

    def is_rule_action_allowed(self, rule: dict[str, Any] | None) -> bool:
        if not isinstance(rule, dict): return False
        if bool(rule.get("dangerous", False)) and self.stop_on_danger: return False
        action_name = rule.get("action_name", rule.get("action"))
        if not isinstance(action_name, str) or not action_name.strip(): return False
        if bool(rule.get("uses_premium_currency", False)) and not self.allow_premium_currency: return False
        if bool(rule.get("purchase", False)) and not self.allow_purchase: return False
        if bool(rule.get("ad", rule.get("ads", False))) and not self.allow_ads: return False
        if bool(rule.get("pvp", False)) and not self.allow_pvp: return False
        return self.is_action_allowed(action_name)

    def ensure_rule_action_allowed(self, rule: dict[str, Any] | None) -> None:
        if not self.is_rule_action_allowed(rule): raise ForbiddenActionError("Правило действия запрещено политикой безопасности или помечено как dangerous.")

    def random_delay(self) -> float:
        input_cfg = self._section("input"); safety_cfg = self._section("safety")
        delay_s = self._resolve_random_delay_seconds(input_cfg, safety_cfg)
        if delay_s > 0: time.sleep(delay_s)
        return delay_s

    def record_network_error(self, error: Exception | None = None) -> int:
        self._consecutive_network_errors += 1
        self.logger.warning("Подряд идущая сетевая/IO ошибка #%s%s", self._consecutive_network_errors, f": {error}" if error is not None else "")
        return self._consecutive_network_errors

    def reset_network_error_count(self) -> None:
        if self._consecutive_network_errors: self.logger.debug("Счётчик подряд идущих сетевых/IO ошибок сброшен: %s -> 0", self._consecutive_network_errors)
        self._consecutive_network_errors = 0

    def consecutive_network_errors(self) -> int: return self._consecutive_network_errors

    def can_retry_after_network_error(self, error: Exception | None = None) -> bool:
        return self.record_network_error(error) < self.max_io_errors

    def ensure_retry_allowed(self) -> None:
        if self._consecutive_network_errors >= self.max_io_errors: raise SafetyError(f"Превышен лимит подряд идущих сетевых/IO ошибок: {self._consecutive_network_errors}/{self.max_io_errors}.")

    def is_network_error(self, error: BaseException) -> bool: return error.__class__.__name__ in self._NETWORK_ERROR_NAMES

    def run_with_network_retry(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        while True:
            try: result = func(*args, **kwargs)
            except Exception as exc:
                if not self.is_network_error(exc): raise
                if not self.can_retry_after_network_error(exc): raise SafetyError(f"Достигнут лимит повторов при сетевых/IO ошибках. Последняя ошибка: {exc}") from exc
                self.random_delay(); continue
            self.reset_network_error_count(); return result

    def _normalize_action_name(self, action_name: str) -> str:
        normalized = action_name.strip().lower()
        return {"purchase": "shop", "buy": "shop", "premium": "premium_currency", "premium-currency": "premium_currency"}.get(normalized, normalized)

    def _section(self, name: str) -> dict[str, Any]:
        raw = getattr(self.config, "raw", {}); section = raw.get(name, {}) if isinstance(raw, dict) else {}
        return section if isinstance(section, dict) else {}

    @staticmethod
    def _read_bool(section: dict[str, Any], key: str, default: bool) -> bool:
        value = section.get(key, default); return value if isinstance(value, bool) else default

    @staticmethod
    def _read_int(section: dict[str, Any], key: str, default: int, minimum: int | None = None) -> int:
        value = section.get(key, default); value = value if isinstance(value, int) else default
        return max(value, minimum) if minimum is not None else value

    def _resolve_random_delay_seconds(self, input_cfg: dict[str, Any], safety_cfg: dict[str, Any]) -> float:
        for cfg in (input_cfg, safety_cfg):
            if "random_delay_ms" in cfg:
                try: return max(0.0, float(cfg["random_delay_ms"]) / 1000.0)
                except (TypeError, ValueError): pass
        for cfg in (input_cfg, safety_cfg):
            if "random_delay_min_ms" in cfg or "random_delay_max_ms" in cfg:
                try:
                    low = float(cfg.get("random_delay_min_ms", 50)); high = float(cfg.get("random_delay_max_ms", low))
                    return max(0.0, random.uniform(min(low, high), max(low, high)) / 1000.0)
                except (TypeError, ValueError): pass
        return random.uniform(0.05, 0.15)

