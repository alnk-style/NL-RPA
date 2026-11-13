"""把录制草稿、观察快照和 step_logs 固化成可重复的回放夹具。

回放不依赖浏览器或网络：录制夹具重新编译事件并比对步骤；观察夹具在
固定快照上解析 ``LocatorSpec`` 候选；step_logs 夹具重新做失败分类并
校验检查点续跑参数。回放结果写入输出目录（含快照副本与 summary.json），
失败只影响 :mod:`scripts.run_regression`，不影响普通 pytest 用例。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.engine.checkpoints import find_checkpoint
from app.engine.recorder import RecordingError, compile_recording
from app.engine.visual_fallback import VisualCapture, VisualFallback
from app.metrics.steps import summarize_steps
from app.visual.provider import (
    VisualCandidate,
    VisualLocateRequest,
    VisualLocateResponse,
)


@dataclass
class ReplayCheck:
    name: str
    passed: bool
    detail: str = ""
    expected: Any = None
    actual: Any = None

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
        }
        if self.expected is not None:
            payload["expected"] = self.expected
        if self.actual is not None:
            payload["actual"] = self.actual
        return payload


@dataclass
class ReplayResult:
    name: str
    fixture_dir: str
    passed: bool
    checks: list[ReplayCheck] = field(default_factory=list)
    failure_categories: dict[str, int] = field(default_factory=dict)
    snapshot_paths: list[str] = field(default_factory=list)
    error: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fixture_dir": self.fixture_dir,
            "passed": self.passed,
            "error": self.error,
            "checks": [check.to_payload() for check in self.checks],
            "failure_categories": dict(self.failure_categories),
            "snapshot_paths": list(self.snapshot_paths),
        }


def load_fixture(fixture_dir: Path) -> dict[str, Any]:
    fixture_path = Path(fixture_dir) / "fixture.json"
    if not fixture_path.is_file():
        raise FileNotFoundError(f"夹具缺少 fixture.json：{fixture_path}")
    with fixture_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"夹具必须是 JSON 对象：{fixture_path}")
    payload.setdefault("name", Path(fixture_dir).name)
    return payload


def replay_all(
    fixtures_root: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    root = Path(fixtures_root)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)

    fixture_dirs = sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and (path / "fixture.json").is_file()
    ) if root.is_dir() else []

    results: list[ReplayResult] = []
    for fixture_dir in fixture_dirs:
        try:
            fixture = load_fixture(fixture_dir)
            result = replay_fixture(fixture, fixture_dir, target)
        except Exception as exc:  # noqa: BLE001
            result = ReplayResult(
                name=fixture_dir.name,
                fixture_dir=str(fixture_dir),
                passed=False,
                error=str(exc) or exc.__class__.__name__,
            )
        results.append(result)
        fixture_output = target / result.name
        fixture_output.mkdir(parents=True, exist_ok=True)
        (fixture_output / "result.json").write_text(
            json.dumps(result.to_payload(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    failed = [result for result in results if not result.passed]
    total = len(results)
    categories: dict[str, int] = {}
    snapshots: list[str] = []
    for result in results:
        for name, count in result.failure_categories.items():
            categories[name] = categories.get(name, 0) + int(count)
        snapshots.extend(result.snapshot_paths)

    summary = {
        "generated_at": datetime.utcnow().isoformat(),
        "fixtures_root": str(root),
        "output_dir": str(target),
        "total": total,
        "passed": total - len(failed),
        "failed": len(failed),
        "pass_rate": (
            round((total - len(failed)) / total, 4) if total else 0.0
        ),
        "failure_categories": categories,
        "snapshots": snapshots,
        "results": [result.to_payload() for result in results],
    }
    (target / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def replay_fixture(
    fixture: dict[str, Any],
    fixture_dir: Path | str,
    output_dir: Path | str,
) -> ReplayResult:
    fixture_dir = Path(fixture_dir)
    output_dir = Path(output_dir)
    result = ReplayResult(
        name=str(fixture.get("name") or fixture_dir.name),
        fixture_dir=str(fixture_dir),
        passed=True,
    )

    if isinstance(fixture.get("recording"), dict):
        result.checks.extend(
            _replay_recording(
                fixture["recording"],
                fixture_dir,
                output_dir,
                result,
            )
        )
    if isinstance(fixture.get("observation"), dict):
        result.checks.extend(
            _replay_observation(
                fixture["observation"],
                fixture_dir,
                output_dir,
                result,
            )
        )
    if "step_logs" in fixture:
        result.checks.extend(
            _replay_step_logs(
                fixture.get("step_logs"),
                fixture,
                result,
            )
        )
    if _is_visual_fixture(fixture):
        result.checks.extend(
            _replay_visual(fixture, output_dir, result)
        )

    if not result.checks:
        result.checks.append(
            ReplayCheck(
                name="fixture",
                passed=False,
                detail="夹具没有任何可回放的片段",
            )
        )
    result.passed = all(check.passed for check in result.checks)
    return result


def _is_visual_fixture(fixture: dict[str, Any]) -> bool:
    return (
        isinstance(fixture.get("visual"), dict)
        or (
            isinstance(fixture.get("step"), dict)
            and isinstance(fixture.get("candidates"), list)
            and "expected_action" in fixture
        )
    )


class _FixtureVisualProvider:
    name = "remote_vlm"

    def __init__(
        self,
        candidates: list[VisualCandidate],
        *,
        provider: str,
    ) -> None:
        self.candidates = candidates
        self.provider = provider

    async def locate(
        self,
        request: VisualLocateRequest,
    ) -> VisualLocateResponse:
        del request
        return VisualLocateResponse(
            provider=self.provider,
            model="fixture-fake-vlm",
            candidates=list(self.candidates),
        )


def _replay_visual(
    fixture: dict[str, Any],
    output_dir: Path,
    result: ReplayResult,
) -> list[ReplayCheck]:
    raw = (
        fixture["visual"]
        if isinstance(fixture.get("visual"), dict)
        else fixture
    )
    step_payload = raw.get("step")
    settings_payload = raw.get("settings")
    candidates_payload = raw.get("candidates")
    if not isinstance(step_payload, dict):
        return [
            ReplayCheck(
                name="visual_fixture",
                passed=False,
                detail="视觉夹具缺少 step",
            )
        ]
    if not isinstance(settings_payload, dict):
        return [
            ReplayCheck(
                name="visual_fixture",
                passed=False,
                detail="视觉夹具缺少 settings",
            )
        ]
    if not isinstance(candidates_payload, list):
        return [
            ReplayCheck(
                name="visual_fixture",
                passed=False,
                detail="视觉夹具缺少 candidates",
            )
        ]

    try:
        candidates = [
            VisualCandidate.model_validate(item)
            for item in candidates_payload
        ]
        provider_name = str(
            settings_payload.get("provider") or "remote_vlm"
        )
        decision = asyncio.run(
            VisualFallback().try_locate(
                SimpleNamespace(**step_payload),
                VisualCapture(
                    screenshot=b"fixture-redacted-image",
                    image_width=int(raw.get("image_width", 1280)),
                    image_height=int(raw.get("image_height", 720)),
                    viewport={
                        "width": int(raw.get("image_width", 1280)),
                        "height": int(raw.get("image_height", 720)),
                    },
                    dom_candidates=[],
                    uia_candidates=[],
                    safe_visible_text="",
                    source="fixture",
                    location="fixture://visual",
                ),
                _FixtureVisualProvider(
                    candidates,
                    provider=provider_name,
                ),
                settings=SimpleNamespace(**settings_payload),
                risk_level=str(
                    raw.get("risk_level") or "read_only"
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001
        return [
            ReplayCheck(
                name="visual_decision",
                passed=False,
                detail=f"视觉回放失败：{exc}",
            )
        ]

    expected_action = str(raw.get("expected_action") or "confirm")
    decision_passed = (
        decision.action == expected_action
        and decision.provider == provider_name
    )
    decision_payload = {
        "action": decision.action,
        "reason": decision.reason,
        "provider": decision.provider,
        "model": decision.model,
        "candidates": [
            {
                "candidate_id": item.candidate_id,
                "target": item.candidate.target,
                "bbox": list(item.candidate.bbox),
                "confidence": item.candidate.confidence,
                "evidence": item.candidate.evidence,
            }
            for item in decision.candidates
        ],
        "automatic_selection": (
            decision.selected.candidate_id
            if decision.selected is not None
            else None
        ),
        "audit": dict(decision.audit),
    }
    result.snapshot_paths.append(
        _write_snapshot(
            output_dir,
            result.name,
            "visual_decision.json",
            decision_payload,
        )
    )
    checks = [
        ReplayCheck(
            name="visual_decision",
            passed=decision_passed,
            detail=(
                "视觉决策符合期望"
                if decision_passed
                else "视觉决策不符合期望"
            ),
            expected={
                "action": expected_action,
                "provider": provider_name,
            },
            actual=decision_payload,
        )
    ]

    expected_targets = [
        candidate.target
        for candidate in sorted(
            candidates,
            key=lambda item: (
                -float(item.confidence),
                item.bbox,
                item.target.casefold(),
            ),
        )
    ]
    actual_targets = [
        item.candidate.target for item in decision.candidates
    ]
    checks.append(
        ReplayCheck(
            name="visual_candidate_order",
            passed=actual_targets == expected_targets,
            detail=(
                "视觉候选顺序稳定"
                if actual_targets == expected_targets
                else "视觉候选顺序不稳定"
            ),
            expected=expected_targets,
            actual=actual_targets,
        )
    )
    return checks


def _replay_recording(
    recording: dict[str, Any],
    fixture_dir: Path,
    output_dir: Path,
    result: ReplayResult,
) -> list[ReplayCheck]:
    checks: list[ReplayCheck] = []
    events = recording.get("events")
    mode = str(recording.get("mode") or "web")
    try:
        draft = compile_recording(events or [], mode=mode)
    except (RecordingError, ValueError) as exc:
        checks.append(
            ReplayCheck(
                name="recording_compile",
                passed=False,
                detail=str(exc),
            )
        )
        return checks

    plan = draft.plan
    result.snapshot_paths.append(
        _write_snapshot(
            output_dir,
            result.name,
            "recording_plan.json",
            plan,
        )
    )
    checks.append(
        ReplayCheck(
            name="recording_compile",
            passed=True,
            detail=f"生成 {len(draft.steps)} 个步骤",
            actual={"steps": len(draft.steps)},
        )
    )
    expected_steps = recording.get("expected_steps")
    if isinstance(expected_steps, list):
        actual_steps = [
            {
                "action": step.get("action"),
                "executor": step.get("executor"),
            }
            for step in draft.steps
        ]
        passed = len(expected_steps) == len(actual_steps) and all(
            _matches_subset(actual, expected)
            for actual, expected in zip(actual_steps, expected_steps)
        )
        checks.append(
            ReplayCheck(
                name="recording_steps",
                passed=passed,
                detail="步骤序列与期望一致" if passed else "步骤序列不一致",
                expected=expected_steps,
                actual=actual_steps,
            )
        )
    expected_keys = recording.get("expected_idempotency_keys")
    if expected_keys is not None:
        actual_keys = [
            step.get("idempotency_key") for step in draft.steps
        ]
        checks.append(
            ReplayCheck(
                name="recording_idempotency_keys",
                passed=actual_keys == list(expected_keys),
                detail="幂等键一致" if actual_keys == list(expected_keys) else "幂等键不一致",
                expected=list(expected_keys),
                actual=actual_keys,
            )
        )
    return checks


def _replay_observation(
    observation: dict[str, Any],
    fixture_dir: Path,
    output_dir: Path,
    result: ReplayResult,
) -> list[ReplayCheck]:
    checks: list[ReplayCheck] = []
    snapshot_name = str(observation.get("snapshot") or "")
    snapshot_path = fixture_dir / snapshot_name if snapshot_name else None
    if snapshot_path is None or not snapshot_path.is_file():
        checks.append(
            ReplayCheck(
                name="observation_snapshot",
                passed=False,
                detail=f"快照文件不存在：{snapshot_name}",
            )
        )
        return checks

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    elements = snapshot.get("elements") if isinstance(snapshot, dict) else None
    if not isinstance(elements, list):
        checks.append(
            ReplayCheck(
                name="observation_snapshot",
                passed=False,
                detail="快照缺少 elements",
            )
        )
        return checks

    copied = output_dir / "snapshots" / result.name
    copied.mkdir(parents=True, exist_ok=True)
    target = copied / snapshot_path.name
    shutil.copyfile(snapshot_path, target)
    result.snapshot_paths.append(str(target))
    checks.append(
        ReplayCheck(
            name="observation_snapshot",
            passed=True,
            detail=f"{len(elements)} 个元素",
        )
    )

    for item in observation.get("expected_locators") or []:
        if not isinstance(item, dict):
            continue
        spec = item.get("spec") if isinstance(item.get("spec"), dict) else item
        expect = item.get("expect") if isinstance(item.get("expect"), dict) else {}
        resolved = resolve_locator(spec, elements)
        passed = True
        for key in ("matched", "unique", "strategy"):
            if key in expect and resolved.get(key) != expect[key]:
                passed = False
        checks.append(
            ReplayCheck(
                name=f"locator:{spec.get('strategy')}:{spec.get('value')}",
                passed=passed,
                detail="定位解析符合期望" if passed else "定位解析不符合期望",
                expected=expect,
                actual=resolved,
            )
        )
    return checks


def _replay_step_logs(
    step_logs: Any,
    fixture: dict[str, Any],
    result: ReplayResult,
) -> list[ReplayCheck]:
    checks: list[ReplayCheck] = []
    summary = summarize_steps(step_logs)
    result.failure_categories = dict(summary["failure_categories"])

    expected_categories = fixture.get("expected_failure_categories")
    if isinstance(expected_categories, dict):
        actual = {
            name: count
            for name, count in summary["failure_categories"].items()
            if count
        }
        checks.append(
            ReplayCheck(
                name="failure_categories",
                passed=actual == expected_categories,
                detail="失败分类一致" if actual == expected_categories else "失败分类不一致",
                expected=expected_categories,
                actual=actual,
            )
        )

    if "expected_next_step_index" in fixture or "expected_idempotency_keys" in fixture:
        checkpoint = find_checkpoint(step_logs)
        actual_next = (
            checkpoint.get("next_step_index") if checkpoint else None
        )
        actual_keys = (
            list(checkpoint.get("completed_idempotency_keys") or [])
            if checkpoint
            else []
        )
        if "expected_next_step_index" in fixture:
            expected_next = fixture["expected_next_step_index"]
            checks.append(
                ReplayCheck(
                    name="checkpoint_next_step_index",
                    passed=actual_next == expected_next,
                    detail="检查点续跑位置一致"
                    if actual_next == expected_next
                    else "检查点续跑位置不一致",
                    expected=expected_next,
                    actual=actual_next,
                )
            )
        if "expected_idempotency_keys" in fixture:
            expected_keys = list(fixture["expected_idempotency_keys"])
            checks.append(
                ReplayCheck(
                    name="checkpoint_idempotency_keys",
                    passed=actual_keys == expected_keys,
                    detail="幂等键一致" if actual_keys == expected_keys else "幂等键不一致",
                    expected=expected_keys,
                    actual=actual_keys,
                )
            )
    return checks


def resolve_locator(
    spec: dict[str, Any],
    elements: list[Any],
) -> dict[str, Any]:
    """在固定快照上解析一个 ``LocatorSpec`` 候选。"""

    strategy = str(spec.get("strategy") or "").strip().lower()
    value = str(spec.get("value") or "")
    name = spec.get("name")
    exact = bool(spec.get("exact"))
    matches: list[int] = []
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            continue
        if _element_matches(strategy, value, name, exact, element):
            matches.append(index)
    return {
        "strategy": strategy,
        "matched": len(matches),
        "unique": len(matches) == 1,
        "index": matches[0] if matches else None,
    }


def _element_matches(
    strategy: str,
    value: str,
    name: Any,
    exact: bool,
    element: dict[str, Any],
) -> bool:
    def text_match(candidate: Any) -> bool:
        text = str(candidate or "")
        if not text:
            return False
        return text == value if exact else value.lower() in text.lower()

    if strategy == "role":
        if str(element.get("role") or "") != value:
            return False
        if name is None or str(name).strip() == "":
            return True
        expected_name = str(name)
        candidate_name = str(element.get("name") or "")
        if not candidate_name:
            return False
        return (
            candidate_name == expected_name
            if exact
            else expected_name.lower() in candidate_name.lower()
        )
    if strategy == "label":
        return text_match(element.get("name")) or text_match(
            element.get("aria_label")
        )
    if strategy == "placeholder":
        return text_match(element.get("placeholder"))
    if strategy == "testid":
        return str(element.get("test_id") or "") == value
    if strategy == "text":
        return text_match(element.get("text")) or text_match(
            element.get("name")
        )
    if strategy == "css":
        element_id = str(element.get("id") or "")
        if value.startswith("#"):
            return element_id == value[1:]
        if "data-testid" in value or "data-test" in value or "data-cy" in value:
            return str(element.get("test_id") or "") in value
        return bool(element_id) and (
            value == element_id
            or value == f"#{element_id}"
            or text_match(element_id)
        )
    if strategy == "xpath":
        element_id = str(element.get("id") or "")
        return bool(element_id) and element_id in value
    return False


def _matches_subset(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            key in actual and _matches_subset(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(
            _matches_subset(item, expected_item)
            for item, expected_item in zip(actual, expected)
        )
    return actual == expected


def _write_snapshot(
    output_dir: Path,
    fixture_name: str,
    filename: str,
    payload: Any,
) -> str:
    target_dir = output_dir / "snapshots" / fixture_name
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(target)


__all__ = [
    "ReplayCheck",
    "ReplayResult",
    "load_fixture",
    "replay_all",
    "replay_fixture",
    "resolve_locator",
]
