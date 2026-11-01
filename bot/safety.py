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
        "TimeoutError",
        "ConnectionError",
        "BrokenPipeError",
        "ConnectionResetError",
        "ConnectionAbortedError",
        "OSError",
        "ADBConnectionError",
        "ADBCommandError",
        "ScreenshotError",
    )

    def __init__(self, config: "BotConfig", logger: "logging.Logger") -> None:
        self.config = config
        self.logger = logger

        safety_cfg = self._section("safety")

        self.allow_ads: bool = self._read_bool(
            section=safety_cfg,
            key="allow_ads",
            default=not self._read_bool(safety_cfg, "block_ads", True),
        )
        self.allow_purchase: bool = self._read_bool(
            section=safety_cfg,
            key="allow_purchase",
            default=not self._read_bool(safety_cfg, "block_shop", True),
        )
        self.allow_premium_currency: bool = self._read_bool(
            section=safety_cfg,
            key="allow_premium_currency",
            default=False,
        )
        self.allow_pvp: bool = self._read_bool(
            section=safety_cfg,
            key="allow_pvp",
            default=not self._read_bool(safety_cfg, "block_pvp", True),
        )

        self.stop_on_danger: bool = self._read_bool(safety_cfg, "stop_on_danger", True)
        self.max_io_errors: int = self._read_int(safety_cfg, "max_io_errors", 5, minimum=1)

        self._consecutive_network_errors: int = 0

    def ensure_safe_frame(
        self,
        frame: "Frame",
        vision: "VisionEngine",
        templates: list["TemplateRecord"],
    ) -> None:
        """
        Проверяет, что на кадре не найден category "danger".

        :raises DangerDetectedError: если найден danger и safety.stop_on_danger = true
        :raises SafetyError: при ошибке проверки
        """
        try:
            has_danger = vision.has_any(frame=frame, templates=templates, categories=["danger"])
        except Exception as exc:  # pragma: no cover - зависит от внешнего vision
            raise SafetyError(f"Не удалось проверить кадр на danger: {exc}") from exc

        if has_danger:
            self.logger.error("Обнаружена категория danger на кадре.")
            if self.stop_on_danger:
                raise DangerDetectedError("Обнаружена категория danger; выполнение остановлено политикой безопасности.")

    def is_action_allowed(self, action_name: str) -> bool:
        """
        Возвращает, разрешено ли действие политикой безопасности.

        Должны поддерживаться как минимум:
          "shop", "ads", "pvp", "explore", "teach"

        :return: bool
        """
        normalized = self._normalize_action_name(action_name)

        if normalized == "shop":
            return self.allow_purchase
        if normalized == "ads":
            return self.allow_ads
        if normalized == "pvp":
            return self.allow_pvp
        if normalized == "premium_currency":
            return self.allow_premium_currency
        if normalized in {"explore", "teach"}:
            return True

        return False

    def ensure_action_allowed(self, action_name: str) -> None:
        """
        :raises ForbiddenActionError: если действие запрещено
        """
        if not self.is_action_allowed(action_name):
            raise ForbiddenActionError(
                f"Действие '{action_name}' запрещено политикой безопасности. "
                f"allow_ads={self.allow_ads}, "
                f"allow_purchase={self.allow_purchase}, "
                f"allow_premium_currency={self.allow_premium_currency}, "
                f"allow_pvp={self.allow_pvp}."
            )

    def is_rule_action_allowed(self, rule: dict[str, Any] | None) -> bool:
        """
        Проверяет, разрешено ли выполнять действие по правилу с учётом danger и политик.

        Ожидаемые поля правила:
          - action / action_name: str
          - dangerous: bool (необязательное)
          - uses_premium_currency: bool (необязательное)
          - purchase: bool (необязательное)
          - ad / ads: bool (необязательное)
          - pvp: bool (необязательное)

        Если правило отсутствует или не содержит понятного действия, возвращает False.
        """
        if not isinstance(rule, dict):
            return False

        if bool(rule.get("dangerous", False)) and self.stop_on_danger:
            return False

        action_name = rule.get("action_name", rule.get("action"))
        if not isinstance(action_name, str) or not action_name.strip():
            return False

        if bool(rule.get("uses_premium_currency", False)) and not self.allow_premium_currency:
            return False

        if bool(rule.get("purchase", False)) and not self.allow_purchase:
            return False

        if bool(rule.get("ad", rule.get("ads", False))) and not self.allow_ads:
            return False

        if bool(rule.get("pvp", False)) and not self.allow_pvp:
            return False

        return self.is_action_allowed(action_name)

    def ensure_rule_action_allowed(self, rule: dict[str, Any] | None) -> None:
        """Выбрасывает ForbiddenActionError, если правило выполнять нельзя."""
        if not self.is_rule_action_allowed(rule):
            raise ForbiddenActionError("Правило действия запрещено политикой безопасности или помечено как dangerous.")

    def random_delay(self) -> float:
        """
        Делает случайную задержку на основе конфига и возвращает фактическое время сна в секундах.

        Поддерживаемые ключи:
          input.random_delay_ms: фиксированная задержка
          input.random_delay_min_ms + input.random_delay_max_ms: диапазон
          safety.random_delay_ms: фиксированная задержка
          safety.random_delay_min_ms + safety.random_delay_max_ms: диапазон

        Если ключи не заданы, используется безопасный небольшой диапазон по умолчанию.
        """
        input_cfg = self._section("input")
        safety_cfg = self._section("safety")

        delay_s = self._resolve_random_delay_seconds(input_cfg, safety_cfg)
        if delay_s > 0:
            time.sleep(delay_s)
        return delay_s

    def record_network_error(self, error: Exception | None = None) -> int:
        """
        Увеличивает счётчик подряд идущих сетевых/IO ошибок и возвращает текущее значение.
        """
        self._consecutive_network_errors += 1
        self.logger.warning(
            "Подряд идущая сетевая/IO ошибка #%s%s",
            self._consecutive_network_errors,
            f": {error}" if error is not None else "",
        )
        return self._consecutive_network_errors

    def reset_network_error_count(self) -> None:
        """Сбрасывает счётчик подряд идущих сетевых/IO ошибок."""
        if self._consecutive_network_errors:
            self.logger.debug(
                "Счётчик подряд идущих сетевых/IO ошибок сброшен: %s -> 0",
                self._consecutive_network_errors,
            )
        self._consecutive_network_errors = 0

    def consecutive_network_errors(self) -> int:
        """Возвращает текущее число подряд идущих сетевых/IO ошибок."""
        return self._consecutive_network_errors

    def can_retry_after_network_error(self, error: Exception | None = None) -> bool:
        """
        Регистрирует сетевую/IO ошибку и сообщает, можно ли делать ещё одну попытку.

        Возвращает False, когда достигнут лимит max_io_errors.
        """
        current = self.record_network_error(error)
        return current < self.max_io_errors

    def ensure_retry_allowed(self) -> None:
        """
        Проверяет, не превышен ли лимит подряд идущих сетевых/IO ошибок.

        :raises SafetyError: если лимит исчерпан
        """
        if self._consecutive_network_errors >= self.max_io_errors:
            raise SafetyError(
                "Превышен лимит подряд идущих сетевых/IO ошибок: "
                f"{self._consecutive_network_errors}/{self.max_io_errors}."
            )

    def is_network_error(self, error: BaseException) -> bool:
        """Эвристически определяет, относится ли исключение к сетевой/IO ошибке."""
        return error.__class__.__name__ in self._NETWORK_ERROR_NAMES

    def run_with_network_retry(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """
        Выполняет func с ограничением повторов при сетевых/IO ошибках.

        После успешного выполнения счётчик ошибок сбрасывается.
        Неподходящие по типу ошибки пробрасываются сразу.
        """
        while True:
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                if not self.is_network_error(exc):
                    raise
                if not self.can_retry_after_network_error(exc):
                    raise SafetyError(
                        "Достигнут лимит повторов при сетевых/IO ошибках. "
                        f"Последняя ошибка: {exc}"
                    ) from exc
                self.random_delay()
                continue

            self.reset_network_error_count()
            return result

    def _normalize_action_name(self, action_name: str) -> str:
        normalized = action_name.strip().lower()
        aliases = {
            "purchase": "shop",
            "buy": "shop",
            "premium": "premium_currency",
            "premium_currency": "premium_currency",
            "premium-currency": "premium_currency",
        }
        return aliases.get(normalized, normalized)

    def _section(self, name: str) -> dict[str, Any]:
        raw = getattr(self.config, "raw", {})
        section = raw.get(name, {}) if isinstance(raw, dict) else {}
        return section if isinstance(section, dict) else {}

    @staticmethod
    def _read_bool(section: dict[str, Any], key: str, default: bool) -> bool:
        value = section.get(key, default)
        if isinstance(value, bool):
            return value
        return default

    @staticmethod
    def _read_int(section: dict[str, Any], key: str, default: int, minimum: int | None = None) -> int:
        value = section.get(key, default)
        if not isinstance(value, int):
            value = default
        if minimum is not None and value < minimum:
            value = minimum
        return value

    def _resolve_random_delay_seconds(self, *sections: dict[str, Any]) -> float:
        for section in sections:
            fixed_ms = section.get("random_delay_ms")
            if isinstance(fixed_ms, (int, float)) and fixed_ms >= 0:
                return float(fixed_ms) / 1000.0

            min_ms = section.get("random_delay_min_ms")
            max_ms = section.get("random_delay_max_ms")
            if isinstance(min_ms, (int, float)) and isinstance(max_ms, (int, float)):
                low = max(0.0, float(min_ms))
                high = max(low, float(max_ms))
                return random.uniform(low, high) / 1000.0

        return random.uniform(0.15, 0.35)
