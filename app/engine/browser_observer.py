"""网页观察层：在生成或执行前采集真实页面事实。

观察结果只来源于浏览器实际渲染的 DOM、可访问性树与页面状态，
规划器不得编造未观察到的控件。完整快照写入日志目录，精简摘要
交给模型使用。
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from contextlib import suppress
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from app.engine.runner import PlaywrightRunner
from app.protocols import ObservationKind, ObservationSnapshot

if TYPE_CHECKING:
    from app.config import Settings


logger = logging.getLogger(__name__)

MAX_FRAMES = 5
DEFAULT_OBSERVATION_TIMEOUT_MS = 30000
MAX_ACCESSIBILITY_NODES = 200
OBSERVATION_WAIT_UNTIL = {"commit", "domcontentloaded", "load", "networkidle"}


class ObservationError(RuntimeError):
    """网页观察失败，错误文本可用于失败分类。"""


OBSERVATION_SCRIPT = r"""
(config) => {
  const options = config || {};
  const maxElements = Math.max(
    0,
    Math.min(300, Number(options.maxElements) || 0)
  );
  const frameLabel =
    typeof options.frameLabel === "string" ? options.frameLabel : null;

  const INTERACTIVE_SELECTOR = [
    "a[href]",
    "button",
    "input",
    "select",
    "textarea",
    "summary",
    "dialog[open]",
    "[role]",
    "[contenteditable='true']",
    "[tabindex]:not([tabindex='-1'])",
    "[onclick]",
    "[aria-label]",
  ].join(",");

  const STRATEGY_SCORE = {
    role: 100,
    label: 92,
    placeholder: 90,
    testid: 86,
    text: 74,
    css: 62,
    xpath: 40,
  };
  const TEXT_ROLES = new Set([
    "button",
    "link",
    "menuitem",
    "option",
    "tab",
    "checkbox",
    "radio",
    "switch",
  ]);
  const LOGIN_MARKERS = [
    "登录",
    "登陆",
    "扫码登录",
    "密码登录",
    "手机号登录",
    "验证码",
    "sign in",
    "sign-in",
    "log in",
    "login",
    "password",
  ];

  const normalizeText = (value) =>
    String(value == null ? "" : value).replace(/\s+/g, " ").trim();

  const truncate = (value, maxLength) => {
    const text = normalizeText(value);
    if (text.length <= maxLength) return text;
    return text.slice(0, Math.max(0, maxLength - 1)) + "\u2026";
  };

  const cssEscape = (value) => {
    if (window.CSS && typeof window.CSS.escape === "function") {
      return window.CSS.escape(String(value));
    }
    return String(value).replace(/["\\\]]/g, "\\$&");
  };

  const attrEscape = (value) =>
    String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');

  const rootOf = (element) =>
    element.getRootNode ? element.getRootNode() : document;

  const countMatches = (element, selector) => {
    if (!selector) return 0;
    try {
      return rootOf(element).querySelectorAll(selector).length;
    } catch (error) {
      return 0;
    }
  };

  const implicitRole = (element) => {
    const explicit = element.getAttribute("role");
    if (explicit && explicit.trim()) {
      return explicit.trim().toLowerCase();
    }
    const tag = element.tagName.toLowerCase();
    const type = (element.getAttribute("type") || "").toLowerCase();
    if (tag === "button") return "button";
    if (tag === "select") {
      return element.hasAttribute("multiple") ? "listbox" : "combobox";
    }
    if (tag === "textarea") return "textbox";
    if (tag === "a") return element.hasAttribute("href") ? "link" : "generic";
    if (tag === "summary") return "button";
    if (tag === "dialog") return "dialog";
    if (tag === "input") {
      if (["button", "submit", "reset", "image"].includes(type)) {
        return "button";
      }
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "range") return "slider";
      if (type === "number") return "spinbutton";
      if (type === "search") return "searchbox";
      return "textbox";
    }
    return "generic";
  };

  const labelText = (element) => {
    const root = rootOf(element);
    const id = element.getAttribute("id");
    if (id) {
      const label = root.querySelector(
        `label[for="${cssEscape(id)}"]`
      );
      if (label) return normalizeText(label.textContent);
    }
    const container = element.closest ? element.closest("label") : null;
    if (container) return normalizeText(container.textContent);

    const ariaLabel = element.getAttribute("aria-label");
    if (ariaLabel && ariaLabel.trim()) return normalizeText(ariaLabel);

    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy) {
      const parts = labelledBy
        .split(/\s+/)
        .map((token) => {
          const target =
            root && typeof root.getElementById === "function"
              ? root.getElementById(token)
              : document.getElementById(token);
          return target ? normalizeText(target.textContent) : "";
        })
        .filter(Boolean);
      if (parts.length) return parts.join(" ");
    }
    return "";
  };

  const accessibleName = (element) => {
    const labelled = labelText(element);
    if (labelled) return truncate(labelled, 80);

    if (element.tagName.toLowerCase() === "input") {
      const type = (element.getAttribute("type") || "text").toLowerCase();
      if (["button", "submit", "reset"].includes(type)) {
        const value = element.getAttribute("value");
        if (value) return truncate(value, 80);
      }
      const placeholder = element.getAttribute("placeholder");
      if (placeholder) return truncate(placeholder, 80);
    }

    const alt = element.getAttribute("alt");
    if (alt) return truncate(alt, 80);
    const title = element.getAttribute("title");
    if (title) return truncate(title, 80);

    const content = normalizeText(element.textContent);
    if (content) return truncate(content, 80);

    const value = element.value;
    if (typeof value === "string" && value.trim()) {
      return truncate(value, 80);
    }
    return "";
  };

  const isVisible = (element) => {
    if (!element || !element.isConnected) return false;
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;

    const style = window.getComputedStyle(element);
    if (
      !style ||
      style.display === "none" ||
      style.visibility === "hidden" ||
      Number(style.opacity) === 0
    ) {
      return false;
    }
    if (element.closest && element.closest("[hidden]")) return false;
    if (element.getAttribute("aria-hidden") === "true") return false;
    return true;
  };

  const isRelated = (a, b) => {
    if (!a || !b) return false;
    if (a === b || a.contains(b) || b.contains(a)) return true;
    const root = b.getRootNode ? b.getRootNode() : null;
    const host = root && root.host ? root.host : null;
    return host ? isRelated(a, host) : false;
  };

  const isObscured = (element) => {
    const rect = element.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    if (
      x < 0 ||
      y < 0 ||
      x > window.innerWidth ||
      y > window.innerHeight
    ) {
      return null;
    }
    const top = document.elementFromPoint(x, y);
    if (!top) return null;
    return !isRelated(top, element);
  };

  const isEnabled = (element) => {
    if (element.disabled) return false;
    if (element.getAttribute("aria-disabled") === "true") return false;
    if (element.closest && element.closest("[inert]")) return false;
    return true;
  };

  const cssPath = (element) => {
    const id = element.getAttribute("id");
    if (id) {
      const idSelector = `#${cssEscape(id)}`;
      if (countMatches(element, idSelector) === 1) return idSelector;
    }

    const parts = [];
    let node = element;
    let depth = 0;
    while (node && node.nodeType === 1 && depth < 6) {
      let selector = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(
          (child) => child.tagName === node.tagName
        );
        if (siblings.length > 1) {
          selector += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        }
      }
      parts.unshift(selector);
      const candidate = parts.join(" > ");
      if (countMatches(element, candidate) === 1) return candidate;
      node = parent;
      depth += 1;
    }
    return parts.join(" > ");
  };

  const xpath = (element) => {
    const id = element.getAttribute("id");
    if (
      id &&
      rootOf(element) === document &&
      countMatches(element, `#${cssEscape(id)}`) === 1
    ) {
      return `//*[@id="${attrEscape(id)}"]`;
    }
    const parts = [];
    let node = element;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let index = 1;
      let sibling = node.previousElementSibling;
      while (sibling) {
        if (sibling.tagName === node.tagName) index += 1;
        sibling = sibling.previousElementSibling;
      }
      parts.unshift(`${node.tagName.toLowerCase()}[${index}]`);
      node = node.parentElement;
    }
    return "/" + parts.join("/");
  };

  const countSameRoleAndName = (element, role, name) => {
    try {
      return Array.from(rootOf(element).querySelectorAll("*")).filter(
        (other) =>
          implicitRole(other) === role && accessibleName(other) === name
      ).length;
    } catch (error) {
      return 0;
    }
  };

  const countSameLabel = (element, text) => {
    try {
      return Array.from(
        rootOf(element).querySelectorAll("input,select,textarea")
      ).filter((other) => labelText(other) === text).length;
    } catch (error) {
      return 0;
    }
  };

  const countSamePlaceholder = (element, text) => {
    try {
      return Array.from(
        rootOf(element).querySelectorAll("[placeholder]")
      ).filter(
        (other) => normalizeText(other.getAttribute("placeholder")) === text
      ).length;
    } catch (error) {
      return 0;
    }
  };

  const countSameText = (element, role, text) => {
    if (!text) return 0;
    try {
      return Array.from(rootOf(element).querySelectorAll("*")).filter(
        (other) =>
          implicitRole(other) === role &&
          truncate(other.textContent, 60) === text
      ).length;
    } catch (error) {
      return 0;
    }
  };

  const countXPath = (element, expression) => {
    if (rootOf(element) !== document) return 0;
    try {
      const result = document.evaluate(
        expression,
        document,
        null,
        XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,
        null
      );
      return result.snapshotLength;
    } catch (error) {
      return 0;
    }
  };

  const scoreFor = (strategy, count) => {
    const base = STRATEGY_SCORE[strategy] || 30;
    if (count === 1) return base;
    if (count <= 0) return Math.max(1, base - 35);
    return Math.max(1, base - 45);
  };

  const addLocator = (locators, strategy, value, extra) => {
    if (value == null || value === "") return;
    const options = extra || {};
    const count =
      typeof options.count === "number" ? options.count : 0;
    const locator = {
      strategy: strategy,
      value: truncate(value, 160),
      name: options.name ? truncate(options.name, 80) : null,
      nth: null,
      exact: options.exact !== false,
      frame: frameLabel,
      count: count,
      unique: count === 1,
      score: scoreFor(strategy, count),
    };
    locators.push(locator);
  };

  const buildLocators = (element) => {
    const role = implicitRole(element);
    const name = accessibleName(element);
    const text = truncate(element.textContent, 60);
    const locators = [];

    addLocator(locators, "role", role, {
      name: name,
      count: countSameRoleAndName(element, role, name),
    });

    const labelled = labelText(element);
    if (labelled) {
      addLocator(locators, "label", labelled, {
        count: countSameLabel(element, labelled),
      });
    }

    const placeholder = element.getAttribute("placeholder");
    if (placeholder) {
      addLocator(locators, "placeholder", placeholder, {
        count: countSamePlaceholder(
          element,
          normalizeText(placeholder)
        ),
      });
    }

    const testId =
      element.getAttribute("data-testid") ||
      element.getAttribute("data-test") ||
      element.getAttribute("data-cy");
    if (testId) {
      addLocator(locators, "testid", testId, {
        count: countMatches(
          element,
          `[data-testid="${attrEscape(testId)}"],` +
            `[data-test="${attrEscape(testId)}"],` +
            `[data-cy="${attrEscape(testId)}"]`
        ),
      });
    }

    if (TEXT_ROLES.has(role) && text) {
      addLocator(locators, "text", text, {
        count: countSameText(element, role, text),
      });
    }

    const selector = cssPath(element);
    if (selector) {
      addLocator(locators, "css", selector, {
        count: countMatches(element, selector),
      });
    }

    const expression = xpath(element);
    if (expression) {
      addLocator(locators, "xpath", expression, {
        count: countXPath(element, expression),
      });
    }

    return locators.sort((a, b) => b.score - a.score);
  };

  const describe = (element) => {
    const rect = element.getBoundingClientRect();
    const tag = element.tagName.toLowerCase();
    const type =
      (element.getAttribute("type") ||
        (tag === "input" ? "text" : "")) ||
      "";
    const visible = isVisible(element);
    return {
      tag: tag,
      role: implicitRole(element),
      name: accessibleName(element),
      text: truncate(element.textContent, 120),
      value:
        "value" in element ? truncate(element.value, 120) : null,
      placeholder: element.getAttribute("placeholder"),
      input_type: type.toLowerCase() || null,
      id: element.getAttribute("id"),
      test_id:
        element.getAttribute("data-testid") ||
        element.getAttribute("data-test") ||
        element.getAttribute("data-cy"),
      name_attr: element.getAttribute("name"),
      href: element.getAttribute("href"),
      aria_label: element.getAttribute("aria-label"),
      checked:
        "checked" in element ? Boolean(element.checked) : null,
      required: element.hasAttribute("required"),
      visible: visible,
      enabled: isEnabled(element),
      obscured: visible ? isObscured(element) : null,
      bounds: {
        x: Math.round(rect.left * 100) / 100,
        y: Math.round(rect.top * 100) / 100,
        width: Math.round(rect.width * 100) / 100,
        height: Math.round(rect.height * 100) / 100,
      },
      frame: frameLabel,
    };
  };

  const roots = [];
  const shadowHosts = [];
  const collectRoots = (root) => {
    roots.push(root);
    if (!root || !root.querySelectorAll) return;
    for (const node of root.querySelectorAll("*")) {
      if (!node.shadowRoot) continue;
      shadowHosts.push({
        tag: node.tagName.toLowerCase(),
        id: node.getAttribute("id"),
        name: accessibleName(node),
      });
      collectRoots(node.shadowRoot);
    }
  };
  collectRoots(document);

  const seen = new Set();
  const candidates = [];
  for (const root of roots) {
    for (const element of root.querySelectorAll(INTERACTIVE_SELECTOR)) {
      if (seen.has(element)) continue;
      seen.add(element);
      candidates.push(element);
    }
  }

  const described = candidates.map(describe);
  const order = described
    .map((item, index) => index)
    .sort((a, b) => Number(described[b].visible) - Number(described[a].visible));
  const limited = order.slice(0, maxElements);
  const elements = limited.map((index) => {
    const descriptor = described[index];
    const locators = buildLocators(candidates[index]);
    return {
      ...descriptor,
      locators: locators,
      best_locator: locators.length ? locators[0] : null,
    };
  });

  const forms = Array.from(document.querySelectorAll("form"))
    .slice(0, 20)
    .map((form) => ({
      action: form.getAttribute("action"),
      method: (form.getAttribute("method") || "get").toLowerCase(),
      field_count: form.querySelectorAll("input,select,textarea").length,
    }));

  const tables = Array.from(document.querySelectorAll("table"))
    .slice(0, 20)
    .map((table) => {
      const rows = table.querySelectorAll("tr");
      const firstRow = rows.length ? rows[0] : null;
      return {
        rows: rows.length,
        columns: firstRow
          ? firstRow.querySelectorAll("th,td").length
          : 0,
        caption: truncate(
          table.querySelector("caption")
            ? table.querySelector("caption").textContent
            : "",
          80
        ),
        visible: isVisible(table),
      };
    });

  const dialogs = Array.from(
    document.querySelectorAll(
      "dialog[open], [role='dialog'], [role='alertdialog']"
    )
  )
    .slice(0, 10)
    .map((dialog) => ({
      role: dialog.getAttribute("role") || "dialog",
      name: accessibleName(dialog),
      visible: isVisible(dialog),
    }));

  const bodyText = normalizeText(
    document.body ? document.body.innerText : ""
  ).toLowerCase();
  const markers = LOGIN_MARKERS.filter((marker) =>
    bodyText.includes(marker.toLowerCase())
  );
  const passwordInputs = document.querySelectorAll(
    "input[type='password']"
  ).length;

  return {
    url: location.href,
    title: document.title,
    ready_state: document.readyState,
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      device_pixel_ratio: window.devicePixelRatio,
    },
    forms: forms,
    tables: tables,
    dialogs: dialogs,
    login: {
      has_password_input: passwordInputs > 0,
      password_input_count: passwordInputs,
      markers: markers.slice(0, 8),
      likely_login_page: passwordInputs > 0 && markers.length > 0,
    },
    shadow_dom: {
      host_count: shadowHosts.length,
      hosts: shadowHosts.slice(0, 10),
    },
    interactive_count: candidates.length,
    elements: elements,
    truncated: candidates.length > elements.length,
  };
}
"""


class BrowserObserver:
    """打开页面并采集一次完整观察快照。"""

    def __init__(self, config: Settings):
        self.config = config
        self.runner = PlaywrightRunner(config)

    @staticmethod
    def validate_url(url: str) -> str:
        candidate = str(url or "").strip()
        if not candidate:
            raise ObservationError("url 不能为空")
        parsed = urlparse(candidate)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ObservationError(
                "只支持 http 或 https 地址，拒绝访问："
                f"{parsed.scheme or '<empty>'}://"
            )
        if not parsed.netloc:
            raise ObservationError(f"无效的页面地址：{candidate}")
        return candidate

    async def observe(
        self,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        timeout_ms: int | None = None,
        max_elements: int = 80,
        include_accessibility: bool = True,
    ) -> ObservationSnapshot:
        target_url = self.validate_url(url)
        wait_condition = str(wait_until or "").strip().lower()
        if wait_condition not in OBSERVATION_WAIT_UNTIL:
            raise ObservationError(
                "wait_until 只能是 "
                + ", ".join(sorted(OBSERVATION_WAIT_UNTIL))
            )
        timeout = int(
            timeout_ms
            if timeout_ms is not None
            else DEFAULT_OBSERVATION_TIMEOUT_MS
        )
        if timeout < 1:
            raise ObservationError("timeout_ms 必须大于 0")
        limit = max(1, min(300, int(max_elements)))
        started_at = time.perf_counter()

        try:
            async with async_playwright() as playwright:
                browser, context = await self.runner.launch_browser(playwright)
                try:
                    page = await context.new_page()
                    await page.goto(
                        target_url,
                        wait_until=wait_condition,
                        timeout=timeout,
                    )
                    snapshot = await self.observe_page(
                        page,
                        max_elements=limit,
                        include_accessibility=include_accessibility,
                    )
                finally:
                    await self._close(browser, context)
        except ObservationError:
            raise
        except Exception as exc:
            raise ObservationError(
                str(exc) or exc.__class__.__name__
            ) from exc

        snapshot.payload["duration_seconds"] = round(
            time.perf_counter() - started_at,
            3,
        )
        return snapshot

    async def observe_page(
        self,
        page: Any,
        *,
        max_elements: int = 80,
        include_accessibility: bool = True,
    ) -> ObservationSnapshot:
        """对已经打开的页面做一次观察，用于失败后的步骤修复。"""

        limit = max(1, min(300, int(max_elements)))
        try:
            payload = await self._collect(
                page,
                max_elements=limit,
                include_accessibility=include_accessibility,
            )
        except ObservationError:
            raise
        except Exception as exc:
            raise ObservationError(
                str(exc) or exc.__class__.__name__
            ) from exc
        return self._snapshot_from_payload(
            payload,
            fallback_url=str(getattr(page, "url", "") or ""),
        )

    @staticmethod
    def _snapshot_from_payload(
        payload: dict[str, Any],
        fallback_url: str = "",
    ) -> ObservationSnapshot:
        return ObservationSnapshot(
            kind=ObservationKind.BROWSER,
            source="playwright",
            url=str(payload.get("url") or fallback_url),
            title=str(payload.get("title") or "") or None,
            summary=build_observation_summary(payload),
            elements=list(payload.get("elements") or []),
            accessibility=dict(payload.get("accessibility") or {}),
            payload=payload,
            truncated=bool(payload.get("truncated")),
        )

    async def _collect(
        self,
        page: Any,
        *,
        max_elements: int,
        include_accessibility: bool,
    ) -> dict[str, Any]:
        main_payload = await page.evaluate(
            OBSERVATION_SCRIPT,
            {"maxElements": max_elements},
        )
        if not isinstance(main_payload, dict):
            raise ObservationError("页面观察脚本没有返回有效结果")

        elements = list(main_payload.get("elements") or [])
        truncated = bool(main_payload.get("truncated"))

        frames = list(getattr(page, "frames", []) or [])
        frames_meta: list[dict[str, Any]] = []
        frame_login_detected = bool(
            (main_payload.get("login") or {}).get("likely_login_page")
        )
        frame_shadow_hosts = 0

        for index, frame in enumerate(frames):
            is_main = getattr(frame, "parent_frame", None) is None
            frames_meta.append(
                {
                    "name": str(getattr(frame, "name", "") or ""),
                    "url": str(getattr(frame, "url", "") or ""),
                    "is_main": is_main,
                }
            )
            if is_main or index > MAX_FRAMES:
                continue
            remaining = max(0, max_elements - len(elements))
            if remaining == 0:
                truncated = True
                continue
            label = (
                str(getattr(frame, "name", "") or "").strip()
                or f"frame_{index}"
            )
            try:
                frame_payload = await frame.evaluate(
                    OBSERVATION_SCRIPT,
                    {
                        "maxElements": remaining,
                        "frameLabel": label,
                    },
                )
            except Exception:
                logger.debug(
                    "Failed to observe frame %s",
                    label,
                    exc_info=True,
                )
                continue
            if not isinstance(frame_payload, dict):
                continue

            frame_elements = list(frame_payload.get("elements") or [])
            for element in frame_elements:
                if isinstance(element, dict):
                    element["frame"] = label
            elements.extend(frame_elements)
            truncated = (
                truncated or bool(frame_payload.get("truncated"))
            )
            frame_login_detected = frame_login_detected or bool(
                (frame_payload.get("login") or {}).get(
                    "likely_login_page"
                )
            )
            shadow = frame_payload.get("shadow_dom") or {}
            frame_shadow_hosts += int(shadow.get("host_count") or 0)

        if len(elements) > max_elements:
            elements = elements[:max_elements]
            truncated = True

        accessibility_nodes: list[dict[str, Any]] = []
        if include_accessibility:
            tree = None
            try:
                accessibility = getattr(page, "accessibility", None)
                if accessibility is not None:
                    tree = await accessibility.snapshot(
                        interesting_only=True
                    )
            except Exception:
                logger.debug(
                    "Accessibility snapshot failed",
                    exc_info=True,
                )
                tree = None
            accessibility_nodes = flatten_accessibility_tree(tree)

        main_payload["elements"] = elements
        main_payload["truncated"] = truncated
        main_payload["frames"] = frames_meta
        login_payload = main_payload.setdefault("login", {})
        login_payload["likely_login_page"] = frame_login_detected
        shadow_payload = main_payload.setdefault("shadow_dom", {})
        shadow_payload["host_count"] = (
            int(shadow_payload.get("host_count") or 0)
            + frame_shadow_hosts
        )
        main_payload["accessibility"] = {
            "nodes": accessibility_nodes,
            "node_count": len(accessibility_nodes),
            "role_counts": dict(
                Counter(
                    node.get("role")
                    for node in accessibility_nodes
                    if node.get("role")
                )
            ),
        }
        return main_payload

    @staticmethod
    async def _close(browser: Any, context: Any) -> None:
        if context is not None:
            with suppress(Exception):
                await context.close()
        if browser is not None:
            with suppress(Exception):
                await browser.close()


def flatten_accessibility_tree(
    tree: Any,
    *,
    limit: int = MAX_ACCESSIBILITY_NODES,
) -> list[dict[str, Any]]:
    """把 Playwright 可访问性树压平成有限节点列表。"""

    nodes: list[dict[str, Any]] = []

    def visit(node: Any, depth: int) -> None:
        if len(nodes) >= limit or not isinstance(node, dict):
            return
        role = str(node.get("role") or "").strip()
        name = str(node.get("name") or "").strip()
        if role or name:
            record: dict[str, Any] = {
                "depth": depth,
                "role": role or None,
                "name": name or None,
            }
            for key in (
                "value",
                "checked",
                "disabled",
                "expanded",
                "level",
                "selected",
            ):
                value = node.get(key)
                if value is not None:
                    record[key] = value
            nodes.append(record)
        children = node.get("children")
        if isinstance(children, list):
            for child in children:
                visit(child, depth + 1)

    visit(tree, 0)
    return nodes


def build_observation_summary(payload: dict[str, Any]) -> str:
    """生成一行人类可读的观察摘要。"""

    parts: list[str] = []
    title = str(payload.get("title") or "").strip()
    if title:
        parts.append(f"标题：{title}")
    parts.append(
        f"可交互元素 {int(payload.get('interactive_count') or 0)} 个"
    )

    forms = payload.get("forms")
    if isinstance(forms, list) and forms:
        parts.append(f"表单 {len(forms)} 个")
    tables = payload.get("tables")
    if isinstance(tables, list) and tables:
        parts.append(f"表格 {len(tables)} 个")
    dialogs = payload.get("dialogs")
    if isinstance(dialogs, list) and dialogs:
        parts.append(f"弹窗 {len(dialogs)} 个")
    shadow = payload.get("shadow_dom")
    if isinstance(shadow, dict) and int(shadow.get("host_count") or 0):
        parts.append(f"Shadow DOM 宿主 {int(shadow['host_count'])} 个")
    frames = payload.get("frames")
    if isinstance(frames, list) and len(frames) > 1:
        parts.append(f"iframe {len(frames) - 1} 个")
    if bool((payload.get("login") or {}).get("likely_login_page")):
        parts.append("检测到疑似登录页面")
    if payload.get("truncated"):
        parts.append("结果已截断")
    return "；".join(parts)


__all__ = [
    "MAX_FRAMES",
    "OBSERVATION_SCRIPT",
    "BrowserObserver",
    "ObservationError",
    "build_observation_summary",
    "flatten_accessibility_tree",
]
