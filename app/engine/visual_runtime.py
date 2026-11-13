from __future__ import annotations

import asyncio
import copy
import hashlib
from contextlib import suppress
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from app.actions.registry import default_action_registry
from app.engine.visual_fallback import (
    VisualCapture,
    VisualDecision,
    VisualFallback,
    should_attempt_visual,
)
from app.visual.local_ocr import LocalOCRProvider
from app.visual.provider import DisabledVisualProvider
from app.visual.redaction import (
    VisualRedactionError,
    redact_image_bytes,
    redact_visible_text,
)
from app.visual.remote_vlm import RemoteVLMProvider
from app.visual.settings import (
    VisualSettings,
    load_visual_settings,
)

WEB_VISUAL_CAPTURE_SCRIPT = """
() => {
  const rects = [];
  const sensitiveSelector = [
    "input[type='password']",
    "input[name*='token']",
    "input[name*='cookie']",
    "input[name*='card']",
    "[data-sensitive='true']"
  ].join(",");
  for (const element of document.querySelectorAll(sensitiveSelector)) {
    const rect = element.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      rects.push([
        Math.max(0, Math.floor(rect.left)),
        Math.max(0, Math.floor(rect.top)),
        Math.min(window.innerWidth, Math.ceil(rect.right)),
        Math.min(window.innerHeight, Math.ceil(rect.bottom))
      ]);
    }
  }
  const text = document.body ? document.body.innerText : "";
  const candidates = [];
  for (const element of document.querySelectorAll(
    "button,a,input,select,textarea,[role='button'],[role='link']"
  )) {
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    candidates.push({
      role: element.getAttribute("role") || element.tagName.toLowerCase(),
      name: (
        element.getAttribute("aria-label") ||
        element.innerText ||
        element.value ||
        ""
      ).trim().slice(0, 120),
      visible: true
    });
    if (candidates.length >= 40) break;
  }
  return {
    width: window.innerWidth,
    height: window.innerHeight,
    redaction_rects: rects,
    visible_text: text,
    dom_candidates: candidates
  };
}
"""


@dataclass(frozen=True)
class VisualRuntimeOutcome:
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    audit: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    decision: VisualDecision | None = None


