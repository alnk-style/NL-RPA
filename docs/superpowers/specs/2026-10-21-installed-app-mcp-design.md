# Installed Application MCP Creation Design

## Goal

Extend the application center so locally installed Windows applications can be
discovered, previewed and converted into persisted in-process MCP applications
without uploading code, executing arbitrary installers or starting third-party
MCP marketplaces.

## Scope

- Reuse the existing installed application discovery service.
- Persist generated application packages under `~/.nl-rpa/apps`.
- Generate a generic scoped Desktop Connector adapter for each selected app.
- Add the selected executable to the owned Desktop Agent allowlist.
- Create, start and bind the generated MCP through the existing
  `LocalAppManager` flow.
- Show discovered applications in the application center with icons, search and
  duplicate detection.
- Allow deleting a generated MCP package without deleting user data.

This phase does not generate semantic WPS, browser or music tools. Those remain
known future templates on top of the generic scoped adapter.

## Architecture

Installed application discovery remains read-only and returns opaque SHA-256
application keys. Creation accepts only an application key, never a path. The
backend resolves the key through the discovery cache, writes a generated
manifest and adapter under the stable user data directory, adds the executable
to the selected agent allowlist, then starts the generated in-process MCP.

Generated adapters subclass a built-in `ScopedDesktopAdapter`. The launch tool
uses the executable path captured in the manifest configuration and does not
accept a model-supplied path. Observe, activate and read-text tools delegate to
the platform's shared Desktop Connector.

## Storage

- Built-in packages: `resource_root()/mcp_apps`
- Generated packages: `~/.nl-rpa/apps/<app_id>`
- Generated metadata: `~/.nl-rpa/apps/<app_id>/integration.json`
- Logs and exports remain under `~/.nl-rpa/logs/app_mcp`

Generated app IDs have the form `<slug>_<first 8 key characters>` and are
stable for a normalized executable path.

## API

- `GET /api/local-apps/discoverable-apps?refresh=false`
- `GET /api/local-apps/discoverable-apps/{app_key}/icon`
- `POST /api/local-apps/discoverable-apps/{app_key}/create`
- `DELETE /api/local-apps/generated/{app_id}`

Create requests contain only `agent_id`. Delete requests may contain
`remove_from_allowlist` and `agent_id`.

## Generated Tools

- `app_launch`: mutating; launches only the configured executable.
- `app_observe`: read-only; observes windows through the shared connector.
- `app_activate`: mutating; activates a matching window.
- `app_read_text`: read-only; reads text from a matching UIA control.

All generated tools retain the platform's authorization and audit behavior.

## Security

- Frontend cannot submit executable paths or adapter source.
- Only cached discovery entries may be converted.
- Generated directories must remain under the configured user apps root.
- Generated app IDs must match the validated manifest ID.
- No generated package is auto-started without the existing binding and
  `auto_start` rules.
- Removing a generated app never deletes the third-party executable or its user
  data.

## Testing

- Discovery cache key lookup and icon routing.
- Generated package manifest, adapter and metadata.
- Duplicate generation for the same executable.
- Path traversal and invalid app ID rejection.
- Local manager scans both built-in and generated roots.
- Scoped adapter rejects a launch path not matching its manifest.
- Create API synchronizes the agent allowlist and starts an in-process app.
- Delete API stops the app and removes only the generated package.
- Frontend filtering, duplicate state and creation flow.
- Packaged backend can discover applications and serve icons.
