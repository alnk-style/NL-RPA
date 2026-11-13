# App MCP Adapter Development Guide

## Scope

An App MCP integration consists of one directory under `mcp_apps/<app_id>`
with a validated `manifest.yaml` and `adapter.py`. The platform owns process
lifecycle, MCP transport, tool discovery, authorization, audit and planning
catalog integration. An adapter should only translate semantic application
operations into safe backend calls.

Only built-in, manifest-validated packages are started. The local manager
never executes an uploaded Python file or accepts a command, working
directory, bind address or port from a browser request.

## Backend Priority

Choose the narrowest control surface that can perform the task:

1. Official API
2. Official CLI with fixed executable and argument construction
3. Windows UI Automation through the existing Desktop Agent
4. Browser automation through the existing browser runtime
5. Visual coordinate operations only as an explicitly experimental fallback

Do not add a second desktop protocol. Desktop applications that require the
platform's existing Desktop Agent must use `DesktopBridgeConnector`.

## Manifest

Local hosting is explicit:

```yaml
local:
  host_mode: subprocess
  default_port: 8790
  auto_start: false
  startup_timeout_seconds: 15
```

`host_mode` accepts:

- `subprocess`: the platform starts the validated adapter in a separate
  Python process on a loopback port.
- `in_process`: the platform loads the adapter into the backend process and
  exposes it at `/api/local-apps/<app_id>/mcp` without a separate PID or port.

Desktop integrations that need the platform's existing Desktop Agent use
`in_process` and receive the shared `desktop` Connector. Their adapter
`close()` must not close that shared Connector. `scripts/run_app_mcp_server.py`
refuses to start an `in_process` package as a standalone process.

Each tool has a stable name, a description, a JSON Schema, a risk level and
an output description:

```yaml
tools:
  - name: records_search
    description: Search records by a user-visible query.
    input_schema:
      type: object
      properties:
        query:
          type: string
          maxLength: 200
      required: [query]
      additionalProperties: false
    risk_level: read_only
    output_description: Matching records and their stable identifiers.
```

Allowed risk levels are `read_only`, `mutating` and `destructive`.
Mutating tools require platform authorization. Destructive tools also require
explicit confirmation. The adapter must not bypass either decision.

Prefer semantic tools such as `invoice_export` over generic click and type
tools. A hidden implementation can use UIA internally while exposing a stable
business operation to the planner.

## Adapter Contract

Implement these methods:

```python
async def probe(self) -> AppProbe
async def observe(self) -> dict
async def execute(self, action: str, args: dict) -> ActionResult
async def close(self) -> None
```

`probe()` reports installed/running/login state. `observe()` returns a small,
stable observation. `execute()` must return `ActionResult`, not raise raw
implementation errors. Unknown tools and invalid arguments are handled by the
runtime.

Use `current_call_context()` for trusted platform metadata. Never trust a
model-supplied user ID, workflow ID or export path.

`probe()` should answer whether the executable or service is discoverable,
whether the process is running, whether login is required, and the detected
application version when available. `observe()` should return stable semantic
state such as documents, records, selected objects or window capability
information. Avoid returning a complete raw UIA tree by default.

## Export Safety

Write files only under `AdapterEnvironment.export_root`. Resolve every path and
verify it remains under that root before writing. Reject separators, `..`,
absolute paths and symlinks that escape the root. Return a `FileArtifact` so
the platform can expose the result through its artifact flow.

Never read arbitrary files, execute arbitrary shell commands, bypass DRM,
payment controls or CAPTCHAs, or delete arbitrary paths.

## Local Lifecycle

Create a starter package:

```powershell
.\.venv\Scripts\python.exe scripts\new_app_mcp.py \
  --app-id example_app \
  --name "Example App"
```

Edit the generated manifest and adapter, then run its test:

```powershell
.\.venv\Scripts\python.exe -m pytest mcp_apps\example_app\tests -q
```

Start one server directly during development:

```powershell
.\.venv\Scripts\python.exe scripts\run_app_mcp_server.py \
  --app example_app --host 127.0.0.1 --port 8790
```

The normal product flow is the application center:

- Scan `mcp_apps`
- Start, stop or restart the managed server
- Inspect health, PID, URL, last error and bounded logs
- Test the generated MCP Server and authorize its tools

Backend shutdown stops only child processes recorded by that backend instance.
The local manager never kills a process based on a PID supplied by a client.
The current verified platform is Windows. macOS and Linux are reported as
unsupported instead of entering an untested process-management path.

## Binding And Auto-Start

The first authenticated start creates the local-application binding in the
existing `MCPServer.config`:

```json
{
  "managed_by": "local_app_manager",
  "local_app_id": "example_app",
  "host_mode": "subprocess",
  "owner_user_id": 1,
  "workspace_id": null,
  "auto_start": false
}
```

Only that owner can stop, restart, unbind or change `auto_start`. Workspace
isolation must be enabled for workspace-specific bindings; otherwise the
workspace is intentionally `null`. Another user may view the application but
cannot take over the running service or silently replace its binding.

Backend startup only considers servers with a valid persisted owner,
`auto_start=true`, and the global `LOCAL_APP_AUTO_START_ENABLED` switch.
Unbound or damaged records are never started automatically.

## Testing

At minimum, test:

- Manifest loading and duplicate tool names
- Valid and invalid arguments for every tool
- Read-only and mutating behavior
- Unknown tools and adapter exception envelopes
- Export path traversal denial
- Fake backend contract behavior
- One MCP Client `tools/list` and `tools/call` round trip

Real applications should also have a capability-limit test proving that an
unsupported control surface returns a clear error instead of a false success.

## Real Application Checklist

Before accepting a real application adapter:

- Identify whether it exposes an official API, a supported CLI, Windows UIA,
  a browser surface, or only visual control. Use the first safe surface.
- Prove `probe()` on installed, running, stopped, unauthenticated and missing
  application states.
- Prove `observe()` returns stable identifiers and does not expose unrelated
  desktop content.
- Validate every tool against strict `additionalProperties: false` schemas.
- Test missing, extra, oversized and malformed arguments.
- Test `read_only`, `mutating` and `destructive` authorization behavior.
- Test export traversal, absolute paths, symlink escape and filename limits.
- Test application unavailable, timeout, login required, version mismatch,
  malformed response and partial failure paths.
- Pin or document the supported application versions and define how `probe()`
  reports unknown or unsupported versions.
- Record the application version in observations and user-facing errors.
- Run one MCP Client discovery and tool-call round trip plus one workflow
  execution before marking the adapter supported.
- Verify stop and backend shutdown leave no managed process or listening port.
- Return explicit capability errors for unsupported software. Never simulate
  success when no controllable API, CLI, UIA or browser surface exists.
