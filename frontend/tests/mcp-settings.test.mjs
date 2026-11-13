import assert from 'node:assert/strict';
import test from 'node:test';

import {
  auditStatusLabel,
  auditStatusVariant,
  formatAuditTarget,
  formatDurationMs,
  mcpSettingSourceLabel,
  mcpSettingsStatusLabel,
  riskLabel,
  riskVariant,
  summarizeTools,
  toolAuthorizationLabel,
  toolNeedsConfirmation,
} from '../src/lib/mcp-settings.ts';

test('mcp runtime toggle labels reflect state and source', () => {
  assert.equal(mcpSettingsStatusLabel(true), '已开启');
  assert.equal(mcpSettingsStatusLabel(false), '已关闭');
  assert.equal(mcpSettingSourceLabel('runtime'), '应用内设置');
  assert.equal(mcpSettingSourceLabel('environment'), '环境默认值');
});

test('risk levels map to labels and badge variants', () => {
  assert.equal(riskLabel('read_only'), '只读');
  assert.equal(riskLabel('mutating'), '写入');
  assert.equal(riskLabel('destructive'), '破坏性');
  assert.equal(riskVariant('read_only'), 'success');
  assert.equal(riskVariant('mutating'), 'warning');
  assert.equal(riskVariant('destructive'), 'danger');
});

test('tool authorization labels follow the permission gate', () => {
  assert.equal(
    toolAuthorizationLabel({
      name: 'read',
      risk_level: 'read_only',
      authorized: false,
      confirmed: false,
    }),
    '默认允许',
  );
  assert.equal(
    toolAuthorizationLabel({
      name: 'write',
      risk_level: 'mutating',
      authorized: false,
      confirmed: false,
    }),
    '未授权',
  );
  assert.equal(
    toolAuthorizationLabel({
      name: 'purge',
      risk_level: 'destructive',
      authorized: true,
      confirmed: false,
    }),
    '已授权，待确认',
  );
  assert.equal(
    toolAuthorizationLabel({
      name: 'purge',
      risk_level: 'destructive',
      authorized: true,
      confirmed: true,
    }),
    '已授权',
  );
  assert.equal(
    toolNeedsConfirmation({
      name: 'purge',
      risk_level: 'destructive',
      authorized: false,
      confirmed: false,
    }),
    true,
  );
});

test('summarizeTools counts risks and pending authorization', () => {
  const summary = summarizeTools([
    {
      name: 'read',
      risk_level: 'read_only',
      authorized: false,
      confirmed: false,
    },
    {
      name: 'write',
      risk_level: 'mutating',
      authorized: false,
      confirmed: false,
    },
    {
      name: 'purge',
      risk_level: 'destructive',
      authorized: true,
      confirmed: false,
    },
    {
      name: 'drop',
      risk_level: 'destructive',
      authorized: true,
      confirmed: true,
    },
  ]);

  assert.deepEqual(summary, {
    total: 4,
    readOnly: 1,
    mutating: 1,
    destructive: 2,
    authorized: 2,
    pendingAuthorization: 1,
    missingConfirmation: 1,
  });
});

test('audit helpers format status and duration', () => {
  assert.equal(auditStatusLabel('denied'), '已拒绝');
  assert.equal(auditStatusVariant('timeout'), 'warning');
  assert.equal(auditStatusVariant('failed'), 'danger');
  assert.equal(auditStatusVariant('success'), 'success');
  assert.equal(formatDurationMs(120), '120ms');
  assert.equal(formatDurationMs(1500), '1.50s');
  assert.equal(formatDurationMs(null), '—');
  assert.equal(
    formatAuditTarget({
      id: 1,
      server_id: 1,
      server: 'local-tools',
      tool: 'echo',
      risk_level: 'read_only',
      decision: 'allowed',
      status: 'success',
      arguments: null,
      result: null,
      error: null,
      duration_ms: 12,
      attempts: 1,
      workflow_id: 1,
      execution_id: 8,
      user_id: 1,
      created_at: null,
    }),
    'local-tools · echo · 执行 #8',
  );
});
