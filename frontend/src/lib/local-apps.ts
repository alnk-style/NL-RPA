import type {
  LocalAppBinding,
  LocalAppHostMode,
  LocalAppInfo,
  LocalAppState,
} from '../types/localApps';
import type { MCPServerRecord } from '../types/mcp';

export const LOCAL_APP_STATE_LABELS: Record<LocalAppState, string> = {
  discovered: '未启动',
  starting: '启动中',
  running: '运行中',
  stopping: '停止中',
  stopped: '已停止',
  error: '启动失败',
  unsupported: '当前不支持',
};

export type LocalAppBadgeVariant =
  | 'success'
  | 'warning'
  | 'danger'
  | 'secondary';

export function localAppStateLabel(state: LocalAppState): string {
  return LOCAL_APP_STATE_LABELS[state] ?? state;
}

export function localAppStateVariant(
  state: LocalAppState,
): LocalAppBadgeVariant {
  if (state === 'running') {
    return 'success';
  }
  if (state === 'starting' || state === 'stopping') {
    return 'warning';
  }
  if (state === 'error') {
    return 'danger';
  }
  return 'secondary';
}

export function hostModeLabel(mode: LocalAppHostMode): string {
  return mode === 'in_process' ? '同进程服务' : '独立进程';
}

export function bindingStatusLabel(
  binding: LocalAppBinding | null,
): string {
  if (!binding) {
    return '正在加载绑定';
  }
  if (!binding.bound) {
    return '尚未绑定';
  }
  return binding.is_owner ? '已绑定当前用户' : '已绑定其他用户';
}

export function hostRuntimeLabel(app: LocalAppInfo): string {
  if (app.host_mode === 'in_process') {
    return '当前应用以内嵌方式运行';
  }
  return app.state === 'running'
    ? '独立进程运行中'
    : '独立进程未运行';
}

export function localAppIsBusy(app: LocalAppInfo): boolean {
  return app.state === 'starting' || app.state === 'stopping';
}

export function localAppCanStart(app: LocalAppInfo): boolean {
  return !localAppIsBusy(app) && app.state !== 'unsupported';
}

export function localAppCanStop(app: LocalAppInfo): boolean {
  return (
    app.state === 'running' ||
    app.state === 'error' ||
    app.state === 'starting'
  );
}

export function localAppMatches(
  app: LocalAppInfo,
  query: string,
): boolean {
  const normalized = query.trim().toLocaleLowerCase('zh-CN');
  if (!normalized) {
    return true;
  }
  return [
    app.app_id,
    app.name,
    app.description,
    app.version,
    app.unsupported_reason ?? '',
  ]
    .join(' ')
    .toLocaleLowerCase('zh-CN')
    .includes(normalized);
}

export function linkedServerForLocalApp(
  app: LocalAppInfo,
  servers: MCPServerRecord[],
): MCPServerRecord | null {
  if (app.server_id !== null) {
    const byId = servers.find((server) => server.id === app.server_id);
    if (byId) {
      return byId;
    }
  }
  return (
    servers.find((server) => server.local_app_id === app.app_id) ?? null
  );
}

export function unlinkedServers(
  localApps: LocalAppInfo[],
  servers: MCPServerRecord[],
): MCPServerRecord[] {
  const linkedIds = new Set(
    localApps
      .map((app) => linkedServerForLocalApp(app, servers)?.id)
      .filter((id): id is number => typeof id === 'number'),
  );
  return servers.filter((server) => !linkedIds.has(server.id));
}

export function localAppStatusCounts(
  apps: LocalAppInfo[],
): Record<'all' | 'running' | 'stopped' | 'error' | 'unsupported', number> {
  const counts = {
    all: apps.length,
    running: 0,
    stopped: 0,
    error: 0,
    unsupported: 0,
  };
  apps.forEach((app) => {
    if (app.state === 'running') {
      counts.running += 1;
    } else if (
      app.state === 'discovered' ||
      app.state === 'stopped' ||
      app.state === 'starting' ||
      app.state === 'stopping'
    ) {
      counts.stopped += 1;
    } else if (app.state === 'error') {
      counts.error += 1;
    } else if (app.state === 'unsupported') {
      counts.unsupported += 1;
    }
  });
  return counts;
}
