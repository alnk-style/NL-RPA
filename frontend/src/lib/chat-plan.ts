import type {
  ExecutorType,
  StructuredPlan,
  WorkflowStep,
} from '../types/workflow';

export interface MCPPlanStepSummary {
  stepId: string;
  server: string;
  tool: string;
  riskLevel: string;
  requiresConfirmation: boolean;
  hasArguments: boolean;
  argumentKeys: string[];
  dependsOn: Array<string | number>;
}

export function normalizeExecutorType(value: unknown): ExecutorType {
  if (value === 'windows' || value === 'mcp' || value === 'hybrid') {
    return value;
  }
  return 'web';
}

export function getPlanExecutorType(
  plan: StructuredPlan | null | undefined,
): ExecutorType {
  if (!plan) {
    return 'web';
  }
  const declared = normalizeExecutorType(plan.executor_type);
  const executors = new Set(
    (plan.steps ?? []).map((step) => (
      normalizeExecutorType(step.executor ?? declared)
    )),
  );
  if (executors.size > 1) {
    return 'hybrid';
  }
  if (executors.has('mcp')) {
    return 'mcp';
  }
  return executors.has('windows') ? 'windows' : declared;
}

export function executorLabel(value: ExecutorType): string {
  if (value === 'mcp') {
    return '应用工具';
  }
  if (value === 'hybrid') {
    return '混合流程';
  }
  if (value === 'windows') {
    return '桌面流程';
  }
  return '网页流程';
}

function isMCPStep(step: WorkflowStep): boolean {
  return step.executor === 'mcp' || step.action === 'call_tool';
}

export function mcpPlanSteps(
  plan: StructuredPlan | null | undefined,
): MCPPlanStepSummary[] {
  return (plan?.steps ?? [])
    .filter(isMCPStep)
    .map((step, index) => {
      const params = step.params ?? {};
      const argumentsValue =
        params.arguments &&
        typeof params.arguments === 'object' &&
        !Array.isArray(params.arguments)
          ? params.arguments as Record<string, unknown>
          : {};
      return {
        stepId: String(step.step_id ?? index + 1),
        server: String(params.server ?? ''),
        tool: String(params.tool ?? ''),
        riskLevel: String(step.risk_level ?? params.risk_level ?? ''),
        requiresConfirmation: Boolean(step.requires_confirmation),
        hasArguments: Object.keys(argumentsValue).length > 0,
        argumentKeys: Object.keys(argumentsValue),
        dependsOn: Array.isArray(step.depends_on) ? step.depends_on : [],
      };
    });
}