class VisualRuntimeService:
    def __init__(
        self,
        *,
        settings_loader: Any | None = None,
        provider_factory: Any | None = None,
        fallback: VisualFallback | None = None,
    ) -> None:
        self.settings_loader = settings_loader
        self.provider_factory = provider_factory
        self.fallback = fallback or VisualFallback()
        self._cache: dict[str, VisualDecision] = {}

    async def evaluate_web(
        self,
        step: Any,
        page: Any,
        *,
        failure_category: str,
        context: dict[str, Any],
        screenshot_path: str | None = None,
    ) -> VisualRuntimeOutcome:
        settings = await self._settings(context)
        attempts = _attempt_count(context, step)
        if not should_attempt_visual(
            step,
            failure_category=failure_category,
            attempts=attempts,
            max_attempts=settings.max_attempts,
            provider=settings.provider,
            enabled=settings.enabled,
        ):
            return VisualRuntimeOutcome(status="skipped")
        _set_attempt_count(context, step, attempts + 1)
        risk_level = _action_risk(step)
        selection = _context_visual_selection(step, context)
        if selection is not None:
            return await self._execute_selected_web(
                step,
                page,
                selection,
                screenshot_path=screenshot_path,
            )

        provider = self._provider(settings)
        try:
            capture = await self.capture_web(page, settings)
        except VisualRedactionError as exc:
            return VisualRuntimeOutcome(
                status="failed",
                reason=f"视觉截图脱敏失败：{exc}",
                audit={"error": str(exc), "trigger": failure_category},
            )
        visual_screenshot_path = _write_redacted_screenshot(
            capture.screenshot,
            screenshot_path,
            context,
        )
        if provider is None:
            return self._awaiting_payload(
                step,
                decision=None,
                reason="视觉 Provider 配置不完整。",
                provider=settings.provider,
                screenshot_path=visual_screenshot_path,
                execution_allowed=False,
                extra_payload={
                    "screenshot_scope": "viewport",
                    "image_width": capture.image_width,
                    "image_height": capture.image_height,
                    "screenshot_path": visual_screenshot_path,
                    "current_url": _safe_url(
                        str(getattr(page, "url", "") or "")
                    ),
                },
            )
        cache_key = _visual_cache_key(step, capture, context)
        decision = self._cache.get(cache_key)
        if decision is not None:
            decision = copy.deepcopy(decision)
            decision.audit["cache_hit"] = True
        else:
            decision = await self.fallback.try_locate(
                step,
                capture,
                provider,
                settings=settings,
                risk_level=risk_level,
            )
            if decision.action in {"execute", "confirm"}:
                self._cache[cache_key] = copy.deepcopy(decision)
        decision.audit["attempt"] = attempts + 1
        if decision.action == "execute" and decision.selected is not None:
            return await self._execute_selected_web(
                step,
                page,
                {
                    "step_id": int(getattr(step, "step_id", 0) or 0),
                    "candidate_id": decision.selected.candidate_id,
                    "target": decision.selected.candidate.target,
                    "bbox": list(decision.selected.candidate.bbox),
                    "confidence": decision.selected.candidate.confidence,
                    "evidence": decision.selected.candidate.evidence,
                    "expected_after": (
                        decision.selected.candidate.expected_after
                    ),
                    "action": str(getattr(step, "action", "")),
                    "risk_level": risk_level,
                    "provider": decision.provider,
                    "model": decision.model,
                    "image_width": capture.image_width,
                    "image_height": capture.image_height,
                },
                screenshot_path=visual_screenshot_path,
            )
        if decision.action == "confirm":
            return self._awaiting_payload(
                step,
                decision=decision,
                reason=decision.reason,
                provider=decision.provider,
                screenshot_path=visual_screenshot_path,
                execution_allowed=True,
                extra_payload={
                    "screenshot_scope": "viewport",
                    "image_width": capture.image_width,
                    "image_height": capture.image_height,
                    "screenshot_path": visual_screenshot_path,
                    "current_url": _safe_url(
                        str(getattr(page, "url", "") or "")
                    ),
                },
            )
        if decision.action == "failed" and decision.request is not None:
            return self._awaiting_payload(
                step,
                decision=decision,
                reason=decision.reason,
                provider=decision.provider,
                screenshot_path=visual_screenshot_path,
                execution_allowed=False,
                candidates=[],
                extra_payload={
                    "screenshot_scope": "viewport",
                    "image_width": capture.image_width,
                    "image_height": capture.image_height,
                    "screenshot_path": visual_screenshot_path,
                    "current_url": _safe_url(
                        str(getattr(page, "url", "") or "")
                    ),
                },
            )
        return VisualRuntimeOutcome(
            status=(
                "disabled"
                if decision.action == "disabled"
                else "failed"
            ),
            reason=decision.reason,
            audit=decision.audit,
            decision=decision,
        )

    async def capture_web(
        self,
        page: Any,
        settings: VisualSettings,
    ) -> VisualCapture:
        payload = await page.evaluate(WEB_VISUAL_CAPTURE_SCRIPT)
        if not isinstance(payload, dict):
            raise VisualRedactionError(
                "page did not return visual capture metadata"
            )
        image_bytes = await page.screenshot(full_page=False)
        css_width = int(payload.get("width") or 0)
        css_height = int(payload.get("height") or 0)
        if css_width <= 0 or css_height <= 0:
            raise VisualRedactionError("invalid viewport size")
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                width, height = image.size
        except Exception as exc:
            raise VisualRedactionError(
                f"cannot decode visual screenshot: {exc}"
            ) from exc
        scale_x = width / css_width
        scale_y = height / css_height
        rects = _scale_rects(
            _normalize_rects(payload.get("redaction_rects")),
            scale_x,
            scale_y,
        )
        policy = _redaction_policy_dict(settings)
        rects.extend(
            _scale_rects(
                _normalize_rects(policy.get("custom_rects")),
                scale_x,
                scale_y,
            )
        )
        redacted = redact_image_bytes(
            image_bytes,
            rects=rects,
            image_size=(width, height),
        )
        safe_text = redact_visible_text(
            str(payload.get("visible_text") or ""),
        )[:8000]
        current_url = _safe_url(str(getattr(page, "url", "") or ""))
        return VisualCapture(
            screenshot=redacted,
            image_width=width,
            image_height=height,
            viewport={"width": css_width, "height": css_height},
            dom_candidates=[
                dict(item)
                for item in payload.get("dom_candidates") or []
                if isinstance(item, dict)
            ][:40],
            safe_visible_text=safe_text,
            source="viewport",
            location=current_url,
            redaction_rects=rects,
        )

    async def evaluate_desktop(
        self,
        step: Any,
        *,
        screenshot: bytes,
        image_size: tuple[int, int],
        window_info: dict[str, Any],
        visible_text: str,
        uia_candidates: list[dict[str, Any]],
        failure_category: str,
        context: dict[str, Any],
        screenshot_path: str | None = None,
        execute_callback: Any | None = None,
    ) -> VisualRuntimeOutcome:
        settings = await self._settings(context)
        attempts = _attempt_count(context, step)
        if not should_attempt_visual(
            step,
            failure_category=failure_category,
            attempts=attempts,
            max_attempts=settings.max_attempts,
            provider=settings.provider,
            enabled=settings.enabled,
        ):
            return VisualRuntimeOutcome(status="skipped")
        _set_attempt_count(context, step, attempts + 1)
        risk_level = _action_risk(step)
        selection = _context_visual_selection(step, context)
        if selection is not None:
            return await self._execute_selected_desktop(
                step,
                selection,
                context,
                execute_callback,
                screenshot_path=screenshot_path,
            )
        provider = self._provider(settings)
        width, height = (int(image_size[0]), int(image_size[1]))
        rects = _normalize_rects(
            window_info.get("redaction_rects")
        )
        policy = _redaction_policy_dict(settings)
        rects.extend(_normalize_rects(policy.get("custom_rects")))
        try:
            redacted = redact_image_bytes(
                screenshot,
                rects=rects,
                image_size=(width, height),
            )
        except VisualRedactionError as exc:
            return VisualRuntimeOutcome(
                status="failed",
                reason=f"桌面截图脱敏失败：{exc}",
                audit={"error": str(exc), "trigger": failure_category},
            )
        visual_screenshot_path = _write_redacted_screenshot(
            redacted,
            screenshot_path,
            context,
        )
        if provider is None:
            return self._awaiting_payload(
                step,
                decision=None,
                reason="视觉 Provider 配置不完整。",
                provider=settings.provider,
                screenshot_path=visual_screenshot_path,
                execution_allowed=False,
                extra_payload={
                    "screenshot_scope": "window",
                    "image_width": width,
                    "image_height": height,
                    "screenshot_path": visual_screenshot_path,
                    "window_info": dict(window_info),
                },
            )
        capture = VisualCapture(
            screenshot=redacted,
            image_width=width,
            image_height=height,
            viewport={"width": width, "height": height},
            uia_candidates=list(uia_candidates)[:40],
            safe_visible_text=redact_visible_text(visible_text)[:8000],
            source="window",
            location=str(window_info.get("title") or ""),
            redaction_rects=rects,
            window_info=dict(window_info),
        )
        cache_key = _visual_cache_key(
            step,
            capture,
            context,
        )
        decision = self._cache.get(cache_key)
        if decision is not None:
            decision = copy.deepcopy(decision)
            decision.audit["cache_hit"] = True
        else:
            decision = await self.fallback.try_locate(
                step,
                capture,
                provider,
                settings=settings,
                risk_level=risk_level,
            )
            if decision.action in {"execute", "confirm"}:
                self._cache[cache_key] = copy.deepcopy(decision)
        decision.audit["attempt"] = attempts + 1
        if decision.action == "execute" and decision.selected is not None:
            selection_payload = {
                "step_id": int(getattr(step, "step_id", 0) or 0),
                "candidate_id": decision.selected.candidate_id,
                "target": decision.selected.candidate.target,
                "bbox": list(decision.selected.candidate.bbox),
                "confidence": decision.selected.candidate.confidence,
                "evidence": decision.selected.candidate.evidence,
                "expected_after": (
                    decision.selected.candidate.expected_after
                ),
                "action": str(getattr(step, "action", "")),
                "risk_level": risk_level,
                "provider": decision.provider,
                "model": decision.model,
                "image_width": width,
                "image_height": height,
            }
            if execute_callback is None:
                return self._awaiting_payload(
                    step,
                    decision=decision,
                    reason=(
                        "桌面视觉候选需要受控点击执行器确认。"
                    ),
                    provider=decision.provider,
                    screenshot_path=visual_screenshot_path,
                    execution_allowed=True,
                )
            return await self._execute_selected_desktop(
                step,
                selection_payload,
                context,
                execute_callback,
                screenshot_path=visual_screenshot_path,
            )
        if decision.action == "confirm":
            return self._awaiting_payload(
                step,
                decision=decision,
                reason=decision.reason,
                provider=decision.provider,
                screenshot_path=visual_screenshot_path,
                execution_allowed=True,
                extra_payload={
                    "screenshot_scope": "window",
                    "image_width": width,
                    "image_height": height,
                    "screenshot_path": visual_screenshot_path,
                    "window_info": dict(window_info),
                },
            )
        if decision.action == "failed" and decision.request is not None:
            return self._awaiting_payload(
                step,
                decision=decision,
                reason=decision.reason,
                provider=decision.provider,
                screenshot_path=visual_screenshot_path,
                execution_allowed=False,
                candidates=[],
                extra_payload={
                    "screenshot_scope": "window",
                    "image_width": width,
                    "image_height": height,
                    "screenshot_path": visual_screenshot_path,
                    "window_info": dict(window_info),
                },
            )
        return VisualRuntimeOutcome(
            status=(
                "disabled"
                if decision.action == "disabled"
                else "failed"
            ),
            reason=decision.reason,
            audit=decision.audit,
            decision=decision,
        )

    async def _execute_selected_web(
        self,
        step: Any,
        page: Any,
        selection: dict[str, Any],
        *,
        screenshot_path: str | None,
    ) -> VisualRuntimeOutcome:
        viewport = getattr(page, "viewport_size", None)
        if not isinstance(viewport, dict):
            viewport = {"width": 1280, "height": 720}
        bbox = _validated_bbox(
            _scale_bbox_to_viewport(selection, viewport),
            viewport,
        )
        if bbox is None:
            return self._awaiting_payload(
                step,
                decision=None,
                reason="视觉候选坐标无效。",
                provider=str(selection.get("provider") or ""),
                screenshot_path=screenshot_path,
                execution_allowed=False,
            )
        before = await _web_state(page)
        x1, y1, x2, y2 = bbox
        await page.mouse.click((x1 + x2) / 2, (y1 + y2) / 2)
        await asyncio.sleep(0.25)
        passed, verification = await _verify_web_action(
            page,
            step,
            before,
            selection,
        )
        audit = {
            "selection": "manual"
            if selection.get("human_task_id")
            else "automatic",
            "candidate_id": selection.get("candidate_id"),
            "action": str(getattr(step, "action", "")),
            "bbox": bbox,
            "provider": selection.get("provider"),
            "model": selection.get("model"),
            "screenshot_path": screenshot_path,
            "verification": verification,
        }
        if passed:
            return VisualRuntimeOutcome(
                status="success",
                verification=verification,
                audit=audit,
                reason="visual candidate executed and verified",
            )
        return self._awaiting_payload(
            step,
            decision=None,
            reason="视觉动作执行后验证失败。",
            provider=str(selection.get("provider") or ""),
            screenshot_path=screenshot_path,
            execution_allowed=False,
            candidates=[
                {
                    "candidate_id": selection.get("candidate_id"),
                    "target": selection.get("target"),
                    "bbox": bbox,
                    "confidence": selection.get("confidence", 0),
                    "evidence": selection.get("evidence", ""),
                    "expected_after": selection.get(
                        "expected_after",
                        "",
                    ),
                }
            ],
            audit=audit,
            verification_failed=True,
            extra_payload={
                "screenshot_scope": "viewport",
                "image_width": selection.get("image_width"),
                "image_height": selection.get("image_height"),
                "current_url": _safe_url(
                    str(getattr(page, "url", "") or "")
                ),
            },
        )

    async def _execute_selected_desktop(
        self,
        step: Any,
        selection: dict[str, Any],
        context: dict[str, Any],
        execute_callback: Any | None,
        *,
        screenshot_path: str | None,
    ) -> VisualRuntimeOutcome:
        if execute_callback is None:
            return self._awaiting_payload(
                step,
                decision=None,
                reason="桌面视觉点击执行器不可用。",
                provider=str(selection.get("provider") or ""),
                screenshot_path=screenshot_path,
                execution_allowed=False,
                candidates=[selection],
            )
        try:
            passed, verification = await execute_callback(
                selection,
                step,
                context,
            )
        except Exception as exc:
            return self._awaiting_payload(
                step,
                decision=None,
                reason=f"桌面视觉动作执行失败：{exc}",
                provider=str(selection.get("provider") or ""),
                screenshot_path=screenshot_path,
                execution_allowed=False,
                candidates=[selection],
            )
        audit = {
            "selection": "manual"
            if selection.get("human_task_id")
            else "automatic",
            "candidate_id": selection.get("candidate_id"),
            "action": str(getattr(step, "action", "")),
            "bbox": selection.get("bbox"),
            "provider": selection.get("provider"),
            "model": selection.get("model"),
            "verification": verification,
        }
        if passed:
            return VisualRuntimeOutcome(
                status="success",
                verification=dict(verification or {}),
                audit=audit,
                reason="desktop visual candidate executed and verified",
            )
        return self._awaiting_payload(
            step,
            decision=None,
            reason="桌面视觉动作执行后验证失败。",
            provider=str(selection.get("provider") or ""),
            screenshot_path=screenshot_path,
            execution_allowed=False,
            candidates=[selection],
            audit=audit,
            verification_failed=True,
            extra_payload={
                "screenshot_scope": "window",
                "image_width": selection.get("image_width"),
                "image_height": selection.get("image_height"),
                "window_info": {
                    "title": selection.get("window_title"),
                },
            },
        )

    def _awaiting_payload(
        self,
        step: Any,
        *,
        decision: VisualDecision | None,
        reason: str,
        provider: str,
        screenshot_path: str | None,
        execution_allowed: bool,
        candidates: list[dict[str, Any]] | None = None,
        audit: dict[str, Any] | None = None,
        verification_failed: bool = False,
        extra_payload: dict[str, Any] | None = None,
    ) -> VisualRuntimeOutcome:
        selected_candidates = (
            candidates
            if candidates is not None
            else [
                {
                    "candidate_id": item.candidate_id,
                    "target": item.candidate.target,
                    "bbox": list(item.candidate.bbox),
                    "confidence": item.candidate.confidence,
                    "evidence": item.candidate.evidence,
                    "expected_after": item.candidate.expected_after,
                }
                for item in (decision.candidates if decision else [])
            ]
        )
        payload = {
            "kind": "visual_ambiguous",
            "instruction": (
                "视觉动作执行后验证失败，请检查页面后取消或续跑。"
                if verification_failed
                else "请选择正确的目标后继续执行。"
            ),
            "action": str(getattr(step, "action", "")),
            "risk_level": _action_risk(step),
            "candidates": selected_candidates,
            "provider": provider,
            "model": decision.model if decision else "",
            "trigger_reason": reason,
            "execution_allowed": bool(execution_allowed),
            "verification_failed": bool(verification_failed),
            "screenshot_path": screenshot_path,
            "verification": {"type": "visual_selection"},
            "audit": audit or (decision.audit if decision else {}),
        }
        if extra_payload:
            payload.update(extra_payload)
        return VisualRuntimeOutcome(
            status="awaiting_user",
            payload=payload,
            audit=payload["audit"],
            reason=reason,
            decision=decision,
        )

    async def _settings(
        self,
        context: dict[str, Any],
    ) -> VisualSettings:
        if self.settings_loader is None:
            return VisualSettings()
        user_id = context.get("user_id")
        if user_id is None:
            return VisualSettings()
        return await self.settings_loader(int(user_id))

    def _provider(self, settings: VisualSettings) -> Any | None:
        if self.provider_factory is not None:
            return self.provider_factory(settings)
        if not settings.enabled:
            return None
        if settings.provider == "disabled":
            return DisabledVisualProvider()
        if settings.provider == "local_ocr":
            return LocalOCRProvider()
        if settings.provider == "remote_vlm":
            try:
                return RemoteVLMProvider(
                    endpoint=settings.endpoint,
                    model=settings.model,
                    api_key=str(settings.api_key or ""),
                )
            except Exception:
                return None
        return None


