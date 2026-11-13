export const queryKeys = {
  workflows: ['workflows'] as const,
  workflow: (workflowId: number) => ['workflows', workflowId] as const,
  workflowVersions: (workflowId: number) =>
    ['workflows', workflowId, 'versions'] as const,
  workflowVersionDiff: (
    workflowId: number,
    fromVersionId: number,
    toVersionId: number,
  ) =>
    [
      'workflows',
      workflowId,
      'versions',
      'diff',
      fromVersionId,
      toVersionId,
    ] as const,
  executions: ['executions'] as const,
  execution: (executionId: number) =>
    ['executions', executionId] as const,
  executionEvents: (executionId: number) =>
    ['executions', executionId, 'events'] as const,
  humanTasks: ['human-tasks'] as const,
  humanTask: (humanTaskId: number) =>
    ['human-tasks', humanTaskId] as const,
  actionCatalog: ['action-catalog'] as const,
  emailSettings: ['settings', 'email'] as const,
  credentials: (workflowId: number) =>
    ['workflows', workflowId, 'credentials'] as const,
  hosts: ['hosts'] as const,
  browserProfiles: ['browser-profiles'] as const,
  visualSettings: ['settings', 'visual'] as const,
};

export type QueryKeys = typeof queryKeys;
