import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildWorkflowGraph,
  buildWorkflowName,
  layoutWorkflowGraph,
} from '../src/lib/workflow-graph.ts';

test('buildWorkflowGraph creates a trigger and sequential steps', () => {
  const graph = buildWorkflowGraph({
    trigger: { type: 'manual', cron: null },
    steps: [
      {
        step_id: 1,
        action: 'navigate',
        params: { url: 'https://example.com' },
      },
      { step_id: 2, action: 'click', target: '登录按钮' },
    ],
  });

  assert.deepEqual(
    graph.nodes.map((node) => node.id),
    ['trigger', 'step-1', 'step-2'],
  );
  assert.deepEqual(graph.edges, [
    {
      id: 'edge-trigger-step-1',
      source: 'trigger',
      target: 'step-1',
    },
    {
      id: 'edge-step-1-step-2',
      source: 'step-1',
      target: 'step-2',
    },
  ]);
  assert.equal(graph.nodes[1].label, '打开页面');
  assert.equal(graph.nodes[1].detail, 'https://example.com');
});

test('buildWorkflowGraph respects explicit dependencies', () => {
  const graph = buildWorkflowGraph({
    trigger: { type: 'cron', cron: '0 9 * * *' },
    steps: [
      { step_id: 'load', action: 'extract' },
      { step_id: 'send', action: 'notify', depends_on: ['load'] },
    ],
  });

  assert.equal(graph.nodes[0].label, '定时触发');
  assert.equal(graph.nodes[0].detail, '0 9 * * *');
  assert.deepEqual(graph.edges.map((edge) => edge.target), [
    'step-load',
    'step-send',
  ]);
});

test('buildWorkflowGraph keeps an explicitly empty dependency list', () => {
  const graph = buildWorkflowGraph({
    steps: [
      { step_id: 'load', action: 'navigate', depends_on: [] },
      { step_id: 'send', action: 'notify', depends_on: [] },
    ],
  });

  assert.deepEqual(graph.edges, [
    {
      id: 'edge-trigger-step-load',
      source: 'trigger',
      target: 'step-load',
    },
  ]);
});

test('buildWorkflowGraph maps trigger dependencies to the trigger node', () => {
  const graph = buildWorkflowGraph({
    steps: [
      { step_id: 'load', action: 'navigate', depends_on: [] },
      {
        step_id: 'send',
        action: 'notify',
        depends_on: ['load', 'trigger'],
      },
    ],
  });

  assert.deepEqual(
    graph.edges.map((edge) => [edge.source, edge.target]),
    [
      ['trigger', 'step-load'],
      ['step-load', 'step-send'],
      ['trigger', 'step-send'],
    ],
  );
});

test('buildWorkflowGraph labels MCP steps by application and tool', () => {
  const graph = buildWorkflowGraph({
    executor_type: 'mcp',
    steps: [
      {
        step_id: 1,
        executor: 'mcp',
        action: 'call_tool',
        params: {
          server: 'demo_app',
          tool: 'demo_status',
        },
      },
    ],
  });

  assert.equal(graph.nodes[1].label, 'MCP · demo_app');
  assert.equal(graph.nodes[1].detail, 'demo_status');
});

test('layoutWorkflowGraph puts dependent steps in later columns', () => {
  const positions = layoutWorkflowGraph({
    nodes: [
      { id: 'trigger', kind: 'trigger', label: '手动触发', stepIndex: -1 },
      { id: 'step-a', kind: 'step', label: 'A', stepIndex: 0 },
      { id: 'step-b', kind: 'step', label: 'B', stepIndex: 1 },
      { id: 'step-c', kind: 'step', label: 'C', stepIndex: 2 },
    ],
    edges: [
      { id: 'one', source: 'trigger', target: 'step-a' },
      { id: 'two', source: 'trigger', target: 'step-b' },
      { id: 'three', source: 'step-a', target: 'step-c' },
      { id: 'four', source: 'step-b', target: 'step-c' },
    ],
  });

  assert.ok(positions['step-c'].x > positions['step-a'].x);
  assert.ok(positions['step-c'].x > positions['step-b'].x);
  assert.notEqual(
    positions['step-a'].y,
    positions['step-b'].y,
  );
});

test('buildWorkflowName trims whitespace and limits long input', () => {
  assert.equal(buildWorkflowName('  网银流水导出  '), '网银流水导出');
  assert.equal(
    buildWorkflowName('这是一个非常长的自动化流程名称用于验证截断行为'),
    '这是一个非常长的自动化流程名称用于验证截断...',
  );
  assert.equal(buildWorkflowName('   '), '未命名流程');
});
