from __future__ import annotations

from .config import AppConfig, ProfileConfig, get_profile


def list_profiles(config: AppConfig) -> list[str]:
    return list(config.profiles.keys())


def resolve_profile(config: AppConfig, profile_name: str | None) -> ProfileConfig:
    return get_profile(config, profile_name)
