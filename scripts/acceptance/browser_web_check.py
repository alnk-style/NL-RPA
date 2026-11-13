from __future__ import annotations

import asyncio
import functools
import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from threading import Thread
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright

from app.engine.web_executor import WebExecutor
from app.engine.visual_runtime import VisualRuntimeService
from app.visual.provider import (
    VisualCandidate,
    VisualLocateRequest,
    VisualLocateResponse,
)
from app.visual.settings import VisualSettings

FIXTURES = ROOT / "tests" / "fixtures" / "sites"
OUTPUT = ROOT / ".tmp" / "stage-g" / "browser"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
        return


class FakeVisualProvider:
    def __init__(
        self,
        *,
        candidates: list[VisualCandidate],
    ) -> None:
        self.candidates = list(candidates)
        self.calls = 0

    async def locate(
        self,
        request: VisualLocateRequest,
    ) -> VisualLocateResponse:
        del request
        self.calls += 1
        return VisualLocateResponse(
            provider="local_ocr",
            model="fake-stage-g",
            candidates=list(self.candidates),
        )


def _config() -> Any:
    return SimpleNamespace(
        EXECUTION_TIMEOUT=30,
        SCREENSHOT_ENABLED=True,
        SCREENSHOT_TIMEOUT_MS=15000,
        SCREENSHOT_FULL_PAGE=False,
        BROWSER_MODE="isolated",
        BROWSER_HEADLESS=True,
        STEP_LEVEL_REPAIR=False,
        WEB_REPAIR_ATTEMPTS=0,
    )


def _settings(
    *,
    enabled: bool = True,
    threshold: float = 0.8,
) -> VisualSettings:
    return VisualSettings(
        enabled=enabled,
        provider="local_ocr" if enabled else "disabled",
        confidence_threshold=threshold,
        max_candidates=3,
        max_attempts=2,
        screenshot_scope="viewport",
    )


def _runtime(
    provider: FakeVisualProvider,
    *,
    threshold: float = 0.8,
) -> VisualRuntimeService:
    async def settings_loader(_user_id: int) -> VisualSettings:
        return _settings(threshold=threshold)

    return VisualRuntimeService(
        settings_loader=settings_loader,
        provider_factory=lambda _settings: provider,
    )


async def _extract_three_sites(
    base_url: str,
    browser: Any,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    executor = WebExecutor(_config())
    for site in ("site_a", "site_b", "site_c"):
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            device_scale_factor=1.0,
        )
        page = await context.new_page()
        result = await executor.execute_steps(
            [
                {
                    "step_id": 1,
                    "executor": "web",
                    "action": "goto",
                    "params": {"url": f"{base_url}/{site}/index.html"},
                },
                {
                    "step_id": 2,
                    "executor": "web",
                    "action": "extract_fields",
                    "params": {
                        "selector": ".record",
                        "fields": {
                            "name": {"selector": ".name"},
                            "amount": {"selector": ".amount"},
                        },
                        "variable": "rows",
                    },
                },
                {
                    "step_id": 3,
                    "executor": "web",
                    "action": "screenshot",
                    "params": {
                        "name": f"{site}-viewport",
                        "full_page": False,
                    },
                },
            ],
            page=page,
            context={"user_id": 1},
            execution_id=f"stage-g-{site}",
        )
        rows = (
            (result.details or {}).get("extracted", {}).get("rows", [])
            if isinstance(result.details, dict)
            else []
        )
        results[site] = {
            "success": result.success,
            "row_count": len(rows),
            "fields": sorted(rows[0]) if rows else [],
            "screenshot_count": len(result.screenshots),
            "error": result.error,
        }
        await context.close()
    return results


async def _login_checks(
    base_url: str,
    browser: Any,
) -> dict[str, Any]:
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        device_scale_factor=1.0,
    )
    page = await context.new_page()
    executor = WebExecutor(_config())
    first = await executor.execute_steps(
        [
            {
                "step_id": 1,
                "executor": "web",
                "action": "goto",
                "params": {
                    "url": f"{base_url}/site_a/login.html"
                },
            },
            {
                "step_id": 2,
                "executor": "web",
                "action": "assert_logged_in",
                "params": {
                    "success_selector": "#authenticated",
                    "login_paths": ["/login.html"],
                },
            },
        ],
        page=page,
        context={"user_id": 1},
        execution_id="stage-g-login-before",
    )
    before = {
        "success": first.success,
        "awaiting_user": bool(
            (first.details or {}).get("awaiting_user")
        ),
        "failure_category": (
            (first.details or {}).get("failed_step", {}).get(
                "failure_category"
            )
        ),
    }
    await page.locator("#username").fill("acceptance-user")
    await page.locator("#password").fill("local-test-only")
    await page.locator("button[type='submit']").click()
    second = await executor.execute_steps(
        [
            {
                "step_id": 1,
                "executor": "web",
                "action": "assert_logged_in",
                "params": {
                    "success_selector": "#authenticated",
                    "login_paths": ["/login.html"],
                },
            }
        ],
        page=page,
        context={"user_id": 1},
        execution_id="stage-g-login-after",
    )
    after = {
        "success": second.success,
        "error": second.error,
    }
    await context.close()
    return {"before_login": before, "after_login": after}


