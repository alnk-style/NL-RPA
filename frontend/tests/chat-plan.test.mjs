import assert from 'node:assert/strict';
import test from 'node:test';

import {
  executorLabel,
  getPlanExecutorType,
  mcpPlanSteps,
  normalizeExecutorType,
} from '../src/lib/chat-plan.ts';

test('chat plan recognizes mcp and hybrid executors', () => {
  assert.equal(normalizeExecutorType('mcp'), 'mcp');
  assert.equal(normalizeExecutorType('hybrid'), 'hybrid');
  assert.equal(getPlanExecutorType({ executor_type: 'mcp', steps: [] }), 'mcp');
  assert.equal(
    getPlanExecutorType({
      executor_type: 'web',
      steps: [
        { executor: 'web', action: 'goto' },
        { executor: 'mcp', action: 'call_tool' },
      ],
    }),
    'hybrid',
  );
  assert.equal(executorLabel('mcp'), '应用工具');
  assert.equal(executorLabel('hybrid'), '混合流程');
});

test('chat plan extracts readable MCP steps', () => {
  const steps = mcpPlanSteps({
    steps: [
      { executor: 'web', action: 'goto' },
      {
        step_id: 2,
        executor: 'mcp',
        action: 'call_tool',
        risk_level: 'mutating',
        requires_confirmation: true,
        params: {
          server: 'demo_app',
          tool: 'demo_export',
          arguments: { text: 'hello' },
          result_var: 'exported',
        },
      },
    ],
  });

  assert.equal(steps.length, 1);
  assert.equal(steps[0].server, 'demo_app');
  assert.equal(steps[0].tool, 'demo_export');
  assert.equal(steps[0].riskLevel, 'mutating');
  assert.equal(steps[0].requiresConfirmation, true);
  assert.equal(steps[0].hasArguments, true);
  assert.deepEqual(steps[0].argumentKeys, ['text']);
});
