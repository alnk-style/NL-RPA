"""通用 Windows 桌面流程规划器。

规划器只允许输出动作目录内的动作，并在返回前用白名单做确定性校验，
使任意白名单程序都能进入桌面执行链路。
"""

from __future__ import annotations

import fnmatch
import json
import ntpath
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from app.agents.base_agent import LLMClient, extract_json_object
from app.agents.desktop_catalog import (
    ACTION_TYPES,
    capabilities_for_actions,
    desktop_action_catalog_text,
)
from app.agents.step_planner import desktop_automation_reason
from runtime_paths import resource_root

if TYPE_CHECKING:
    from app.config import Settings


DESKTOP_PLANNER_SYSTEM_PROMPT = """你是 Windows 桌面 RPA 规划器。\
请把用户的自然语言需求转换成受控的桌面动作序列，只输出 JSON。

规则：
1. 只能使用动作目录中列出的动作，禁止发明新动作或新参数。
2. 如果输入中包含 app_profile，优先使用配方步骤。verified=false 的配方
   仍必须展开成可执行步骤，并在顶层设置 "requires_confirmation": true；
   计划会先展示给用户，用户确认后才执行。禁止因此返回暂不支持。
   配方名不是动作名：search_and_play、open_lyrics_panel、read_lyrics、
   export_word、export_excel、compute_line_lengths 等名称不得直接写成
   action，必须展开为动作目录中的原子动作。
3. launch_app.path 必须原样复制白名单中的一项；如果用户要操作的程序不在
   白名单里，直接返回 {"error": "程序不在白名单：<程序名>"}，不要编造路径。
4. 执行顺序通常为：launch_app 或 activate_window，然后 type_text、
   click_element、press_hotkey、read_text，必要时用 wait 等待窗口或控件。
   用户明确要求打开或启动程序时，始终使用 launch_app（已有窗口会自动复用）；
   activate_window 仅用于切换已经打开的其他窗口。
5. 控件优先使用 UI Automation 的 automation_id、name、control_type；
   无法确定时用 name 描述控件，不要编造屏幕坐标。
6. 输入中文文本时使用 type_text，并把 method 设为 clipboard。
7. 参数可使用只读变量占位符 ${name}，由 TaskRuntime 在执行前解析。
8. 如果输入里带有“控件摘要”，click_element、read_text、wait 的元素条件
   必须使用摘要中真实存在的 name、automation_id 或 control_type；优先原样
   使用 best_locator 里的取值，不要使用摘要之外的控件。
9. 用户指定了保存目录或文件名时，必须原样表达，不要省略保存位置；
   如果指定目录不在“可写入目录”里，直接返回 {"error": "目录不在白名单：<目录>"}。
10. 用户只要求截取某个程序的窗口时，用 screenshot.window 指定窗口标题（例如 {"title": "微信"}），
   不要截取全屏。
11. 用户要求“等待 N 秒”时，用 wait 并且只给 seconds，不要附加 condition；
   condition 会在条件已满足时立即返回，达不到固定等待的效果。
12. 把图片插入文档时必须用 insert_image（image_path 指向图片文件，path 指向文档）；
   screenshot 只用于把当前屏幕保存成图片文件，禁止用它代替插入图片。
13. 不要在计划中向用户追问可执行文件路径、快捷方式名称、窗口标题/类名或
    UIA 定位信息。优先使用 resolved_apps、白名单、窗口快照和运行时探测；
    目标窗口未打开时先 launch_app，未知标题时使用 screenshot.window="last"。
14. 没有已验证的 UIA 定位时优先使用键盘操作：搜索联系人可用 press_hotkey ^f、
    type_text(method=clipboard)、press_hotkey {ENTER} 完成，不要因此要求用户补定位。
15. 发送已有文件时用 copy_file_to_clipboard 复制文件，再 press_hotkey ^v，
    必要时输入说明文字并 press_hotkey {ENTER}；不要发明 send_file 或
    search_contact 等目录外动作。
16. 从应用界面复制歌词时用 copy_selected_text 写入 result_var，再用
    transform.split_lines 拆行、transform.count_chars 统计每行字数。
    Word 用 create_document；Excel 用 create_spreadsheet，歌词和字数各占
    一列、每句歌词占一行。所有文件路径必须使用用户指定且位于可写目录。
17. 配方中的扁平步骤字段必须转换到 params，例如
    {"action":"click_relative","x":0.78,"y":0.06} 转为
    {"action":"click_relative","params":{"x":0.78,"y":0.06}}。
18. 输出中的需要确认信息仅用于风险提示，不得包含用户本应在运行时自动探测的信息。
19. 如果用户的任何一条要求无法用动作目录里的动作如实完成，不要用相似动作凑合，
    直接返回 {"error": "该需求暂不支持：<具体说明>"}。
20. 输出结构：
{
  "executor_type": "windows",
  "requires_confirmation": false,
  "trigger": {"type": "manual", "cron": null},
  "steps": [
    {
      "step_id": 1,
      "action": "launch_app",
      "params": {"path": "notepad.exe"},
      "depends_on": []
    }
  ],
  "variables": []
}"""

