import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyExecutionStatusUpdate,
  buildExecutionWebSocketUrl,
  buildScreenshotRequestPath,
  deliveryStatusVariant,
  executionVersionLabel,
  formatDuration,
  getExecutionDurationSeconds,
  mergeExecutionStepLogs,
  normalizeExecutionStepLog,
} from '../src/lib/execution.ts';

test('terminal live status freezes execution duration', () => {
  const startedAt = '2026-09-16T10:00:00.000Z';
  const receivedAt = Date.parse('2026-09-16T10:00:12.000Z');
  const current = {
    id: 1,
    status: 'running',
    started_at: startedAt,
    finished_at: null,
  };

  const updated = applyExecutionStatusUpdate(
    current,
    { type: 'status', status: 'success' },
    receivedAt,
  );

  assert.equal(updated.status, 'success');
  assert.equal(updated.finished_at, new Date(receivedAt).toISOString());
  assert.equal(
    getExecutionDurationSeconds(
      updated.started_at,
      updated.finished_at,
      Date.parse('2026-09-16T10:30:00.000Z'),
    ),
    12,
  );
});

test('normalizeExecutionStepLog maps backend logs to a stable shape', () => {
  const step = normalizeExecutionStepLog(
    {
      step_index: 2,
      action: 'goto',
      target: 'https://example.com',
      selector: null,
      status: 'success',
      screenshot_path: 'logs/run/step.png',
      started_at: 10.25,
      finished_at: 11.75,
    },
    4,
  );

  assert.equal(step.index, 2);
  assert.equal(step.action, 'goto');
  assert.equal(step.durationSeconds, 1.5);
  assert.equal(step.hasScreenshot, true);
});

test('mergeExecutionStepLogs updates an existing step in place', () => {
  const current = [
    normalizeExecutionStepLog({ step_index: 0, status: 'running' }, 0),
  ];
  const merged = mergeExecutionStepLogs(
    current,
    normalizeExecutionStepLog({ step_index: 0, status: 'success' }, 0),
  );

  assert.equal(merged.length, 1);
  assert.equal(merged[0].status, 'success');
});

test('normalizeExecutionStepLog keeps screenshot warnings', () => {
  const step = normalizeExecutionStepLog(
    {
      step_index: 8,
      action: 'screenshot',
      target: 'step_4_save_file',
      status: 'warning',
      screenshot_error: 'Page.screenshot: Timeout 15000ms exceeded.',
      error: 'Page.screenshot: Timeout 15000ms exceeded.',
    },
    0,
  );

  assert.equal(step.status, 'warning');
  assert.equal(step.hasScreenshot, false);
  assert.equal(
    step.screenshotError,
    'Page.screenshot: Timeout 15000ms exceeded.',
  );
});

test('normalizeExecutionStepLog maps desktop agent step logs', () => {
  const step = normalizeExecutionStepLog(
    {
      step_index: 1,
      action_type: 'click_element',
      status: 'failed',
      message: 'No matching UI Automation element',
      error: 'No matching UI Automation element',
    },
    0,
  );

  assert.equal(step.action, 'click_element');
  assert.equal(step.target, 'No matching UI Automation element');
  assert.equal(step.status, 'failed');
});

test('normalizeExecutionStepLog maps automatic repair logs', () => {
  const step = normalizeExecutionStepLog(
    {
      step_index: 1,
      action_type: 'auto_repair',
      status: 'skipped',
      message: '自动修复第 2 步：No matching UI Automation element',
    },
    0,
  );

  assert.equal(step.action, 'auto_repair');
  assert.match(step.target, /自动修复/);
  assert.equal(step.status, 'skipped');
});

test('normalizeExecutionStepLog keeps MCP execution metadata', () => {
  const step = normalizeExecutionStepLog(
    {
      step_index: 3,
      executor: 'mcp',
      action: 'call_tool',
      server: 'demo_app',
      tool: 'demo_export',
      risk_level: 'mutating',
      decision: 'allowed',
      duration_ms: 850,
      attempts: 2,
      audit_id: 19,
      artifact_count: 1,
      result_path: '/artifacts/0/path',
      status: 'success',
      arguments_summary: { text: 'hello' },
      result_summary: '{"ok":true}',
    },
    3,
  );

  assert.equal(step.executor, 'mcp');
  assert.equal(step.server, 'demo_app');
  assert.equal(step.tool, 'demo_export');
  assert.equal(step.risk_level, 'mutating');
  assert.equal(step.audit_id, 19);
  assert.equal(step.artifact_count, 1);
  assert.equal(step.duration_ms, 850);
  assert.equal(step.result_path, '/artifacts/0/path');
});

test('execution version labels use the recorded version and never invent one', () => {
  assert.equal(executionVersionLabel({
    workflow_version: 4,
    workflow_version_id: 17,
  }), 'v4');
  assert.equal(executionVersionLabel({
    workflow_version: null,
    workflow_version_id: 17,
  }), '版本 ID 17');
  assert.equal(executionVersionLabel({
    workflow_version: null,
    workflow_version_id: null,
  }), '未记录版本');
});

test('unknown, cancelled and awaiting step states stay non-success', () => {
  assert.equal(
    normalizeExecutionStepLog({ status: 'awaiting_user' }, 0).status,
    'awaiting_user',
  );
  assert.equal(
    normalizeExecutionStepLog({ status: 'cancelled' }, 0).status,
    'cancelled',
  );
  assert.equal(
    normalizeExecutionStepLog({ status: 'future_state' }, 0).status,
    'unknown',
  );
});

test('delivery status mapping keeps failed and warning distinct', () => {
  assert.equal(deliveryStatusVariant('success'), 'success');
  assert.equal(deliveryStatusVariant('warning'), 'warning');
  assert.equal(deliveryStatusVariant('failed'), 'danger');
  assert.equal(deliveryStatusVariant('cancelled'), 'danger');
  assert.equal(deliveryStatusVariant('pending'), 'secondary');
  assert.equal(deliveryStatusVariant('unknown'), 'warning');
});

test('formatDuration renders sub-second and longer durations', () => {
  assert.equal(formatDuration(0.348), '348 ms');
  assert.equal(formatDuration(12.4), '12.4 s');
  assert.equal(formatDuration(72), '1 min 12 s');
});

test('getExecutionDurationSeconds prefers finished time', () => {
  assert.equal(
    getExecutionDurationSeconds(
      '2026-09-16T10:00:00.000Z',
      '2026-09-16T10:00:05.500Z',
      Date.parse('2026-09-16T10:00:20.000Z'),
    ),
    5.5,
  );
  assert.equal(
    getExecutionDurationSeconds(
      '2026-09-16T10:00:00.000Z',
      null,
      Date.parse('2026-09-16T10:00:07.000Z'),
    ),
    7,
  );
});

test('execution URLs target the live channel and screenshot endpoint', () => {
  assert.equal(
    buildExecutionWebSocketUrl(17, 'http://localhost:8000'),
    'ws://localhost:8000/api/executions/17/live',
  );
  assert.equal(
    buildScreenshotRequestPath(17, 2),
    '/api/executions/17/screenshot/2',
  );
});
