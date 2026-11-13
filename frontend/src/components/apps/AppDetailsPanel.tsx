import {
  AlertTriangle,
  ChevronDown,
  CircleCheck,
  Clock3,
  Code2,
  FileWarning,
  LoaderCircle,
  Plug,
  Power,
  RefreshCw,
  ShieldCheck,
  ShieldX,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import {
  appStatus,
  appStatusLabel,
  appTransportLabel,
} from '../../lib/apps';
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
  MCPServerRecord,
  MCPToolCallAudit,
  MCPToolRecord,
} from '../../types/mcp';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { Switch } from '../ui/Switch';

type TabId = 'overview' | 'tools' | 'permissions' | 'audits' | 'advanced';

const TABS: Array<{ id: TabId; label: string }> = [
  { id: 'overview', label: '概览' },
  { id: 'tools', label: '工具' },
  { id: 'permissions', label: '权限' },
  { id: 'audits', label: '调用记录' },
  { id: 'advanced', label: '高级配置' },
];

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
  const requiredText = required.length > 0
    ? `，必填 ${required.join('、')}`
    : '';
  return `${keys.join('、')}${requiredText}`;
}

function StatusBadge({ server }: { server: MCPServerRecord }) {
  const status = appStatus(server);
  return (
    <Badge
      variant={
        status === 'connected'
          ? 'success'
          : status === 'pending_authorization'
            ? 'warning'
            : status === 'error'
              ? 'danger'
              : 'secondary'
      }
    >
      {appStatusLabel(status)}
    </Badge>
  );
}

export interface AppDetailsPanelProps {
  server: MCPServerRecord;
  audits: MCPToolCallAudit[];
  auditLoading: boolean;
  busyKey: string | null;
  onClose: () => void;
  onTest: (server: MCPServerRecord) => void;
  onToggle: (server: MCPServerRecord) => void;
  onRefreshAudits: (server: MCPServerRecord) => void;
  onAuthorize: (
    server: MCPServerRecord,
    tool: MCPToolRecord,
    authorized: boolean,
    confirmed: boolean,
  ) => void;
}

