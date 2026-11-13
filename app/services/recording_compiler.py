"""录制事件到结构化步骤草稿。

录制端（浏览器注入脚本或桌面 Agent 拾取）只上报“发生了什么”，编译端
负责把事件映射到动作目录内的动作，补齐定位、幂等标记与重试策略，
最后用执行器同一套校验函数验证草稿，保证“录制即可发布”。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agents.desktop_planner import (
    DesktopPlanError,
    validate_desktop_plan,
)
from app.engine.web_catalog import WebPlanError, validate_web_plan
from app.protocols import LOCATOR_PRIORITY, LocatorStrategy

WEB_MODE = "web"
DESKTOP_MODE = "windows"

_MODES = {WEB_MODE, DESKTOP_MODE, "desktop"}
_MODIFIER_KEYS = {
    "shift",
    "control",
    "ctrl",
    "alt",
    "meta",
    "capslock",
}

# 有副作用的动作默认只执行一次，避免重试造成重复提交。
_SINGLE_ATTEMPT_ACTIONS = frozenset(
    {
        "click",
        "press",
        "click_element",
        "type_text",
        "press_hotkey",
        "download",
        "upload",
        "launch_app",
        "close_app",
        "create_document",
        "insert_image",
        "save_document",
        "export_pdf",
    }
)

_DEFAULT_RETRY_POLICY = {"max_attempts": 2, "backoff_seconds": 0.5}
_SINGLE_ATTEMPT_RETRY_POLICY = {"max_attempts": 1, "backoff_seconds": 0.0}

_WEB_PARAM_KEYS: dict[str, tuple[str, ...]] = {
    "click": ("button", "click_count", "force", "position"),
    "download": ("save_as", "timeout_ms"),
    "wait_for": ("milliseconds", "state"),
}
_DESKTOP_CLICK_PARAM_KEYS = (
    "automation_id",
    "name",
    "control_type",
    "button",
    "double_click",
    "x",
    "y",
)
_WINDOW_PARAM_KEYS = ("title", "class_name", "pid", "index")


class RecordingError(ValueError):
    """录制事件无法转换为合法的结构化草稿。"""


class RecordingEventKind(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    PRESS = "press"
    SELECT_OPTION = "select_option"
    CHECK = "check"
    UNCHECK = "uncheck"
    UPLOAD = "upload"
    DOWNLOAD = "download"
    WAIT = "wait"
    DESKTOP_LAUNCH = "desktop_launch"
    DESKTOP_WINDOW = "desktop_window"
    DESKTOP_CLICK = "desktop_click"
    DESKTOP_TYPE = "desktop_type"
    DESKTOP_HOTKEY = "desktop_hotkey"
    DESKTOP_WAIT = "desktop_wait"


_WEB_KINDS = frozenset(
    {
        RecordingEventKind.NAVIGATE,
        RecordingEventKind.CLICK,
        RecordingEventKind.FILL,
        RecordingEventKind.PRESS,
        RecordingEventKind.SELECT_OPTION,
        RecordingEventKind.CHECK,
        RecordingEventKind.UNCHECK,
        RecordingEventKind.UPLOAD,
        RecordingEventKind.DOWNLOAD,
        RecordingEventKind.WAIT,
    }
)
_DESKTOP_KINDS = frozenset(
    {
        RecordingEventKind.DESKTOP_LAUNCH,
        RecordingEventKind.DESKTOP_WINDOW,
        RecordingEventKind.DESKTOP_CLICK,
        RecordingEventKind.DESKTOP_TYPE,
        RecordingEventKind.DESKTOP_HOTKEY,
        RecordingEventKind.DESKTOP_WAIT,
    }
)


class RecordingEvent(BaseModel):
    """一条录制事件；字段从宽，编译阶段再收敛到动作参数。"""

    kind: RecordingEventKind
    at: float | None = None
    url: str | None = None
    title: str | None = None
    locator: dict[str, Any] | None = None
    locators: list[dict[str, Any]] = Field(default_factory=list)
    value: str | None = None
    key: str | None = None
    path: str | None = None
    window: dict[str, Any] | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None

    @field_validator(
        "url",
        "title",
        "value",
        "key",
        "path",
        "label",
        mode="before",
    )
    @classmethod
    def _coerce_text(cls, value: Any) -> Any:
        if value is None or isinstance(value, str):
            return value
        return str(value)


@dataclass
class RecordingDraft:
    """编译结果：可直接写入 Workflow.structured_plan 的草稿。"""

    plan: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    event_count: int = 0
    dropped_count: int = 0

    @property
    def steps(self) -> list[dict[str, Any]]:
        steps = self.plan.get("steps")
        return list(steps) if isinstance(steps, list) else []

    def to_payload(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "warnings": list(self.warnings),
            "event_count": self.event_count,
            "dropped_count": self.dropped_count,
        }


def compile_recording(
    events: Iterable[RecordingEvent | dict[str, Any]],
    *,
    mode: str = WEB_MODE,
    allowed_apps: Iterable[str] | None = None,
) -> RecordingDraft:
    """把录制事件编译成通过执行器校验的结构化草稿。

    ``mode`` 为 ``web`` 或 ``windows``；``allowed_apps`` 只在桌面模式下
    用于校验 ``launch_app`` 的白名单。
    """

    normalized_mode = _normalize_mode(mode)
    parsed = _parse_events(events)
    warnings: list[str] = []
    prepared, dropped = _prepare_events(parsed, normalized_mode, warnings)
    steps = _build_steps(prepared, normalized_mode, warnings)
    if not steps:
        raise RecordingError("录制事件没有生成任何可用步骤")

    _assign_step_metadata(steps)
    plan: dict[str, Any] = {
        "executor_type": normalized_mode,
        "steps": steps,
        "variables": [],
    }
    try:
        if normalized_mode == WEB_MODE:
            validated = validate_web_plan(plan)
            validated.setdefault("required_capabilities", [])
        else:
            validated = validate_desktop_plan(
                plan,
                allowed_apps or (),
            )
    except (WebPlanError, DesktopPlanError) as exc:
        raise RecordingError(f"录制草稿未通过校验：{exc}") from exc

    return RecordingDraft(
        plan=validated,
        warnings=warnings,
        event_count=len(parsed),
        dropped_count=dropped,
    )


def step_summaries(
    plan: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """生成前端可编辑列表使用的精简步骤说明。"""

    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list):
        return []
    summaries: list[dict[str, Any]] = []
    for raw in steps:
        if not isinstance(raw, dict):
            continue
        summaries.append(
            {
                "step_id": raw.get("step_id"),
                "executor": raw.get("executor"),
                "action": raw.get("action"),
                "params": raw.get("params") or {},
                "locator": raw.get("locator"),
                "idempotency_key": raw.get("idempotency_key"),
                "retry_policy": raw.get("retry_policy"),
            }
        )
    return summaries


def _normalize_mode(mode: Any) -> str:
    text = str(mode or "").strip().lower()
    if text not in _MODES:
        raise RecordingError(
            "录制模式只支持 web 或 windows，"
            f"当前为 {text or '<empty>'}"
        )
    return DESKTOP_MODE if text == "desktop" else text


def _parse_events(
    events: Iterable[RecordingEvent | dict[str, Any]],
) -> list[RecordingEvent]:
    parsed: list[RecordingEvent] = []
    for index, raw in enumerate(events):
        if isinstance(raw, RecordingEvent):
            parsed.append(raw)
            continue
        try:
            parsed.append(RecordingEvent.model_validate(raw))
        except ValidationError as exc:
            errors = exc.errors()
            detail = (
                str(errors[0].get("msg")) if errors else str(exc)
            )
            raise RecordingError(
                f"第 {index + 1} 条录制事件无效：{detail}"
            ) from exc
    return parsed


def _prepare_events(
    events: list[RecordingEvent],
    mode: str,
    warnings: list[str],
) -> tuple[list[RecordingEvent], int]:
    allowed_kinds = _WEB_KINDS if mode == WEB_MODE else _DESKTOP_KINDS
    scoped: list[RecordingEvent] = []
    dropped = 0
    for event in events:
        if event.kind in allowed_kinds:
            scoped.append(event)
            continue
        dropped += 1
        warnings.append(
            f"忽略与 {mode} 录制不匹配的事件：{event.kind.value}"
        )

    merged: list[RecordingEvent] = []
    for event in scoped:
        if (
            event.kind is RecordingEventKind.FILL
            and merged
            and merged[-1].kind is RecordingEventKind.FILL
            and _same_target(merged[-1], event)
        ):
            merged[-1] = event
            continue
        merged.append(event)

    prepared: list[RecordingEvent] = []
    for position, event in enumerate(merged):
        if event.kind is RecordingEventKind.CLICK and position + 1 < len(
            merged
        ):
            following = merged[position + 1]
            if (
                following.kind
                in {
                    RecordingEventKind.FILL,
                    RecordingEventKind.SELECT_OPTION,
                }
                and _same_target(event, following)
            ):
                # 点击输入框只是为了聚焦，真正的动作是随后的输入。
                continue
        prepared.append(event)
    return prepared, dropped


def _build_steps(
    events: list[RecordingEvent],
    mode: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    window_key: str | None = None
    for event in events:
        if mode == DESKTOP_MODE:
            window_params = _window_params(event.window)
            if event.kind is RecordingEventKind.DESKTOP_WINDOW:
                if window_params:
                    window_key = _target_key(window_params)
            elif window_params:
                key = _target_key(window_params)
                if key != window_key:
                    steps.append(
                        _make_step(
                            "activate_window",
                            window_params,
                            mode,
                        )
                    )
                    window_key = key
        step = _step_from_event(event, mode, warnings)
        if step is not None:
            steps.append(step)
    return steps


def _step_from_event(
    event: RecordingEvent,
    mode: str,
    warnings: list[str],
) -> dict[str, Any] | None:
    kind = event.kind
    if mode == WEB_MODE:
        return _web_step_from_event(event, warnings)
    if kind is RecordingEventKind.DESKTOP_LAUNCH:
        path = _text(event.path) or _text(event.params.get("path"))
        if not path:
            warnings.append("忽略缺少 path 的 desktop_launch 事件")
            return None
        return _make_step("launch_app", {"path": path}, mode)
    if kind is RecordingEventKind.DESKTOP_WINDOW:
        params = _window_params(event.window)
        if not params:
            warnings.append("忽略缺少窗口信息的 desktop_window 事件")
            return None
        return _make_step("activate_window", params, mode)
    if kind is RecordingEventKind.DESKTOP_CLICK:
        params = _pick_params(event.params, _DESKTOP_CLICK_PARAM_KEYS)
        if not any(
            name in params
            for name in ("automation_id", "name", "control_type", "x", "y")
        ):
            warnings.append("忽略缺少控件定位的 desktop_click 事件")
            return None
        return _make_step("click_element", params, mode)
    if kind is RecordingEventKind.DESKTOP_TYPE:
        text = _text(event.value) or _text(event.params.get("text"))
        if text is None:
            warnings.append("忽略缺少文本的 desktop_type 事件")
            return None
        params: dict[str, Any] = {"text": text}
        clear_first = event.params.get("clear_first")
        if isinstance(clear_first, bool):
            params["clear_first"] = clear_first
        method = _text(event.params.get("method"))
        if method in {"send_keys", "clipboard"}:
            params["method"] = method
        elif not text.isascii():
            # 中文输入建议走剪贴板，避免键盘布局差异。
            params["method"] = "clipboard"
        return _make_step("type_text", params, mode)
    if kind is RecordingEventKind.DESKTOP_HOTKEY:
        keys = _text(event.key) or _text(event.params.get("keys"))
        if not keys:
            warnings.append("忽略缺少按键的 desktop_hotkey 事件")
            return None
        return _make_step("press_hotkey", {"keys": keys}, mode)
    if kind is RecordingEventKind.DESKTOP_WAIT:
        seconds = _positive_number(
            event.params.get("seconds", event.value)
        )
        return _make_step(
            "wait",
            {"seconds": seconds if seconds is not None else 1.0},
            mode,
        )
    warnings.append(f"暂不支持桌面录制事件：{kind.value}")
    return None


def _web_step_from_event(
    event: RecordingEvent,
    warnings: list[str],
) -> dict[str, Any] | None:
    kind = event.kind
    if kind is RecordingEventKind.NAVIGATE:
        url = _text(event.url) or _text(event.params.get("url"))
        if not url:
            warnings.append("忽略缺少 url 的 navigate 事件")
            return None
        return _make_step("goto", {"url": url}, WEB_MODE)

    locator = _locator_payload(event)
    if kind is RecordingEventKind.CLICK:
        if locator is None:
            warnings.append("忽略缺少定位的 click 事件")
            return None
        params = _pick_params(event.params, _WEB_PARAM_KEYS["click"])
        return _make_step("click", params, WEB_MODE, locator=locator)
    if kind is RecordingEventKind.FILL:
        if locator is None:
            warnings.append("忽略缺少定位的 fill 事件")
            return None
        text = _text(event.value)
        if text is None:
            text = _text(event.params.get("text"))
        if text is None:
            warnings.append("忽略缺少文本的 fill 事件")
            return None
        return _make_step(
            "fill",
            {"text": text},
            WEB_MODE,
            locator=locator,
        )
    if kind is RecordingEventKind.PRESS:
        if locator is None:
            warnings.append("忽略缺少定位的 press 事件")
            return None
        key = _text(event.key) or _text(event.params.get("key"))
        if not key:
            warnings.append("忽略缺少按键的 press 事件")
            return None
        return _make_step("press", {"key": key}, WEB_MODE, locator=locator)
    if kind is RecordingEventKind.SELECT_OPTION:
        if locator is None:
            warnings.append("忽略缺少定位的 select_option 事件")
            return None
        params: dict[str, Any] = {}
        values = event.params.get("values")
        if isinstance(values, (list, tuple)) and values:
            params["values"] = [str(item) for item in values]
        elif values is not None:
            params["values"] = str(values)
        elif "index" in event.params:
            params["index"] = int(event.params["index"])
        elif _text(event.value) is not None:
            params["label"] = _text(event.value)
        else:
            warnings.append("忽略缺少选项的 select_option 事件")
            return None
        return _make_step(
            "select_option",
            params,
            WEB_MODE,
            locator=locator,
        )
    if kind in {RecordingEventKind.CHECK, RecordingEventKind.UNCHECK}:
        if locator is None:
            warnings.append(f"忽略缺少定位的 {kind.value} 事件")
            return None
        return _make_step(kind.value, {}, WEB_MODE, locator=locator)
    if kind is RecordingEventKind.UPLOAD:
        if locator is None:
            warnings.append("忽略缺少定位的 upload 事件")
            return None
        files = event.params.get("files")
        if isinstance(files, str) and files.strip():
            files = [files.strip()]
        if not isinstance(files, list) or not files:
            warnings.append("忽略缺少文件路径的 upload 事件")
            return None
        warnings.append(
            "upload 步骤记录的是文件名，发布前请人工确认文件路径"
        )
        return _make_step(
            "upload",
            {"files": [str(item) for item in files]},
            WEB_MODE,
            locator=locator,
        )
    if kind is RecordingEventKind.DOWNLOAD:
        params = _pick_params(event.params, _WEB_PARAM_KEYS["download"])
        return _make_step("download", params, WEB_MODE)
    if kind is RecordingEventKind.WAIT:
        milliseconds = _positive_number(
            event.params.get("milliseconds", event.value)
        )
        return _make_step(
            "wait_for",
            {"milliseconds": int(milliseconds or 500)},
            WEB_MODE,
        )
    warnings.append(f"暂不支持网页录制事件：{kind.value}")
    return None


def _make_step(
    action: str,
    params: dict[str, Any],
    mode: str,
    *,
    locator: dict[str, Any] | None = None,
) -> dict[str, Any]:
    step: dict[str, Any] = {
        "executor": mode,
        "action": action,
        "params": params,
        "depends_on": [],
    }
    if locator is not None:
        step["locator"] = locator
    return step


def _assign_step_metadata(steps: list[dict[str, Any]]) -> None:
    for index, step in enumerate(steps, start=1):
        step["step_id"] = index
        action = str(step.get("action") or "")
        step["retry_policy"] = (
            dict(_SINGLE_ATTEMPT_RETRY_POLICY)
            if action in _SINGLE_ATTEMPT_ACTIONS
            else dict(_DEFAULT_RETRY_POLICY)
        )
        step["idempotency_key"] = _idempotency_key(index, step)


def _idempotency_key(index: int, step: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "action": step.get("action"),
            "params": step.get("params") or {},
            "locator": step.get("locator"),
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]
    action = str(step.get("action") or "step")
    return f"rec-{index:02d}-{action}-{digest}"


def _locator_payload(event: RecordingEvent) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    if isinstance(event.locator, dict):
        candidates.append(event.locator)
    for candidate in event.locators:
        if isinstance(candidate, dict):
            candidates.append(candidate)

    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        payload = _normalize_locator(candidate)
        if payload is not None and payload not in normalized:
            normalized.append(payload)
    if not normalized:
        return None

    def priority(item: dict[str, Any]) -> int:
        try:
            strategy = LocatorStrategy(str(item.get("strategy")))
        except ValueError:
            return len(LOCATOR_PRIORITY)
        return LOCATOR_PRIORITY[strategy]

    normalized.sort(key=priority)
    primary = dict(normalized[0])
    fallbacks = [dict(item) for item in normalized[1:]]
    if fallbacks:
        primary["fallbacks"] = fallbacks
    return primary


def _normalize_locator(
    candidate: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        strategy = LocatorStrategy(str(candidate.get("strategy") or ""))
    except ValueError:
        return None
    value = _text(candidate.get("value"))
    if value is None:
        return None

    payload: dict[str, Any] = {
        "strategy": strategy.value,
        "value": value,
    }
    name = _text(candidate.get("name"))
    if name is not None:
        payload["name"] = name
    nth = candidate.get("nth")
    if isinstance(nth, int) and nth >= 0:
        payload["nth"] = nth
    if candidate.get("exact") is True:
        payload["exact"] = True
    frame = _text(candidate.get("frame"))
    if frame is not None:
        payload["frame"] = frame
    return payload


def _window_params(window: Any) -> dict[str, Any] | None:
    if not isinstance(window, dict):
        return None
    params = _pick_params(window, _WINDOW_PARAM_KEYS)
    if not any(
        name in params for name in ("title", "class_name", "pid")
    ):
        return None
    return params


def _pick_params(
    source: Any,
    allowed: Iterable[str],
) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {
        key: source[key]
        for key in allowed
        if source.get(key) is not None
    }


def _same_target(
    first: RecordingEvent,
    second: RecordingEvent,
) -> bool:
    left = _target_key(_first_locator(first))
    if left is None:
        return False
    return left == _target_key(_first_locator(second))


def _first_locator(event: RecordingEvent) -> dict[str, Any] | None:
    locator = _locator_payload(event)
    if locator is None:
        return None
    return {
        "strategy": locator.get("strategy"),
        "value": locator.get("value"),
        "name": locator.get("name"),
    }


def _target_key(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict) or not payload:
        return None
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


BROWSER_RECORDER_SCRIPT = r"""
(() => {
  const STORE_KEY = "__nlrpa_recorder_v1";

  const load = () => {
    try {
      const raw = window.sessionStorage.getItem(STORE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && Array.isArray(parsed.events)) {
          return { events: parsed.events, stopped: !!parsed.stopped };
        }
      }
    } catch (error) {
      // 忽略损坏的会话数据，重新开始录制。
    }
    return { events: [], stopped: false };
  };

  const state = load();
  if (window.__nlrpaRecorder) return;

  const save = () => {
    try {
      window.sessionStorage.setItem(STORE_KEY, JSON.stringify(state));
    } catch (error) {
      // 隐私模式下 sessionStorage 可能不可写，仍在内存里继续录制。
    }
  };

  const push = (event) => {
    if (state.stopped) return;
    state.events.push({ at: performance.now() / 1000, ...event });
    save();
  };

  const clean = (value, limit) =>
    (value || "").replace(/\s+/g, " ").trim().slice(0, limit || 80);

  const implicitRole = (element) => {
    const tag = element.tagName.toLowerCase();
    const type = (element.getAttribute("type") || "").toLowerCase();
    if (tag === "button") return "button";
    if (tag === "a" && element.hasAttribute("href")) return "link";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "input") {
      if (["button", "submit", "reset"].includes(type)) return "button";
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      return "textbox";
    }
    return "";
  };

  const accessibleName = (element) => {
    const labelled =
      element.getAttribute("aria-label") ||
      element.getAttribute("title") ||
      element.getAttribute("placeholder");
    if (labelled) return clean(labelled);
    if (element.labels && element.labels.length) {
      return clean(element.labels[0].innerText || element.labels[0].textContent);
    }
    return clean(element.innerText || element.textContent);
  };

  const cssPath = (element) => {
    const parts = [];
    let current = element;
    while (current && current.nodeType === 1 && parts.length < 4) {
      let part = current.tagName.toLowerCase();
      if (current.id) {
        part += "#" + current.id;
        parts.unshift(part);
        break;
      }
      const parent = current.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(
          (child) => child.tagName === current.tagName
        );
        if (siblings.length > 1) {
          part += ":nth-of-type(" + (siblings.indexOf(current) + 1) + ")";
        }
      }
      parts.unshift(part);
      current = parent;
    }
    return parts.join(" > ");
  };

  const locatorsFor = (element) => {
    if (!element || element.nodeType !== 1) return [];
    const candidates = [];
    const testid =
      element.getAttribute("data-testid") ||
      element.getAttribute("data-test-id") ||
      element.getAttribute("data-test");
    if (testid) candidates.push({ strategy: "testid", value: testid });
    const role = element.getAttribute("role") || implicitRole(element);
    const name = accessibleName(element);
    if (role && name) {
      candidates.push({ strategy: "role", value: role, name: name });
    }
    const label = element.labels && element.labels.length
      ? clean(element.labels[0].innerText || element.labels[0].textContent)
      : "";
    if (label) candidates.push({ strategy: "label", value: label });
    const placeholder = element.getAttribute("placeholder");
    if (placeholder) {
      candidates.push({ strategy: "placeholder", value: clean(placeholder) });
    }
    if (name && !element.children.length) {
      candidates.push({ strategy: "text", value: name });
    }
    const path = cssPath(element);
    if (path) candidates.push({ strategy: "css", value: path });
    return candidates;
  };

  const targetOf = (event) => {
    const target = event.target;
    if (target && target.nodeType === 1) return target;
    return target && target.parentElement ? target.parentElement : null;
  };

  const recordNavigate = () => {
    const last = [...state.events].reverse().find(
      (item) => item.kind === "navigate"
    );
    if (last && last.url === location.href) return;
    push({ kind: "navigate", url: location.href, title: document.title });
  };

  const combo = (event) => {
    const parts = [];
    if (event.ctrlKey) parts.push("Control");
    if (event.altKey) parts.push("Alt");
    if (event.shiftKey) parts.push("Shift");
    if (event.metaKey) parts.push("Meta");
    const key = event.key;
    if (!["Control", "Alt", "Shift", "Meta"].includes(key)) {
      parts.push(key.length === 1 ? key.toUpperCase() : key);
    }
    return parts.join("+");
  };

  document.addEventListener(
    "click",
    (event) => {
      const element = targetOf(event);
      if (!element) return;
      const type = (element.getAttribute("type") || "").toLowerCase();
      if (type === "checkbox" || type === "radio") {
        push({
          kind: element.checked ? "check" : "uncheck",
          locators: locatorsFor(element),
        });
        return;
      }
      push({
        kind: "click",
        locators: locatorsFor(element),
        label: clean(element.innerText || element.textContent, 40),
      });
    },
    true
  );

  document.addEventListener(
    "input",
    (event) => {
      const element = targetOf(event);
      if (!element) return;
      const tag = element.tagName.toLowerCase();
      if (tag === "textarea" || tag === "input") {
        const type = (element.getAttribute("type") || "text").toLowerCase();
        if (["checkbox", "radio", "file", "button", "submit"].includes(type)) {
          return;
        }
        push({
          kind: "fill",
          locators: locatorsFor(element),
          value: String(element.value || ""),
        });
      }
    },
    true
  );

  document.addEventListener(
    "change",
    (event) => {
      const element = targetOf(event);
      if (!element) return;
      const tag = element.tagName.toLowerCase();
      if (tag === "select") {
        const option = element.selectedOptions && element.selectedOptions[0];
        push({
          kind: "select_option",
          locators: locatorsFor(element),
          value: option ? option.textContent : String(element.value || ""),
        });
        return;
      }
      const type = (element.getAttribute("type") || "").toLowerCase();
      if (type === "file") {
        const files = Array.from(element.files || []).map((file) => file.name);
        push({
          kind: "upload",
          locators: locatorsFor(element),
          params: { files: files },
        });
      }
    },
    true
  );

  document.addEventListener(
    "keydown",
    (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        event.stopPropagation();
        state.stopped = true;
        save();
        return;
      }
      if (event.key === "Tab") {
        // Tab 只用于切换字段，不产生步骤，避免草稿里出现噪音。
        return;
      }
      if (event.key === "Enter" || event.key === "Escape") {
        push({
          kind: "press",
          key: event.key,
          locators: locatorsFor(targetOf(event)),
        });
      }
    },
    true
  );

  window.addEventListener("popstate", recordNavigate);
  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", recordNavigate);
  } else {
    recordNavigate();
  }

  window.__nlrpaRecorder = {
    snapshot() {
      return { events: state.events.slice(), stopped: state.stopped };
    },
    stop() {
      state.stopped = true;
      save();
    },
  };
})();
"""


__all__ = [
    "BROWSER_RECORDER_SCRIPT",
    "DESKTOP_MODE",
    "WEB_MODE",
    "RecordingDraft",
    "RecordingError",
    "RecordingEvent",
    "RecordingEventKind",
    "compile_recording",
    "step_summaries",
]

