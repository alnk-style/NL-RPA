export type HostEnvironment = 'local' | 'vm' | 'unknown';
export type HostStatus = 'online' | 'busy' | 'offline' | 'unknown';
export type HostRequirement =
  | 'any'
  | 'headless_web'
  | 'interactive_desktop'
  | 'isolated_desktop';

export interface HostView {
  host_id?: number;
  name?: string;
  environment?: string;
  capabilities?: string[];
  status?: string;
  online?: boolean;
  last_seen_at?: string | null;
  agent_version?: string;
  platform?: string;
  active_task_count?: number;
  session_ready?: boolean;
  human_access_info?: {
    url?: string | null;
    instructions?: string | null;
  } | null;
  connection_security?: string;
}

export function normalizeHostEnvironment(value: unknown): HostEnvironment {
  const text = String(value ?? '').trim().toLowerCase();
  if (text === 'isolated') {
    return 'vm';
  }
  return text === 'local' || text === 'vm' ? text : 'unknown';
}

export function hostEnvironmentLabel(value: unknown): string {
  const environment = normalizeHostEnvironment(value);
  if (environment === 'vm') {
    return 'VM';
  }
  if (environment === 'local') {
    return '本机';
  }
  return '未知环境';
}

export function hostStatusLabel(value: unknown): string {
  const status = String(value ?? '').trim().toLowerCase();
  if (status === 'online') {
    return '在线';
  }
  if (status === 'busy') {
    return '忙碌';
  }
  if (status === 'offline') {
    return '离线';
  }
  return '未知';
}

export function canResumeOnHost(host: HostView | null | undefined): boolean {
  if (!host) {
    return true;
  }
  return String(host.status ?? '').toLowerCase() === 'online';
}

export function vmAccessFromHost(host: HostView | null | undefined): {
  url: string | null;
  instructions: string | null;
  hasAccess: boolean;
} {
  if (
    normalizeHostEnvironment(host?.environment)
    !== 'vm'
  ) {
    return {
      url: null,
      instructions: null,
      hasAccess: false,
    };
  }
  const url = String(host?.human_access_info?.url ?? '').trim() || null;
  const instructions =
    String(host?.human_access_info?.instructions ?? '').trim() || null;
  return {
    url,
    instructions,
    hasAccess: Boolean(url || instructions),
  };
}

export const HOST_REQUIREMENT_OPTIONS: Array<{
  value: HostRequirement;
  label: string;
}> = [
  { value: 'any', label: '自动选择' },
  { value: 'headless_web', label: '服务器无头 Web' },
  { value: 'interactive_desktop', label: '交互桌面' },
  { value: 'isolated_desktop', label: '隔离桌面（VM）' },
];

export function normalizeHostRequirement(value: unknown): HostRequirement {
  const text = String(value ?? 'any').trim();
  return HOST_REQUIREMENT_OPTIONS.some((item) => item.value === text)
    ? (text as HostRequirement)
    : 'any';
}

export function hostRequirementOptionsForExecutor(
  executor: unknown,
): typeof HOST_REQUIREMENT_OPTIONS {
  const value = String(executor ?? 'web').trim().toLowerCase();
  if (value === 'windows' || value === 'desktop') {
    return HOST_REQUIREMENT_OPTIONS.filter((item) =>
      ['any', 'interactive_desktop', 'isolated_desktop'].includes(item.value)
    );
  }
  if (value === 'web') {
    return HOST_REQUIREMENT_OPTIONS.filter((item) =>
      ['any', 'headless_web'].includes(item.value)
    );
  }
  return HOST_REQUIREMENT_OPTIONS.filter((item) => item.value === 'any');
}

export function buildHostEventsWebSocketUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = new URL('/api/hosts/events', `${protocol}//${window.location.host}`);
  const token = localStorage.getItem('token');
  if (token) {
    url.searchParams.set('token', token);
  }
  const workspaceId = localStorage.getItem('workspaceId');
  if (workspaceId) {
    url.searchParams.set('workspace_id', workspaceId);
  }
  return url.toString();
}

export function shouldWarnHostRestart(
  executions: Array<{
    workflow_id: number;
    status?: string;
    host_bound?: boolean;
    host_status?: string | null;
  }>,
  workflowId: number,
): boolean {
  const latest = executions.find(
    (execution) => execution.workflow_id === workflowId,
  );
  if (!latest) {
    return false;
  }
  return Boolean(
    latest.host_bound
    && ['failed', 'error', 'paused'].includes(String(latest.status))
    && String(latest.host_status).toLowerCase() !== 'online',
  );
}
