from __future__ import annotations

import random
import time
from typing import Any, Mapping, Optional, Sequence


class SafetyStop(RuntimeError):
    pass


class SafetyViolation(RuntimeError):
    pass


class NetworkRetryExceeded(RuntimeError):
    pass


class SafetyService:
    """
    Политики безопасности для бота.

    По умолчанию:
    - покупки заблокированы;
    - реклама заблокирована;
    - PvP заблокирован;
    - при обнаружении danger бот останавливается.

    Дополнительно:
    - поддерживаются флаги allow_ads / allow_purchase /
      allow_premium_currency / allow_pvp из конфига или словаря;
    - есть случайные задержки из конфига;
    - есть счётчик подряд идущих сетевых ошибок с ограничением повторов.
    """

    def __init__(
        self,
        block_purchases: bool = True,
        block_ads: bool = True,
        block_pvp: bool = True,
        stop_on_danger: bool = True,
        enforce_safe_actions: bool = False,
        config: Optional[Mapping[str, Any]] = None,
    ) -> None:
        cfg = dict(config or {})

        self.block_purchases = bool(block_purchases)
        self.block_ads = bool(block_ads)
        self.block_pvp = bool(block_pvp)
        self.stop_on_danger = bool(stop_on_danger)
        self.enforce_safe_actions = bool(enforce_safe_actions)

        self.allow_ads = bool(cfg.get("allow_ads", False))
        self.allow_purchase = bool(cfg.get("allow_purchase", False))
        self.allow_premium_currency = bool(cfg.get("allow_premium_currency", False))
        self.allow_pvp = bool(cfg.get("allow_pvp", False))

        self.random_delay_min_s = float(cfg.get("random_delay_min_s", 0.0))
        self.random_delay_max_s = float(cfg.get("random_delay_max_s", 0.0))
        if self.random_delay_min_s < 0:
            self.random_delay_min_s = 0.0
        if self.random_delay_max_s < 0:
            self.random_delay_max_s = 0.0
        if self.random_delay_max_s < self.random_delay_min_s:
            self.random_delay_min_s, self.random_delay_max_s = (
                self.random_delay_max_s,
                self.random_delay_min_s,
            )

        self.max_consecutive_network_errors = int(
            cfg.get("max_consecutive_network_errors", 3)
        )
        if self.max_consecutive_network_errors < 1:
            self.max_consecutive_network_errors = 1

        self.consecutive_network_errors = 0

    @classmethod
    def from_config(cls, config: Any) -> "SafetyService":
        """
        Удобный конструктор из AppConfig или словаря.
        Поддерживает:
        - AppConfig с полями контракта;
        - dict-подобный объект.
        """
        if config is None:
            return cls()

        if isinstance(config, Mapping):
            safety_cfg = config.get("safety", {})
            return cls(
                block_purchases=bool(safety_cfg.get("block_purchases", True)),
                block_ads=bool(safety_cfg.get("block_ads", True)),
                block_pvp=bool(safety_cfg.get("block_pvp", True)),
                stop_on_danger=bool(safety_cfg.get("stop_on_danger", True)),
                enforce_safe_actions=bool(safety_cfg.get("enforce_safe_actions", False)),
                config=safety_cfg,
            )

        raw = getattr(config, "raw", {}) or {}
        safety_cfg = raw.get("safety", {}) if isinstance(raw, dict) else {}
        return cls(
            block_purchases=bool(getattr(config, "block_purchases", True)),
            block_ads=bool(getattr(config, "block_ads", True)),
            block_pvp=bool(getattr(config, "block_pvp", True)),
            stop_on_danger=bool(getattr(config, "stop_on_danger", True)),
            enforce_safe_actions=bool(getattr(config, "enforce_safe_actions", False)),
            config=safety_cfg,
        )

    def check_danger(self, frame, vision: "VisionService", templates: Sequence["Template"]) -> bool:
        """
        Проверяет наличие category='danger'.

        Возвращает:
        - True, если danger найден
        - False, если не найден

        Исключения:
        - SafetyStop, если stop_on_danger=True и danger найден
        """
        match = vision.best_match_by_category(frame=frame, templates=templates, category="danger")
        found = match is not None
        if found and self.stop_on_danger:
            raise SafetyStop("Обнаружен danger. Работа бота остановлена политикой безопасности.")
        return found

    def validate_tap(self, x: int, y: int, allowed_rois: Optional[list] = None) -> None:
        """
        Проверяет, допустим ли тап по политике безопасности.

        allowed_rois ожидается в формате:
        [
            [x, y, w, h],
            ...
        ]

        Если enforce_safe_actions=False, проверка белых зон не применяется.
        """
        if not self.enforce_safe_actions:
            return

        if not allowed_rois:
            raise SafetyViolation(
                "Тап запрещён: включён enforce_safe_actions, но список allowed_rois пуст."
            )

        for roi in allowed_rois:
            if not isinstance(roi, (list, tuple)) or len(roi) != 4:
                continue
            rx, ry, rw, rh = roi
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return

        raise SafetyViolation(
            f"Тап в точку ({x}, {y}) запрещён политикой безопасности: точка вне allowed_rois."
        )

    def assert_action_allowed(self, action_name: str) -> None:
        """
        Блокирует запрещённые действия по имени.

        Обязательные запрещаемые имена:
        - purchase
        - ad
        - pvp
        """
        normalized = (action_name or "").strip().lower()

        if normalized == "purchase":
            if self.block_purchases or not self.allow_purchase:
                raise SafetyViolation("Действие purchase запрещено политикой безопасности.")
            return

        if normalized == "ad":
            if self.block_ads or not self.allow_ads:
                raise SafetyViolation("Действие ad запрещено политикой безопасности.")
            return

        if normalized == "pvp":
            if self.block_pvp or not self.allow_pvp:
                raise SafetyViolation("Действие pvp запрещено политикой безопасности.")
            return

    def is_action_allowed(
        self,
        action_name: str,
        *,
        dangerous: bool = False,
        uses_premium_currency: bool = False,
    ) -> bool:
        """
        Проверка, можно ли выполнять действие для правила с учётом dangerous.

        Правила:
        - dangerous блокируется, если stop_on_danger=True;
        - purchase требует allow_purchase=True и отсутствие block_purchases;
        - ad требует allow_ads=True и отсутствие block_ads;
        - pvp требует allow_pvp=True и отсутствие block_pvp;
        - действие с premium currency требует allow_premium_currency=True.
        """
        if dangerous and self.stop_on_danger:
            return False

        normalized = (action_name or "").strip().lower()

        if uses_premium_currency and not self.allow_premium_currency:
            return False

        if normalized == "purchase":
            return (not self.block_purchases) and self.allow_purchase

        if normalized == "ad":
            return (not self.block_ads) and self.allow_ads

        if normalized == "pvp":
            return (not self.block_pvp) and self.allow_pvp

        return True

    def assert_rule_action_allowed(
        self,
        action_name: str,
        *,
        dangerous: bool = False,
        uses_premium_currency: bool = False,
    ) -> None:
        """
        Строгая версия is_action_allowed(...): бросает исключение при запрете.
        """
        if not self.is_action_allowed(
            action_name,
            dangerous=dangerous,
            uses_premium_currency=uses_premium_currency,
        ):
            raise SafetyViolation(
                "Действие запрещено политикой безопасности: "
                f"action_name={action_name!r}, dangerous={dangerous}, "
                f"uses_premium_currency={uses_premium_currency}"
            )

    def random_delay(self) -> float:
        """
        Случайная задержка из конфига.
        Возвращает фактическую длительность задержки в секундах.
        """
        delay = random.uniform(self.random_delay_min_s, self.random_delay_max_s)
        if delay > 0:
            time.sleep(delay)
        return delay

    def register_network_error(self) -> int:
        """
        Увеличивает счётчик подряд идущих сетевых ошибок.

        Исключения:
        - NetworkRetryExceeded, если превышен лимит повторов.
        """
        self.consecutive_network_errors += 1
        if self.consecutive_network_errors > self.max_consecutive_network_errors:
            raise NetworkRetryExceeded(
                "Превышен лимит подряд идущих сетевых ошибок: "
                f"{self.consecutive_network_errors} > {self.max_consecutive_network_errors}"
            )
        return self.consecutive_network_errors

    def reset_network_error_counter(self) -> None:
        """
        Сбрасывает счётчик подряд идущих сетевых ошибок после успешной операции.
        """
        self.consecutive_network_errors = 0

    def can_retry_after_network_error(self) -> bool:
        """
        Возвращает True, если ещё можно делать повтор после сетевой ошибки.
        """
        return self.consecutive_network_errors < self.max_consecutive_network_errors

    def get_network_error_count(self) -> int:
        """
        Текущее число подряд идущих сетевых ошибок.
        """
        return self.consecutive_network_errors
