import assert from 'node:assert/strict';
import test from 'node:test';

import {
  executionVersionLabel,
  workflowModeLabel,
} from '../src/lib/workflow-mode.ts';

test('structured workflows expose their active version', () => {
  const mode = workflowModeLabel({
    execution_mode: 'structured',
    active_version_id: 12,
  });
  assert.equal(mode.label, '结构化');
  assert.equal(mode.versionLabel, 'v12');
  assert.equal(mode.warning, false);
});

test('legacy code workflows are clearly marked read-only', () => {
  const mode = workflowModeLabel({
    execution_mode: 'legacy_code',
    active_version_id: 3,
  });
  assert.equal(mode.label, '旧代码模式');
  assert.equal(mode.readOnly, true);
  assert.equal(mode.versionLabel, 'v3');
});

test('unknown execution mode is a warning', () => {
  const mode = workflowModeLabel({
    execution_mode: 'mystery',
    active_version_id: null,
  });
  assert.equal(mode.warning, true);
  assert.equal(mode.label, '未知模式');
});

test('execution version labels never invent a version', () => {
  assert.equal(executionVersionLabel({ workflow_version_id: 8 }), 'v8');
  assert.equal(executionVersionLabel({ workflow_version_id: null }), '无版本');
});
