"""一键回放录制/观察/检查点回归夹具。

用法：

    .\\.venv\\Scripts\\python.exe scripts/run_regression.py
    .\\.venv\\Scripts\\python.exe scripts/run_regression.py \
        --fixtures tests/regression/fixtures --output .tmp/regression
    .\\.venv\\Scripts\\python.exe scripts/run_regression.py --json report.json

输出通过率、失败分类与快照路径；退出码非 0 表示存在回放失败，
但普通 pytest 用例不依赖本脚本。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.regression.replay import replay_all

DEFAULT_FIXTURES = PROJECT_ROOT / "tests" / "regression" / "fixtures"
DEFAULT_OUTPUT = PROJECT_ROOT / ".tmp" / "regression"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="回放 NL-RPA 录制/观察/step_logs 回归夹具",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=DEFAULT_FIXTURES,
        help="夹具根目录（每个子目录包含 fixture.json）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="输出目录（结果、快照副本与 summary.json）",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        type=Path,
        default=None,
        help="额外把 summary 写到指定 JSON 文件",
    )
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="存在失败时仍以退出码 0 结束",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = replay_all(args.fixtures, args.output)
    if args.json_path is not None:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(
        f"[regression] fixtures={summary['total']} "
        f"passed={summary['passed']} failed={summary['failed']} "
        f"pass_rate={summary['pass_rate']:.2%}"
    )
    for result in summary["results"]:
        marker = "PASS" if result["passed"] else "FAIL"
        detail = result.get("error") or ""
        print(f"  [{marker}] {result['name']} {detail}".rstrip())
        if not result["passed"]:
            for check in result["checks"]:
                if check["passed"]:
                    continue
                print(
                    f"      - {check['name']}: {check['detail']}"
                )
    if summary["failure_categories"]:
        print("[regression] failure_categories:")
        for name, count in sorted(summary["failure_categories"].items()):
            print(f"  - {name}: {count}")
    if summary["snapshots"]:
        print(f"[regression] snapshots ({len(summary['snapshots'])}):")
        for snapshot in summary["snapshots"]:
            print(f"  - {snapshot}")
    print(f"[regression] summary: {args.output / 'summary.json'}")

    if summary["failed"] and not args.allow_failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
