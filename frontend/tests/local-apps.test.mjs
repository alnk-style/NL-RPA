import assert from 'node:assert/strict';
import test from 'node:test';

import {
  bindingStatusLabel,
  hostModeLabel,
  hostRuntimeLabel,
  linkedServerForLocalApp,
  localAppCanStart,
  localAppCanStop,
  localAppMatches,
  localAppStateLabel,
  localAppStateVariant,
  localAppStatusCounts,
  unlinkedServers,
} from '../src/lib/local-apps.ts';

function app(overrides = {}) {
  return {
    app_id: 'demo_app',
    name: 'Demo App',
    description: 'Demo',
    version: '0.1.0',
    platform: 'cross_platform',
    tools_count: 3,
    state: 'discovered',
    url: null,
    pid: null,
    port: null,
    started_at: null,
    last_health_at: null,
    last_error: null,
    auto_start: false,
    server_id: null,
    discovered_tool_count: null,
    unsupported_reason: null,
    host_mode: 'subprocess',
    transport_kind: 'process',
    ...overrides,
  };
}

function server(overrides = {}) {
  return {
    id: 1,
    name: 'Demo App',
    local_app_id: null,
    tools: [],
    ...overrides,
  };
}

test('local app state labels and variants are stable', () => {
  assert.equal(localAppStateLabel('running'), '运行中');
  assert.equal(localAppStateLabel('unsupported'), '当前不支持');
  assert.equal(localAppStateVariant('running'), 'success');
  assert.equal(localAppStateVariant('starting'), 'warning');
  assert.equal(localAppStateVariant('error'), 'danger');
  assert.equal(localAppStateVariant('stopped'), 'secondary');
});

test('local app controls reflect unsupported and active states', () => {
  assert.equal(localAppCanStart(app()), true);
  assert.equal(
    localAppCanStart(app({ state: 'unsupported' })),
    false,
  );
  assert.equal(localAppCanStart(app({ state: 'starting' })), false);
  assert.equal(localAppCanStop(app({ state: 'running' })), true);
  assert.equal(localAppCanStop(app({ state: 'discovered' })), false);
});

test('local app search includes app metadata', () => {
  assert.equal(localAppMatches(app(), 'demo'), true);
  assert.equal(localAppMatches(app(), '0.1.0'), true);
  assert.equal(localAppMatches(app(), 'missing'), false);
});

test('local apps link to servers by id or app metadata', () => {
  const byId = server({ id: 4 });
  assert.equal(
    linkedServerForLocalApp(app({ server_id: 4 }), [byId])?.id,
    4,
  );
  const byMetadata = server({ id: 5, local_app_id: 'demo_app' });
  assert.equal(
    linkedServerForLocalApp(app(), [byMetadata])?.id,
    5,
  );
  assert.deepEqual(
    unlinkedServers([app()], [
      byMetadata,
      server({ id: 7, local_app_id: 'other_app' }),
    ]).map((item) => item.id),
    [7],
  );
});

test('local app status counts group lifecycle states', () => {
  assert.deepEqual(
    localAppStatusCounts([
      app({ state: 'running' }),
      app({ app_id: 'stopped_app', state: 'stopped' }),
      app({ app_id: 'broken_app', state: 'error' }),
      app({ app_id: 'desktop', state: 'unsupported' }),
    ]),
    {
      all: 4,
      running: 1,
      stopped: 1,
      error: 1,
      unsupported: 1,
    },
  );
});

test('host mode and binding labels distinguish local hosting', () => {
  assert.equal(hostModeLabel('subprocess'), '独立进程');
  assert.equal(hostModeLabel('in_process'), '同进程服务');
  assert.equal(
    hostRuntimeLabel(app({ host_mode: 'in_process' })),
    '当前应用以内嵌方式运行',
  );
  assert.equal(
    bindingStatusLabel({
      app_id: 'demo_app',
      bound: true,
      is_owner: true,
      can_manage: true,
      auto_start: false,
      workspace_bound: true,
      host_mode: 'subprocess',
    }),
    '已绑定当前用户',
  );
  assert.equal(
    bindingStatusLabel({
      app_id: 'demo_app',
      bound: true,
      is_owner: false,
      can_manage: false,
      auto_start: true,
      workspace_bound: true,
      host_mode: 'in_process',
    }),
    '已绑定其他用户',
  );
});
