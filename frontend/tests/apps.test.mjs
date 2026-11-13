import assert from 'node:assert/strict';
import test from 'node:test';

import {
  appStatus,
  appStatusCounts,
  appStatusLabel,
  filterApps,
} from '../src/lib/apps.ts';

function server(overrides = {}) {
  return {
    id: 1,
    name: 'demo_app',
    description: '演示应用',
    transport: 'http',
    url: 'http://127.0.0.1:8767/mcp',
    command: null,
    args: [],
    env_keys: [],
    headers: {},
    enabled: true,
    default_risk: 'mutating',
    timeout_seconds: null,
    has_secret: false,
    tools: [],
    last_error: null,
    last_tested_at: '2026-10-18T10:00:00Z',
    created_at: null,
    updated_at: null,
    ...overrides,
  };
}

test('app status distinguishes connected, pending, error, untested and disabled', () => {
  assert.equal(appStatus(server()), 'connected');
  assert.equal(
    appStatus(
      server({
        tools: [
          {
            name: 'write',
            risk_level: 'mutating',
            authorized: false,
            confirmed: false,
          },
        ],
      }),
    ),
    'pending_authorization',
  );
  assert.equal(
    appStatus(server({ last_error: '连接失败' })),
    'error',
  );
  assert.equal(
    appStatus(server({ last_tested_at: null })),
    'untested',
  );
  assert.equal(
    appStatus(server({ enabled: false })),
    'disabled',
  );
  assert.equal(appStatusLabel('connected'), '已连接');
});

test('app filters combine keyword and status', () => {
  const servers = [
    server(),
    server({
      id: 2,
      name: 'notes',
      description: '笔记应用',
      last_error: '认证失败',
    }),
    server({
      id: 3,
      name: 'disabled_app',
      enabled: false,
    }),
  ];

  assert.deepEqual(
    filterApps(servers, '笔记', 'all').map((item) => item.id),
    [2],
  );
  assert.deepEqual(
    filterApps(servers, '', 'error').map((item) => item.id),
    [2],
  );
  assert.deepEqual(
    filterApps(servers, 'demo', 'all').map((item) => item.id),
    [1],
  );
});

test('app status counts are stable', () => {
  const counts = appStatusCounts([
    server(),
    server({ id: 2, last_error: 'failed' }),
    server({ id: 3, enabled: false }),
    server({ id: 4, last_tested_at: null }),
  ]);

  assert.deepEqual(counts, {
    all: 4,
    connected: 1,
    pending_authorization: 0,
    error: 1,
    untested: 1,
    disabled: 1,
  });
});

