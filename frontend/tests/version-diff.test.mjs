import assert from 'node:assert/strict';
import test from 'node:test';

import {
  formatVersionChangeValue,
  groupVersionChanges,
} from '../src/lib/version-diff.ts';

const changes = [
  { kind: 'change', path: 'steps[0].params.value', before: 1000, after: 2000 },
  { kind: 'add', path: 'steps[1]', after: { action: 'sort' } },
  { kind: 'remove', path: 'variables[0]', before: { name: 'old' } },
  { kind: 'change', path: 'execution_policy.required_delivery', before: false, after: true },
  { kind: 'unknown', path: 'future.field', before: 1, after: 2 },
];

test('groups version changes without inventing a successful operation', () => {
  const grouped = groupVersionChanges(changes);

  assert.deepEqual(grouped.counts, {
    add: 1,
    remove: 1,
    change: 2,
    unknown: 1,
    total: 5,
  });
  assert.equal(grouped.groups[0].kind, 'change');
  assert.equal(grouped.groups[0].changes.length, 2);
  assert.equal(grouped.groups[1].kind, 'add');
  assert.equal(grouped.groups[2].kind, 'remove');
  assert.equal(grouped.groups[3].kind, 'unknown');
});

test('formats empty and structured diff values deterministically', () => {
  assert.equal(formatVersionChangeValue(undefined), '未设置');
  assert.equal(formatVersionChangeValue(null), 'null');
  assert.equal(formatVersionChangeValue('paid'), '"paid"');
  assert.equal(
    formatVersionChangeValue({ status: 'paid', count: 2 }),
    '{"status":"paid","count":2}',
  );
});

test('an empty diff stays explicit', () => {
  assert.deepEqual(groupVersionChanges([]), {
    groups: [],
    counts: {
      add: 0,
      remove: 0,
      change: 0,
      unknown: 0,
      total: 0,
    },
  });
});
