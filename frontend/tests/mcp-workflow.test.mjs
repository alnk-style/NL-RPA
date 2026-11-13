import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildMCPStep,
  formatMCPResultPath,
  parseMCPStep,
  schemaToFields,
} from '../src/lib/mcp-workflow.ts';

test('schemaToFields maps supported JSON schema controls', () => {
  const fields = schemaToFields({
    type: 'object',
    required: ['name', 'count'],
    properties: {
      name: { type: 'string', title: '名称' },
      count: { type: 'integer', description: '数量' },
      enabled: { type: 'boolean' },
      format: { type: 'string', enum: ['json', 'csv'] },
      tags: { type: 'array', items: { type: 'string' } },
      options: { type: 'object' },
    },
  });

  assert.deepEqual(
    fields.map((field) => [field.key, field.control, field.required]),
    [
      ['name', 'text', true],
      ['count', 'number', true],
      ['enabled', 'boolean', false],
      ['format', 'enum', false],
      ['tags', 'string_list', false],
      ['options', 'json', false],
    ],
  );
  assert.deepEqual(fields[3].options, ['json', 'csv']);
});

test('MCP step serializes and deserializes without losing fields', () => {
  const step = buildMCPStep({
    stepId: 3,
    server: 'demo_app',
    tool: 'demo_export',
    arguments: { text: 'hello', format: 'txt' },
    resultVar: 'export_result',
    resultPath: '/artifacts/0/path',
    confirm: true,
    timeoutSeconds: 45,
    retryAttempts: 2,
    dependsOn: [2],
  });

  assert.equal(step.executor, 'mcp');
  assert.equal(step.action, 'call_tool');
  assert.equal(step.params.server, 'demo_app');
  assert.equal(step.params.tool, 'demo_export');
  assert.deepEqual(step.params.arguments, {
    text: 'hello',
    format: 'txt',
  });
  assert.equal(step.params.result_var, 'export_result');
  assert.equal(step.params.result_path, '/artifacts/0/path');
  assert.equal(step.retry_policy.max_attempts, 2);

  const parsed = parseMCPStep(step);
  assert.equal(parsed.server, 'demo_app');
  assert.equal(parsed.tool, 'demo_export');
  assert.equal(parsed.resultVar, 'export_result');
  assert.equal(parsed.confirm, true);
  assert.equal(parsed.timeoutSeconds, 45);
});

test('result path display handles empty and long paths', () => {
  assert.equal(formatMCPResultPath(null), '未设置');
  assert.equal(
    formatMCPResultPath('/artifacts/0/path'),
    '/artifacts/0/path',
  );
  assert.match(
    formatMCPResultPath(`/${'segment/'.repeat(30)}`),
    /…$/,
  );
});

