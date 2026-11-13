# Installed Application MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox syntax for tracking.

**Goal:** Discover installed Windows applications and create persisted,
scoped, in-process MCP applications from the application center.

**Architecture:** Reuse installed application discovery, generate a manifest
and thin adapter under `~/.nl-rpa/apps`, synchronize the Desktop Agent allowlist
and start the generated app through `LocalAppManager`.

**Tech Stack:** FastAPI, SQLAlchemy, PyInstaller, React, TypeScript, Vitest,
Playwright.

**Spec:** `docs/superpowers/specs/2026-10-21-installed-app-mcp-design.md`

## Global Constraints

- Windows local mode only.
- No arbitrary executable path, command, working directory or adapter source
  from the frontend.
- No new runtime dependency.
- No automatic installer, download or MCP marketplace behavior.
- Generated files remain under `~/.nl-rpa/apps`.
- Each production change starts with a failing test.
- The workspace has no `.git`; do not run commit, reset or checkout commands.

---

### Task 1: Persistent Generated App Storage

**Files:**
- Modify: `runtime_paths.py`
- Create: `app/engine/installed_app_mcp.py`
- Test: `tests/test_installed_app_mcp.py`

**Interfaces:**
- Produces: `generated_apps_dir(data_dir: Path | None = None) -> Path`
- Produces: `GeneratedInstalledApp`
- Produces: `create_installed_app_package(app: InstalledApp, root=None)
  -> GeneratedInstalledApp`
- Produces: `load_generated_integration(app_dir: Path) -> dict | None`
- Produces: `remove_installed_app_package(app_id: str, root=None) -> bool`

- [ ] Write tests for deterministic app IDs, manifest/adapter creation,
  duplicate reuse, invalid IDs and path traversal rejection.
- [ ] Run `pytest tests/test_installed_app_mcp.py -q` and verify missing-module
  failures.
- [ ] Add `generated_apps_dir()` and implement atomic package generation using
  a temporary sibling directory followed by `replace`.
- [ ] Write `manifest.yaml`, `adapter.py` and `integration.json`; use
  `host_mode: in_process`, platform `windows`, connector `desktop` and tool set
  `app_launch`, `app_observe`, `app_activate`, `app_read_text`.
- [ ] Run the tests and verify all pass.

### Task 2: Scoped Desktop Adapter

**Files:**
- Create: `app/engine/app_mcp/scoped_desktop.py`
- Test: `tests/test_scoped_desktop_adapter.py`

**Interfaces:**
- Produces: `ScopedDesktopAdapter(environment: AdapterEnvironment)`
- Consumes manifest options: `connector`, `executable_path`
- Produces tool execution for `app_launch`, `app_observe`, `app_activate`,
  `app_read_text`

- [ ] Write failing tests with a fake connector proving `app_launch` always
  uses the configured executable and no model-supplied path.
- [ ] Implement probe, observe, execute and close using
  `DesktopBackend` and the shared connector.
- [ ] Verify launch, observe and read-text behavior and cross-app path
  rejection.

### Task 3: Multi-Root Local Manager

**Files:**
- Modify: `app/engine/app_mcp/local_manager.py`
- Test: `tests/app_mcp/test_local_manager.py`

**Interfaces:**
- Consumes: `generated_apps_dir()`
- Adds constructor argument `generated_apps_root: str | Path | None = None`
- Scans built-in apps first and generated apps second.

- [ ] Write a failing test that puts a generated package in a separate root and
  expects both packages to appear.
- [ ] Write a failing duplicate-ID test.
- [ ] Replace single-root scan with ordered multi-root scanning while preserving
  the existing missing-built-in-root error.
- [ ] Verify existing lifecycle, in-process connector and close-all tests.

### Task 4: Discovery And Creation API

**Files:**
- Modify: `app/api/agent_config.py`
- Modify: `app/api/local_apps.py`
- Modify: `app/schemas/agent.py`
- Test: `tests/test_agent_config_api.py`
- Test: `tests/app_mcp/test_generated_app_api.py`

**Interfaces:**
- Produces: public `add_app_to_owned_agent(...)` and
  `remove_app_from_owned_agent(...)`
- Produces: discoverable app list/icon routes using the shared cache.
- Produces: create route accepting `{ "agent_id": int }`.
- Produces: response `{ "app": LocalAppInfo, "generated": {...} }`.
- Produces: delete route accepting `remove_from_allowlist` and `agent_id`.

- [ ] Write failing API tests using a cached fake installed app and an in-memory
  SQLite agent.
- [ ] Resolve creation only from `cached_installed_app(app_key)`.
- [ ] Generate the package, update the owned agent allowlist, rescan and start
  the in-process app with the existing request context.
- [ ] On start failure, remove the generated package and restore the previous
  allowlist entry state.
- [ ] For deletion, stop and unbind the generated app, remove only its generated
  directory and optionally remove its path from the owned agent allowlist.
- [ ] Verify create, duplicate, invalid key, foreign agent, delete and traversal
  tests.

### Task 5: Application Center UI

**Files:**
- Create: `frontend/src/components/apps/InstalledAppMcpDialog.tsx`
- Create: `frontend/src/lib/installed-app-mcp.ts`
- Modify: `frontend/src/pages/AppsPage.tsx`
- Test: `tests/installed-app-mcp.test.ts`
- Modify: `desktop/vite.config.ts`

**Interfaces:**
- Consumes `GET /api/local-apps/discoverable-apps`
- Consumes create/delete routes from Task 4
- Displays icon, name, exe, duplicate state, agent selection, tool preview and
  create action.

- [ ] Write failing helper tests for search, duplicate mapping and icon URLs.
- [ ] Add a “从已安装软件创建” button and modal to the application center.
- [ ] Load discovered apps on open, search by name/path and show generated state.
- [ ] Show agent selection, generated tool names, risk labels and launch path
  preview before creation.
- [ ] After create, close the dialog, refresh apps and select the new card.
- [ ] Add delete control only for generated apps and require confirmation when
  removing the allowlist entry.
- [ ] Verify desktop and mobile rendering with Playwright.

### Task 6: Packaging And Installed Acceptance

**Files:**
- Modify: `scripts/build_backend.py`
- Test: `tests/test_build_scripts.py`

**Interfaces:**
- Bundles `app.engine.installed_app_mcp` and `app.engine.app_mcp.scoped_desktop`.

- [ ] Write failing PyInstaller argument assertions.
- [ ] Add hidden imports for the generated package and scoped adapter modules.
- [ ] Run the complete Python baseline and frontend tests.
- [ ] Build backend, Tauri release and NSIS.
- [ ] Upgrade the current-user installation.
- [ ] Verify installed discovery, icon, generated app creation, tools/list,
  tools/call, duplicate prevention, deletion and no orphan processes.
