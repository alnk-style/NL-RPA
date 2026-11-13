import assert from 'node:assert/strict';
import test from 'node:test';

import {
  APP_ROUTES,
  isNavigationItemActive,
} from '../src/app/router.ts';

test('navigation recognizes nested workflow and execution routes', () => {
  assert.equal(isNavigationItemActive('/workflows', '/workflows'), true);
  assert.equal(
    isNavigationItemActive('/workflows/12/studio', '/workflows'),
    true,
  );
  assert.equal(
    isNavigationItemActive('/executions/12', '/executions'),
    true,
  );
  assert.equal(isNavigationItemActive('/settings', '/workflows'), false);
});

test('root dashboard is active only at the root path', () => {
  assert.equal(isNavigationItemActive('/', '/'), true);
  assert.equal(isNavigationItemActive('/workflows', '/'), false);
});

test('router contains the Stage C routes', () => {
  const paths = APP_ROUTES.map((route) => route.path);
  for (const expected of [
    '/',
    '/workflows',
    '/workflows/:id/studio',
    '/executions',
    '/executions/:id',
    '/pending',
    '/connections',
    '/settings',
    '/advanced/tools',
    '/advanced/chat',
    '/apps',
  ]) {
    assert.ok(paths.includes(expected), `missing route ${expected}`);
  }
});
