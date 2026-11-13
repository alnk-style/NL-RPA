import {
  Activity,
  AlertTriangle,
  ChevronDown,
  CircleCheck,
  Clock3,
  Code2,
  FileText,
  FileWarning,
  LoaderCircle,
  Play,
  Plug,
  RotateCcw,
  ShieldCheck,
  ShieldX,
  Square,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import {
  bindingStatusLabel,
  hostModeLabel,
  localAppCanStart,
  localAppCanStop,
  localAppStateLabel,
  localAppStateVariant,
} from '../../lib/local-apps';
import {
  auditStatusLabel,
  auditStatusVariant,
  formatDurationMs,
  riskLabel,
  riskVariant,
  summarizeTools,
  toolAuthorizationLabel,
  toolNeedsConfirmation,
} from '../../lib/mcp-settings';
import { redactSensitiveText } from '../../lib/display';
import { cn } from '../../lib/utils';
import type {
  LocalAppBinding,
  LocalAppInfo,
} from '../../types/localApps';
import type {
  MCPServerRecord,
  MCPToolCallAudit,
  MCPToolRecord,
} from '../../types/mcp';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Switch } from '../ui/Switch';

type TabId =
  | 'overview'
  | 'local'
  | 'tools'
  | 'permissions'
  | 'logs'
  | 'audits'
  | 'advanced';

const TABS: Array<{ id: TabId; label: string }> = [
  { id: 'overview', label: '概览' },
  { id: 'local', label: '本地服务' },
  { id: 'tools', label: '工具' },
  { id: 'permissions', label: '权限' },
  { id: 'logs', label: '运行日志' },
  { id: 'audits', label: '调用记录' },
  { id: 'advanced', label: '高级配置' },
];

export interface LocalAppDetailsPanelProps {
  app: LocalAppInfo;
  server: MCPServerRecord | null;
  binding: LocalAppBinding | null;
  bindingLoading: boolean;
  audits: MCPToolCallAudit[];
  auditLoading: boolean;
  busyKey: string | null;
  onClose: () => void;
  onStart: () => void;
  onStop: () => void;
  onRestart: () => void;
  onLogs: () => void;
  onAutoStartChange: (autoStart: boolean) => void;
  onUnbind: () => void;
  onDeleteGenerated: () => void;
  onTest: () => void;
  onToggle: () => void;
  onRefreshAudits: () => void;
  onAuthorize: (
    tool: MCPToolRecord,
    authorized: boolean,
    confirmed: boolean,
  ) => void;
}

