"""浏览器录制脚本：录一次操作，生成结构化步骤草稿。

本地生成草稿（不需要后端）：

    python scripts/record_browser.py --url https://example.com \
        --out logs/recordings/demo.json

直接写入后端录制会话并落库为草稿流程：

    python scripts/record_browser.py --url https://example.com \
        --api http://127.0.0.1:8000 --token <JWT>

录制时在页面里正常操作，按 Ctrl+Enter 结束录制，或直接关闭页面。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.engine.recorder import (  # noqa: E402
    BROWSER_RECORDER_SCRIPT,
    RecordingDraft,
    RecordingError,
    compile_recording,
)

DEFAULT_OUTPUT_DIR = Path("logs") / "recordings"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record browser actions into structured step drafts."
    )
    parser.add_argument("--url", required=True, help="要录制操作的网址")
    parser.add_argument(
        "--out",
        default=None,
        help="本地草稿输出路径，默认 logs/recordings/recording_<时间>.json",
    )
    parser.add_argument(
        "--api",
        default=None,
        help="后端地址；提供后把事件写入 /api/recordings 并落库为草稿",
    )
    parser.add_argument("--token", default=None, help="后端 JWT")
    parser.add_argument(
        "--recording-id",
        default=None,
        help="写入已有的录制会话（配合 --api/--token，用于编辑器发起录制）",
    )
    parser.add_argument(
        "--no-finish",
        action="store_true",
        help="追加事件后保留录制会话，等待前端或人工结束",
    )
    parser.add_argument(
        "--create-workflow",
        action="store_true",
        help="结束时把草稿落库为流程；复用已有会话时默认只生成草稿",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无头运行（无头模式下无法人工操作，仅用于冒烟）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="最长录制秒数，默认 600",
    )
    return parser.parse_args(argv)


def _summary_lines(draft: RecordingDraft) -> list[str]:
    lines: list[str] = []
    for step in draft.steps:
        params = step.get("params") or {}
        locator = step.get("locator")
        detail = json.dumps(params, ensure_ascii=False)
        if isinstance(locator, dict):
            detail = (
                f"{detail} locator={locator.get('strategy')}="
                f"{locator.get('value')}"
            )
        lines.append(
            f"{step.get('step_id')}. {step.get('action')} {detail}"
        )
    for warning in draft.warnings:
        lines.append(f"! {warning}")
    return lines


def _payload(
    events: list[dict[str, Any]],
    draft: RecordingDraft,
) -> dict[str, Any]:
    return {
        "events": events,
        "draft": draft.plan,
        "warnings": draft.warnings,
    }


def _write_payload(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


async def _collect_events(
    page: Any,
    downloads: list[Any],
    timeout: float,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + max(float(timeout), 1.0)
    state: dict[str, Any] | None = None
    while True:
        if page.is_closed():
            break
        try:
            state = await page.evaluate(
                "() => window.__nlrpaRecorder "
                "? window.__nlrpaRecorder.snapshot() : null"
            )
        except Exception:
            # 页面切换时上下文短暂失效，继续轮询。
            state = None
        if isinstance(state, dict) and state.get("stopped"):
            break
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.5)

    events: list[dict[str, Any]] = []
    if isinstance(state, dict) and isinstance(state.get("events"), list):
        events.extend(
            event for event in state["events"] if isinstance(event, dict)
        )
    for download in downloads:
        name = getattr(download, "suggested_filename", None)
        if name:
            events.append(
                {
                    "kind": "download",
                    "params": {"save_as": str(name)},
                }
            )
    return events


async def _send_to_api(
    api: str,
    token: str,
    url: str,
    events: list[dict[str, Any]],
    *,
    recording_id: str | None = None,
    finish: bool = True,
    create_workflow: bool = True,
) -> dict[str, Any]:
    import httpx

    base = api.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(
        base_url=base,
        headers=headers,
        timeout=30.0,
    ) as client:
        if recording_id:
            target_id = recording_id
        else:
            started = await client.post(
                "/api/recordings",
                json={
                    "mode": "web",
                    "name": f"浏览器录制 {url}",
                    "start_url": url,
                },
            )
            started.raise_for_status()
            target_id = started.json()["id"]
        if events:
            appended = await client.post(
                f"/api/recordings/{target_id}/events",
                json={"events": events},
            )
            appended.raise_for_status()
        if finish:
            finished = await client.post(
                f"/api/recordings/{target_id}/finish",
                json={"create_workflow": create_workflow},
            )
            finished.raise_for_status()
            payload = finished.json()
            payload.setdefault("id", target_id)
            return payload

        current = await client.get(f"/api/recordings/{target_id}")
        current.raise_for_status()
        payload = current.json()
        payload.setdefault("id", target_id)
        return payload


async def _run(args: argparse.Namespace) -> int:
    from playwright.async_api import async_playwright

    downloads: list[Any] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=bool(args.headless)
        )
        context = await browser.new_context()
        await context.add_init_script(BROWSER_RECORDER_SCRIPT)
        page = await context.new_page()
        page.on("download", downloads.append)

        print(f"打开 {args.url}，按 Ctrl+Enter 结束录制。")
        await page.goto(args.url, wait_until="domcontentloaded")
        events = await _collect_events(page, downloads, args.timeout)
        await context.close()
        await browser.close()

    try:
        draft = compile_recording(events, mode="web")
    except RecordingError as exc:
        print(f"录制未生成草稿：{exc}")
        return 1

    if args.api:
        if not args.token:
            print("使用 --api 时必须同时提供 --token。")
            return 2
        payload = await _send_to_api(
            args.api,
            args.token,
            args.url,
            events,
            recording_id=args.recording_id,
            finish=not args.no_finish,
            create_workflow=(
                args.create_workflow or args.recording_id is None
            ),
        )
        print(
            "已写入录制会话 "
            f"{payload.get('id')}，流程 ID：{payload.get('workflow_id')}"
        )

    if args.out:
        out_path = Path(args.out)
    else:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_path = DEFAULT_OUTPUT_DIR / f"recording_{stamp}.json"
    _write_payload(out_path, _payload(events, draft))

    print(f"共 {len(events)} 条事件，生成 {len(draft.steps)} 个步骤：")
    for line in _summary_lines(draft):
        print(f"  {line}")
    print(f"草稿已写入 {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