DESKTOP_REPAIR_SYSTEM_PROMPT = """你是 Windows 桌面 RPA 修复规划器。\
上一步在真实执行中失败了，请用当前窗口里真实存在的控件重写它，并保留后续步骤的意图。

规则：
1. 只输出 JSON：{"steps": [...]}。
2. steps 从失败的那一步开始：第一项是修复后的失败步骤，后面按原样保留原有后续步骤。
3. click_element、read_text 和 wait 的元素条件必须使用“候选控件”里列出的
   name、automation_id 或 control_type，优先使用 best_locator 的取值，不要编造控件。
4. activate_window 失败时，必须使用“候选窗口”里真实存在的 title、class_name 或 pid。
5. 候选控件里没有可用项时，先用 activate_window 激活目标窗口再重试原条件；仍然无法
   确定时返回 {"error": "原因"}。wait 失败时可以保留 element_appears 条件并替换为
   真实候选，也可以改成先 activate_window 再继续后续步骤。
6. 只能使用动作目录中的动作，参数规则与首次规划一致。
7. 需要让窗口重新获得焦点时，在修复后的步骤前面加 activate_window。"""


class DesktopPlanError(ValueError):
    """桌面计划不满足动作目录或白名单约束。"""


@dataclass(frozen=True)
class DesktopExecutionContext:
    """规划桌面流程时可用的 Agent 能力与白名单。"""

    allowed_apps: tuple[str, ...] = ()
    allowed_doc_dirs: tuple[str, ...] = ()
    capabilities: frozenset[str] = frozenset()
    online_agent_count: int = 0
    paired_agent_count: int = 0
    agent_ids: tuple[int, ...] = ()
    app_profiles: tuple[dict[str, Any], ...] = ()

    @property
    def has_agents(self) -> bool:
        return self.paired_agent_count > 0

    @property
    def has_allowed_apps(self) -> bool:
        return bool(self.allowed_apps)


def is_desktop_intent(
    parsed_plan: object,
    structured_plan: object | None = None,
) -> bool:
    """判断一次对话请求是否应该走桌面规划器。"""

    if isinstance(parsed_plan, dict):
        executor_type = str(
            parsed_plan.get("executor_type") or ""
        ).strip().lower()
        if executor_type in {"windows", "desktop"}:
            return True
        app_hint = parsed_plan.get("app_hint")
        if (
            executor_type != "web"
            and isinstance(app_hint, str)
            and app_hint.strip()
        ):
            return True

    if desktop_automation_reason(parsed_plan):
        return True
    return bool(desktop_automation_reason(structured_plan))


def _normalized_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(str(value).strip()))


def allowlist_app_name(value: object) -> str:
    text = str(value).strip().replace("/", "\\")
    if "\\" in text:
        return ntpath.basename(text).lower()
    return text.lower()


def window_hints(parsed_plan: object) -> list[str]:
    """从意图中提取可用于匹配窗口的提示词。"""

    hints: list[str] = []
    if not isinstance(parsed_plan, dict):
        return hints

    app_hint = parsed_plan.get("app_hint")
    if isinstance(app_hint, str) and app_hint.strip():
        hints.append(app_hint.strip())

    steps = parsed_plan.get("steps")
    if isinstance(steps, list):
        for step in steps[:5]:
            if not isinstance(step, dict):
                continue
            target = step.get("target")
            if not isinstance(target, str):
                continue
            candidate = target.strip()
            if not candidate or len(candidate) > 40:
                continue
            if candidate.lower().startswith(("http://", "https://")):
                continue
            hints.append(candidate)

    unique: list[str] = []
    for hint in hints:
        if hint not in unique:
            unique.append(hint)
    return unique


def select_target_window(
    windows: list[dict[str, Any]],
    hints: list[str],
) -> dict[str, Any] | None:
    """按标题、进程名和类名给窗口打分，返回最匹配的一个。"""

    normalized_hints = [
        str(hint).strip().lower()
        for hint in hints
        if isinstance(hint, str) and str(hint).strip()
    ]
    if not normalized_hints:
        return None

    best: dict[str, Any] | None = None
    best_score = 0
    for window in windows:
        if not isinstance(window, dict):
            continue

        haystacks: list[str] = []
        for key in ("title", "class_name", "process_name"):
            value = window.get(key)
            if isinstance(value, str) and value.strip():
                haystacks.append(value.strip().lower())
        process_name = window.get("process_name")
        if isinstance(process_name, str) and "." in process_name:
            stem = process_name.rsplit(".", 1)[0].strip().lower()
            if stem:
                haystacks.append(stem)

        score = 0
        for hint in normalized_hints:
            for haystack in haystacks:
                if hint == haystack:
                    score += 6
                elif len(hint) >= 2 and hint in haystack:
                    score += 3
                elif len(haystack) >= 2 and haystack in hint:
                    score += 2
        if score > best_score:
            best = window
            best_score = score

    return best


