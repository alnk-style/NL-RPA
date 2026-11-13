from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

CANONICAL_ENVIRONMENTS = frozenset({"local", "vm"})
LEGACY_ENVIRONMENT_ALIASES = {"isolated": "vm"}

CANONICAL_CAPABILITY_ORDER = (
    "headless_web",
    "interactive_desktop",
    "isolated_desktop",
    "visual_fallback",
    "legacy_wps",
)
LEGACY_CAPABILITY_ALIASES = {
    "window": "interactive_desktop",
    "uia": "interactive_desktop",
    "screenshot": "interactive_desktop",
    "wps": "legacy_wps",
}
LEGACY_CAPABILITY_EXPANSIONS = {
    "wps": ("interactive_desktop", "legacy_wps"),
}
CANONICAL_CAPABILITIES = frozenset(CANONICAL_CAPABILITY_ORDER)


def normalize_environment(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = LEGACY_ENVIRONMENT_ALIASES.get(text, text)
    if text not in CANONICAL_ENVIRONMENTS:
        raise ValueError("environment must be local or vm")
    return text


def environment_or_unknown(value: Any) -> str:
    try:
        return normalize_environment(value)
    except ValueError:
        return "unknown"


def normalize_capabilities(
    values: Iterable[Any] | None,
) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    normalized: set[str] = set()
    for value in values:
        text = str(value or "").strip().lower()
        if not text:
            continue
        expanded = LEGACY_CAPABILITY_EXPANSIONS.get(text)
        canonical = (
            expanded
            if expanded is not None
            else (LEGACY_CAPABILITY_ALIASES.get(text, text),)
        )
        if all(
            item not in CANONICAL_CAPABILITIES
            for item in canonical
        ):
            logger.warning(
                "Ignoring unknown Agent capability %r",
                value,
            )
            continue
        normalized.update(
            item
            for item in canonical
            if item in CANONICAL_CAPABILITIES
        )
    return [
        item
        for item in CANONICAL_CAPABILITY_ORDER
        if item in normalized
    ]


def normalize_required_capabilities(
    values: Iterable[Any] | None,
) -> set[str]:
    return set(normalize_capabilities(values))


__all__ = [
    "CANONICAL_CAPABILITIES",
    "CANONICAL_CAPABILITY_ORDER",
    "CANONICAL_ENVIRONMENTS",
    "LEGACY_CAPABILITY_ALIASES",
    "LEGACY_ENVIRONMENT_ALIASES",
    "environment_or_unknown",
    "normalize_capabilities",
    "normalize_environment",
    "normalize_required_capabilities",
]
