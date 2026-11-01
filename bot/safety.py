from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.config import Config
    from bot.vision import Frame, Template, VisionService


class SafetyStop(Exception):
    """Аварийная остановка по правилам безопасности."""


class SafetyConfigError(Exception):
    """Ошибка конфигурации безопасности."""


@dataclass(frozen=True)
class _Policy:
    stop_on_danger: bool
    allow_ads: bool
    allow_purchase: bool
    allow_premium_currency: bool
    allow_pvp: bool
    min_action_interval_ms: int
    random_delay_min_ms: int
    random_delay_max_ms: int
    max_consecutive_network_errors: int


class SafetyService:
    """Сервис защитных правил для действий бота."""

    _KNOWN_ACTIONS: frozenset[str] = frozenset(
        {
            "purchase",
            "ad",
            "pvp",
            "tap",
            "back",
            "swipe",
            "enter_room",
            "next_floor",
        }
    )

    def __init__(self, config: "Config") -> None:
        """
        Исключения:
            - SafetyConfigError
        """
        self._config = config
        self._policy = self._load_policy(config)
        self._last_action_monotonic: float | None = None
        self._consecutive_network_errors: int = 0

    def check_danger(self, frame: "Frame", vision: "VisionService", templates: list["Template"]) -> None:
        """
        Если найден danger и stop_on_danger=true, выбрасывает SafetyStop.

        Возвращает:
            None

        Исключения:
            - SafetyStop
            - VisionError
        """
        if not self._policy.stop_on_danger:
            return

        danger_templates = [template for template in templates if template.enabled and template.category == "danger"]
        if not danger_templates:
            return

        matches = vision.find_all(frame, danger_templates, category="danger")
        if matches:
            best = matches[0]
            raise SafetyStop(
                f"Обнаружен danger: category={best.category}, template_id={best.template_id}, score={best.score:.4f}"
            )

    def ensure_action_allowed(self, action_name: str) -> None:
        """
        Проверяет, не запрещено ли действие политикой безопасности.
        Используемые action_name:
            'purchase'
            'ad'
            'pvp'
            'tap'
            'back'
            'swipe'
            'enter_room'
            'next_floor'

        Возвращает:
            None

        Исключения:
            - SafetyStop: если действие запрещено
        """
        if not action_name or not isinstance(action_name, str):
            raise SafetyStop("Пустое или невалидное имя действия запрещено политикой безопасности")

        normalized = action_name.strip().lower()

        if normalized == "purchase" and not self._policy.allow_purchase:
            raise SafetyStop("Действие 'purchase' запрещено: allow_purchase=false")

        if normalized == "ad" and not self._policy.allow_ads:
            raise SafetyStop("Действие 'ad' запрещено: allow_ads=false")

        if normalized == "pvp" and not self._policy.allow_pvp:
            raise SafetyStop("Действие 'pvp' запрещено: allow_pvp=false")

        if normalized == "premium_currency" and not self._policy.allow_premium_currency:
            raise SafetyStop("Действие 'premium_currency' запрещено: allow_premium_currency=false")

        if normalized not in self._KNOWN_ACTIONS and normalized != "premium_currency":
            raise SafetyStop(f"Неизвестное действие '{action_name}' запрещено политикой безопасности")

        self._enforce_min_action_interval()

    def random_delay(self) -> float:
        """
        Выполняет случайную задержку из конфига и возвращает её длительность в секундах.

        Возвращает:
            float

        Исключения:
            Нет
        """
        min_ms = self._policy.random_delay_min_ms
        max_ms = self._policy.random_delay_max_ms
        delay_ms = random.randint(min_ms, max_ms)
        delay_s = delay_ms / 1000.0
        if delay_s > 0:
            time.sleep(delay_s)
        return delay_s

    def register_network_error(self) -> int:
        """
        Увеличивает счётчик подряд идущих сетевых ошибок.

        Возвращает:
            int: текущее число подряд идущих ошибок.

        Исключения:
            - SafetyStop: если превышен лимит повторов
        """
        self._consecutive_network_errors += 1
        if self._consecutive_network_errors > self._policy.max_consecutive_network_errors:
            raise SafetyStop(
                "Превышен лимит подряд идущих сетевых ошибок: "
                f"{self._consecutive_network_errors} > {self._policy.max_consecutive_network_errors}"
            )
        return self._consecutive_network_errors

    def reset_network_error_counter(self) -> None:
        """Сбрасывает счётчик подряд идущих сетевых ошибок."""
        self._consecutive_network_errors = 0

    def consecutive_network_errors(self) -> int:
        """Возвращает текущее число подряд идущих сетевых ошибок."""
        return self._consecutive_network_errors

    def can_retry_after_network_error(self) -> bool:
        """
        Проверяет, можно ли ещё повторять операцию после сетевых ошибок.
        """
        return self._consecutive_network_errors < self._policy.max_consecutive_network_errors

    def mark_action_executed(self) -> None:
        """Фиксирует время последнего выполненного действия."""
        self._last_action_monotonic = time.monotonic()

    def _enforce_min_action_interval(self) -> None:
        now = time.monotonic()
        min_interval_s = self._policy.min_action_interval_ms / 1000.0

        if self._last_action_monotonic is not None:
            elapsed = now - self._last_action_monotonic
            remaining = min_interval_s - elapsed
            if remaining > 0:
                time.sleep(remaining)

        self._last_action_monotonic = time.monotonic()

    def _load_policy(self, config: "Config") -> _Policy:
        stop_on_danger = self._get_bool(config, "safety.stop_on_danger", True)

        forbid_ads = self._get_bool(config, "safety.forbid_ads", True)
        forbid_purchases = self._get_bool(config, "safety.forbid_purchases", True)
        forbid_pvp = self._get_bool(config, "safety.forbid_pvp", True)

        allow_ads = self._get_bool(config, "safety.allow_ads", not forbid_ads)
        allow_purchase = self._get_bool(config, "safety.allow_purchase", not forbid_purchases)
        allow_pvp = self._get_bool(config, "safety.allow_pvp", not forbid_pvp)
        allow_premium_currency = self._get_bool(config, "safety.allow_premium_currency", False)

        min_action_interval_ms = self._get_int(config, "safety.min_action_interval_ms", 250, minimum=0)
        random_delay_min_ms = self._get_int(config, "safety.random_delay_min_ms", 0, minimum=0)
        random_delay_max_ms = self._get_int(config, "safety.random_delay_max_ms", random_delay_min_ms, minimum=0)
        max_consecutive_network_errors = self._get_int(
            config,
            "safety.max_consecutive_network_errors",
            3,
            minimum=0,
        )

        if random_delay_max_ms < random_delay_min_ms:
            raise SafetyConfigError(
                "Некорректный диапазон случайной задержки: "
                f"safety.random_delay_max_ms={random_delay_max_ms} < "
                f"safety.random_delay_min_ms={random_delay_min_ms}"
            )

        return _Policy(
            stop_on_danger=stop_on_danger,
            allow_ads=allow_ads,
            allow_purchase=allow_purchase,
            allow_premium_currency=allow_premium_currency,
            allow_pvp=allow_pvp,
            min_action_interval_ms=min_action_interval_ms,
            random_delay_min_ms=random_delay_min_ms,
            random_delay_max_ms=random_delay_max_ms,
            max_consecutive_network_errors=max_consecutive_network_errors,
        )

    @staticmethod
    def _get_bool(config: "Config", key: str, default: bool) -> bool:
        value = config.get(key, default)
        if not isinstance(value, bool):
            raise SafetyConfigError(f"Параметр '{key}' должен быть bool, получено: {type(value).__name__}")
        return value

    @staticmethod
    def _get_int(config: "Config", key: str, default: int, minimum: int | None = None) -> int:
        value = config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise SafetyConfigError(f"Параметр '{key}' должен быть int, получено: {type(value).__name__}")
        if minimum is not None and value < minimum:
            raise SafetyConfigError(f"Параметр '{key}' должен быть >= {minimum}, получено: {value}")
        return value