def match_allowlisted_app(
    app_path: object,
    allowed_apps: Iterable[str],
) -> str | None:
    """返回命中的白名单条目，未命中返回 None。

    与桌面 Agent 的 ActionValidator 使用同一套匹配规则：完整路径按路径
    比较，程序名按文件名比较，并支持 * ? [ 通配符。
    """

    candidate = str(app_path).strip()
    if not candidate:
        return None

    candidate_name = allowlist_app_name(candidate)
    candidate_path = _normalized_path(candidate)

    for entry in allowed_apps:
        entry_text = str(entry).strip()
        if not entry_text:
            continue

        entry_name = allowlist_app_name(entry_text)
        if any(char in entry_text for char in "*?["):
            if "\\" in entry_text or "/" in entry_text:
                if fnmatch.fnmatchcase(
                    candidate_path,
                    _normalized_path(entry_text),
                ):
                    return entry_text
            elif fnmatch.fnmatchcase(candidate_name, entry_name):
                return entry_text
            continue

        if "\\" in entry_text or "/" in entry_text:
            if _normalized_path(entry_text) == candidate_path:
                return entry_text
            continue

        if entry_name == candidate_name:
            return entry_text

    return None


def _compact_app_text(value: object) -> str:
    text = str(value or "").strip().casefold()
    if text.endswith(".exe"):
        text = text[:-4]
    return "".join(char for char in text if char.isalnum())


def _allowlist_entry_for_path(
    path: object,
    allowed_apps: Iterable[str],
) -> str | None:
    candidate = str(path or "").strip()
    if not candidate:
        return None
    candidate_path = _normalized_path(candidate)
    candidate_name = allowlist_app_name(candidate)
    for entry in allowed_apps:
        entry_text = str(entry).strip()
        if not entry_text:
            continue
        if _normalized_path(entry_text) == candidate_path:
            return entry_text
        if allowlist_app_name(entry_text) == candidate_name:
            return entry_text
    return None


def resolve_allowed_apps(
    text: object,
    allowed_apps: Iterable[str],
    installed_apps: Iterable[Any] = (),
) -> list[dict[str, str]]:
    """Map app display names mentioned in a request to allowlisted paths."""

    haystack = _compact_app_text(text)
    if not haystack:
        return []

    allowed = list(allowed_apps)
    resolved: list[tuple[int, dict[str, str]]] = []
    seen_paths: set[str] = set()

    for app in installed_apps:
        if isinstance(app, Mapping):
            name = str(app.get("name") or "").strip()
            path = str(
                app.get("executable_path")
                or app.get("path")
                or ""
            ).strip()
        else:
            name = str(getattr(app, "name", "") or "").strip()
            path = str(
                getattr(app, "executable_path", "") or ""
            ).strip()
        if not path:
            continue
        terms = [
            _compact_app_text(name),
            _compact_app_text(ntpath.basename(path)),
        ]
        positions = [
            haystack.find(term)
            for term in terms
            if term and term in haystack
        ]
        if not positions:
            continue
        entry = _allowlist_entry_for_path(path, allowed)
        if entry is None:
            continue
        normalized = _normalized_path(entry)
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        resolved.append(
            (
                min(positions),
                {
                    "name": name or ntpath.basename(entry),
                    "path": entry,
                },
            )
        )

    for entry in allowed:
        entry_text = str(entry).strip()
        if not entry_text:
            continue
        normalized = _normalized_path(entry_text)
        if normalized in seen_paths:
            continue
        term = _compact_app_text(ntpath.basename(entry_text))
        position = haystack.find(term) if term else -1
        if position < 0:
            continue
        seen_paths.add(normalized)
        resolved.append(
            (
                position,
                {
                    "name": ntpath.basename(entry_text),
                    "path": entry_text,
                },
            )
        )

    resolved.sort(key=lambda item: item[0])
    return [item for _, item in resolved]


def validate_desktop_plan(
    plan: object,
    allowed_apps: Iterable[str],
    allowed_doc_dirs: Iterable[str] = (),
) -> dict[str, Any]:
    """校验并规范化桌面计划，失败时抛出 DesktopPlanError。"""

    return _validate_task_plan(
        plan,
        allowed_apps,
        allowed_doc_dirs=allowed_doc_dirs,
        preserve_executor_type=False,
    )


def validate_task_graph_plan(
    plan: object,
    allowed_apps: Iterable[str] = (),
    allowed_doc_dirs: Iterable[str] = (),
) -> dict[str, Any]:
    """校验可跨 web/windows/files/transform/mcp 的 TaskGraph v2。"""

    return _validate_task_plan(
        plan,
        allowed_apps,
        allowed_doc_dirs=allowed_doc_dirs,
        preserve_executor_type=True,
    )


