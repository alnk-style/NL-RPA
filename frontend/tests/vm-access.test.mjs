import test from 'node:test';
import assert from 'node:assert/strict';

import {
  canResumeOnHost,
  buildHostEventsWebSocketUrl,
  hostRequirementOptionsForExecutor,
  hostEnvironmentLabel,
  hostStatusLabel,
  normalizeHostEnvironment,
  normalizeHostRequirement,
  shouldWarnHostRestart,
  vmAccessFromHost,
} from '../src/lib/hosts.ts';

test('host environment compatibility normalizes isolated to vm', () => {
  assert.equal(normalizeHostEnvironment('isolated'), 'vm');
  assert.equal(normalizeHostEnvironment('vm'), 'vm');
  assert.equal(normalizeHostEnvironment('local'), 'local');
  assert.equal(normalizeHostEnvironment('cloud'), 'unknown');
});

test('host labels distinguish online, busy, offline and unknown', () => {
  assert.equal(hostStatusLabel('online'), '在线');
  assert.equal(hostStatusLabel('busy'), '忙碌');
  assert.equal(hostStatusLabel('offline'), '离线');
  assert.equal(hostStatusLabel('unknown'), '未知');
  assert.equal(hostEnvironmentLabel('vm'), 'VM');
  assert.equal(hostEnvironmentLabel('local'), '本机');
});

test('offline or busy hosts cannot resume a pending task', () => {
  assert.equal(canResumeOnHost({ status: 'online' }), true);
  assert.equal(canResumeOnHost({ status: 'busy' }), false);
  assert.equal(canResumeOnHost({ status: 'offline' }), false);
  assert.equal(canResumeOnHost(null), true);
});

test('vm access falls back to instructions without a console link', () => {
  assert.deepEqual(
    vmAccessFromHost({
      environment: 'vm',
      human_access_info: {
        instructions: 'Use the approved console.',
      },
    }),
    {
      url: null,
      instructions: 'Use the approved console.',
      hasAccess: true,
    },
  );
  assert.equal(
    vmAccessFromHost({
      environment: 'local',
      human_access_info: {},
    }).hasAccess,
    false,
  );
});

test('host requirement values are explicit and fail safe to any', () => {
  assert.equal(normalizeHostRequirement('isolated_desktop'), 'isolated_desktop');
  assert.equal(normalizeHostRequirement('unknown'), 'any');
});

test('host requirement choices follow executor capabilities', () => {
  assert.deepEqual(
    hostRequirementOptionsForExecutor('windows').map((item) => item.value),
    ['any', 'interactive_desktop', 'isolated_desktop'],
  );
  assert.deepEqual(
    hostRequirementOptionsForExecutor('web').map((item) => item.value),
    ['any', 'headless_web'],
  );
  assert.deepEqual(
    hostRequirementOptionsForExecutor('transform').map((item) => item.value),
    ['any'],
  );
});

test('host event websocket URL includes auth without changing the route', () => {
  const previousWindow = globalThis.window;
  const previousStorage = globalThis.localStorage;
  globalThis.window = { location: { protocol: 'http:', host: 'localhost:5173' } };
  globalThis.localStorage = {
    getItem(key) {
      return key === 'token' ? 'test-token' : null;
    },
  };
  try {
    const url = new URL(buildHostEventsWebSocketUrl());
    assert.equal(url.pathname, '/api/hosts/events');
    assert.equal(url.searchParams.get('token'), 'test-token');
  } finally {
    globalThis.window = previousWindow;
    globalThis.localStorage = previousStorage;
  }
});

test('restart warning appears only for host-bound execution with lost host', () => {
  assert.equal(
    shouldWarnHostRestart(
      [{
        workflow_id: 1,
        status: 'failed',
        host_bound: true,
        host_status: 'offline',
      }],
      1,
    ),
    true,
  );
  assert.equal(
    shouldWarnHostRestart(
      [{
        workflow_id: 1,
        status: 'failed',
        host_bound: false,
        host_status: null,
      }],
      1,
    ),
    false,
  );
});
