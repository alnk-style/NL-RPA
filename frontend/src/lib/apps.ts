import { summarizeTools } from './mcp-settings.ts';
import type { MCPServerRecord } from '../types/mcp';

export type AppStatus =
  | 'connected'
  | 'pending_authorization'
  | 'error'
  | 'untested'
  | 'disabled';

export type AppStatusFilter = 'all' | AppStatus;

export const APP_STATUS_OPTIONS: Array<{
  value: AppStatusFilter;
  label: string;
}> = [
  { value: 'all', label: '全部' },
  { value: 'connected', label: '已连接' },
  { value: 'pending_authorization', label: '待授权' },
  { value: 'error', label: '异常' },
  { value: 'disabled', label: '停用' },
];

const STATUS_LABELS: Record<AppStatus, string> = {
  connected: '已连接',
  pending_authorization: '待授权',
  error: '连接异常',
  untested: '尚未测试',
  disabled: '已停用',
};

export function appStatus(server: MCPServerRecord): AppStatus {
  if (!server.enabled) {
    return 'disabled';
  }
  if (server.last_error) {
    return 'error';
  }
  if (!server.last_tested_at) {
    return 'untested';
  }
  const summary = summarizeTools(server.tools);
  if (summary.pendingAuthorization > 0 || summary.missingConfirmation > 0) {
    return 'pending_authorization';
  }
  return 'connected';
}

export function appStatusLabel(status: AppStatus): string {
  return STATUS_LABELS[status];
}

export function appTransportLabel(server: MCPServerRecord): string {
  return server.transport === 'stdio' ? 'stdio' : 'HTTP';
}

export function appSearchText(server: MCPServerRecord): string {
  return [
    server.name,
    server.description ?? '',
    ...server.tools.map((tool) => tool.name),
    ...server.tools.map((tool) => tool.description ?? ''),
  ]
    .join(' ')
    .toLocaleLowerCase('zh-CN');
}

export function filterApps(
  servers: MCPServerRecord[],
  query: string,
  status: AppStatusFilter,
): MCPServerRecord[] {
  const normalized = query.trim().toLocaleLowerCase('zh-CN');
  return servers.filter((server) => (
    (status === 'all' || appStatus(server) === status) &&
    (!normalized || appSearchText(server).includes(normalized))
  ));
}

export function appStatusCounts(
  servers: MCPServerRecord[],
): Record<AppStatusFilter, number> {
  const counts: Record<AppStatusFilter, number> = {
    all: servers.length,
    connected: 0,
    pending_authorization: 0,
    error: 0,
    untested: 0,
    disabled: 0,
  };
  servers.forEach((server) => {
    counts[appStatus(server)] += 1;
  });
  return counts;
}