export function AppDetailsPanel({
  server,
  audits,
  auditLoading,
  busyKey,
  onClose,
  onTest,
  onToggle,
  onRefreshAudits,
  onAuthorize,
}: AppDetailsPanelProps) {
  const [activeTab, setActiveTab] = useState<TabId>('overview');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [confirmMap, setConfirmMap] = useState<Record<string, boolean>>({});
  const summary = useMemo(() => summarizeTools(server.tools), [server.tools]);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

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
        aria-labelledby="app-detail-title"
        className="relative flex h-full w-full flex-col overflow-hidden border-l border-slate-200 bg-white shadow-2xl sm:max-w-2xl dark:border-slate-800 dark:bg-slate-900"
      >
        <header className="border-b border-slate-200 px-4 py-3 sm:px-5 dark:border-slate-800">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2
                  id="app-detail-title"
                  className="break-words text-lg font-semibold text-slate-950 dark:text-white"
                >
                  {server.name}
                </h2>
                <StatusBadge server={server} />
              </div>
              <p className="mt-1 break-words text-sm text-slate-500 dark:text-slate-400">
                {server.description || '暂无应用描述'}
              </p>
            </div>
            <button
              type="button"
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
              leftIcon={
                busyKey === `${server.id}:test` ? (
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Plug className="h-3.5 w-3.5" />
                )
              }
              disabled={busyKey === `${server.id}:test`}
              onClick={() => onTest(server)}
            >
              测试连接
            </Button>
            <Button
              variant="ghost"
              size="sm"
              leftIcon={<Power className="h-3.5 w-3.5" />}
              onClick={() => onToggle(server)}
              disabled={busyKey === `${server.id}:toggle`}
            >
              {server.enabled ? '停用应用' : '启用应用'}
            </Button>
          </div>
          <div className="mt-4 flex gap-1 overflow-x-auto pb-1">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
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
              <dl className="grid gap-px overflow-hidden rounded-lg border border-slate-200 bg-slate-200 sm:grid-cols-2 dark:border-slate-800 dark:bg-slate-800">
                {[
                  ['接入方式', appTransportLabel(server)],
                  ['工具数量', String(summary.total)],
                  ['最近测试', formatDateTime(server.last_tested_at)],
                  ['默认风险', riskLabel(server.default_risk)],
                ].map(([label, value]) => (
                  <div
                    key={label}
                    className="bg-white px-3 py-3 dark:bg-slate-900"
                  >
                    <dt className="text-xs text-slate-500">{label}</dt>
                    <dd className="mt-1 break-words text-sm font-medium text-slate-900 dark:text-slate-100">
                      {value}
                    </dd>
                  </div>
                ))}
              </dl>
              <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
                <p className="text-xs font-medium text-slate-500">最近错误</p>
                <p className="mt-2 break-words text-sm leading-6 text-slate-700 dark:text-slate-300">
                  {redactSensitiveText(
                    server.last_error,
                    '暂无错误记录。',
                  )}
                </p>
              </div>
            </div>
          ) : null}

          {activeTab === 'tools' ? (
            <div className="space-y-2">
              {server.tools.length === 0 ? (
                <EmptyState
                  icon={Plug}
                  title="尚未发现工具"
                  description="先测试连接，工具清单会在这里出现。"
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
                      当前授权策略
                    </p>
                    <p className="mt-1 text-sm leading-6 text-slate-500 dark:text-slate-400">
                      只读工具默认允许；写入工具需要明确授权；高风险工具还需要执行前确认。
                    </p>
                  </div>
                </div>
              </div>
              <div className="flex items-start gap-3 rounded-md border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <p>
                  流程执行前仍需确认工具参数、风险级别和授权状态。平台不会因工具已发现而自动执行。
                </p>
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
                      <RefreshCw className="h-3.5 w-3.5" />
                    )
                  }
                  disabled={auditLoading}
                  onClick={() => onRefreshAudits(server)}
                >
                  刷新记录
                </Button>
              </div>
              {audits.length === 0 ? (
                <EmptyState
                  icon={Clock3}
                  title="暂无调用记录"
                  description="工具调用后会在这里显示授权、状态和耗时。"
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
                          {formatDateTime(audit.created_at)}
                        </p>
                      </div>
                      <Badge variant={auditStatusVariant(audit.status)}>
                        {auditStatusLabel(audit.status)}
                      </Badge>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500">
                      <span>{riskLabel(audit.risk_level)}</span>
                      <span>{audit.decision === 'denied' ? '已拒绝' : '已允许'}</span>
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
                className="flex w-full items-center justify-between rounded-md border border-slate-200 px-3 py-2.5 text-left dark:border-slate-800"
                onClick={() => setAdvancedOpen((current) => !current)}
                aria-expanded={advancedOpen}
              >
                <span className="flex items-center gap-2 text-sm font-medium text-slate-800 dark:text-slate-100">
                  <Code2 className="h-4 w-4 text-slate-400" />
                  MCP 连接参数
                </span>
                <ChevronDown
                  className={cn(
                    'h-4 w-4 text-slate-400 transition-transform',
                    advancedOpen && 'rotate-180',
                  )}
                />
              </button>
              {advancedOpen ? (
                <dl className="divide-y divide-slate-100 rounded-md border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
                  {[
                    ['传输方式', server.transport],
                    [
                      '地址或命令',
                      redactSensitiveText(server.url || server.command),
                    ],
                    ['默认风险', riskLabel(server.default_risk)],
                    ['版本', server.version || '—'],
                    ['工具指纹', server.toolset_fingerprint || '—'],
                    ['Server ID', String(server.id)],
                  ].map(([label, value]) => (
                    <div
                      key={label}
                      className="grid gap-1 px-3 py-2.5 text-xs sm:grid-cols-[110px_minmax(0,1fr)]"
                    >
                      <dt className="text-slate-500">{label}</dt>
                      <dd className="break-all font-mono text-slate-700 dark:text-slate-300">
                        {value}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="text-xs text-slate-500">
                  高级参数默认折叠。访问令牌、环境变量和完整请求头不会在这里显示。
                </p>
              )}
            </div>
          ) : null}
        </div>
      </aside>
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
      <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
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
  onAuthorize: AppDetailsPanelProps['onAuthorize'];
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
            工具 ID：{tool.name}
          </p>
          <p className="mt-1 break-all text-[11px] text-slate-400">
            参数：{schemaSummary(tool)}
          </p>
        </div>
        {tool.risk_level === 'read_only' ? (
          <CircleCheck className="h-4 w-4 shrink-0 text-emerald-600" />
        ) : (
          <div className="flex flex-wrap items-center gap-1">
            {tool.authorized && !effectiveAuthorized ? (
              <Button
                variant="ghost"
                size="sm"
                leftIcon={<ShieldX className="h-3.5 w-3.5" />}
                disabled={busy}
                onClick={() =>
                  onAuthorize(server, tool, false, false)
                }
              >
                取消授权
              </Button>
            ) : null}
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
                  server,
                  tool,
                  !effectiveAuthorized,
                  !effectiveAuthorized &&
                    toolNeedsConfirmation(tool) &&
                    confirmed,
                )
              }
            >
              {effectiveAuthorized
                ? '取消授权'
                : tool.authorized
                  ? '补充确认'
                  : '授权'}
            </Button>
          </div>
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

export default AppDetailsPanel;
