"""Read-only Windows installed application discovery and icon rendering."""

from __future__ import annotations

import hashlib
import io
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_ICON_INDEX_RE = re.compile(r",\s*-?\d+\s*$")
_BAD_EXECUTABLE_RE = re.compile(
    r"(?i)(?:^|[-_.\s])(?:unins\d*|uninstall|updater?|crash|reporter)(?:$|[-_.\s])"
)
_SOURCE_PRIORITY = {
    "start_menu": 30,
    "desktop": 30,
    "app_paths": 20,
    "uninstall_registry": 10,
}
_INSTALLED_APPS_TTL_SECONDS = 60.0
_installed_apps_cache: (
    tuple[float, tuple["InstalledApp", ...]] | None
) = None
_installed_apps_lock = threading.Lock()
_icon_cache: dict[str, bytes] = {}


@dataclass(frozen=True)
class AppCandidate:
    name: str
    executable_path: str | Path
    source: str


@dataclass(frozen=True)
class InstalledApp:
    key: str
    name: str
    executable_path: str
    source: str


def normalize_executable_path(value: str | Path) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith('"'):
        closing = text.find('"', 1)
        if closing < 0:
            return None
        text = text[1:closing]
    else:
        text = _ICON_INDEX_RE.sub("", text).strip().strip('"')
    text = os.path.expandvars(text)
    try:
        path = Path(text).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None
    if path.suffix.casefold() != ".exe" or not path.is_file():
        return None
    if _BAD_EXECUTABLE_RE.search(path.stem):
        return None
    return path


def app_key_for_path(executable_path: str | Path) -> str:
    normalized = normalize_executable_path(executable_path)
    if normalized is None:
        normalized = Path(executable_path).expanduser().resolve(strict=False)
    return hashlib.sha256(
        str(normalized).casefold().encode("utf-8")
    ).hexdigest()


def merge_installed_app_candidates(
    candidates: list[AppCandidate] | tuple[AppCandidate, ...],
) -> list[InstalledApp]:
    selected: dict[str, tuple[tuple[int, int, str], InstalledApp]] = {}
    for candidate in candidates:
        path = normalize_executable_path(candidate.executable_path)
        if path is None:
            continue
        name = str(candidate.name or path.stem).strip() or path.stem
        source = str(candidate.source or "unknown")
        key = app_key_for_path(path)
        rank = (
            _SOURCE_PRIORITY.get(source, 0),
            len(name),
            name.casefold(),
        )
        app = InstalledApp(
            key=key,
            name=name,
            executable_path=str(path),
            source=source,
        )
        current = selected.get(key)
        if current is None or rank > current[0]:
            selected[key] = (rank, app)
    return sorted(
        (item[1] for item in selected.values()),
        key=lambda app: (app.name.casefold(), app.executable_path.casefold()),
    )


def discover_installed_apps() -> list[InstalledApp]:
    if os.name != "nt":
        return []
    candidates: list[AppCandidate] = []
    candidates.extend(_collect_app_path_candidates())
    candidates.extend(_collect_uninstall_candidates())
    candidates.extend(_collect_shortcut_candidates())
    return merge_installed_app_candidates(candidates)


def cached_installed_apps(
    refresh: bool = False,
) -> tuple[InstalledApp, ...]:
    global _installed_apps_cache
    now = time.monotonic()
    with _installed_apps_lock:
        cached = _installed_apps_cache
        if (
            not refresh
            and cached is not None
            and now - cached[0] <= _INSTALLED_APPS_TTL_SECONDS
        ):
            return cached[1]
        discovered = tuple(discover_installed_apps())
        _installed_apps_cache = (now, discovered)
        return discovered


def cached_installed_app(app_key: str) -> InstalledApp | None:
    target = str(app_key or "").strip().lower()
    return next(
        (
            app
            for app in cached_installed_apps()
            if app.key == target
        ),
        None,
    )


def cached_app_icon(
    app_key: str,
    executable_path: str,
) -> bytes:
    with _installed_apps_lock:
        cached = _icon_cache.get(app_key)
    if cached is not None:
        return cached
    rendered = render_app_icon_png(executable_path)
    with _installed_apps_lock:
        _icon_cache[app_key] = rendered
    return rendered


def _collect_app_path_candidates() -> list[AppCandidate]:
    try:
        import winreg
    except ImportError:
        return []

    subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    candidates: list[AppCandidate] = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            root = winreg.OpenKey(hive, subkey)
        except OSError:
            continue
        with root:
            index = 0
            while True:
                try:
                    child_name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(root, child_name) as child:
                        value, _ = winreg.QueryValueEx(child, "")
                except OSError:
                    continue
                candidates.append(
                    AppCandidate(
                        name=Path(child_name).stem,
                        executable_path=str(value),
                        source="app_paths",
                    )
                )
    return candidates