export function LocalAppDetailsPanel({
  app,
  server,
  binding,
  bindingLoading,
  audits,
  auditLoading,
  busyKey,
  onClose,
  onStart,
  onStop,
  onRestart,
  onLogs,
  onAutoStartChange,
  onUnbind,
  onDeleteGenerated,
  onTest,
  onToggle,
  onRefreshAudits,
  onAuthorize,
}: LocalAppDetailsPanelProps) {
  const [activeTab, setActiveTab] = useState<TabId>('overview');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [confirmMap, setConfirmMap] = useState<Record<string, boolean>>({});
  const [confirmUnbind, setConfirmUnbind] = useState(false);
  const [confirmDeleteGenerated, setConfirmDeleteGenerated] =
    useState(false);
  const summary = useMemo(
    () => summarizeTools(server?.tools ?? []),
    [server?.tools],
  );
  const localKey = `local:${app.app_id}`;
  const localBusy =
    busyKey === `${localKey}:start` ||
    busyKey === `${localKey}:stop` ||
    busyKey === `${localKey}:restart`;
  const controlsLocked = Boolean(binding?.bound && !binding.is_owner);
  const testBusy = Boolean(server && busyKey === `${server.id}:test`);
  const toggleBusy = Boolean(server && busyKey === `${server.id}:toggle`);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (confirmUnbind) {
          setConfirmUnbind(false);
        } else {
          onClose();
        }
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [confirmUnbind, onClose]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-950/40">
      <button
        type="button"
        className="absolute inset-0 cursor-default"
        aria-label="关闭应用详情"
        onClick={onClose}
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="local-app-detail-title"
        data-testid="local-app-details"
        className="relative flex h-full w-full flex-col overflow-hidden border-l border-slate-200 bg-white shadow-2xl sm:max-w-3xl dark:border-slate-800 dark:bg-slate-900"
      >
        <header className="border-b border-slate-200 px-4 py-3 sm:px-5 dark:border-slate-800">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2
                  id="local-app-detail-title"
                  className="break-words text-lg font-semibold text-slate-950 dark:text-white"
                >
                  {app.name}
                </h2>
                <Badge variant={localAppStateVariant(app.state)}>
                  {localAppStateLabel(app.state)}
                </Badge>
                <Badge variant="outline">
                  {hostModeLabel(app.host_mode)}
                </Badge>
                {server ? (
                  <Badge variant={server.enabled ? 'outline' : 'secondary'}>
                    MCP {server.enabled ? '已启用' : '已停用'}
                  </Badge>
                ) : null}
              </div>
              <p className="mt-1 break-words text-sm text-slate-500 dark:text-slate-400">
                {app.description || '暂无应用描述'}
              </p>
              <p className="mt-1 break-all font-mono text-[11px] text-slate-400">
                {app.app_id} · v{app.version} · {app.platform}
              </p>
            </div>
            <button
              type="button"
              data-testid="local-details-close"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-800 dark:hover:text-white"
              onClick={onClose}
              aria-label="关闭应用详情"
              title="关闭"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              variant="secondary"
              size="sm"
              data-testid="local-details-start"
              leftIcon={
                busyKey === `${localKey}:start` ? (
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Play className="h-3.5 w-3.5" />
                )
              }
              disabled={
                !localAppCanStart(app) ||
                localBusy ||
                controlsLocked
              }
              onClick={onStart}
            >
              启动
            </Button>
            <Button
              variant="secondary"
              size="sm"
              data-testid="local-details-stop"
              leftIcon={
                busyKey === `${localKey}:stop` ? (
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Square className="h-3.5 w-3.5" />
                )
              }
              disabled={
                !localAppCanStop(app) ||
                localBusy ||
                controlsLocked
              }
              onClick={onStop}
            >
              停止
            </Button>
            <Button
              variant="ghost"
              size="sm"
              data-testid="local-details-restart"
              leftIcon={
                busyKey === `${localKey}:restart` ? (
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <RotateCcw className="h-3.5 w-3.5" />
                )
              }
              disabled={
                app.state === 'discovered' ||
                app.state === 'unsupported' ||
                localBusy ||
                controlsLocked
              }
              onClick={onRestart}
            >
              重启
            </Button>
            <Button
              variant="ghost"
              size="sm"
              data-testid="local-details-logs"
              leftIcon={<FileText className="h-3.5 w-3.5" />}
              onClick={onLogs}
              disabled={controlsLocked}
            >
              日志
            </Button>
            {server ? (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  data-testid="local-details-test"
                  leftIcon={
                    testBusy ? (
                      <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Plug className="h-3.5 w-3.5" />
                    )
                  }
                  disabled={
                    testBusy ||
                    app.state !== 'running' ||
                    controlsLocked
                  }
                  onClick={onTest}
                >
                  测试连接
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  data-testid="local-details-toggle"
                  disabled={toggleBusy || controlsLocked}
                  onClick={onToggle}
                >
                  {server.enabled ? '停用 MCP' : '启用 MCP'}
                </Button>
              </>
            ) : null}
          </div>

          <div className="mt-4 flex flex-wrap gap-1 pb-1">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                data-testid={`local-details-tab-${tab.id}`}
                className={cn(
                  'shrink-0 rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                  activeTab === tab.id
                    ? 'bg-slate-900 text-white'
                    : 'text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-800 dark:hover:text-white',
                )}
                onClick={() => setActiveTab(tab.id)}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-5">
          {activeTab === 'overview' ? (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-3">
                <Metric label="声明工具" value={app.tools_count} />
                <Metric label="已发现工具" value={server?.tools.length ?? 0} />
                <Metric label="已授权" value={summary.authorized} />
              </div>
              <InfoGrid
                items={[
                  ['运行状态', localAppStateLabel(app.state)],
                  ['Host Mode', hostModeLabel(app.host_mode)],
                  ['本机 URL', app.url || '—'],
                  [
                    'PID',
                    app.host_mode === 'in_process'
                      ? '无独立进程'
                      : app.pid
                        ? String(app.pid)
                        : '—',
                  ],
                  [
                    '端口',
                    app.host_mode === 'in_process'
                      ? '使用平台后端'
                      : app.port
                        ? String(app.port)
                        : '—',
                  ],
                  ['启动时间', formatDateTime(app.started_at)],
                  ['最近健康检查', formatDateTime(app.last_health_at)],
                ]}
              />
              <ErrorBlock
                error={app.unsupported_reason || app.last_error}
              />
            </div>
          ) : null}

          {activeTab === 'local' ? (
            <div className="space-y-4">
              <div className="flex items-start gap-3 rounded-md border border-slate-200 p-4 dark:border-slate-800">
                <Activity className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                <div>
                  <p className="text-sm font-medium text-slate-900 dark:text-white">
                    {app.host_mode === 'in_process'
                      ? '同进程 MCP 宿主'
                      : '单机受控进程'}
                  </p>
                  <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">
                    {app.host_mode === 'in_process'
                      ? '应用加载在当前后端进程内，直接复用平台 Connector，不创建独立进程或监听端口。'
                      : '平台只启动经 Manifest 校验的内置应用包，运行参数由后端生成，不接受前端传入命令、路径、端口或监听地址。'}
                  </p>
                </div>
              </div>
              <InfoGrid
                items={[
                  ['状态', localAppStateLabel(app.state)],
                  ['Host Mode', hostModeLabel(app.host_mode)],
                  ['本机 URL', app.url || '—'],
                  [
                    'PID',
                    app.host_mode === 'in_process'
                      ? '无独立进程'
                      : app.pid
                        ? String(app.pid)
                        : '—',
                  ],
                  [
                    '端口',
                    app.host_mode === 'in_process'
                      ? '使用平台后端'
                      : app.port
                        ? String(app.port)
                        : '—',
                  ],
                  ['Server ID', server ? String(server.id) : '—'],
                ]}
              />
              <div className="rounded-md border border-slate-200 p-4 dark:border-slate-800">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-slate-900 dark:text-white">
                      {bindingStatusLabel(binding)}
                    </p>
                    <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">
                      {!binding?.bound
                        ? '需要先启动一次并完成绑定，之后才能开启自动启动。'
                        : binding.is_owner
                          ? binding.workspace_bound
                            ? '已绑定当前用户和工作区。'
                            : '已绑定当前用户，当前未绑定独立工作区。'
                          : '该应用已由其他用户绑定，当前用户只能查看。'}
                    </p>
                  </div>
                  <label className="flex shrink-0 items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
                    <Switch
                      checked={binding?.auto_start ?? false}
                      onCheckedChange={onAutoStartChange}
                      label={`${app.name} 自动启动`}
                      data-testid="local-binding-auto-start"
                      disabled={
                        bindingLoading ||
                        !binding?.is_owner ||
                        controlsLocked
                      }
                    />
                    自动启动
                  </label>
                </div>
                {binding?.is_owner ? (
                  <div className="mt-3 flex flex-wrap justify-end gap-2">
                    {app.generated ? (
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => setConfirmDeleteGenerated(true)}
                        disabled={bindingLoading}
                      >
                        删除生成的 MCP
                      </Button>
                    ) : null}
                    <Button
                      variant="danger"
                      size="sm"
                      data-testid="local-binding-unbind"
                      onClick={() => setConfirmUnbind(true)}
                      disabled={bindingLoading}
                    >
                      解除绑定
                    </Button>
                  </div>
                ) : null}
              </div>
              {app.unsupported_reason ? (
                <div className="flex items-start gap-3 rounded-md border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                  <p>{redactSensitiveText(app.unsupported_reason)}</p>
                </div>
              ) : null}
              <ErrorBlock error={app.last_error} />
            </div>
          ) : null}

          {activeTab === 'tools' ? (
            <div className="space-y-2">
              {!server || server.tools.length === 0 ? (
                <EmptyState
                  icon={Plug}
                  title="尚未发现工具"
                  description={
                    app.state === 'running'
                      ? '测试连接后工具清单会出现在这里。'
                      : '启动本机服务并完成工具发现后，这里会显示工具和风险级别。'
                  }
                />
              ) : (
                server.tools.map((tool) => (
                  <ToolRow
                    key={tool.name}
                    server={server}
                    tool={tool}
                    busyKey={busyKey}
                    confirmed={confirmMap[tool.name] ?? true}
                    onConfirmChange={(value) =>
                      setConfirmMap((current) => ({
                        ...current,
                        [tool.name]: value,
                      }))
                    }
                    onAuthorize={onAuthorize}
                  />
                ))
              )}
            </div>
          ) : null}

          {activeTab === 'permissions' ? (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-3">
                <Metric label="只读工具" value={summary.readOnly} />
                <Metric label="写入工具" value={summary.mutating} />
                <Metric label="高风险工具" value={summary.destructive} />
              </div>
              <div className="rounded-md border border-slate-200 p-4 dark:border-slate-800">
                <div className="flex items-start gap-3">
                  <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                  <div>
                    <p className="text-sm font-medium text-slate-900 dark:text-white">
                      授权与审计复用平台 MCP Registry
                    </p>
                    <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">
                      只读工具默认允许；写入工具需要授权；高风险工具还需要确认。
                      所有调用都会写入现有审计记录。
                    </p>
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {activeTab === 'logs' ? (
            <div className="space-y-4">
              <EmptyState
                icon={FileText}
                title="最近 500 行运行日志"
                description={
                  app.host_mode === 'in_process'
                    ? '同进程应用没有独立进程标准输出；无可用日志时会显示内嵌运行提示。'
                    : '日志不会自动轮询。打开后按需刷新，并只显示已脱敏的有限行数。'
                }
              />
              <div className="flex justify-center">
                <Button
                  variant="secondary"
                  leftIcon={<FileText className="h-4 w-4" />}
                  data-testid="local-details-open-logs"
                  onClick={onLogs}
                >
                  打开日志
                </Button>
              </div>
            </div>
          ) : null}

          {activeTab === 'audits' ? (
            <div className="space-y-3">
              <div className="flex justify-end">
                <Button
                  variant="secondary"
                  size="sm"
                  leftIcon={
                    auditLoading ? (
                      <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Clock3 className="h-3.5 w-3.5" />
                    )
                  }
                  disabled={auditLoading || !server}
                  onClick={onRefreshAudits}
                >
                  刷新记录
                </Button>
              </div>
              {!server ? (
                <EmptyState
                  icon={Clock3}
                  title="尚无 MCP 调用记录"
                  description="启动应用并测试连接后才能查询该应用的调用审计。"
                />
              ) : audits.length === 0 ? (
                <EmptyState
                  icon={Clock3}
                  title="暂无调用记录"
                  description="工具调用后会在这里显示授权、状态、耗时和审计编号。"
                />
              ) : (
                audits.map((audit) => (
                  <div
                    key={audit.id}
                    className="rounded-md border border-slate-200 px-3 py-3 dark:border-slate-800"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="min-w-0">
                        <p className="break-all text-sm font-medium text-slate-900 dark:text-white">
                          {audit.tool}
                        </p>
                        <p className="mt-1 text-xs text-slate-500">
                          #{audit.id} · {formatDateTime(audit.created_at)}
                        </p>
                      </div>
                      <Badge variant={auditStatusVariant(audit.status)}>
                        {auditStatusLabel(audit.status)}
                      </Badge>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500">
                      <span>{riskLabel(audit.risk_level)}</span>
                      <span>
                        {audit.decision === 'denied' ? '已拒绝' : '已允许'}
                      </span>
                      <span>{formatDurationMs(audit.duration_ms)}</span>
                    </div>
                    {audit.error ? (
                      <p className="mt-2 line-clamp-2 break-words text-xs leading-5 text-red-600 dark:text-red-300">
                        {redactSensitiveText(audit.error)}
                      </p>
                    ) : null}
                  </div>
                ))
              )}
            </div>
          ) : null}

          {activeTab === 'advanced' ? (
            <div className="space-y-3">
              <button
                type="button"
                data-testid="local-details-toggle-advanced"
                className="flex w-full items-center justify-between rounded-md border border-slate-200 px-3 py-2.5 text-left dark:border-slate-800"
                onClick={() => setAdvancedOpen((current) => !current)}
                aria-expanded={advancedOpen}
              >
                <span className="flex items-center gap-2 text-sm font-medium text-slate-800 dark:text-slate-100">
                  <Code2 className="h-4 w-4 text-slate-400" />
                  MCP 与进程参数
                </span>
                <ChevronDown
                  className={cn(
                    'h-4 w-4 text-slate-400 transition-transform',
                    advancedOpen && 'rotate-180',
                  )}
                />
              </button>
              {advancedOpen ? (
                <InfoGrid
                  items={[
                    ['应用 ID', app.app_id],
                    ['Manifest 版本', app.version],
                    ['平台', app.platform],
                    ['Host Mode', hostModeLabel(app.host_mode)],
                    ['运行时', app.transport_kind],
                    ['传输方式', server?.transport || 'http'],
                    [
                      'MCP URL',
                      redactSensitiveText(server?.url || app.url),
                    ],
                    [
                      'PID',
                      app.host_mode === 'in_process'
                        ? '无独立进程'
                        : app.pid
                          ? String(app.pid)
                          : '—',
                    ],
                    ['工具指纹', server?.toolset_fingerprint || '—'],
                    ['Server ID', server ? String(server.id) : '—'],
                  ]}
                />
              ) : (
                <p className="text-xs leading-5 text-slate-500">
                  高级参数默认折叠。访问令牌、环境变量、完整命令和内部项目路径不会显示。
                </p>
              )}
            </div>
          ) : null}
        </div>
      </aside>
      {confirmUnbind ? (
        <div className="fixed inset-0 z-[55] flex items-center justify-center bg-slate-950/50 p-4">
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="unbind-local-app-title"
            className="w-full max-w-md rounded-lg border border-slate-200 bg-white p-5 shadow-2xl dark:border-slate-800 dark:bg-slate-900"
          >
            <h3
              id="unbind-local-app-title"
              className="text-base font-semibold text-slate-950 dark:text-white"
            >
              解除本地应用绑定？
            </h3>
            <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">
              解除绑定会立即停止应用、关闭自动启动并清除本地访问凭据。其他用户之后可以重新绑定。
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button
                variant="secondary"
                size="sm"
                data-testid="local-binding-cancel"
                onClick={() => setConfirmUnbind(false)}
              >
                取消
              </Button>
              <Button
                variant="danger"
                size="sm"
                data-testid="local-binding-confirm"
                onClick={() => {
                  setConfirmUnbind(false);
                  onUnbind();
                }}
              >
                确认解除
              </Button>
            </div>
          </div>
        </div>
      ) : null}
      {confirmDeleteGenerated ? (
        <div className="fixed inset-0 z-[55] flex items-center justify-center bg-slate-950/50 p-4">
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="delete-generated-app-title"
            className="w-full max-w-md rounded-lg border border-slate-200 bg-white p-5 shadow-2xl dark:border-slate-800 dark:bg-slate-900"
          >
            <h3
              id="delete-generated-app-title"
              className="text-base font-semibold text-slate-950 dark:text-white"
            >
              删除生成的 MCP？
            </h3>
            <p className="mt-2 text-sm leading-6 text-slate-500 dark:text-slate-400">
              将停止并移除该应用包和绑定。第三方软件本身以及 Agent 白名单不会被删除。
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setConfirmDeleteGenerated(false)}
              >
                取消
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={() => {
                  setConfirmDeleteGenerated(false);
                  onDeleteGenerated();
                }}
              >
                确认删除
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function InfoGrid({ items }: { items: Array<[string, string]> }) {
  return (
    <dl className="grid gap-px overflow-hidden rounded-lg border border-slate-200 bg-slate-200 sm:grid-cols-2 dark:border-slate-800 dark:bg-slate-800">
      {items.map(([label, value]) => (
        <div
          key={label}
          className="min-w-0 bg-white px-3 py-3 dark:bg-slate-900"
        >
          <dt className="text-xs text-slate-500">{label}</dt>
          <dd className="mt-1 break-words text-sm font-medium text-slate-900 dark:text-slate-100">
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function ErrorBlock({ error }: { error: string | null | undefined }) {
  return (
    <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
      <p className="text-xs font-medium text-slate-500">最近错误</p>
      <p className="mt-2 break-words text-sm leading-6 text-slate-700 dark:text-slate-300">
        {redactSensitiveText(error, '暂无错误记录。')}
      </p>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-slate-200 px-3 py-3 dark:border-slate-800">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold text-slate-900 dark:text-white">
        {value}
      </p>
    </div>
  );
}

function EmptyState({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof Plug;
  title: string;
  description: string;
}) {
  return (
    <div className="flex min-h-44 flex-col items-center justify-center rounded-md border border-dashed border-slate-300 px-4 text-center dark:border-slate-700">
      <Icon className="h-5 w-5 text-slate-400" />
      <p className="mt-2 text-sm font-medium text-slate-700 dark:text-slate-200">
        {title}
      </p>
      <p className="mt-1 max-w-md text-xs leading-5 text-slate-500">
        {description}
      </p>
    </div>
  );
}

function ToolRow({
  server,
  tool,
  busyKey,
  confirmed,
  onConfirmChange,
  onAuthorize,
}: {
  server: MCPServerRecord;
  tool: MCPToolRecord;
  busyKey: string | null;
  confirmed: boolean;
  onConfirmChange: (value: boolean) => void;
  onAuthorize: LocalAppDetailsPanelProps['onAuthorize'];
}) {
  const busy = busyKey === `${server.id}:${tool.name}:authorize`;
  const effectiveAuthorized =
    tool.risk_level === 'read_only' ||
    (tool.authorized &&
      (!toolNeedsConfirmation(tool) || tool.confirmed));

  return (
    <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="break-all text-sm font-medium text-slate-900 dark:text-white">
              {tool.name}
            </p>
            <Badge variant={riskVariant(tool.risk_level)}>
              {riskLabel(tool.risk_level)}
            </Badge>
            <Badge variant={effectiveAuthorized ? 'success' : 'warning'}>
              {toolAuthorizationLabel(tool)}
            </Badge>
            {tool.available === false ? (
              <Badge variant="secondary">不可用</Badge>
            ) : null}
          </div>
          <p className="mt-1 break-words text-xs leading-5 text-slate-500 dark:text-slate-400">
            {tool.description || '暂无工具说明'}
          </p>
          <p className="mt-1 break-all text-[11px] text-slate-400">
            参数：{schemaSummary(tool)}
          </p>
        </div>
        {tool.risk_level === 'read_only' ? (
          <CircleCheck className="h-4 w-4 shrink-0 text-emerald-600" />
        ) : (
          <Button
            variant="secondary"
            size="sm"
            leftIcon={
              busy ? (
                <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
              ) : effectiveAuthorized ? (
                <ShieldX className="h-3.5 w-3.5" />
              ) : (
                <ShieldCheck className="h-3.5 w-3.5" />
              )
            }
            disabled={busy || tool.available === false}
            onClick={() =>
              onAuthorize(
                tool,
                !effectiveAuthorized,
                !effectiveAuthorized &&
                  toolNeedsConfirmation(tool) &&
                  confirmed,
              )
            }
          >
            {effectiveAuthorized ? '取消授权' : '授权'}
          </Button>
        )}
      </div>
      {toolNeedsConfirmation(tool) && !effectiveAuthorized ? (
        <label className="mt-3 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
          <Switch
            checked={confirmed}
            onCheckedChange={onConfirmChange}
            label={`确认授权 ${tool.name}`}
          />
          授权时同时确认高风险操作
        </label>
      ) : null}
      {tool.available === false ? (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-amber-600">
          <FileWarning className="h-3.5 w-3.5" />
          工具最近一次发现时已不可用。
        </p>
      ) : null}
    </div>
  );
}

function schemaSummary(tool: MCPToolRecord): string {
  const schema = tool.input_schema ?? {};
  const properties = (
    schema.properties &&
    typeof schema.properties === 'object' &&
    !Array.isArray(schema.properties)
  )
    ? schema.properties as Record<string, unknown>
    : {};
  const required = Array.isArray(schema.required)
    ? schema.required.map(String)
    : [];
  const keys = Object.keys(properties);
  if (keys.length === 0) {
    return '无参数';
  }
  return `${keys.join('、')}${
    required.length > 0 ? `，必填 ${required.join('、')}` : ''
  }`;
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return '—';
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      }).format(date);
}

export default LocalAppDetailsPanel;
