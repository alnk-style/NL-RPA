from __future__ import annotations

import argparse
import asyncio
import functools
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "sites"
OUTPUT = ROOT / ".tmp" / "stage-g" / "edge"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate real Microsoft Edge login detection."
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Keep the visible login window open until Enter is pressed.",
    )
    return parser.parse_args()


async def run(manual: bool) -> dict[str, Any]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(
        QuietHandler,
        directory=str(FIXTURES),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    result: dict[str, Any] = {
        "engine": "microsoft-edge",
        "manual": manual,
    }
    try:
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(
                    channel="msedge",
                    headless=False,
                )
            except Exception as exc:
                result.update(
                    {
                        "status": "skipped",
                        "reason": "Microsoft Edge is not available",
                        "error_type": type(exc).__name__,
                    }
                )
                return result
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()
            await page.goto(f"{base_url}/site_a/login.html")
            result["login_form_visible"] = (
                await page.locator(
                    "input[type='password']:visible"
                ).count()
                > 0
            )
            if manual:
                await asyncio.to_thread(
                    input,
                    "Complete login in Edge, then press Enter: ",
                )
            else:
                await page.locator("#username").fill(
                    "acceptance-user"
                )
                await page.locator("#password").fill(
                    "local-test-only"
                )
                await page.locator(
                    "button[type='submit']"
                ).click()
            try:
                await page.locator("#authenticated").wait_for(
                    state="visible",
                    timeout=10000,
                )
                result["authenticated"] = True
            except Exception:
                result["authenticated"] = False
            screenshot = OUTPUT / "edge-login-result.png"
            await page.screenshot(
                path=str(screenshot),
                full_page=False,
            )
            result["screenshot_path"] = str(screenshot)
            result["status"] = (
                "passed"
                if result["login_form_visible"]
                and result["authenticated"]
                else "failed"
            )
            await context.close()
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return result


def main() -> int:
    args = parse_args()
    try:
        result = asyncio.run(run(args.manual))
    except Exception as exc:
        result = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = OUTPUT / "edge-login-acceptance.json"
    report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"REPORT={report}")
    if result.get("status") == "passed":
        return 0
    if result.get("status") == "skipped":
        return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