def _attempt_count(context: dict[str, Any], step: Any) -> int:
    attempts = context.get("visual_attempts")
    if not isinstance(attempts, dict):
        attempts = {}
        context["visual_attempts"] = attempts
    key = str(getattr(step, "step_id", "unknown"))
    try:
        return max(int(attempts.get(key, 0)), 0)
    except (TypeError, ValueError):
        return 0


def _set_attempt_count(
    context: dict[str, Any],
    step: Any,
    value: int,
) -> None:
    attempts = context.setdefault("visual_attempts", {})
    attempts[str(getattr(step, "step_id", "unknown"))] = int(value)


def _context_visual_selection(
    step: Any,
    context: dict[str, Any],
) -> dict[str, Any] | None:
    selection = context.get("visual_selection")
    if not isinstance(selection, dict):
        return None
    try:
        if int(selection.get("step_id") or 0) != int(
            getattr(step, "step_id", 0) or 0
        ):
            return None
    except (TypeError, ValueError):
        return None
    return dict(selection)


def _action_risk(step: Any) -> str:
    try:
        definition = default_action_registry().resolve(
            str(getattr(step, "executor", "web") or "web"),
            str(getattr(step, "action", "") or ""),
        ).definition
        return str(definition.risk_level or "read_only")
    except Exception:
        return "read_only"


