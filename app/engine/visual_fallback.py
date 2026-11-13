from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.visual.provider import (
    VisualCandidate,
    VisualLocateRequest,
    VisualProvider,
    VisualProviderUnavailable,
    validate_visual_response,
)
from app.actions.registry import default_action_registry

VISUAL_ACTIONS = {
    "click",
    "check",
    "uncheck",
    "click_element",
    "click_relative",
}
VISUAL_ACTIONS_BY_EXECUTOR = {
    "web": {"click", "check", "uncheck"},
    "windows": {"click_element", "click_relative"},
    "desktop": {"click_element", "click_relative"},
}
VISUAL_TRIGGER_CATEGORIES = {
    "locator_not_found",
    "locator_not_unique",
    "element_not_visible",
    "element_disabled",
}


@dataclass(frozen=True)
class VisualCapture:
    screenshot: bytes
    image_width: int
    image_height: int
    viewport: dict[str, int]
    dom_candidates: list[dict[str, Any]] = field(default_factory=list)
    uia_candidates: list[dict[str, Any]] = field(default_factory=list)
    safe_visible_text: str = ""
    source: str = "web"
    location: str = ""
    redaction_rects: list[tuple[int, int, int, int]] = field(
        default_factory=list
    )
    window_info: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VisualCandidateSelection:
    candidate_id: str
    candidate: VisualCandidate
    risk_level: str


@dataclass(frozen=True)
class VisualDecision:
    action: str
    reason: str
    provider: str = ""
    model: str = ""
    request: VisualLocateRequest | None = None
    candidates: list[VisualCandidateSelection] = field(
        default_factory=list
    )
    selected: VisualCandidateSelection | None = None
    audit: dict[str, Any] = field(default_factory=dict)


def should_attempt_visual(
    step: Any,
    *,
    failure_category: str,
    attempts: int,
    max_attempts: int,
    provider: str,
    enabled: bool,
) -> bool:
    if not enabled or provider == "disabled":
        return False
    if int(attempts) >= int(max_attempts):
        return False
    params = getattr(step, "params", None)
    params = params if isinstance(params, dict) else {}
    explicit = params.get(
        "visual_fallback",
        getattr(step, "visual_fallback", False),
    )
    if explicit is not True:
        return False
    if str(failure_category or "") not in VISUAL_TRIGGER_CATEGORIES:
        return False
    action = str(getattr(step, "action", "") or "")
    return action in VISUAL_ACTIONS and _action_in_catalog(step)


