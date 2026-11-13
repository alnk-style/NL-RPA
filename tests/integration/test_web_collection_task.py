from __future__ import annotations

import asyncio
import html
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.agents.desktop_planner import validate_task_graph_plan
from app.engine.task_runtime import StepResult, TaskRuntime
from app.engine.web_executor import WebExecutor
from desktop_agent.actions import create_spreadsheet as spreadsheet_module


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.item_links: list[str] = []
        self.next_link: str | None = None
        self.title = ""
        self.price = ""
        self._capture: str | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = dict(attrs)
        classes = set(str(values.get("class") or "").split())
        if tag == "a" and "item" in classes:
            self.item_links.append(str(values.get("href") or ""))
        if tag == "a" and "next" in classes:
            self.next_link = str(values.get("href") or "")
        if tag == "h2" and "title" in classes:
            self._capture = "title"
        if tag == "span" and "price" in classes:
            self._capture = "price"

    def handle_data(self, data: str) -> None:
        if self._capture == "title":
            self.title += data.strip()
        elif self._capture == "price":
            self.price += data.strip()

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h2", "span"}:
            self._capture = None


def _parse(page: str) -> _PageParser:
    parser = _PageParser()
    parser.feed(page)
    return parser


class _StaticPool:
    def __init__(self) -> None:
        rows = [
            ("商品 1", "10"),
            ("商品 2", "20"),
            ("商品 3", "30"),
            ("商品 4", "40"),
            ("商品 5", "50"),
            ("商品 6", "60"),
            ("商品 7", "70"),
            ("商品 8", "80"),
            ("商品 9", "90"),
            ("商品 10", "100"),
            ("商品 11", "110"),
            ("商品 12", "120"),
        ]
        self.pages: dict[str, str] = {}
        for index, (title, price) in enumerate(rows, start=1):
            self.pages[f"/detail/{index}.html"] = (
                '<div class="item"><h2 class="title">'
                f"{html.escape(title)}</h2><span class=\"price\">"
                f"{price}</span></div>"
            )
        groups = [rows[:5], rows[5:9], rows[9:]]
        duplicate = rows[0]
        for page_index, group in enumerate(groups, start=1):
            links = [
                f'<a class="item" href="/detail/{rows.index(row) + 1}.html">'
                f"{html.escape(row[0])}</a>"
                for row in group
            ]
            if page_index == 2:
                links.insert(
                    0,
                    '<a class="item" href="/detail/1.html">'
                    f"{html.escape(duplicate[0])}</a>",
                )
            next_link = (
                f'<a class="next" href="/page{page_index + 1}.html">next</a>'
                if page_index < len(groups)
                else ""
            )
            self.pages[f"/page{page_index}.html"] = (
                '<html><body><div class="list">'
                + "".join(links)
                + next_link
                + "</div></body></html>"
            )


class _ContentLocator:
    def __init__(self, text: str) -> None:
        self.text = text

    async def inner_text(self, **_kwargs: Any) -> str:
        return self.text


class _RowLocator:
    def __init__(self, parsed: _PageParser) -> None:
        self.parsed = parsed

    def locator(self, selector: str) -> _ContentLocator:
        if selector == ".title":
            return _ContentLocator(self.parsed.title)
        if selector == ".price":
            return _ContentLocator(self.parsed.price)
        return _ContentLocator("")


class _RowsLocator:
    def __init__(self, parsed: _PageParser) -> None:
        self.parsed = parsed

    async def count(self) -> int:
        return 1

    def nth(self, _index: int) -> _RowLocator:
        return _RowLocator(self.parsed)


class _LinkLocator:
    def __init__(self, href: str) -> None:
        self.href = href

    async def get_attribute(self, name: str) -> str | None:
        return self.href if name == "href" else None

    async def inner_text(self, **_kwargs: Any) -> str:
        return self.href


class _LinksLocator:
    def __init__(self, links: list[str]) -> None:
        self.links = links

    async def count(self) -> int:
        return len(self.links)

    def nth(self, index: int) -> _LinkLocator:
        return _LinkLocator(self.links[index])


class _NextLocator:
    def __init__(self, page: _StaticHtmlPage, href: str | None) -> None:
        self.page = page
        self.href = href

    async def count(self) -> int:
        return int(bool(self.href))

    def nth(self, _index: int) -> _NextLocator:
        return self

    async def is_enabled(self) -> bool:
        return bool(self.href)

    async def click(self, **_kwargs: Any) -> None:
        if self.href:
            await self.page.goto(self.href)


class _StaticHtmlPage:
    def __init__(self, pool: _StaticPool) -> None:
        self.pool = pool
        self.url = "https://example.com/page1.html"
        self.requests: list[str] = []
        self.parsed = _parse(pool.pages["/page1.html"])

    async def goto(self, url: str, **_kwargs: Any) -> None:
        path = urlparse(url).path
        self.requests.append(path)
        self.url = f"https://example.com{path}"
        self.parsed = _parse(self.pool.pages[path])

    def locator(self, selector: str) -> Any:
        if selector == "a.item":
            return _LinksLocator(self.parsed.item_links)
        if selector == "a.next":
            return _NextLocator(self, self.parsed.next_link)
        if selector == ".item":
            return _RowsLocator(self.parsed)
        return _LinksLocator([])

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