def _collect_uninstall_candidates() -> list[AppCandidate]:
    try:
        import winreg
    except ImportError:
        return []

    subkeys = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    )
    candidates: list[AppCandidate] = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for subkey in subkeys:
            try:
                root = winreg.OpenKey(hive, subkey)
            except OSError:
                continue
            with root:
                index = 0
                while True:
                    try:
                        child_name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(root, child_name) as child:
                            display_name = _registry_string(
                                child,
                                "DisplayName",
                            )
                            display_icon = _registry_string(
                                child,
                                "DisplayIcon",
                            )
                    except OSError:
                        continue
                    if not display_name or not display_icon:
                        continue
                    candidates.append(
                        AppCandidate(
                            name=display_name,
                            executable_path=display_icon,
                            source="uninstall_registry",
                        )
                    )
    return candidates


def _registry_string(key, value_name: str) -> str:
    try:
        value, _ = key.QueryValueEx(value_name)
    except (AttributeError, OSError):
        try:
            import winreg

            value, _ = winreg.QueryValueEx(key, value_name)
        except OSError:
            return ""
    return str(value).strip() if isinstance(value, str) else ""


def _collect_shortcut_candidates() -> list[AppCandidate]:
    roots = _shortcut_roots()
    if not roots:
        return []
    try:
        import comtypes.client
    except ImportError:
        return []

    candidates: list[AppCandidate] = []
    shell = comtypes.client.CreateObject(
        "WScript.Shell",
        dynamic=True,
    )
    for root, source in roots:
        if not root.is_dir():
            continue
        for shortcut_path in root.rglob("*.lnk"):
            try:
                shortcut = shell.CreateShortcut(str(shortcut_path))
                target = str(shortcut.TargetPath or "").strip()
            except Exception:
                continue
            if not target:
                continue
            name = re.sub(
                r"(?i)\s*-\s*shortcut$",
                "",
                shortcut_path.stem,
            ).strip()
            candidates.append(
                AppCandidate(
                    name=name or shortcut_path.stem,
                    executable_path=target,
                    source=source,
                )
            )
    return candidates


def _shortcut_roots() -> list[tuple[Path, str]]:
    roots: list[tuple[Path, str]] = []
    appdata = os.getenv("APPDATA")
    programdata = os.getenv("PROGRAMDATA")
    userprofile = os.getenv("USERPROFILE")
    if appdata:
        roots.append(
            (
                Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
                "start_menu",
            )
        )
    if programdata:
        roots.append(
            (
                Path(programdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
                "start_menu",
            )
        )
    if userprofile:
        roots.append((Path(userprofile) / "Desktop", "desktop"))
    public = os.getenv("PUBLIC")
    if public:
        roots.append((Path(public) / "Desktop", "desktop"))
    return roots


def render_app_icon_png(
    executable_path: str | Path,
    *,
    size: int = 64,
) -> bytes:
    if os.name != "nt":
        raise OSError("Application icons are only available on Windows.")
    normalized = normalize_executable_path(executable_path)
    if normalized is None:
        raise OSError("Application executable is unavailable.")

    import win32gui
    import win32ui
    from PIL import Image

    large, small = win32gui.ExtractIconEx(str(normalized), 0, 1)
    icons = [*large, *small]
    if not icons:
        raise OSError("Application has no extractable icon.")
    hicon = icons[0]
    hbm_mask = None
    hbm_color = None
    try:
        _reserved, _hotspot_x, _hotspot_y, hbm_mask, hbm_color = (
            win32gui.GetIconInfo(hicon)
        )
        bitmap = win32ui.CreateBitmapFromHandle(hbm_color or hbm_mask)
        details = bitmap.GetInfo()
        bits = bitmap.GetBitmapBits(True)
        image = Image.frombuffer(
            "RGBA",
            (details["bmWidth"], details["bmHeight"]),
            bits,
            "raw",
            "BGRA",
            0,
            1,
        )
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        image.thumbnail((size, size), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()
    finally:
        for icon in icons:
            try:
                win32gui.DestroyIcon(icon)
            except Exception:
                pass
        for handle in (hbm_mask, hbm_color):
            if handle:
                try:
                    win32gui.DeleteObject(handle)
                except Exception:
                    pass


__all__ = [
    "AppCandidate",
    "InstalledApp",
    "app_key_for_path",
    "cached_app_icon",
    "cached_installed_app",
    "cached_installed_apps",
    "discover_installed_apps",
    "merge_installed_app_candidates",
    "normalize_executable_path",
    "render_app_icon_png",
]