class VisualFallback:
    def __init__(
        self,
        *,
        second_candidate_margin: float = 0.10,
    ) -> None:
        self.second_candidate_margin = max(
            float(second_candidate_margin),
            0.0,
        )

    async def try_locate(
        self,
        step: Any,
        capture: VisualCapture,
        provider: VisualProvider,
        *,
        settings: Any,
        risk_level: str,
    ) -> VisualDecision:
        action = str(getattr(step, "action", "") or "")
        if action not in VISUAL_ACTIONS or not _action_in_catalog(step):
            return VisualDecision(
                action="failed",
                reason=(
                    f"action {action!r} is not in the visual allowlist"
                ),
            )
        enabled = bool(getattr(settings, "enabled", False))
        provider_name = str(
            getattr(settings, "provider", "disabled")
        )
        if not enabled or provider_name == "disabled":
            return VisualDecision(
                action="disabled",
                reason="visual fallback is disabled",
                provider=provider_name,
            )
        params = getattr(step, "params", None)
        params = params if isinstance(params, dict) else {}
        target_description = str(
            params.get("target_description")
            or params.get("target")
            or params.get("name")
            or action
        ).strip()
        request = VisualLocateRequest(
            step_id=int(getattr(step, "step_id", 1) or 1),
            action=action,
            target_description=target_description,
            screenshot=capture.screenshot,
            image_width=capture.image_width,
            image_height=capture.image_height,
            viewport=capture.viewport,
            dom_candidates=[
                *capture.dom_candidates,
                *capture.uia_candidates,
            ],
            safe_visible_text=capture.safe_visible_text,
            allowed_actions=[action],
            max_candidates=int(
                getattr(settings, "max_candidates", 3)
            ),
            window_info=capture.window_info,
            coordinate_space="pixels",
        )
        try:
            response = await provider.locate(request)
            validated = validate_visual_response(response, request)
        except VisualProviderUnavailable as exc:
            return VisualDecision(
                action="failed",
                reason=str(exc),
                provider=provider_name,
                request=request,
                audit={
                    "trigger": "structured_locator_failed",
                    "provider": provider_name,
                    "screenshot_scope": capture.source,
                    "candidate_count": 0,
                    "error": str(exc),
                },
            )
        except Exception as exc:
            return VisualDecision(
                action="failed",
                reason=f"visual provider response rejected: {exc}",
                provider=provider_name,
                request=request,
                audit={
                    "trigger": "structured_locator_failed",
                    "provider": provider_name,
                    "screenshot_scope": capture.source,
                    "candidate_count": 0,
                    "error": str(exc),
                },
            )

        candidates = _normalize_candidates(
            validated.candidates,
            request.max_candidates,
            risk_level=risk_level,
        )
        if not candidates:
            return VisualDecision(
                action="failed",
                reason="视觉提供方未返回可用候选。",
                provider=validated.provider,
                model=validated.model,
                request=request,
                audit={
                    "trigger": "structured_locator_failed",
                    "provider": validated.provider,
                    "model": validated.model,
                    "screenshot_scope": capture.source,
                    "candidate_count": 0,
                    "selection": "none",
                },
            )

        threshold = float(
            getattr(settings, "confidence_threshold", 0.9)
        )
        top = candidates[0]
        reason = "high-confidence unique candidate"
        decision_action = "execute"
        if risk_level in {"high", "high_risk", "destructive"}:
            decision_action = "confirm"
            reason = "high-risk action requires human confirmation"
        elif len(candidates) > 1:
            decision_action = "confirm"
            reason = "multiple visual candidates are ambiguous"
        elif top.candidate.confidence < threshold:
            decision_action = "confirm"
            reason = "visual candidate confidence is below threshold"
        elif (
            len(candidates) > 1
            and (
                top.candidate.confidence
                - candidates[1].candidate.confidence
            )
            < self.second_candidate_margin
        ):
            decision_action = "confirm"
            reason = "top visual candidate is not sufficiently ahead"

        return VisualDecision(
            action=decision_action,
            reason=reason,
            provider=validated.provider,
            model=validated.model,
            request=request,
            candidates=candidates,
            selected=top if decision_action == "execute" else None,
            audit={
                "trigger": "structured_locator_failed",
                "provider": validated.provider,
                "model": validated.model,
                "screenshot_scope": capture.source,
                "candidate_count": len(candidates),
                "selection": (
                    "automatic"
                    if decision_action == "execute"
                    else "required"
                ),
                "candidate_ids": [
                    item.candidate_id for item in candidates
                ],
            },
        )


def _normalize_candidates(
    candidates: list[VisualCandidate],
    limit: int,
    *,
    risk_level: str,
) -> list[VisualCandidateSelection]:
    ordered = sorted(
        candidates,
        key=lambda item: (
            -float(item.confidence),
            item.bbox,
            item.target.casefold(),
        ),
    )
    normalized: list[VisualCandidateSelection] = []
    seen: set[tuple[str, tuple[int, int, int, int]]] = set()
    for candidate in ordered:
        key = (candidate.target.casefold(), candidate.bbox)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            VisualCandidateSelection(
                candidate_id=f"cand_{uuid4().hex[:12]}",
                candidate=candidate,
                risk_level=str(risk_level),
            )
        )
        if len(normalized) >= max(1, min(int(limit), 3)):
            break
    return normalized


def _action_in_catalog(step: Any) -> bool:
    raw_executor = getattr(step, "executor", "web") or "web"
    executor = str(
        getattr(raw_executor, "value", raw_executor)
    ).lower()
    raw_action = getattr(step, "action", "")
    action = str(
        getattr(raw_action, "value", raw_action) or ""
    )
    try:
        default_action_registry().resolve(executor, action)
        return True
    except Exception:
        return action in VISUAL_ACTIONS_BY_EXECUTOR.get(
            executor,
            set(),
        )


__all__ = [
    "VISUAL_ACTIONS",
    "VISUAL_ACTIONS_BY_EXECUTOR",
    "VISUAL_TRIGGER_CATEGORIES",
    "VisualCandidateSelection",
    "VisualCapture",
    "VisualDecision",
    "VisualFallback",
    "should_attempt_visual",
]