async def _visual_check_for_scale(
    base_url: str,
    browser: Any,
    scale: float,
) -> dict[str, Any]:
    context = await browser.new_context(
        viewport={"width": 1024, "height": 768},
        device_scale_factor=scale,
    )
    page = await context.new_page()
    await page.goto(f"{base_url}/site_a/visual.html")
    box = await page.locator("#target").bounding_box()
    if not isinstance(box, dict):
        raise RuntimeError("visual target has no bounding box")
    bbox = (
        round(float(box["x"]) * scale),
        round(float(box["y"]) * scale),
        round((float(box["x"]) + float(box["width"])) * scale),
        round((float(box["y"]) + float(box["height"])) * scale),
    )
    provider = FakeVisualProvider(
        candidates=[
            VisualCandidate(
                target="Continue",
                bbox=bbox,
                confidence=0.96,
                evidence="known fixture button",
                expected_after="Visual action completed",
            )
        ]
    )
    executor = WebExecutor(
        _config(),
        visual_runtime=_runtime(provider),
    )
    result = await executor.execute_steps(
        [
            {
                "step_id": 1,
                "executor": "web",
                "action": "click",
                "target": "Continue",
                "params": {
                    "visual_fallback": True,
                    "target_description": "Continue",
                },
            }
        ],
        page=page,
        context={"user_id": 1},
        execution_id=f"stage-g-visual-{scale}",
    )
    status_text = await page.locator("#status").inner_text()
    audit = (
        result.step_logs[0].get("visual_audit", {})
        if result.step_logs
        else {}
    )
    output = {
        "scale": scale,
        "provider_calls": provider.calls,
        "success": result.success,
        "status_text": status_text,
        "selection": audit.get("selection"),
        "candidate_id": audit.get("candidate_id"),
        "bbox": audit.get("bbox"),
        "verification": audit.get("verification"),
        "screenshot_path": audit.get("screenshot_path"),
    }
    await context.close()
    return output


async def _ambiguity_check(
    base_url: str,
    browser: Any,
    *,
    candidates: list[VisualCandidate],
    threshold: float = 0.8,
) -> dict[str, Any]:
    context = await browser.new_context(
        viewport={"width": 1024, "height": 768},
        device_scale_factor=1.0,
    )
    page = await context.new_page()
    await page.goto(f"{base_url}/site_a/visual.html")
    provider = FakeVisualProvider(candidates=candidates)
    executor = WebExecutor(
        _config(),
        visual_runtime=_runtime(provider, threshold=threshold),
    )
    result = await executor.execute_steps(
        [
            {
                "step_id": 1,
                "executor": "web",
                "action": "click",
                "target": "Continue",
                "params": {
                    "visual_fallback": True,
                    "target_description": "Continue",
                },
            }
        ],
        page=page,
        context={"user_id": 1},
        execution_id="stage-g-visual-ambiguous",
    )
    details = result.details if isinstance(result.details, dict) else {}
    output = {
        "success": result.success,
        "awaiting_user": bool(details.get("awaiting_user")),
        "failure_category": details.get("failed_step", {}).get(
            "failure_category"
        ),
        "human_action_kind": details.get("failed_step", {})
        .get("human_action", {})
        .get("kind"),
        "provider_calls": provider.calls,
    }
    await context.close()
    return output


async def _disabled_check(
    base_url: str,
    browser: Any,
) -> dict[str, Any]:
    context = await browser.new_context(
        viewport={"width": 1024, "height": 768},
        device_scale_factor=1.0,
    )
    page = await context.new_page()
    await page.goto(f"{base_url}/site_a/visual.html")
    provider = FakeVisualProvider(candidates=[])

    async def settings_loader(_user_id: int) -> VisualSettings:
        return _settings(enabled=False)

    runtime = VisualRuntimeService(
        settings_loader=settings_loader,
        provider_factory=lambda _settings: provider,
    )
    executor = WebExecutor(_config(), visual_runtime=runtime)
    result = await executor.execute_steps(
        [
            {
                "step_id": 1,
                "executor": "web",
                "action": "click",
                "target": "Missing",
                "params": {
                    "visual_fallback": True,
                    "target_description": "Missing",
                },
            }
        ],
        page=page,
        context={"user_id": 1},
        execution_id="stage-g-visual-disabled",
    )
    output = {
        "success": result.success,
        "provider_calls": provider.calls,
        "failure_category": (
            (result.details or {}).get("failed_step", {}).get(
                "failure_category"
            )
        ),
    }
    await context.close()
    return output


async def run() -> dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(QuietHandler, directory=str(FIXTURES))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                results = {
                    "engine": "playwright-chromium",
                    "edge_installed": False,
                    "three_sites": await _extract_three_sites(
                        base_url,
                        browser,
                    ),
                    "login": await _login_checks(base_url, browser),
                    "visual_dpr": [
                        await _visual_check_for_scale(
                            base_url,
                            browser,
                            scale,
                        )
                        for scale in (1.0, 1.25, 1.5)
                    ],
                    "visual_multi_candidate": await _ambiguity_check(
                        base_url,
                        browser,
                        candidates=[
                            VisualCandidate(
                                target="Continue A",
                                bbox=(420, 220, 600, 276),
                                confidence=0.94,
                            ),
                            VisualCandidate(
                                target="Continue B",
                                bbox=(430, 222, 598, 278),
                                confidence=0.91,
                            ),
                        ],
                    ),
                    "visual_low_confidence": await _ambiguity_check(
                        base_url,
                        browser,
                        candidates=[
                            VisualCandidate(
                                target="Continue",
                                bbox=(420, 220, 600, 276),
                                confidence=0.55,
                            )
                        ],
                        threshold=0.8,
                    ),
                    "visual_disabled": await _disabled_check(
                        base_url,
                        browser,
                    ),
                }
            finally:
                await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    report_path = OUTPUT / "browser-web-acceptance.json"
    report_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    results["report_path"] = str(report_path)
    return results


def main() -> int:
    results = asyncio.run(run())
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
