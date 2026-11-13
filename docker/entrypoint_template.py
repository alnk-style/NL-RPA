def generate_entrypoint_script() -> str:
    return r'''import asyncio
import json
import os
import re
import textwrap
import time
from urllib.parse import urlparse

from playwright.async_api import async_playwright


WORKSPACE = "/workspace"
CONTEXT_PATH = "/workspace/context.json"
MAIN_PATH = "/workspace/main.py"
RESULT_PATH = "/workspace/output/result.json"
SCREENSHOT_DIR = "/workspace/output/screenshots"
ARTIFACT_DIR = "/workspace/output/artifacts"

ALLOWED_DOMAINS = json.loads(os.environ.get("ALLOWED_DOMAINS", "[]"))
if not isinstance(ALLOWED_DOMAINS, list):
    ALLOWED_DOMAINS = []
ALLOWED_DOMAINS = [
    str(domain).strip().lower().rstrip(".")
    for domain in ALLOWED_DOMAINS
    if str(domain).strip()
]

_CREDENTIALS = json.loads(
    os.environ.pop("NL_RPA_CREDENTIALS", "{}") or "{}"
)
if not isinstance(_CREDENTIALS, dict):
    _CREDENTIALS = {}


class _CompletedAwaitable:
    def __await__(self):
        return iter(())


def _normalize_domain(domain):
    normalized = str(domain).strip().lower().rstrip(".")
    if normalized.startswith("*."):
        return "*." + normalized[2:].lstrip(".")
    return normalized


def is_url_allowed(url, allowed_domains=None):
    if not isinstance(url, str):
        return False

    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not hostname:
        return False

    domains = ALLOWED_DOMAINS if allowed_domains is None else allowed_domains
    for domain in domains:
        normalized = _normalize_domain(domain)
        if not normalized:
            continue
        if normalized.startswith("*."):
            if hostname.endswith(normalized[1:]):
                return True
        elif hostname == normalized:
            return True

    return False


class NetworkRestrictedPage:
    def __init__(self, page, allowed_domains):
        self._page = page
        self._allowed_domains = list(allowed_domains)

    def __getattr__(self, name):
        return getattr(self._page, name)

    async def goto(self, url, *args, **kwargs):
        if not is_url_allowed(url, self._allowed_domains):
            raise PermissionError(
                f"Domain is not allowed by sandbox policy: {url}"
            )
        return await self._page.goto(url, *args, **kwargs)


async def main():
    started_at = time.perf_counter()
    output = []
    screenshots = []
    artifacts = []
    step_logs = []
    error = None
    success = False
    browser = None

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    os.makedirs(ARTIFACT_DIR, exist_ok=True)

    try:
        with open(CONTEXT_PATH, "r", encoding="utf-8") as context_file:
            context = json.load(context_file)
        if not isinstance(context, dict):
            context = {}
        if _CREDENTIALS:
            context["credentials"] = _CREDENTIALS

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            browser_context = await browser.new_context(
                viewport={"width": 1920, "height": 1080}
            )
            page = NetworkRestrictedPage(
                await browser_context.new_page(),
                ALLOWED_DOMAINS,
            )

            async def screenshot(step_name):
                step_index = len(step_logs)
                step_started_at = time.perf_counter()
                safe_name = re.sub(
                    r"[^A-Za-z0-9_.-]+",
                    "_",
                    step_name,
                ).strip("._") or "screenshot"
                relative_path = f"output/screenshots/{safe_name}.png"
                absolute_path = f"{WORKSPACE}/{relative_path}"

                try:
                    await page.screenshot(
                        path=absolute_path,
                        full_page=True,
                    )
                    screenshots.append(relative_path)
                    step_logs.append(
                        {
                            "step_index": step_index,
                            "action": "screenshot",
                            "target": step_name,
                            "selector": None,
                            "status": "success",
                            "screenshot_path": relative_path,
                            "error": None,
                            "started_at": step_started_at,
                            "finished_at": time.perf_counter(),
                        }
                    )
                    return relative_path
                except Exception as exc:
                    step_logs.append(
                        {
                            "step_index": step_index,
                            "action": "screenshot",
                            "target": step_name,
                            "selector": None,
                            "status": "failed",
                            "screenshot_path": None,
                            "error": str(exc) or exc.__class__.__name__,
                            "started_at": step_started_at,
                            "finished_at": time.perf_counter(),
                        }
                    )
                    raise

            def log(message):
                output.append(str(message))
                return _CompletedAwaitable()

            async def save_file(path, content, *, encoding="utf-8"):
                requested = str(path or "").strip()
                safe_name = requested.replace("\\", "/").rsplit("/", 1)[-1]
                for char in '<>:"/\\|?*':
                    safe_name = safe_name.replace(char, "_")
                safe_name = safe_name.strip(" .") or "artifact"

                os.makedirs(ARTIFACT_DIR, exist_ok=True)
                absolute_path = os.path.join(ARTIFACT_DIR, safe_name)
                if isinstance(content, bytes):
                    with open(absolute_path, "wb") as artifact_file:
                        artifact_file.write(content)
                else:
                    with open(
                        absolute_path,
                        "w",
                        encoding=encoding,
                    ) as artifact_file:
                        artifact_file.write(str(content))

                is_absolute = bool(
                    re.match(r"^[A-Za-z]:[\\/]", requested)
                    or requested.startswith("\\\\")
                    or requested.startswith("/")
                )
                record = {
                    "name": safe_name,
                    "path": "output/artifacts/" + safe_name,
                    "requested_path": requested if is_absolute else None,
                    "size": os.path.getsize(absolute_path),
                    "delivered": False,
                    "delivered_path": None,
                    "error": None,
                }
                artifacts.append(record)
                output.append("artifact saved: " + record["path"])
                return record["path"]

            async def extract_table(selector):
                return await page.locator(selector).evaluate(
                    """
                    (table) => {
                        const rows = Array.from(table.querySelectorAll("tr"));
                        if (rows.length === 0) {
                            return [];
                        }
                        const readCells = (row) => Array.from(
                            row.querySelectorAll("th, td")
                        ).map((cell) => (cell.textContent || "").trim());
                        const headers = readCells(rows[0]);
                        return rows.slice(1).map((row) => {
                            const values = readCells(row);
                            return headers.reduce((record, header, index) => {
                                const key = header || `column_${index + 1}`;
                                record[key] = values[index] || "";
                                return record;
                            }, {});
                        });
                    }
                    """
                )

            async def wait_for(selector_or_milliseconds, timeout=None):
                if (
                    isinstance(selector_or_milliseconds, (int, float))
                    and not isinstance(selector_or_milliseconds, bool)
                ):
                    if timeout is not None:
                        raise TypeError(
                            "wait_for(milliseconds) does not accept timeout"
                        )
                    await page.wait_for_timeout(selector_or_milliseconds)
                    return None

                selector = str(selector_or_milliseconds).strip()
                if not selector:
                    raise ValueError("wait_for selector must not be empty")
                wait_timeout = 30000 if timeout is None else int(timeout)
                return await page.wait_for_selector(
                    selector,
                    timeout=wait_timeout,
                )

            namespace = dict(context)
            namespace.update(
                {
                    "page": page,
                    "context": context,
                    "asyncio": asyncio,
                    "screenshot": screenshot,
                    "log": log,
                    "save_file": save_file,
                    "extract_table": extract_table,
                    "wait_for": wait_for,
                }
            )

            with open(MAIN_PATH, "r", encoding="utf-8") as main_file:
                code = main_file.read()

            source = code if code.strip() else "pass"
            wrapped_code = (
                "async def __rpa_main__():\n"
                + textwrap.indent(source, "    ")
            )
            exec(
                compile(
                    wrapped_code,
                    "<rpa_generated_code>",
                    "exec",
                ),
                namespace,
            )
            await namespace["__rpa_main__"]()
            success = True
    except Exception as exc:
        error = str(exc) or exc.__class__.__name__
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

        result = {
            "success": success,
            "output": "\n".join(output),
            "error": error,
            "screenshots": screenshots,
            "artifacts": artifacts,
            "step_logs": step_logs,
            "duration_seconds": time.perf_counter() - started_at,
        }
        os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
        with open(RESULT_PATH, "w", encoding="utf-8") as result_file:
            json.dump(result, result_file, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
'''
