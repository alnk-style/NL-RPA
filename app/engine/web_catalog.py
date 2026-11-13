"""规划器可见的网页动作目录。

目录与结构化执行器共用同一份定义：规划器只能输出目录内的动作和
参数，执行前再用 :func:`validate_web_plan` 做一次确定性校验，避免
出现“规划通过但执行时报未知动作”的漂移。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.protocols import parse_structured_steps


class WebPlanError(ValueError):
    """网页结构化计划不满足动作目录或参数约束。"""


WEB_ACTIONS: dict[str, dict[str, Any]] = {
    "goto": {
        "summary": "打开页面并等待加载状态",
        "params": {
            "url": "要打开的 http/https 地址",
            "wait_until": "可选，commit、domcontentloaded、load 或 networkidle",
            "timeout_ms": "可选，页面加载超时毫秒数",
        },
        "required": ("url",),
        "requires_locator": False,
    },
    "click": {
        "summary": "点击元素",
        "params": {
            "button": "可选，left、right 或 middle",
            "click_count": "可选，点击次数，默认 1",
            "force": "可选，布尔，跳过可操作性检查",
            "position": "可选，{x, y} 相对元素左上角的点击位置",
        },
        "required": (),
        "requires_locator": True,
    },
    "fill": {
        "summary": "清空输入框并输入文本",
        "params": {
            "text": "要输入的文本",
        },
        "required": ("text",),
        "requires_locator": True,
    },
    "press": {
        "summary": "在元素上按下键盘按键",
        "params": {"key": "Playwright 按键名，例如 Enter 或 Control+A"},
        "required": ("key",),
        "requires_locator": True,
    },
    "select_option": {
        "summary": "选择下拉框选项",
        "params": {
            "values": "可选，字符串或字符串数组，按 value 选择",
            "label": "可选，按可见文本选择",
            "index": "可选，按序号选择",
        },
        "required": (),
        "any_of": (("values", "value", "label", "index"),),
        "requires_locator": True,
    },
    "check": {
        "summary": "勾选复选框或单选框",
        "params": {},
        "required": (),
        "requires_locator": True,
    },
    "uncheck": {
        "summary": "取消勾选复选框",
        "params": {},
        "required": (),
        "requires_locator": True,
    },
    "wait_for": {
        "summary": "等待固定毫秒数，或等待元素、选择器到达指定状态",
        "params": {
            "milliseconds": "可选，等待的毫秒数",
            "selector": "可选，CSS 选择器；与 locator 二选一",
            "state": "可选，attached、detached、visible 或 hidden",
        },
        "required": (),
        "any_of": (("milliseconds", "selector", "locator"),),
        "requires_locator": False,
    },
    "extract_text": {
        "summary": "读取元素文本，可写入变量供后续步骤使用",
        "params": {
            "multiple": "可选，布尔，读取全部匹配元素的文本",
            "variable": "可选，写入的执行变量名",
            "allow_empty": "可选，布尔；默认 false，空结果失败",
        },
        "required": (),
        "requires_locator": True,
    },
    "extract_table": {
        "summary": "把表格提取为字典数组",
        "params": {
            "selector": "可选，表格的 CSS 选择器；与 locator 二选一",
            "variable": "可选，写入的执行变量名",
            "allow_empty": "可选，布尔；默认 false，空结果失败",
        },
        "required": (),
        "any_of": (("selector", "locator"),),
        "requires_locator": False,
    },
    "observe_page": {
        "summary": "观察当前页面的标题、URL、表格和可交互元素",
        "params": {
            "max_elements": "可选，最多返回的元素数，默认 80",
            "variable": "可选，写入的执行变量名",
        },
        "required": (),
        "requires_locator": False,
    },
    "collect_links": {
        "summary": "按 CSS 选择器收集去重后的站内链接",
        "params": {
            "selector": "链接元素 CSS 选择器",
            "attribute": "可选，默认 href",
            "limit": "可选，本次最多返回条目数",
            "allowed_domains": "可选，允许域名数组；默认使用计划 crawl_policy",
            "variable": "可选，写入的执行变量名",
        },
        "required": ("selector",),
        "requires_locator": False,
    },
    "extract_fields": {
        "summary": "按声明式字段映射提取列表或详情数据",
        "params": {
            "selector": "可选，重复行容器选择器；省略时提取单条",
            "fields": "字段名到 {selector, attribute, url} 的映射",
            "variable": "可选，写入的执行变量名",
            "allow_empty": "可选，布尔；默认 false，空结果失败",
        },
        "required": ("fields",),
        "requires_locator": False,
    },
    "paginate": {
        "summary": "收集当前页并点击下一页，直到无新条目或达到预算",
        "params": {
            "item_selector": "列表项链接选择器",
            "next_selector": "下一页按钮选择器",
            "attribute": "可选，默认 href",
            "max_pages": "最大页数",
            "max_items": "最大条目数",
            "allowed_domains": "可选，允许域名数组",
            "variable": "可选，写入的执行变量名",
        },
        "required": (
            "item_selector",
            "next_selector",
            "max_pages",
            "max_items",
        ),
        "requires_locator": False,
    },
    "wait_for_response": {
        "summary": "等待匹配的 HTTP 响应并返回状态与 JSON",
        "params": {
            "url_contains": "可选，URL 片段",
            "status": "可选，期望状态码",
            "timeout_ms": "可选，等待毫秒数",
            "trigger_selector": "可选，等待期间先点击的选择器",
            "variable": "可选，写入的执行变量名",
        },
        "required": (),
        "requires_locator": False,
    },
    "assert_data": {
        "summary": "对声明式前后置条件执行数据校验",
        "params": {
            "checks": "条件数组，例如 row_count_min 或 text_non_empty",
        },
        "required": ("checks",),
        "requires_locator": False,
    },
    "screenshot": {
        "summary": "保存一张页面截图",
        "params": {
            "name": "可选，文件名或文件名前缀",
            "full_page": "可选，布尔，是否整页截图",
        },
        "required": (),
        "requires_locator": False,
    },
    "save_file": {
        "summary": "把文本或变量内容保存为执行产物",
        "params": {
            "path": "保存文件名或位于白名单目录内的完整路径",
            "content": "要保存的文本内容；与 variable 二选一",
            "variable": "可选，要保存的变量名；与 content 二选一",
            "encoding": "可选，文本编码，默认 utf-8",
        },
        "required": ("path",),
        "any_of": (("content", "variable"),),
        "requires_locator": False,
    },
    "assert_text": {
        "summary": "断言页面或元素文本符合预期",
        "params": {
            "text": "期望文本",
            "exact": "可选，布尔，默认 false 表示包含匹配",
        },
        "required": ("text",),
        "requires_locator": False,
    },
    "assert_logged_in": {
        "summary": "断言登录态有效，检测到登录表单时按登录失效处理",
        "params": {
            "success_selector": "可选，已登录时才会出现的元素选择器",
            "login_paths": "可选，URL 片段数组，命中即视为需要登录",
        },
        "required": (),
        "requires_locator": False,
    },
    "download": {
        "summary": "等待下载完成并保存到执行产物",
        "params": {
            "save_as": "可选，保存文件名",
            "timeout_ms": "可选，等待下载的毫秒数",
        },
        "required": (),
        "requires_locator": False,
    },
    "upload": {
        "summary": "向文件输入框设置要上传的本地文件",
        "params": {
            "files": "文件路径，或文件路径数组",
        },
        "required": ("files",),
        "requires_locator": True,
    },
    "code_step": {
        "summary": "页面上下文内的受控代码兜底，只在页面里执行表达式",
        "params": {
            "script": "传给 page.evaluate 的 JavaScript 表达式",
            "expect": "可选，布尔，表达式结果必须为真",
            "variable": "可选，把结果写入的执行变量名",
        },
        "required": ("script",),
        "requires_locator": False,
    },
}

WEB_ACTION_TYPES = frozenset(WEB_ACTIONS)

CRAWL_ACTIONS = frozenset(
    {
        "collect_links",
        "extract_fields",
        "paginate",
    }
)


def _normalize_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise WebPlanError("网页结构化计划至少需要一个步骤")

    normalized: list[dict[str, Any]] = []
    for index, raw_step in enumerate(steps):
        if not isinstance(raw_step, dict):
            raise WebPlanError(f"第 {index + 1} 个步骤必须是 JSON 对象")

        action = str(
            raw_step.get("action") or raw_step.get("type") or ""
        ).strip()
        spec = WEB_ACTIONS.get(action)
        if spec is None:
            raise WebPlanError(
                f"动作不在网页动作目录中：{action or '<empty>'}"
            )

        executor = str(raw_step.get("executor") or "web").strip().lower()
        if executor != "web":
            raise WebPlanError(
                f"网页流程的步骤 {index + 1} executor 必须是 web，"
                f"当前为 {executor}"
            )

        params = raw_step.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise WebPlanError(f"{action} 的参数必须是 JSON 对象")
        params = dict(params)

        allowed_params = set(spec.get("params") or {})
        unknown = sorted(set(params) - allowed_params)
        if unknown:
            raise WebPlanError(
                f"{action} 存在未知参数：{', '.join(unknown)}"
            )

        missing = [
            name
            for name in spec.get("required", ())
            if name not in params
        ]
        if missing:
            raise WebPlanError(
                f"{action} 缺少必填参数：{', '.join(missing)}"
            )

        has_locator = isinstance(raw_step.get("locator"), dict)
        available = set(params)
        if has_locator:
            available.add("locator")
        for group in spec.get("any_of", ()):
            if not any(name in available for name in group):
                raise WebPlanError(
                    f"{action} 需要以下参数之一：{', '.join(group)}"
                )

        if spec.get("requires_locator") and not has_locator:
            raise WebPlanError(f"{action} 需要 locator 定位元素")

        if action == "goto":
            _validate_http_url(str(params["url"]))
        if action == "upload":
            _validate_files(params["files"])
        if action == "code_step" and not str(
            params.get("script") or ""
        ).strip():
            raise WebPlanError("code_step 的 script 不能为空")

        normalized.append(
            {
                **raw_step,
                "step_id": raw_step.get("step_id", index + 1),
                "executor": "web",
                "action": action,
                "params": params,
                "retry_policy": (
                    raw_step["retry_policy"]
                    if isinstance(raw_step.get("retry_policy"), dict)
                    else {"max_attempts": 2, "backoff_seconds": 1.0}
                ),
                "depends_on": (
                    list(raw_step["depends_on"])
                    if isinstance(raw_step.get("depends_on"), list)
                    else []
                ),
            }
        )

    return normalized


def _validate_http_url(url: str) -> None:
    candidate = str(url or "").strip()
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise WebPlanError(
            "goto 只支持 http 或 https 地址："
            f"{candidate or '<empty>'}"
        )


def _validate_files(files: Any) -> None:
    if isinstance(files, str):
        entries = [files]
    elif isinstance(files, (list, tuple)):
        entries = list(files)
    else:
        raise WebPlanError("upload 的 files 必须是路径或路径数组")
    if not entries or any(
        not isinstance(entry, str) or not entry.strip()
        for entry in entries
    ):
        raise WebPlanError("upload 的 files 不能为空")


def validate_web_plan(plan: object) -> dict[str, Any]:
    """校验并规范化网页结构化计划，失败时抛出 ``WebPlanError``。"""

    if not isinstance(plan, dict):
        raise WebPlanError("网页结构化计划必须是 JSON 对象")

    error = plan.get("error")
    if isinstance(error, str) and error.strip():
        raise WebPlanError(error.strip())

    normalized_steps = _normalize_steps(plan)
    crawl_policy = _validate_crawl_policy(
        plan,
        normalized_steps,
    )
    try:
        parse_structured_steps({"steps": normalized_steps})
    except ValueError as exc:
        raise WebPlanError(str(exc)) from exc

    validated = dict(plan)
    validated.pop("error", None)
    validated["executor_type"] = "web"
    validated["execution_mode"] = "structured"
    validated["steps"] = normalized_steps
    if crawl_policy is not None:
        validated["crawl_policy"] = crawl_policy
    if not isinstance(validated.get("variables"), list):
        validated["variables"] = []
    return validated


def _validate_crawl_policy(
    plan: dict[str, Any],
    steps: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not any(
        str(step.get("action") or "") in CRAWL_ACTIONS
        for step in steps
    ):
        return None

    raw = plan.get("crawl_policy")
    if not isinstance(raw, dict):
        raise WebPlanError(
            "网页抓取计划必须声明 crawl_policy"
        )
    domains = raw.get("allowed_domains")
    if not isinstance(domains, list) or not domains:
        raise WebPlanError(
            "crawl_policy.allowed_domains 不能为空"
        )
    normalized_domains: list[str] = []
    for value in domains:
        domain = str(value or "").strip().lower().rstrip(".")
        if (
            not domain
            or "://" in domain
            or "/" in domain
            or ":" in domain
        ):
            raise WebPlanError(
                f"crawl_policy 域名无效：{value}"
            )
        if domain not in normalized_domains:
            normalized_domains.append(domain)

    max_pages = _positive_int(raw.get("max_pages"))
    max_items = _positive_int(raw.get("max_items"))
    if max_pages is None:
        raise WebPlanError(
            "crawl_policy.max_pages 必须是正整数"
        )
    if max_items is None:
        raise WebPlanError(
            "crawl_policy.max_items 必须是正整数"
        )
    rate = raw.get("requests_per_second", 1)
    try:
        requests_per_second = float(rate)
    except (TypeError, ValueError):
        requests_per_second = 0
    if requests_per_second <= 0:
        raise WebPlanError(
            "crawl_policy.requests_per_second 必须大于 0"
        )
    return {
        "allowed_domains": normalized_domains,
        "requests_per_second": min(requests_per_second, 10.0),
        "max_pages": max_pages,
        "max_items": max_items,
        "max_runtime_seconds": _positive_float(
            raw.get("max_runtime_seconds")
        ),
    }


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def web_action_catalog_text() -> str:
    """生成给规划器使用的动作目录说明。"""

    lines: list[str] = []
    for action, spec in WEB_ACTIONS.items():
        lines.append(f"- {action}：{spec.get('summary', '')}")
        params = spec.get("params")
        if isinstance(params, dict):
            for name, description in params.items():
                lines.append(f"  - {name}：{description}")
    return "\n".join(lines)


__all__ = [
    "WEB_ACTIONS",
    "WEB_ACTION_TYPES",
    "CRAWL_ACTIONS",
    "WebPlanError",
    "validate_web_plan",
    "web_action_catalog_text",
]

