"""采集 NL-RPA 可靠性与 MCP 方案的阶段 0 基线指标。

直接读取 SQLite 数据库里的 workflow / execution_log 两张表，输出
成功率、失败分类、人工介入和耗时基线，供后续阶段对比。

用法：

    python scripts/collect_baseline.py
    python scripts/collect_baseline.py --database rpa.db --format json
    python scripts/collect_baseline.py --output logs/baseline.md
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.protocols import FailureCategory, classify_failure

SUCCESS_STATUSES = {"success", "completed"}
FAILURE_STATUSES = {"failed", "error", "cancelled"}
AWAITING_USER_STATUSES = {"awaiting_user"}
PAUSED_STATUSES = {"paused"}
LOCATOR_FAILURE_CATEGORIES = {
    FailureCategory.LOCATOR_NOT_FOUND,
    FailureCategory.LOCATOR_NOT_UNIQUE,
    FailureCategory.ELEMENT_NOT_VISIBLE,
    FailureCategory.ELEMENT_DISABLED,
    FailureCategory.WINDOW_NOT_FOUND,
}
NEEDS_ATTENTION_CATEGORIES = {
    FailureCategory.AUTH_REQUIRED,
    FailureCategory.PERMISSION_DENIED,
}


def _parse_database_location(value: str) -> Path:
    """把 DATABASE_URL 或文件路径转换成 SQLite 文件路径。"""

    candidate = str(value or "").strip()
    if not candidate:
        candidate = "rpa.db"

    for prefix in (
        "sqlite+aiosqlite:///",
        "sqlite+aiosqlite://",
        "sqlite:///",
        "sqlite://",
    ):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):]
            break
    else:
        if "://" in candidate:
            raise SystemExit(
                "collect_baseline 目前只支持 SQLite 数据库："
                f"{value}"
            )

    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _execution_error_texts(row: sqlite3.Row) -> list[str]:
    texts: list[str] = []
    error_message = row["error_message"]
    if isinstance(error_message, str) and error_message.strip():
        texts.append(error_message.strip())

    step_logs = _json_loads(row["step_logs"], [])
    if isinstance(step_logs, list):
        for step_log in step_logs:
            if not isinstance(step_log, dict):
                continue
            error = step_log.get("error")
            if isinstance(error, str) and error.strip():
                texts.append(error.strip())
    return texts


def _classify_execution(row: sqlite3.Row) -> FailureCategory:
    for text in _execution_error_texts(row):
        category = classify_failure(text)
        if category != FailureCategory.UNKNOWN:
            return category
    return FailureCategory.UNKNOWN


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100, 2)


def collect_baseline(
    database_path: Path | str,
) -> dict[str, Any]:
    """读取执行记录并返回基线指标。数据库不存在时返回空基线。"""

    path = Path(database_path).expanduser()
    metrics: dict[str, Any] = {
        "database": str(path),
        "exists": path.is_file(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workflow_count": 0,
        "execution_count": 0,
        "status_counts": {},
        "success_count": 0,
        "failure_count": 0,
        "awaiting_user_count": 0,
        "paused_count": 0,
        "manual_intervention_count": 0,
        "success_rate": 0.0,
        "locator_failure_count": 0,
        "locator_failure_rate": 0.0,
        "failure_category_counts": {},
        "step_count": 0,
        "failed_step_count": 0,
        "screenshot_count": 0,
        "artifact_count": 0,
        "average_duration_seconds": 0.0,
        "p50_duration_seconds": 0.0,
    }
    if not metrics["exists"]:
        return metrics

    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    try:
        if not _table_exists(connection, "execution_log"):
            return metrics

        if _table_exists(connection, "workflow"):
            metrics["workflow_count"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM workflow"
                ).fetchone()[0]
            )

        rows = connection.execute(
            "SELECT * FROM execution_log"
        ).fetchall()
    finally:
        connection.close()

    metrics["execution_count"] = len(rows)
    status_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    durations: list[float] = []
    step_count = 0
    failed_step_count = 0
    screenshot_count = 0
    artifact_count = 0
    manual_intervention = 0

    for row in rows:
        status = str(row["status"] or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

        if status in SUCCESS_STATUSES:
            metrics["success_count"] += 1
        elif status in FAILURE_STATUSES:
            metrics["failure_count"] += 1
            category = _classify_execution(row)
            category_counts[category.value] = (
                category_counts.get(category.value, 0) + 1
            )
            if category in NEEDS_ATTENTION_CATEGORIES:
                manual_intervention += 1

        if status in AWAITING_USER_STATUSES:
            metrics["awaiting_user_count"] += 1
            manual_intervention += 1
        elif status in PAUSED_STATUSES:
            metrics["paused_count"] += 1
            manual_intervention += 1

        started_at = _parse_datetime(row["started_at"])
        finished_at = _parse_datetime(row["finished_at"])
        if started_at is not None and finished_at is not None:
            duration = (finished_at - started_at).total_seconds()
            if duration >= 0:
                durations.append(duration)

        step_logs = _json_loads(row["step_logs"], [])
        if isinstance(step_logs, list):
            step_count += len(step_logs)
            failed_step_count += sum(
                1
                for step_log in step_logs
                if isinstance(step_log, dict)
                and str(step_log.get("status") or "") == "failed"
            )

        screenshots = _json_loads(row["screenshots"], [])
        if isinstance(screenshots, list):
            screenshot_count += len(screenshots)
        artifacts = _json_loads(row["artifacts"], [])
        if isinstance(artifacts, list):
            artifact_count += len(artifacts)

    completed = metrics["success_count"] + metrics["failure_count"]
    metrics["status_counts"] = status_counts
    metrics["manual_intervention_count"] = manual_intervention
    metrics["success_rate"] = _percent(
        int(metrics["success_count"]),
        completed,
    )
    metrics["failure_category_counts"] = category_counts
    locator_failures = sum(
        count
        for category, count in category_counts.items()
        if FailureCategory(category) in LOCATOR_FAILURE_CATEGORIES
    )
    metrics["locator_failure_count"] = locator_failures
    metrics["locator_failure_rate"] = _percent(
        locator_failures,
        int(metrics["failure_count"]),
    )
    metrics["step_count"] = step_count
    metrics["failed_step_count"] = failed_step_count
    metrics["screenshot_count"] = screenshot_count
    metrics["artifact_count"] = artifact_count
    if durations:
        metrics["average_duration_seconds"] = round(
            statistics.fmean(durations),
            2,
        )
        metrics["p50_duration_seconds"] = round(
            statistics.median(durations),
            2,
        )
    return metrics


def _metric_table(metrics: dict[str, Any]) -> list[tuple[str, Any]]:
    return [
        ("流程数", metrics["workflow_count"]),
        ("执行次数", metrics["execution_count"]),
        ("成功次数", metrics["success_count"]),
        ("失败次数", metrics["failure_count"]),
        ("首次成功率", f"{metrics['success_rate']}%"),
        ("人工介入次数", metrics["manual_intervention_count"]),
        ("定位失败次数", metrics["locator_failure_count"]),
        ("定位失败率", f"{metrics['locator_failure_rate']}%"),
        ("步骤总数", metrics["step_count"]),
        ("失败步骤数", metrics["failed_step_count"]),
        ("截图数量", metrics["screenshot_count"]),
        ("产物数量", metrics["artifact_count"]),
        (
            "平均耗时",
            f"{metrics['average_duration_seconds']} 秒",
        ),
        ("耗时中位数", f"{metrics['p50_duration_seconds']} 秒"),
    ]


def render_markdown(metrics: dict[str, Any]) -> str:
    lines = [
        "# NL-RPA 执行基线",
        "",
        f"- 数据库：`{metrics['database']}`",
        f"- 生成时间：{metrics['generated_at']}",
    ]
    if not metrics["exists"]:
        lines.extend(
            [
                "",
                "数据库文件不存在，当前没有可统计的生产执行记录。",
                "后续每次执行都会写入 execution_log，重新运行本脚本即可更新基线。",
            ]
        )
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "",
            "| 指标 | 当前值 |",
            "| --- | --- |",
        ]
    )
    for name, value in _metric_table(metrics):
        lines.append(f"| {name} | {value} |")

    status_counts = metrics["status_counts"]
    if status_counts:
        lines.extend(["", "## 执行状态分布", "", "| 状态 | 次数 |", "| --- | --- |"])
        for status, count in sorted(status_counts.items()):
            lines.append(f"| {status} | {count} |")

    category_counts = metrics["failure_category_counts"]
    if category_counts:
        lines.extend(
            [
                "",
                "## 失败分类分布",
                "",
                "| 分类 | 次数 |",
                "| --- | --- |",
            ]
        )
        for category, count in sorted(category_counts.items()):
            lines.append(f"| {category} | {count} |")

    return "\n".join(lines) + "\n"


def _default_database() -> str:
    return os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./rpa.db")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="采集 NL-RPA 阶段 0 基线指标",
    )
    parser.add_argument(
        "--database",
        default=_default_database(),
        help="SQLite 数据库路径或 DATABASE_URL",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="输出格式",
    )
    parser.add_argument(
        "--output",
        default="",
        help="可选，把报告写入文件",
    )
    args = parser.parse_args(argv)

    database_path = _parse_database_location(args.database)
    metrics = collect_baseline(database_path)
    if args.format == "json":
        report = json.dumps(metrics, ensure_ascii=False, indent=2)
    else:
        report = render_markdown(metrics)

    if args.output:
        output_path = Path(args.output).expanduser()
        if not output_path.is_absolute():
            output_path = PROJECT_ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