def _validate_task_plan(
    plan: object,
    allowed_apps: Iterable[str],
    *,
    allowed_doc_dirs: Iterable[str],
    preserve_executor_type: bool,
) -> dict[str, Any]:
    from app.agents.web_catalog import WEB_ACTION_TYPES
    from app.engine.transforms import TRANSFORM_ACTIONS

    if not isinstance(plan, dict):
        raise DesktopPlanError("桌面计划必须是 JSON 对象")

    error = plan.get("error")
    if isinstance(error, str) and error.strip():
        raise DesktopPlanError(error.strip())

    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise DesktopPlanError("桌面计划至少需要一个步骤")

    declared_variables = _declared_variable_names(
        plan.get("variables")
    )
    available_variables = set(declared_variables)
    future_result_vars = _result_vars_after(steps, 0)
    result_vars: set[str] = set()
    produced_result_vars: set[str] = set()
    allowed_document_dirs = [
        str(item).strip()
        for item in allowed_doc_dirs
        if str(item).strip()
    ]

    normalized_steps: list[dict[str, Any]] = []
    used_actions: list[str] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise DesktopPlanError(f"第 {index + 1} 个步骤必须是 JSON 对象")

        action = str(step.get("action") or "").strip()
        executor = str(step.get("executor") or "windows").strip().lower()
        if executor in {"desktop", "win"}:
            executor = "windows"
        if action == "foreach":
            valid_action = True
        elif executor == "transform":
            valid_action = action in TRANSFORM_ACTIONS
        elif executor == "web":
            valid_action = action in WEB_ACTION_TYPES
        elif executor == "mcp":
            valid_action = action in {"call_tool", "call", "tool"}
        elif executor == "files":
            valid_action = action in ACTION_TYPES
        else:
            valid_action = action in ACTION_TYPES or action == "foreach"
        if not valid_action:
            raise DesktopPlanError(
                f"动作不在桌面动作目录中：{action or '<empty>'}"
            )

        params = step.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise DesktopPlanError(f"{action} 的参数必须是 JSON 对象")
        params = dict(params)

        if action == "foreach":
            iteration_limit = params.get("max_iterations")
            foreach = step.get("foreach")
            if iteration_limit is None and isinstance(foreach, dict):
                iteration_limit = foreach.get("max_iterations")
            try:
                iteration_limit = int(iteration_limit)
            except (TypeError, ValueError):
                iteration_limit = 0
            if iteration_limit <= 0:
                raise DesktopPlanError(
                    "foreach 必须显式配置 max_iterations"
                )
            child_steps = params.get("steps")
            if not isinstance(child_steps, list):
                child_steps = []
            for child in child_steps:
                _validate_control_child(
                    child,
                    action_types=ACTION_TYPES,
                    web_actions=WEB_ACTION_TYPES,
                    transform_actions=TRANSFORM_ACTIONS,
                )

        if action == "launch_app":
            raw_path = params.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise DesktopPlanError("launch_app 需要非空的 path")
            matched = match_allowlisted_app(raw_path, allowed_apps)
            if matched is None:
                raise DesktopPlanError(
                    "程序不在桌面白名单中："
                    f"{allowlist_app_name(raw_path) or raw_path}"
                )
            params["path"] = matched

        output_var = str(step.get("output_var") or "").strip()
        if output_var:
            if output_var not in declared_variables:
                raise DesktopPlanError(
                    f"output_var 未在 variables 中声明：{output_var}"
                )
            available_variables.add(output_var)

        reference_scope = set(available_variables)
        reference_params = params
        if action == "foreach":
            item_var = str(params.get("item_var") or "item").strip()
            if item_var:
                reference_scope.add(item_var)
            reference_params = {
                key: value
                for key, value in params.items()
                if key != "steps"
            }
        references = _template_references(reference_params)
        for reference in references:
            root = reference.split(".", 1)[0]
            if (
                root in future_result_vars
                and root not in produced_result_vars
            ):
                raise DesktopPlanError(
                    f"forward variable reference：{reference}"
                )
            if root not in reference_scope:
                raise DesktopPlanError(
                    f"unknown variable reference：{reference}"
                )

        result_var = str(step.get("result_var") or "").strip()
        if result_var:
            if result_var in result_vars:
                raise DesktopPlanError(
                    f"result_var 只能由一个步骤生产：{result_var}"
                )
            result_vars.add(result_var)
            produced_result_vars.add(result_var)
            available_variables.add(result_var)

        if (
            allowed_document_dirs
            and action
            in {
                "open_document",
                "create_document",
                "create_spreadsheet",
                "save_document",
                "export_pdf",
                "insert_image",
                "screenshot",
                "copy_file_to_clipboard",
                "upload",
            }
        ):
            _validate_plan_file_paths(
                params,
                allowed_document_dirs,
            )

        depends_on = step.get("depends_on")
        normalized_step = {
            **step,
            "step_id": step.get("step_id", index + 1),
            "executor": (
                str(step.get("executor") or "windows").strip().lower()
            ),
            "action": action,
            "params": params,
            "depends_on": (
                list(depends_on)
                if isinstance(depends_on, list)
                else []
            ),
        }
        normalized_step.pop("selector", None)
        normalized_step.pop("selector_reason", None)
        normalized_steps.append(normalized_step)
        used_actions.append(action)

    required = set(capabilities_for_actions(used_actions))
    declared = plan.get("required_capabilities")
    if isinstance(declared, (list, tuple, set)):
        required.update(
            str(capability)
            for capability in declared
            if str(capability).strip()
        )

    validated = dict(plan)
    validated.pop("error", None)
    validated.pop("needs_clarification", None)
    validated["executor_type"] = (
        str(plan.get("executor_type") or "hybrid")
        if preserve_executor_type
        else "windows"
    )
    validated["steps"] = normalized_steps
    validated["required_capabilities"] = sorted(required)
    if not isinstance(validated.get("variables"), list):
        validated["variables"] = []
    return validated


