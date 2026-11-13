import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BINARIES_DIR = ROOT / "desktop" / "src-tauri" / "binaries"


def _option_value(args: list[str], option: str) -> str:
    index = args.index(option)
    return args[index + 1]


def _add_data(args: list[str]) -> list[tuple[Path, str]]:
    entries: list[tuple[Path, str]] = []
    prefix = "--add-data="
    for value in args:
        if not value.startswith(prefix):
            continue
        source, destination = value[len(prefix):].rsplit(os.pathsep, 1)
        entries.append((Path(source), destination))
    return entries


def test_backend_pyinstaller_arguments() -> None:
    from scripts import build_backend

    args = build_backend.pyinstaller_args()

    assert str(ROOT / "app" / "main.py") in args
    assert "--onefile" in args
    assert "--name=backend-server" in args
    assert _option_value(args, "--distpath") == str(BINARIES_DIR)
    assert any(
        value.startswith("--add-data=")
        and "docker" in value
        for value in args
    )
    assert any(
        value.startswith("--add-data=")
        and "ms-playwright" in value
        for value in args
    )
    for package in (
        "uvicorn",
        "sqlalchemy",
        "aiosqlite",
        "app.models",
        "app.engine",
        "app.agents",
        "app.scheduler",
        "app.notifications",
    ):
        assert f"--hidden-import={package}" in args


def test_backend_bundles_builtin_apps_without_pycache() -> None:
    from scripts import build_backend

    args = build_backend.pyinstaller_args()
    app_entries = [
        (source, destination)
        for source, destination in _add_data(args)
        if destination == "mcp_apps"
        or destination.startswith("mcp_apps/")
        or destination.startswith("mcp_apps\\")
    ]
    sources = {source for source, _destination in app_entries}

    assert ROOT / "mcp_apps" / "demo_app" / "manifest.yaml" in sources
    assert ROOT / "mcp_apps" / "demo_app" / "adapter.py" in sources
    assert ROOT / "mcp_apps" / "generic_desktop" / "manifest.yaml" in sources
    assert ROOT / "mcp_apps" / "generic_desktop" / "adapter.py" in sources
    assert all("__pycache__" not in str(source) for source in sources)
    assert (
        "--hidden-import=app.engine.app_mcp.process_main"
        in args
    )
    for module in (
        "app.engine.app_mcp.backends",
        "app.engine.app_mcp.backends.api",
        "app.engine.app_mcp.backends.browser",
        "app.engine.app_mcp.backends.cli",
        "app.engine.app_mcp.backends.desktop",
    ):
        assert f"--hidden-import={module}" in args
    for module in (
        "app.engine.installed_apps",
        "app.engine.installed_app_mcp",
        "app.engine.app_mcp.scoped_desktop",
        "comtypes.client",
        "win32gui",
        "win32ui",
        "PIL.Image",
    ):
        assert f"--hidden-import={module}" in args
    assert "--exclude-module=PIL" not in args
    assert "--exclude-module=win32com" in args


def test_backend_bundles_application_profiles() -> None:
    from scripts import build_backend

    args = build_backend.pyinstaller_args()
    entries = _add_data(args)
    profile_entries = [
        (source, destination)
        for source, destination in entries
        if destination == "desktop_agent/profiles"
    ]

    assert profile_entries
    assert any(source.name == "kugou.json" for source, _ in profile_entries)


def test_agent_pyinstaller_arguments() -> None:
    from scripts import build_agent

    args = build_agent.pyinstaller_args()

    assert str(ROOT / "desktop_agent" / "main.py") in args
    assert "--onefile" in args
    assert "--name=desktop-agent" in args
    assert _option_value(args, "--distpath") == str(BINARIES_DIR)
    for module in (
        "websockets",
        "pywinauto",
        "win32gui",
        "win32api",
        "win32con",
        "PIL",
    ):
        assert f"--hidden-import={module}" in args


def test_build_all_plan_runs_required_steps_in_order() -> None:
    from scripts import build_all

    plan = build_all.build_plan()
    commands = [step.command for step in plan]

    assert commands[0] == [
        build_all.sys.executable,
        "-m",
        "pip",
        "install",
        "-r",
        str(ROOT / "requirements.txt"),
    ]
    assert commands[1] == [
        build_all.sys.executable,
        "-m",
        "pip",
        "install",
        "-r",
        str(ROOT / "desktop_agent" / "requirements.txt"),
    ]
    assert commands[2] == [
        build_all.sys.executable,
        str(ROOT / "scripts" / "build_backend.py"),
    ]
    assert commands[3] == [
        build_all.sys.executable,
        str(ROOT / "scripts" / "build_agent.py"),
    ]
    assert commands[4][-1:] == ["install"]
    assert commands[5][-3:] == ["run", "tauri", "build"]
    assert plan[4].cwd == ROOT / "desktop"
    assert plan[5].cwd == ROOT / "desktop"
    environment = build_all.build_environment()
    assert environment["TAURI_BUNDLER_TOOLS_GITHUB_MIRROR"]


def test_desktop_dialog_permissions_allow_confirmation() -> None:
    capability_path = (
        ROOT / "desktop" / "src-tauri" / "capabilities" / "default.json"
    )
    capability = json.loads(capability_path.read_text(encoding="utf-8"))
    permissions = set(capability["permissions"])

    assert "dialog:default" in permissions
    assert "dialog:allow-confirm" in permissions


def test_backend_bundle_includes_stage5_modules_and_drivers() -> None:
    from scripts import build_backend

    args = build_backend.pyinstaller_args()
    for module in (
        "app.metrics",
        "app.ops",
        "app.security",
        "app.migrations",
        "asyncpg",
        "redis.asyncio",
        "mcp",
    ):
        assert f"--hidden-import={module}" in args
    for module in (
        "app.metrics",
        "app.ops",
        "app.security",
    ):
        assert f"--collect-submodules={module}" in args
    # 仅测试使用、且体积较大的可选依赖不打进桌面包。
    for module in ("app.testing", "embedded_postgres", "fakeredis"):
        assert f"--exclude-module={module}" in args
