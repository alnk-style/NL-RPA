import json
import sqlite3
from pathlib import Path

from scripts.collect_baseline import collect_baseline, render_markdown


def _create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE workflow (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE execution_log (
            id INTEGER PRIMARY KEY,
            workflow_id INTEGER,
            status TEXT,
            started_at TEXT,
            finished_at TEXT,
            error_message TEXT,
            screenshots TEXT,
            artifacts TEXT,
            step_logs TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO workflow (id, name) VALUES (1, '试点流程')"
    )
    connection.execute(
        """
        INSERT INTO execution_log (
            id, workflow_id, status, started_at, finished_at,
            error_message, screenshots, artifacts, step_logs
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            1,
            "success",
            "2026-10-02 10:00:00",
            "2026-10-02 10:00:10",
            None,
            json.dumps(["step_0.png", "step_1.png"]),
            json.dumps([{"path": "report.csv"}]),
            json.dumps(
                [
                    {"step_index": 0, "status": "success"},
                    {"step_index": 1, "status": "success"},
                ]
            ),
        ),
    )
    connection.execute(
        """
        INSERT INTO execution_log (
            id, workflow_id, status, started_at, finished_at,
            error_message, screenshots, artifacts, step_logs
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            2,
            1,
            "failed",
            "2026-10-02 10:01:00",
            "2026-10-02 10:01:05",
            'Timeout 30000ms exceeded.\nwaiting for locator("#login")',
            json.dumps(["step_0_failed.png"]),
            json.dumps([]),
            json.dumps(
                [
                    {
                        "step_index": 0,
                        "status": "failed",
                        "error": "waiting for locator",
                    }
                ]
            ),
        ),
    )
    connection.execute(
        """
        INSERT INTO execution_log (
            id, workflow_id, status, started_at, finished_at,
            error_message, screenshots, artifacts, step_logs
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            3,
            1,
            "paused",
            "2026-10-02 10:02:00",
            None,
            "AUTH_REQUIRED: 请重新登录",
            json.dumps([]),
            json.dumps([]),
            json.dumps([]),
        ),
    )
    connection.commit()
    connection.close()


def test_collect_baseline_handles_missing_database(
    tmp_path: Path,
) -> None:
    metrics = collect_baseline(tmp_path / "missing.db")

    assert metrics["exists"] is False
    assert metrics["execution_count"] == 0
    assert "数据库文件不存在" in render_markdown(metrics)


def test_collect_baseline_summarizes_executions(tmp_path: Path) -> None:
    database = tmp_path / "baseline.db"
    _create_database(database)

    metrics = collect_baseline(database)

    assert metrics["exists"] is True
    assert metrics["workflow_count"] == 1
    assert metrics["execution_count"] == 3
    assert metrics["success_count"] == 1
    assert metrics["failure_count"] == 1
    assert metrics["success_rate"] == 50.0
    assert metrics["paused_count"] == 1
    assert metrics["manual_intervention_count"] == 1
    assert metrics["failure_category_counts"] == {
        "locator_not_found": 1
    }
    assert metrics["locator_failure_count"] == 1
    assert metrics["locator_failure_rate"] == 100.0
    assert metrics["step_count"] == 3
    assert metrics["failed_step_count"] == 1
    assert metrics["screenshot_count"] == 3
    assert metrics["artifact_count"] == 1
    assert metrics["average_duration_seconds"] == 7.5
    assert metrics["p50_duration_seconds"] == 7.5

    report = render_markdown(metrics)
    assert "首次成功率" in report
    assert "locator_not_found" in report