def _normalize_rects(
    value: Any,
) -> list[tuple[int, int, int, int]]:
    if not isinstance(value, list):
        return []
    rects: list[tuple[int, int, int, int]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            continue
        try:
            rects.append(tuple(int(part) for part in item))
        except (TypeError, ValueError):
            continue
    return rects


def _redaction_policy_dict(settings: VisualSettings) -> dict[str, Any]:
    policy = settings.redaction_policy
    if hasattr(policy, "model_dump"):
        return dict(policy.model_dump(mode="json"))
    return dict(policy) if isinstance(policy, dict) else {}


def _validated_bbox(
    value: Any,
    viewport: dict[str, Any],
) -> tuple[int, int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x1, y1, x2, y2 = (int(item) for item in value)
        width = int(viewport.get("width") or 0)
        height = int(viewport.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if (
        min(x1, y1, x2, y2) < 0
        or x2 <= x1
        or y2 <= y1
        or x2 > width
        or y2 > height
    ):
        return None
    return x1, y1, x2, y2


def _scale_bbox_to_viewport(
    selection: dict[str, Any],
    viewport: dict[str, Any],
) -> Any:
    raw = selection.get("bbox")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return raw
    try:
        image_width = int(selection.get("image_width") or 0)
        image_height = int(selection.get("image_height") or 0)
        view_width = int(viewport.get("width") or 0)
        view_height = int(viewport.get("height") or 0)
    except (TypeError, ValueError):
        return raw
    if (
        image_width <= 0
        or image_height <= 0
        or view_width <= 0
        or view_height <= 0
    ):
        return raw
    scale_x = view_width / image_width
    scale_y = view_height / image_height
    try:
        x1, y1, x2, y2 = (int(item) for item in raw)
    except (TypeError, ValueError):
        return raw
    return (
        round(x1 * scale_x),
        round(y1 * scale_y),
        round(x2 * scale_x),
        round(y2 * scale_y),
    )


def _scale_rects(
    rects: list[tuple[int, int, int, int]],
    scale_x: float,
    scale_y: float,
) -> list[tuple[int, int, int, int]]:
    return [
        (
            round(x1 * scale_x),
            round(y1 * scale_y),
            round(x2 * scale_x),
            round(y2 * scale_y),
        )
        for x1, y1, x2, y2 in rects
    ]


async def _web_state(page: Any) -> dict[str, Any]:
    text = ""
    try:
        text = str(
            await page.locator("body").inner_text(timeout=1000)
        )
    except Exception:
        pass
    return {
        "url": str(getattr(page, "url", "") or ""),
        "text_hash": hashlib.sha256(
            text.encode("utf-8", errors="ignore")
        ).hexdigest(),
        "text": text,
    }


async def _verify_web_action(
    page: Any,
    step: Any,
    before: dict[str, Any],
    selection: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    params = getattr(step, "params", None)
    params = params if isinstance(params, dict) else {}
    selector = str(
        params.get("visual_success_selector")
        or params.get("success_selector")
        or ""
    ).strip()
    if selector:
        try:
            await page.locator(selector).wait_for(
                state="visible",
                timeout=3000,
            )
            return True, {
                "passed": True,
                "type": "dom_selector",
                "selector": selector,
            }
        except Exception:
            return False, {
                "passed": False,
                "type": "dom_selector",
                "selector": selector,
            }
    after = await _web_state(page)
    expected_url = str(
        params.get("success_url_contains")
        or params.get("url_contains")
        or ""
    ).strip()
    if expected_url and expected_url in str(after["url"]):
        return True, {
            "passed": True,
            "type": "url",
            "expected": expected_url,
        }
    if after["url"] != before["url"]:
        return True, {
            "passed": True,
            "type": "url_changed",
            "before": before["url"],
            "after": after["url"],
        }
    if after["text_hash"] != before["text_hash"]:
        return True, {
            "passed": True,
            "type": "dom_changed",
        }
    expected_after = str(selection.get("expected_after") or "")
    if expected_after and expected_after in str(after["text"]):
        return True, {
            "passed": True,
            "type": "expected_text",
            "expected": expected_after,
        }
    return False, {
        "passed": False,
        "type": "no_observable_change",
    }


def _safe_url(value: str) -> str:
    if "?" not in value:
        return value.split("#", 1)[0]
    base, _, _query = value.partition("?")
    return base


def _visual_cache_key(
    step: Any,
    capture: VisualCapture,
    context: dict[str, Any],
) -> str:
    params = getattr(step, "params", None)
    params = params if isinstance(params, dict) else {}
    target = str(
        params.get("target_description")
        or params.get("target")
        or params.get("name")
        or ""
    )
    parts = [
        str(context.get("user_id") or ""),
        str(context.get("workflow_version_id") or ""),
        str(getattr(step, "step_id", "")),
        capture.location,
        str(capture.viewport),
        target,
        str(getattr(step, "action", "")),
    ]
    return hashlib.sha256(
        "\x1f".join(parts).encode("utf-8", errors="ignore")
    ).hexdigest()


def _write_redacted_screenshot(
    image_bytes: bytes,
    source_path: str | None,
    context: dict[str, Any],
) -> str:
    if source_path:
        source = Path(source_path)
        target = source.with_name(
            f"{source.stem}.redacted{source.suffix or '.png'}"
        )
    else:
        execution_id = str(context.get("execution_log_id") or "unknown")
        target = (
            Path("logs")
            / f"execution-{execution_id}"
            / "visual-redacted.png"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image_bytes)
    if source_path:
        with suppress(OSError):
            Path(source_path).unlink()
    return str(target)


def default_visual_runtime(
    *,
    session_factory: Any,
    config: Any,
) -> VisualRuntimeService:
    async def settings_loader(user_id: int) -> VisualSettings:
        async with session_factory() as session:
            return await load_visual_settings(
                session,
                config,
                user_id,
                include_api_key=True,
            )

    return VisualRuntimeService(settings_loader=settings_loader)


__all__ = [
    "VisualRuntimeOutcome",
    "VisualRuntimeService",
    "WEB_VISUAL_CAPTURE_SCRIPT",
    "default_visual_runtime",
]
