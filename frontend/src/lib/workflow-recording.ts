import type {
  RecordingDraftStep,
  StructuredPlan,
  WorkflowStep,
} from '../types/workflow';

export interface MergeRecordingDraftOptions {
  replace?: boolean;
}

function numericStepId(value: unknown): number | null {
  if (typeof value === 'number' && Number.isInteger(value) && value > 0) {
    return value;
  }
  if (typeof value === 'string') {
    const text = value.trim();
    if (/^\d+$/.test(text)) {
      return Number(text);
    }
  }
  return null;
}

export function nextRecordingStepId(
  steps: WorkflowStep[],
): number {
  let maxId = 0;
  steps.forEach((step) => {
    const id = numericStepId(step.step_id);
    if (id !== null) {
      maxId = Math.max(maxId, id);
    }
  });
  return maxId + 1;
}

export function mergeRecordingDraft(
  plan: StructuredPlan,
  draftPlan: StructuredPlan,
  options: MergeRecordingDraftOptions = {},
): StructuredPlan {
  const existingSteps = Array.isArray(plan.steps) ? plan.steps : [];
  const draftSteps = Array.isArray(draftPlan.steps)
    ? draftPlan.steps
    : [];
  const replace = options.replace ?? existingSteps.length === 0;
  const baseSteps = replace ? [] : existingSteps;
  const startId = nextRecordingStepId(baseSteps);

  const idMap = new Map<string, number>();
  draftSteps.forEach((step, index) => {
    idMap.set(String(step.step_id ?? index + 1), startId + index);
  });

  const defaultExecutor =
    draftPlan.executor_type === 'windows' ? 'windows' : 'web';
  const mappedSteps: WorkflowStep[] = draftSteps.map((step, index) => {
    const key = String(step.step_id ?? index + 1);
    const rawDependencies = Array.isArray(step.depends_on)
      ? step.depends_on
      : [];
    const dependencies = rawDependencies
      .map((dependency) => idMap.get(String(dependency)))
      .filter(
        (dependency): dependency is number =>
          typeof dependency === 'number',
      );
    const executor =
      typeof step.executor === 'string' && step.executor
        ? step.executor
        : defaultExecutor;
    return {
      ...step,
      step_id: idMap.get(key) ?? startId + index,
      executor: executor as WorkflowStep['executor'],
      depends_on: dependencies,
    };
  });

  const capabilities = new Set<string>();
  (plan.required_capabilities ?? []).forEach((item) =>
    capabilities.add(String(item)),
  );
  (draftPlan.required_capabilities ?? []).forEach((item) =>
    capabilities.add(String(item)),
  );

  const merged: StructuredPlan = {
    ...plan,
    steps: [...baseSteps, ...mappedSteps],
    required_capabilities: Array.from(capabilities),
  };
  if (!merged.executor_type && draftPlan.executor_type) {
    merged.executor_type = draftPlan.executor_type;
  }
  return merged;
}

export function planExecutors(plan: StructuredPlan): string[] {
  const defaultExecutor =
    plan.executor_type === 'windows' ? 'windows' : 'web';
  const executors = new Set<string>();
  (plan.steps ?? []).forEach((step) => {
    const executor =
      typeof step.executor === 'string' && step.executor
        ? step.executor
        : defaultExecutor;
    executors.add(executor);
  });
  return Array.from(executors);
}

export function isMixedPlan(plan: StructuredPlan): boolean {
  return planExecutors(plan).length > 1;
}

export function describeRecordingStep(
  step: RecordingDraftStep,
): string {
  const parts: string[] = [];
  const locator = step.locator;
  if (locator && typeof locator === 'object') {
    const strategy = String(
      (locator as Record<string, unknown>).strategy ?? '',
    ).trim();
    const value = String(
      (locator as Record<string, unknown>).value ?? '',
    ).trim();
    if (strategy && value) {
      parts.push(`${strategy}=${value}`);
    }
  } else if (typeof locator === 'string' && locator.trim()) {
    parts.push(locator.trim());
  }

  const params = step.params;
  if (params && typeof params === 'object') {
    const entries = Object.entries(params as Record<string, unknown>);
    entries.slice(0, 3).forEach(([key, value]) => {
      if (value === null || value === undefined) {
        return;
      }
      const text =
        typeof value === 'object'
          ? JSON.stringify(value)
          : String(value);
      if (text) {
        parts.push(`${key}=${text}`);
      }
    });
  }
  if (parts.length === 0 && step.target) {
    parts.push(String(step.target));
  }
  return parts.join(' · ');
}

export function recordingCommand(
  options: {
    baseUrl: string;
    token?: string | null;
    recordingId: string;
    startUrl?: string | null;
  },
): string {
  const url = options.startUrl?.trim() || 'https://example.com';
  const token = options.token?.trim() || '<JWT>';
  return [
    'python scripts/record_browser.py',
    `--url "${url}"`,
    `--api ${options.baseUrl.replace(/\/$/, '')}`,
    `--token ${token}`,
    `--recording-id ${options.recordingId}`,
  ].join(' ');
}
