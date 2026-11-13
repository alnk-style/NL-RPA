from __future__ import annotations

import asyncio
import ctypes
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image

from app.visual.redaction import redact_image_bytes
from desktop_agent.action_validator import ActionValidator
from desktop_agent.actions import get_action
from desktop_agent.actions._win32 import is_process_running, window_rect
from desktop_agent.config import AgentConfig

OUTPUT = ROOT / ".tmp" / "stage-g" / "desktop"
CHARMAP = Path(r"C:\Windows\System32\charmap.exe")


async def run_check() -> dict[str, Any]:
    if not CHARMAP.is_file():
        return {
            "status": "skipped",
            "reason": "charmap.exe is not available",
        }
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    OUTPUT.mkdir(parents=True, exist_ok=True)
    config = AgentConfig(
        server_url="http://127.0.0.1:8000",
        agent_id=1,
        token="acceptance-only",
        environment="local",
        capabilities=["interactive_desktop"],
        allowed_apps=["charmap.exe"],
        allowed_doc_dirs=[str(OUTPUT)],
        log_dir=str(OUTPUT / "logs"),
    )
    context: dict[str, Any] = {
        "task_id": "stage-g-desktop",
        "processes": {},
        "log_dir": str(OUTPUT / "logs"),
        "step_results": [],
    }
    results: dict[str, Any] = {}

    async def call(name: str, params: dict[str, Any]) -> Any:
        action = {"type": name, "params": params}
        validation = ActionValidator.validate(action, config)
        if not validation.allowed:
            raise RuntimeError(
                f"{name} rejected: {validation.reason}"
            )
        result = await get_action(name)(params, context)
        if result.pid is not None:
            context["last_pid"] = result.pid
        if result.window_handle is not None:
            context["last_window_handle"] = result.window_handle
        return result

    import win32con
    import win32gui

    process = None
    try:
        launch = await call(
            "launch_app",
            {
                "path": str(CHARMAP),
                "wait_for_window": True,
                "window_timeout": 10,
            },
        )
        process = context["last_process"]["process"]
        handle = int(context["last_window_handle"])
        pid = int(context["last_pid"])
        dpi = int(ctypes.windll.user32.GetDpiForWindow(handle))
        left, top, right, bottom = window_rect(handle)
        results["launch"] = {
            "success": bool(launch.success),
            "window_found": bool(launch.window_handle),
            "pid": pid,
            "handle": handle,
            "dpi": dpi,
            "scale_percent": round(dpi / 96 * 100),
            "window_rect": [left, top, right, bottom],
        }

        clicked = await call(
            "click_element",
            {
                "automation_id": "104",
                "control_type": "Edit",
            },
        )
        await call(
            "type_text",
            {
                "text": "NL-RPA",
                "clear_first": True,
                "method": "send_keys",
            },
        )
        read_before = await call(
            "read_window_text",
            {"min_chars": 1},
        )
        shot = await call(
            "screenshot",
            {
                "window": "last",
                "path": str(
                    OUTPUT / "desktop-window-before-click.png"
                ),
                "overwrite": True,
            },
        )
        image = Image.open(shot.screenshot_path)
        results["uia_and_shot"] = {
            "clicked": bool(clicked.success),
            "text_contains_marker": (
                "NL-RPA" in str(read_before.text or "")
            ),
            "screenshot_path": str(shot.screenshot_path),
            "screenshot_size": list(image.size),
            "matches_window_rect": (
                image.size == (right - left, bottom - top)
            ),
            "nonblank": (
                image.convert("RGB").getbbox() is not None
            ),
        }

        relative = await call(
            "click_relative",
            {"x": 0.5, "y": 0.5, "button": "left"},
        )
        await call(
            "click_element",
            {
                "automation_id": "104",
                "control_type": "Edit",
            },
        )
        await call(
            "type_text",
            {
                "text": "-CLICKED",
                "clear_first": False,
                "method": "send_keys",
            },
        )
        read_after = await call(
            "read_window_text",
            {"min_chars": 1},
        )
        results["controlled_click"] = {
            "inside_window": (
                left <= int(relative.data["x"]) <= right
                and top <= int(relative.data["y"]) <= bottom
            ),
            "text_contains_marker": (
                "CLICKED" in str(read_after.text or "")
            ),
        }

        win32gui.ShowWindow(handle, win32con.SW_MINIMIZE)
        time.sleep(0.5)
        minimized = bool(win32gui.IsIconic(handle))
        restored = await call("activate_window", {"pid": pid})
        time.sleep(0.5)
        results["minimize_restore"] = {
            "minimized": minimized,
            "selected_handle": restored.window_handle,
            "not_iconic": not bool(win32gui.IsIconic(handle)),
            "foreground": (
                int(win32gui.GetForegroundWindow()) == handle
            ),
        }

        missing = await call(
            "activate_window",
            {"title": "NL-RPA-DEFINITELY-MISSING-WINDOW"},
        )
        results["missing_window_error"] = {
            "success": bool(missing.success),
            "candidate_count": len(
                missing.data.get("candidates") or []
            ),
        }

        redacted = redact_image_bytes(
            Path(shot.screenshot_path).read_bytes(),
            rects=[(0, 0, image.width, image.height)],
            image_size=image.size,
        )
        redacted_path = OUTPUT / "desktop-window-redacted.png"
        redacted_path.write_bytes(redacted)
        redacted_image = Image.open(redacted_path).convert("RGB")
        results["redaction"] = {
            "path": str(redacted_path),
            "all_black": redacted_image.getbbox() is None,
        }

        closed = await call(
            "close_app",
            {"target": "last", "graceful_timeout": 2},
        )
        time.sleep(1.0)
        results["close"] = {
            "success": bool(closed.success),
            "process_running": is_process_running(pid),
        }
    finally:
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
        except Exception:
            pass

    checks = [
        results.get("launch", {}).get("window_found"),
        results.get("uia_and_shot", {}).get(
            "text_contains_marker"
        ),
        results.get("uia_and_shot", {}).get(
            "matches_window_rect"
        ),
        results.get("controlled_click", {}).get(
            "text_contains_marker"
        ),
        results.get("minimize_restore", {}).get("not_iconic"),
        results.get("minimize_restore", {}).get("foreground"),
        not results.get("missing_window_error", {}).get(
            "success",
            True,
        ),
        results.get("redaction", {}).get("all_black"),
        results.get("close", {}).get("success"),
        not results.get("close", {}).get("process_running", True),
    ]
    results["status"] = (
        "passed" if all(bool(item) for item in checks) else "failed"
    )
    return results


def main() -> int:
    try:
        results = asyncio.run(run_check())
    except Exception as exc:
        results = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = OUTPUT / "windows-desktop-acceptance.json"
    report.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"REPORT={report}")
    return 0 if results.get("status") == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