def _declared_variable_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, str) and entry.strip():
                names.add(entry.strip())
            elif isinstance(entry, dict):
                name = str(entry.get("name") or "").strip()
                if name:
                    names.add(name)
    elif isinstance(value, dict):
        names.update(
            str(name).strip()
            for name in value
            if str(name).strip()
        )
    return names


def _result_vars_after(
    steps: list[Any],
    start_index: int,
) -> set[str]:
    names: set[str] = set()
    for step in steps[start_index:]:
        if not isinstance(step, dict):
            continue
        result_var = str(step.get("result_var") or "").strip()
        if result_var:
            names.add(result_var)
        params = step.get("params")
        if isinstance(params, dict):
            children = params.get("steps")
            if isinstance(children, list):
                names.update(_result_vars_after(children, 0))
    return names


def _template_references(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, str):
        references.update(
            match.group(1).strip()
            for match in re.finditer(r"\$\{([^}]+)\}", value)
            if match.group(1).strip()
        )
    elif isinstance(value, dict):
        for child in value.values():
            references.update(_template_references(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            references.update(_template_references(child))
    return references


def _validate_control_child(
    child: Any,
    *,
    action_types: Iterable[str],
    web_actions: Iterable[str],
    transform_actions: Iterable[str],
) -> None:
    if not isinstance(child, dict):
        raise DesktopPlanError("foreach 子步骤必须是 JSON 对象")
    action = str(child.get("action") or "").strip()
    executor = str(child.get("executor") or "windows").strip().lower()
    if executor == "web":
        valid = action in set(web_actions)
    elif executor == "transform":
        valid = action in set(transform_actions)
    else:
        valid = action in set(action_types) or action == "foreach"
    if not valid:
        raise DesktopPlanError(
            f"foreach 子步骤动作不受支持：{action or '<empty>'}"
        )
    if action == "foreach":
        params = child.get("params")
        if not isinstance(params, dict):
            params = {}
        try:
            limit = int(params.get("max_iterations"))
        except (TypeError, ValueError):
            limit = 0
        if limit <= 0:
            raise DesktopPlanError(
                "嵌套 foreach 必须显式配置 max_iterations"
            )


def _validate_plan_file_paths(
    value: Any,
    allowed_doc_dirs: list[str],
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).strip().lower()
            if (
                normalized_key
                in {
                    "path",
                    "file_path",
                    "save_path",
                    "output_path",
                    "image_path",
                }
                and isinstance(child, str)
                and child.strip()
                and "${" not in child
            ):
                if not _path_is_allowed(child, allowed_doc_dirs):
                    raise DesktopPlanError(
                        f"文件路径不在白名单内：{child}"
                    )
            else:
                _validate_plan_file_paths(
                    child,
                    allowed_doc_dirs,
                )
    elif isinstance(value, (list, tuple)):
        for child in value:
            _validate_plan_file_paths(child, allowed_doc_dirs)


def _path_is_allowed(path: str, allowed_doc_dirs: list[str]) -> bool:
    candidate = _normalized_path(str(Path(path).expanduser()))
    for root in allowed_doc_dirs:
        normalized_root = _normalized_path(str(Path(root).expanduser()))
        if candidate == normalized_root or candidate.startswith(
            normalized_root.rstrip("\\/") + ntpath.sep
        ):
            return True
    return False


def _extract_song_query(text: str) -> str:
    for pattern in (
        r"播放[《“\"]?([^，,。；;\n》”\"]+)",
        r"歌曲[：:]\s*[《“\"]?([^，,。；;\n》”\"]+)",
        r"搜索[《“\"]?([^，,。；;\n》”\"]+)",
    ):
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip()
            value = re.sub(
                r"(?:并|然后|再|把|导出).*$",
                "",
                value,
            ).strip()
            if value:
                return value
    return ""


def _select_output_dir(
    text: str,
    allowed_doc_dirs: Iterable[str],
) -> Path | None:
    normalized_text = text.replace("/", "\\").casefold()
    dirs = [
        Path(str(item).strip()).expanduser()
        for item in allowed_doc_dirs
        if str(item).strip()
    ]
    for directory in dirs:
        if str(directory).replace("/", "\\").casefold() in normalized_text:
            return directory
    if dirs:
        return dirs[0]
    return None


def _flatten_profile_recipe(
    recipe: object,
    variables: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(recipe, dict):
        return []
    raw_steps = recipe.get("steps")
    if not isinstance(raw_steps, list):
        return []
    flattened: list[dict[str, Any]] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            continue
        action = str(raw_step.get("action") or "").strip()
        if not action:
            continue
        params = {
            key: _replace_recipe_value(value, variables)
            for key, value in raw_step.items()
            if key not in {"action", "verified"}
        }
        flattened.append(
            {
                "action": action,
                "params": params,
            }
        )
    return flattened


def _replace_recipe_value(
    value: Any,
    variables: dict[str, Any],
) -> Any:
    if isinstance(value, str):
        rendered = value
        for name, replacement in variables.items():
            rendered = rendered.replace(
                "${" + name + "}",
                str(replacement),
            )
        return rendered
    if isinstance(value, list):
        return [
            _replace_recipe_value(item, variables)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _replace_recipe_value(item, variables)
            for key, item in value.items()
        }
    return value


class DesktopPlannerAgent:
    def __init__(self, config: Settings):
        self.llm = LLMClient(config)

    async def plan(
        self,
        parsed_plan: dict[str, Any],
        context: DesktopExecutionContext,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not context.allowed_apps:
            raise DesktopPlanError(
                "桌面白名单为空，请先在桌面客户端的设置里添加允许的程序"
            )

        prompt_plan = self._with_app_profile(parsed_plan, context)
        deterministic = self._compile_profile_lyrics_export(
            prompt_plan,
            context,
        )
        if deterministic is not None:
            return validate_desktop_plan(
                deterministic,
                context.allowed_apps,
                allowed_doc_dirs=context.allowed_doc_dirs,
            )
        messages = [
            {
                "role": "system",
                "content": DESKTOP_PLANNER_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": self._build_user_prompt(
                    prompt_plan,
                    context,
                    snapshot,
                ),
            },
        ]
        response = await self.llm.chat(messages)

        try:
            plan = extract_json_object(response)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise DesktopPlanError(
                "桌面规划器没有返回有效的 JSON"
            ) from exc

        try:
            return validate_desktop_plan(
                plan,
                context.allowed_apps,
                allowed_doc_dirs=context.allowed_doc_dirs,
            )
        except DesktopPlanError:
            fallback = self._compile_profile_lyrics_export(
                prompt_plan,
                context,
            )
            if fallback is None:
                raise
            return validate_desktop_plan(
                fallback,
                context.allowed_apps,
                allowed_doc_dirs=context.allowed_doc_dirs,
            )

    @staticmethod
    def _compile_profile_lyrics_export(
        parsed_plan: dict[str, Any],
        context: DesktopExecutionContext,
    ) -> dict[str, Any] | None:
        raw_request = str(parsed_plan.get("raw_request") or "")
        profile = parsed_plan.get("app_profile")
        if "歌词" not in raw_request or not isinstance(profile, dict):
            return None
        recipes = profile.get("recipes")
        if not isinstance(recipes, dict):
            return None
        if not isinstance(recipes.get("search_song"), dict):
            return None
        if not isinstance(recipes.get("open_lyrics"), dict):
            return None

        query = _extract_song_query(raw_request)
        if not query:
            return None
        app_path = _allowlist_entry_for_path(
            profile.get("executable_path"),
            context.allowed_apps,
        )
        if app_path is None:
            return None
        output_dir = _select_output_dir(
            raw_request,
            context.allowed_doc_dirs,
        )
        if output_dir is None:
            return None

        safe_name = re.sub(r'[<>:"/\\|?*]+', "_", query).strip(" .")
        if not safe_name:
            return None
        document_path = str(output_dir / f"{safe_name}歌词.docx")
        spreadsheet_path = str(
            output_dir / f"{safe_name}歌词.xlsx"
        )
        variables = [
            {"name": "query", "default": query},
            {"name": "lyrics_text", "default": ""},
            {"name": "lyrics_lines", "default": []},
            {"name": "lyrics_lengths", "default": []},
        ]

        raw_steps: list[dict[str, Any]] = [
            {
                "action": "launch_app",
                "params": {
                    "path": app_path,
                    "wait_for_window": True,
                },
            }
        ]
        window = profile.get("window")
        window_title = (
            str(window.get("title") or "").strip()
            if isinstance(window, dict)
            else ""
        )
        if window_title:
            raw_steps.append(
                {
                    "action": "activate_window",
                    "params": {"title": window_title},
                }
            )
        raw_steps.extend(
            _flatten_profile_recipe(
                recipes.get("search_song"),
                {"query": query},
            )
        )
        if isinstance(recipes.get("play_first_result"), dict):
            raw_steps.extend(
                _flatten_profile_recipe(
                    recipes.get("play_first_result"),
                    {},
                )
            )
        raw_steps.extend(
            [
                {
                    "action": "fetch_kugou_lyrics",
                    "result_var": "lyrics_text",
                    "params": {
                        "query": query,
                        "min_chars": 2,
                    },
                    "postconditions": [
                        {
                            "type": "text_not_equals",
                            "value": "${lyrics_text}",
                            "other": query,
                        }
                    ],
                },
                {
                    "executor": "transform",
                    "action": "split_lines",
                    "result_var": "lyrics_lines",
                    "params": {"text": "${lyrics_text}"},
                    "postconditions": [
                        {
                            "type": "list_min_length",
                            "value": "${lyrics_lines}",
                            "min": 1,
                        }
                    ],
                },
                {
                    "executor": "transform",
                    "action": "count_chars",
                    "result_var": "lyrics_lengths",
                    "params": {"values": "${lyrics_lines}"},
                },
                {
                    "executor": "files",
                    "action": "create_document",
                    "params": {
                        "path": document_path,
                        "content": "${lyrics_text}",
                        "progid": "kwps.Application",
                        "overwrite": True,
                    },
                    "postconditions": [
                        {
                            "type": "file_non_empty",
                            "path": document_path,
                        }
                    ],
                },
                {
                    "executor": "files",
                    "action": "create_spreadsheet",
                    "params": {
                        "path": spreadsheet_path,
                        "sheet_name": "歌词",
                        "columns": [
                            {
                                "header": "歌词",
                                "values": "${lyrics_lines}",
                            },
                            {
                                "header": "字数",
                                "values": "${lyrics_lengths}",
                            },
                        ],
                        "column_widths": [80, 12],
                        "progid": "ket.Application",
                        "overwrite": True,
                    },
                    "postconditions": [
                        {
                            "type": "file_non_empty",
                            "path": spreadsheet_path,
                        }
                    ],
                },
            ]
        )
        steps: list[dict[str, Any]] = []
        for index, step in enumerate(raw_steps, start=1):
            steps.append(
                {
                    "step_id": index,
                    **step,
                    "depends_on": [index - 1] if index > 1 else [],
                }
            )
        return {
            "executor_type": "windows",
            "trigger": parsed_plan.get(
                "trigger",
                {"type": "manual", "cron": None},
            ),
            "requires_confirmation": True,
            "variables": variables,
            "steps": steps,
        }

    async def repair(
        self,
        *,
        plan: dict[str, Any],
        step_index: int,
        failure: dict[str, Any],
        context: DesktopExecutionContext,
        snapshot: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """用真实控件重写失败步骤，返回从该步骤开始的修复后步骤。"""

        if not context.allowed_apps:
            raise DesktopPlanError(
                "桌面白名单为空，无法自动修复"
            )

        messages = [
            {
                "role": "system",
                "content": DESKTOP_REPAIR_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": self._build_repair_prompt(
                    plan,
                    step_index,
                    failure,
                    context,
                    snapshot,
                ),
            },
        ]
        response = await self.llm.chat(messages)

        try:
            payload = extract_json_object(response)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise DesktopPlanError(
                "修复规划器没有返回有效的 JSON"
            ) from exc

        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            raise DesktopPlanError(error.strip())

        steps = payload.get("steps")
        if not isinstance(steps, list) or not steps:
            raise DesktopPlanError("修复规划器没有返回步骤")

        validated = validate_desktop_plan(
            {
                "executor_type": "windows",
                "steps": steps,
                "variables": plan.get("variables", []),
            },
            context.allowed_apps,
            allowed_doc_dirs=context.allowed_doc_dirs,
        )
        return list(validated["steps"])

    @staticmethod
    def _with_app_profile(
        parsed_plan: dict[str, Any],
        context: DesktopExecutionContext,
    ) -> dict[str, Any]:
        from app.engine.app_profiles import (
            load_app_profiles,
            resolve_profile,
        )

        profiles = list(context.app_profiles)
        if not profiles:
            profiles = load_app_profiles(
                resource_root() / "desktop_agent" / "profiles"
            )
        hint = " ".join(
            str(value)
            for value in (
                parsed_plan.get("raw_request"),
                parsed_plan.get("app_hint"),
            )
            if value
        )
        profile = resolve_profile(hint, profiles)
        if profile is None:
            return parsed_plan
        return {**parsed_plan, "app_profile": profile}

    @staticmethod
    def _build_user_prompt(
        parsed_plan: dict[str, Any],
        context: DesktopExecutionContext,
        snapshot: dict[str, Any] | None = None,
    ) -> str:
        apps = "\n".join(f"- {app}" for app in context.allowed_apps)
        capabilities = ", ".join(sorted(context.capabilities)) or "未上报"
        writable_dirs = (
            "\n".join(f"- {item}" for item in context.allowed_doc_dirs)
            or "（未配置，无法指定保存位置）"
        )
        parts = [
            "可用的白名单程序（launch_app.path 必须原样使用其中一项）：",
            apps,
            "",
            f"已连接 Agent 的能力：{capabilities}",
            "",
            "可写入目录（screenshot.path 与文档保存路径必须位于其中）：",
            writable_dirs,
            "",
            "动作目录：",
            desktop_action_catalog_text(),
            "",
            "用户需求的结构化意图：",
            json.dumps(parsed_plan, ensure_ascii=False, indent=2),
        ]
        resolved_apps = parsed_plan.get("resolved_apps")
        if isinstance(resolved_apps, list) and resolved_apps:
            parts.extend(
                [
                    "",
                    "resolved_apps（用户提到的程序及其白名单路径）：",
                    json.dumps(
                        resolved_apps,
                        ensure_ascii=False,
                        indent=2,
                    ),
                ]
            )
        app_profile = parsed_plan.get("app_profile")
        if isinstance(app_profile, dict):
            parts.extend(
                [
                    "",
                    "app_profile（优先使用已验证配方）：",
                    json.dumps(
                        app_profile,
                        ensure_ascii=False,
                        indent=2,
                    ),
                ]
            )
        snapshot_text = _snapshot_text(snapshot)
        if snapshot_text:
            parts.extend(["", snapshot_text])
        return "\n".join(parts)

    @staticmethod
    def _build_repair_prompt(
        plan: dict[str, Any],
        step_index: int,
        failure: dict[str, Any],
        context: DesktopExecutionContext,
        snapshot: dict[str, Any] | None = None,
    ) -> str:
        apps = "\n".join(f"- {app}" for app in context.allowed_apps)
        writable_dirs = (
            "\n".join(f"- {item}" for item in context.allowed_doc_dirs)
            or "（未配置，无法指定保存位置）"
        )
        candidate_details = failure.get("candidate_details")
        candidates = failure.get("candidates")
        if isinstance(candidate_details, list) and candidate_details:
            candidate_text = "\n".join(
                _candidate_detail_text(detail)
                for detail in candidate_details
                if isinstance(detail, dict)
            )
        elif isinstance(candidates, list) and candidates:
            candidate_text = "\n".join(
                f"- {candidate}" for candidate in candidates
            )
        else:
            candidate_text = "- 未提供"

        last_locator = failure.get("last_locator")
        last_locator_text = (
            json.dumps(last_locator, ensure_ascii=False)
            if isinstance(last_locator, dict) and last_locator
            else "- 无"
        )

        parts = [
            "白名单程序（launch_app.path 必须原样使用其中一项）：",
            apps,
            "",
            "可写入目录（screenshot.path 与文档保存路径必须位于其中）：",
            writable_dirs,
            "",
            f"失败步骤序号（从 0 开始）：{step_index}",
            f"失败动作：{failure.get('action_type') or '未知'}",
            f"失败原因：{failure.get('error') or ''}",
            "",
            "候选控件：",
            candidate_text,
            "",
            "上次成功定位：",
            last_locator_text,
            "",
            "动作目录：",
            desktop_action_catalog_text(),
            "",
            "原流程：",
            json.dumps(plan, ensure_ascii=False, indent=2),
        ]
        snapshot_text = _snapshot_text(snapshot)
        if snapshot_text:
            parts.extend(["", snapshot_text])
        return "\n".join(parts)


def _snapshot_text(snapshot: dict[str, Any] | None) -> str:
    if not isinstance(snapshot, dict):
        return ""

    lines: list[str] = []
    windows = snapshot.get("windows")
    if isinstance(windows, list) and windows:
        lines.append("当前已打开的可见窗口：")
        for window in windows[:20]:
            if not isinstance(window, dict):
                continue
            parts = [str(window.get("title") or "")]
            if window.get("process_name"):
                parts.append(f"进程 {window['process_name']}")
            if window.get("class_name"):
                parts.append(f"类名 {window['class_name']}")
            if window.get("pid") is not None:
                parts.append(f"pid {window['pid']}")
            lines.append(
                "- " + " | ".join(part for part in parts if part)
            )

    target = snapshot.get("target")
    elements = snapshot.get("elements")
    if (
        isinstance(target, dict)
        and isinstance(elements, list)
        and elements
    ):
        title = str(target.get("title") or "")
        lines.append(f"目标窗口“{title}”的控件摘要：")
        for element in elements[:60]:
            if not isinstance(element, dict):
                continue
            parts = []
            if element.get("name"):
                parts.append(f"name={element['name']}")
            if element.get("automation_id"):
                parts.append(f"automation_id={element['automation_id']}")
            if element.get("control_type"):
                parts.append(f"control_type={element['control_type']}")
            if element.get("class_name"):
                parts.append(f"class_name={element['class_name']}")
            if element.get("value"):
                parts.append(f"value={element['value']}")
            if element.get("enabled") is not None:
                parts.append(
                    "enabled=" + str(bool(element["enabled"])).lower()
                )
            if element.get("visible") is not None:
                parts.append(
                    "visible=" + str(bool(element["visible"])).lower()
                )
            best_locator = element.get("best_locator")
            if isinstance(best_locator, dict) and best_locator.get("value"):
                parts.append(
                    "best_locator="
                    f"{best_locator.get('strategy')}:"
                    f"{best_locator.get('value')}"
                    f"({best_locator.get('score')})"
                )
            if not parts:
                continue
            depth = element.get("depth", 0)
            lines.append(f"- depth {depth}：" + " | ".join(parts))

    return "\n".join(lines)


def _candidate_detail_text(detail: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "name",
        "automation_id",
        "control_type",
        "class_name",
        "title",
        "process_name",
        "pid",
        "value",
        "enabled",
        "visible",
        "score",
    ):
        value = detail.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, bool):
            value = str(value).lower()
        parts.append(f"{key}={value}")

    best_locator = detail.get("best_locator")
    if isinstance(best_locator, dict) and best_locator.get("value"):
        parts.append(
            "best_locator="
            f"{best_locator.get('strategy')}:"
            f"{best_locator.get('value')}"
            f"({best_locator.get('score')})"
        )
    if not parts:
        return "- ?"
    return "- " + " | ".join(parts)


__all__ = [
    "DESKTOP_PLANNER_SYSTEM_PROMPT",
    "DESKTOP_REPAIR_SYSTEM_PROMPT",
    "DesktopExecutionContext",
    "DesktopPlanError",
    "DesktopPlannerAgent",
    "allowlist_app_name",
    "is_desktop_intent",
    "match_allowlisted_app",
    "resolve_allowed_apps",
    "select_target_window",
    "validate_desktop_plan",
    "validate_task_graph_plan",
    "window_hints",
]
