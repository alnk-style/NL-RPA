import assert from 'node:assert/strict';
import test from 'node:test';

import { buildCondition } from '../src/lib/action-schema.ts';

test('condition builder emits declarative condition objects', () => {
  assert.deepEqual(
    buildCondition({
      variable: 'transactions.amount',
      operator: 'gt',
      value: '1000',
      cast: 'number',
    }),
    {
      left: { var: 'transactions.amount' },
      operator: 'gt',
      right: { literal: 1000 },
      cast: 'number',
    },
  );
});

test('condition builder never creates executable expressions', () => {
  const condition = buildCondition({
    variable: 'row.status',
    operator: 'eq',
    value: 'active',
  });
  assert.equal('expression' in condition, false);
  assert.deepEqual(condition.right, { literal: 'active' });
});
