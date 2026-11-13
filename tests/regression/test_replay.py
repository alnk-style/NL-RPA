"""回放引擎自身的测试（真实夹具的通过率由脚本报告）。"""

from __future__ import annotations

import json
from pathlib import Path

from app.regression.replay import replay_all, resolve_locator

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"


def test_replay_all_writes_summary_snapshots_and_results(tmp_path) -> None:
    summary = replay_all(FIXTURES_ROOT, tmp_path)

    assert summary["total"] >= 4
    assert "pass_rate" in summary
    assert (tmp_path / "summary.json").is_file()
    for result in summary["results"]:
        assert (tmp_path / result["name"] / "result.json").is_file()
        for snapshot in result["snapshot_paths"]:
            assert Path(snapshot).is_file()

    # 回放失败不应抛异常影响普通用例；这里只校验引擎产出结构。
    assert all(
        isinstance(result["passed"], bool)
        for result in summary["results"]
    )


def test_broken_fixture_reports_failure_without_raising(tmp_path) -> None:
    fixtures = tmp_path / "fixtures"
    broken = fixtures / "broken"
    broken.mkdir(parents=True)
    (broken / "fixture.json").write_text(
        json.dumps(
            {
                "name": "broken",
                "observation": {
                    "snapshot": "missing.json",
                    "expected_locators": [],
                },
            }
        ),
        encoding="utf-8",
    )

    summary = replay_all(fixtures, tmp_path / "out")
    assert summary["total"] == 1
    assert summary["failed"] == 1
    assert summary["pass_rate"] == 0.0
    result = summary["results"][0]
    assert result["passed"] is False
    assert result["checks"][0]["passed"] is False


def test_visual_ambiguous_fixture_replays_offline(tmp_path) -> None:
    summary = replay_all(FIXTURES_ROOT, tmp_path)

    result = next(
        item
        for item in summary["results"]
        if item["name"] == "visual_ambiguous_two_candidates"
    )
    assert result["passed"] is True
    checks = {
        check["name"]: check for check in result["checks"]
    }
    assert checks["visual_decision"]["actual"]["action"] == "confirm"
    assert checks["visual_decision"]["actual"]["provider"] == "remote_vlm"
    assert checks["visual_candidate_order"]["actual"] == [
        "导出",
        "导出全部",
    ]


def test_resolve_locator_supports_all_snapshot_strategies() -> None:
    elements = [
        {
            "role": "textbox",
            "name": "用户名",
            "text": "",
            "placeholder": "请输入用户名",
            "id": "username",
            "test_id": "username-input",
        },
        {
            "role": "button",
            "name": "登录",
            "text": "登录",
            "id": "login",
            "test_id": "login-button",
        },
    ]
    role = resolve_locator(
        {"strategy": "role", "value": "button", "name": "登录"},
        elements,
    )
    assert role == {
        "strategy": "role",
        "matched": 1,
        "unique": True,
        "index": 1,
    }
    assert resolve_locator(
        {"strategy": "placeholder", "value": "请输入用户名", "exact": False},
        elements,
    )["index"] == 0
    assert resolve_locator(
        {"strategy": "testid", "value": "login-button"},
        elements,
    )["index"] == 1
    assert resolve_locator(
        {"strategy": "css", "value": "#username"},
        elements,
    )["index"] == 0
    assert resolve_locator(
        {"strategy": "text", "value": "登录", "exact": True},
        elements,
    )["index"] == 1
    assert resolve_locator(
        {"strategy": "label", "value": "用户名", "exact": True},
        elements,
    )["index"] == 0
