import {
  ArrowRight,
  FileText,
  LoaderCircle,
  Plug,
  Power,
  Play,
  RotateCcw,
  Square,
} from 'lucide-react';

import {
  appStatus,
  appStatusLabel,
} from '../../lib/apps';
import { redactSensitiveText } from '../../lib/display';
import {
  hostModeLabel,
  localAppCanStart,
  localAppCanStop,
  localAppIsBusy,
  localAppStateLabel,
  localAppStateVariant,
} from '../../lib/local-apps';
import { summarizeTools } from '../../lib/mcp-settings';
import type {
  LocalAppBinding,
  LocalAppInfo,
} from '../../types/localApps';
import type { MCPServerRecord } from '../../types/mcp';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';

function statusVariant(
  status: ReturnType<typeof appStatus>,
): 'success' | 'warning' | 'danger' | 'secondary' {
  if (status === 'connected') {
    return 'success';
  }
  if (status === 'pending_authorization') {
    return 'warning';
  }
  if (status === 'error') {
    return 'danger';
  }
  return 'secondary';
}

function formatLastTested(value: string | null): string {
  if (!value) {
    return '尚未测试';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export interface AppCardProps {
  server: MCPServerRecord | null;
  localApp?: LocalAppInfo;
  binding?: LocalAppBinding | null;
  busyKey: string | null;
  onOpen: () => void;
  onTest?: () => void;
  onToggle?: () => void;
  onStart?: () => void;
  onStop?: () => void;
  onRestart?: () => void;
  onLogs?: () => void;
}

export function AppCard({
  server,
  localApp,
  binding,
  busyKey,
  onOpen,
  onTest,
  onToggle,
  onStart,
  onStop,
  onRestart,
  onLogs,
}: AppCardProps) {
  const status = server ? appStatus(server) : null;
  const summary = summarizeTools(server?.tools ?? []);
  const name = localApp?.name ?? server?.name ?? 'Unknown application';
  const description =
    localApp?.description || server?.description || '暂无应用描述';
  const testBusy = Boolean(server && busyKey === `${server.id}:test`);
  const toggleBusy = Boolean(server && busyKey === `${server.id}:toggle`);
  const testDisabled =
    testBusy ||
    toggleBusy ||
    Boolean(localApp && localApp.state !== 'running');
  const controlsLocked = Boolean(binding?.bound && !binding.is_owner);
  const localKey = localApp ? `local:${localApp.app_id}` : '';
  const startBusy = busyKey === `${localKey}:start`;
  const stopBusy = busyKey === `${localKey}:stop`;
  const restartBusy = busyKey === `${localKey}:restart`;
  const localBusy =
    Boolean(localApp && localAppIsBusy(localApp)) ||
    startBusy ||
    stopBusy ||
    restartBusy;
  const badgeVariant = localApp
    ? localAppStateVariant(localApp.state)
    : status
      ? statusVariant(status)
      : 'secondary';
  const badgeLabel = localApp
    ? localAppStateLabel(localApp.state)
    : status
      ? appStatusLabel(status)
      : '未连接';
  const lastError = localApp ? localApp.last_error : server?.last_error;

  return (
    <Card
      data-testid={
        localApp
          ? `local-app-card-${localApp.app_id}`
          : `mcp-app-card-${server?.id ?? 'unknown'}`
      }
      data-local-state={localApp?.state}
      className="flex min-h-[240px] min-w-0 flex-col overflow-hidden transition-colors hover:border-slate-300"
    >
      <button
        type="button"
        className="min-w-0 flex-1 p-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500"
        onClick={onOpen}
      >
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="break-words text-base font-semibold leading-6 text-slate-950 dark:text-white">
              {name}
            </h2>
            <p className="mt-1 line-clamp-2 min-h-10 break-words text-sm leading-5 text-slate-500 dark:text-slate-400">
              {description}
            </p>
          </div>
          <Badge variant={badgeVariant} className="shrink-0">
            {badgeLabel}
          </Badge>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
          <span>
            {localApp?.app_id ? `应用包 ${localApp.app_id}` : '外部 MCP'}
          </span>
          {localApp ? (
            <>
              <span aria-hidden="true">·</span>
              <span>{hostModeLabel(localApp.host_mode)}</span>
            </>
          ) : null}
          <span aria-hidden="true">·</span>
          <span>{summary.total || localApp?.tools_count || 0} 个工具</span>
          {server ? (
            <>
              <span aria-hidden="true">·</span>
              <span>{summary.authorized} 个已授权</span>
            </>
          ) : null}
          {summary.pendingAuthorization + summary.missingConfirmation > 0 ? (
            <>
              <span aria-hidden="true">·</span>
              <span className="text-amber-600">
                {summary.pendingAuthorization + summary.missingConfirmation} 个待授权
              </span>
            </>
          ) : null}
        </div>

        <div className="mt-3 flex items-center gap-1.5 text-xs text-slate-400">
          <Plug className="h-3.5 w-3.5" />
          {localApp
            ? localApp.state === 'running'
              ? localApp.host_mode === 'in_process'
                ? '同进程运行中 · 使用平台后端'
                : `独立进程运行中${localApp.port ? ` · 端口 ${localApp.port}` : ''}`
              : localApp.auto_start
                ? '后端启动时自动运行'
                : '按需启动本机服务'
            : `最近测试：${formatLastTested(server?.last_tested_at ?? null)}`}
        </div>

        {controlsLocked ? (
          <p className="mt-2 text-xs font-medium text-amber-600 dark:text-amber-300">
            已绑定其他用户，当前仅可查看
          </p>
        ) : null}

        {localApp?.unsupported_reason ? (
          <p className="mt-3 line-clamp-2 break-words rounded-md bg-amber-50 px-2.5 py-2 text-xs leading-5 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
            {redactSensitiveText(localApp.unsupported_reason)}
          </p>
        ) : lastError ? (
          <p className="mt-3 line-clamp-2 break-words rounded-md bg-red-50 px-2.5 py-2 text-xs leading-5 text-red-700 dark:bg-red-950/30 dark:text-red-300">
            {redactSensitiveText(lastError)}
          </p>
        ) : null}
      </button>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-3 py-2 dark:border-slate-800">
        <div className="flex flex-wrap items-center gap-1">
          {localApp ? (
            <>
              <Button
                variant="ghost"
                size="sm"
                data-testid={
                  localApp ? `local-start-${localApp.app_id}` : undefined
                }
                className="w-8 px-0"
                leftIcon={
                  startBusy || localApp.state === 'starting' ? (
                    <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Play className="h-3.5 w-3.5" />
                  )
                }
                disabled={
                  !localAppCanStart(localApp) ||
                  localBusy ||
                  controlsLocked
                }
                onClick={onStart}
                aria-label="启动本机服务"
                title="启动本机服务"
              />
              <Button
                variant="ghost"
                size="sm"
                data-testid={
                  localApp ? `local-stop-${localApp.app_id}` : undefined
                }
                className="w-8 px-0"
                leftIcon={
                  stopBusy || localApp.state === 'stopping' ? (
                    <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Square className="h-3.5 w-3.5" />
                  )
                }
                disabled={
                  !localAppCanStop(localApp) ||
                  localBusy ||
                  controlsLocked
                }
                onClick={onStop}
                aria-label="停止本机服务"
                title="停止本机服务"
              />
              <Button
                variant="ghost"
                size="sm"
                data-testid={
                  localApp ? `local-restart-${localApp.app_id}` : undefined
                }
                className="w-8 px-0"
                leftIcon={
                  restartBusy ? (
                    <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <RotateCcw className="h-3.5 w-3.5" />
                  )
                }
                disabled={
                  localApp.state === 'discovered' ||
                  localApp.state === 'unsupported' ||
                  localBusy ||
                  controlsLocked
                }
                onClick={onRestart}
                aria-label="重启本机服务"
                title="重启本机服务"
              />
              <Button
                variant="ghost"
                size="sm"
                data-testid={
                  localApp ? `local-logs-${localApp.app_id}` : undefined
                }
                className="w-8 px-0"
                leftIcon={<FileText className="h-3.5 w-3.5" />}
                onClick={onLogs}
                disabled={localBusy || controlsLocked}
                aria-label="查看运行日志"
                title="查看运行日志"
              />
            </>
          ) : null}
          {server ? (
            <Button
              variant="ghost"
              size="sm"
              data-testid={server ? `local-test-${server.id}` : undefined}
              leftIcon={
                testBusy ? (
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Plug className="h-3.5 w-3.5" />
                )
              }
              disabled={testDisabled}
              onClick={onTest}
            >
              测试连接
            </Button>
          ) : null}
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            data-testid={
              localApp
                ? `local-details-${localApp.app_id}`
                : `mcp-details-${server?.id ?? 'unknown'}`
            }
            onClick={onOpen}
            rightIcon={<ArrowRight className="h-3.5 w-3.5" />}
          >
            详情
          </Button>
          {server && !localApp ? (
            <Button
              variant="ghost"
              size="sm"
              data-testid={
                server ? `mcp-toggle-${server.id}` : undefined
              }
              className="w-8 px-0"
              leftIcon={
                toggleBusy ? (
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Power className="h-3.5 w-3.5" />
                )
              }
              aria-label={server.enabled ? '停用应用' : '启用应用'}
              title={server.enabled ? '停用应用' : '启用应用'}
              disabled={testBusy || toggleBusy}
              onClick={onToggle}
            />
          ) : null}
        </div>
      </div>
    </Card>
  );
}

export default AppCard;
