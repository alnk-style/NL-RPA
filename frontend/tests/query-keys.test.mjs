import assert from 'node:assert/strict';
import test from 'node:test';

import { queryKeys } from '../src/api/queryKeys.ts';

test('query keys are stable and hierarchical', () => {
  assert.deepEqual(queryKeys.workflows, ['workflows']);
  assert.deepEqual(queryKeys.workflow(7), ['workflows', 7]);
  assert.deepEqual(queryKeys.workflowVersions(7), [
    'workflows',
    7,
    'versions',
  ]);
  assert.deepEqual(queryKeys.executions, ['executions']);
  assert.deepEqual(queryKeys.execution(9), ['executions', 9]);
  assert.deepEqual(queryKeys.humanTasks, ['human-tasks']);
  assert.deepEqual(queryKeys.actionCatalog, ['action-catalog']);
  assert.deepEqual(queryKeys.emailSettings, ['settings', 'email']);
});
