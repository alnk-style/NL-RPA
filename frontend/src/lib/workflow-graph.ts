import type {
  StructuredPlan,
  WorkflowGraph,
  WorkflowGraphEdge,
  WorkflowStep,
} from '../types/workflow';

const ACTION_LABELS: Record<string, string> = {
  navigate: '打开页面',
  click: '点击',
  fill: '填写',
  extract: '提取数据',
  compare: '数据对比',
  notify: '发送通知',
  call_tool: 'MCP 工具',
};

const DETAIL_KEYS = [
  'url',
  'value',
  'threshold',
  'selector',
  'field',
  'message',
];

const MAX_WORKFLOW_NAME_LENGTH = 21;
const GRAPH_COLUMN_GAP = 300;
const GRAPH_ROW_GAP = 150;

function stepId(step: WorkflowStep, index: number): string {
  return String(step.step_id ?? index + 1);
}

function detailValue(value: unknown): string | undefined {
  if (
    value === null ||
    value === undefined ||
    typeof value === 'object'
  ) {
    return undefined;
  }

  const text = String(value).trim();
  return text || undefined;
}

function stepDetail(step: WorkflowStep): string | undefined {
  const direct = detailValue(step.target);
  if (direct) {
    return direct;
  }

  const params = step.params ?? {};
  for (const key of DETAIL_KEYS) {
    const value = detailValue(params[key]);
    if (value) {
      return value;
    }
  }

  return detailValue(step.selector);
}

export function buildWorkflowGraph(
  plan: StructuredPlan | null | undefined,
): WorkflowGraph {
  if (!plan) {
    return { nodes: [], edges: [] };
  }

  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  const trigger = plan.trigger ?? {};
  const isCron = trigger.type === 'cron';

  const nodes = [
    {
      id: 'trigger',
      kind: 'trigger' as const,
      label: isCron ? '定时触发' : '手动触发',
      detail: isCron ? detailValue(trigger.cron) : undefined,
      stepIndex: -1,
    },
    ...steps.map((step, index) => {
      const action = String(step.action ?? '');
      const params = step.params ?? {};
      const isMCP = step.executor === 'mcp' || action === 'call_tool';
      if (isMCP) {
        return {
          id: `step-${stepId(step, index)}`,
          kind: 'step' as const,
          label: `MCP · ${String(params.server ?? '应用')}`,
          detail: String(params.tool ?? '未选择工具'),
          stepIndex: index,
        };
      }
      return {
        id: `step-${stepId(step, index)}`,
        kind: 'step' as const,
        label:
          ACTION_LABELS[action] ??
          (action || `步骤 ${index + 1}`),
        detail: stepDetail(step),
        stepIndex: index,
      };
    }),
  ];

  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges: WorkflowGraphEdge[] = [];

  steps.forEach((step, index) => {
    const target = `step-${stepId(step, index)}`;
    const hasExplicitDependencies = Array.isArray(step.depends_on);
    const dependencies = (step.depends_on ?? [])
      .map((dependency) => (
        String(dependency) === 'trigger'
          ? 'trigger'
          : `step-${String(dependency)}`
      ))
      .filter((dependency) => nodeIds.has(dependency));

    if (dependencies.length > 0) {
      dependencies.forEach((source) => {
        edges.push({
          id: `edge-${source}-${target}`,
          source,
          target,
        });
      });
      return;
    }

    if (index === 0) {
      edges.push({
        id: `edge-trigger-${target}`,
        source: 'trigger',
        target,
      });
      return;
    }

    if (hasExplicitDependencies) {
      return;
    }

    const previous = `step-${stepId(steps[index - 1], index - 1)}`;
    edges.push({
      id: `edge-${previous}-${target}`,
      source: previous,
      target,
    });
  });

  return { nodes, edges };
}

export function buildWorkflowName(input: string): string {
  const normalized = input.replace(/\s+/g, ' ').trim();
  if (!normalized) {
    return '未命名流程';
  }

  if (normalized.length <= MAX_WORKFLOW_NAME_LENGTH) {
    return normalized;
  }

  return `${normalized.slice(0, MAX_WORKFLOW_NAME_LENGTH)}...`;
}

export function layoutWorkflowGraph(
  graph: WorkflowGraph,
): Record<string, { x: number; y: number }> {
  const incoming = new Map<string, string[]>();
  graph.nodes.forEach((node) => incoming.set(node.id, []));
  graph.edges.forEach((edge) => {
    const dependencies = incoming.get(edge.target);
    if (dependencies) {
      dependencies.push(edge.source);
    }
  });

  const depths = new Map<string, number>();
  const visiting = new Set<string>();

  function resolveDepth(nodeId: string): number {
    const cached = depths.get(nodeId);
    if (cached !== undefined) {
      return cached;
    }
    if (visiting.has(nodeId)) {
      return 0;
    }

    visiting.add(nodeId);
    const dependencies = incoming.get(nodeId) ?? [];
    const depth = dependencies.length
      ? Math.max(...dependencies.map(resolveDepth)) + 1
      : (
          graph.nodes.find((node) => node.id === nodeId)?.kind === 'trigger'
            ? 0
            : 1
        );
    visiting.delete(nodeId);
    depths.set(nodeId, depth);
    return depth;
  }

  graph.nodes.forEach((node) => resolveDepth(node.id));

  const rows = new Map<number, number>();
  const positions: Record<string, { x: number; y: number }> = {};
  graph.nodes.forEach((node) => {
    const depth = depths.get(node.id) ?? 0;
    const row = rows.get(depth) ?? 0;
    rows.set(depth, row + 1);
    positions[node.id] = {
      x: 140 + depth * GRAPH_COLUMN_GAP,
      y: 80 + row * GRAPH_ROW_GAP,
    };
  });

  return positions;
}