def _minimal_xlsx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            'content-types"><Default Extension="xml" ContentType="application/'
            'xml"/></Types>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"><sheetData/></worksheet>',
        )


def test_web_collection_exports_deduplicated_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pool = _StaticPool()
    page = _StaticHtmlPage(pool)
    executor = WebExecutor(
        type(
            "Config",
            (),
            {
                "EXECUTION_TIMEOUT": 10,
                "BROWSER_MODE": "isolated",
                "BROWSER_HEADLESS": True,
                "SCREENSHOT_ENABLED": False,
                "SCREENSHOT_TIMEOUT_MS": 1000,
                "SCREENSHOT_FULL_PAGE": False,
            },
        )()
    )
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        spreadsheet_module,
        "_create_spreadsheet",
        lambda path, sheet, columns, widths, overwrite: (
            captured.update(
                {
                    "sheet": sheet,
                    "columns": columns,
                    "widths": widths,
                    "overwrite": overwrite,
                }
            )
            or _minimal_xlsx(Path(path))
        ),
    )
    target = tmp_path / "商品数据.xlsx"
    plan = {
        "executor_type": "hybrid",
        "variables": [
            {"name": "links", "default": []},
            {"name": "rows", "default": []},
        ],
        "crawl_policy": {
            "allowed_domains": ["example.com"],
            "requests_per_second": 100,
            "max_pages": 3,
            "max_items": 20,
        },
        "steps": [
            {
                "step_id": 1,
                "executor": "web",
                "action": "paginate",
                "result_var": "links",
                "params": {
                    "item_selector": "a.item",
                    "next_selector": "a.next",
                    "max_pages": 3,
                    "max_items": 20,
                    "variable": "links",
                    "allowed_domains": ["example.com"],
                },
            },
            {
                "step_id": 2,
                "executor": "web",
                "action": "foreach",
                "output_var": "rows",
                "params": {
                    "items": "${links}",
                    "item_var": "link",
                    "max_iterations": 20,
                    "steps": [
                        {
                            "executor": "web",
                            "action": "goto",
                            "params": {"url": "${link.url}"},
                        },
                        {
                            "executor": "web",
                            "action": "extract_fields",
                            "result_var": "row",
                            "params": {
                                "selector": ".item",
                                "fields": {
                                    "title": {"selector": ".title"},
                                    "price": {"selector": ".price"},
                                },
                                "variable": "row",
                            },
                        },
                    ],
                },
            },
            {
                "step_id": 3,
                "executor": "files",
                "action": "create_spreadsheet",
                "params": {
                    "path": str(target),
                    "sheet_name": "商品",
                    "columns": [
                        {"header": "商品数据", "values": "${rows}"}
                    ],
                },
                "postconditions": [
                    {"type": "file_non_empty", "path": str(target)}
                ],
            },
        ],
    }
    validated = validate_task_graph_plan(
        plan,
        allowed_doc_dirs=(str(tmp_path),),
    )

    async def execute_step(
        step: dict[str, Any],
        state: dict[str, Any],
    ) -> StepResult:
        action = str(step.get("action") or "")
        params = step.get("params") or {}
        context = {"crawl_policy": validated["crawl_policy"]}
        if action == "goto":
            await executor._before_navigation(params["url"], context)
            await page.goto(params["url"])
            return StepResult(success=True, value=page.url)
        if action in {"paginate", "extract_fields"}:
            handler = getattr(executor, f"_action_{action}")
            extracted: dict[str, Any] = {}
            value = await handler(
                page,
                params,
                extracted=extracted,
                context=context,
            )
            variable = str(params.get("variable") or "")
            result_value = extracted.get(variable, value.get("items"))
            return StepResult(
                success=True,
                value=result_value,
                items=(
                    result_value
                    if isinstance(result_value, list)
                    else None
                ),
                data={"variables": extracted},
            )
        if action == "create_spreadsheet":
            result = await spreadsheet_module.handle_create_spreadsheet(
                params,
                {},
            )
            return StepResult(
                success=result.success,
                message=result.message,
                data=dict(result.data),
            )
        raise RuntimeError(f"Unsupported fixture action: {action}")

    runtime = TaskRuntime(step_executor=execute_step)
    result = asyncio.run(runtime.execute(validated, {"variables": {}}))

    assert result.success is True, result.error
    assert len(result.variables["links"]) == 12
    assert len(result.variables["rows"]) == 12
    assert result.variables["rows"][0] == {
        "title": "商品 1",
        "price": "10",
    }
    list_requests = 1 + len(
        re.findall(r"/page\d+\.html", " ".join(page.requests))
    )
    assert list_requests == 3
    assert target.is_file()
    assert captured["sheet"] == "商品"
    assert len(captured["columns"][0]["values"]) == 12
