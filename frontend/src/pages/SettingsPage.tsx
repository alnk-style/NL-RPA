import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  LoaderCircle,
  Plug,
  Plus,
  Power,
  RefreshCw,
  ShieldCheck,
  Trash2,
  XCircle,
} from 'lucide-react';

import * as mcpApi from '../api/mcp';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '../components/ui/Card';
import { Input } from '../components/ui/Input';
import { Switch } from '../components/ui/Switch';
import { getApiErrorMessage } from '../lib/errors';
import {
  auditStatusLabel,
  auditStatusVariant,
  formatAuditTarget,
  formatDurationMs,
  mcpSettingSourceLabel,
  mcpSettingsStatusLabel,
  riskLabel,
  riskVariant,
  summarizeTools,
  toolAuthorizationLabel,
  toolNeedsConfirmation,
} from '../lib/mcp-settings';
import { cn } from '../lib/utils';
import type {
  MCPSettingsRecord,
  MCPServerRecord,
  MCPToolCallAudit,
  MCPToolRecord,
  MCPRiskLevel,
  MCPTransport,
} from '../types/mcp';

interface ServerFormState {
  name: string;
  transport: MCPTransport;
  url: string;
  command: string;
  authToken: string;
  defaultRisk: MCPRiskLevel;
}

const EMPTY_FORM: ServerFormState = {
  name: '',
  transport: 'http',
  url: '',
  command: '',
  authToken: '',
  defaultRisk: 'mutating',
};

const selectClassName =
  'h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-950 shadow-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-white';

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return '—';
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
    second: '2-digit',
  }).format(date);
}

export function SettingsPage() {
  const [settings, setSettings] = useState<MCPSettingsRecord | null>(null);
  const [servers, setServers] = useState<MCPServerRecord[]>([]);
  const [audits, setAudits] = useState<MCPToolCallAudit[]>([]);
  const [sdkAvailable, setSdkAvailable] = useState(false);
  const [gateMessage, setGateMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [form, setForm] = useState<ServerFormState>(EMPTY_FORM);
  const [confirmMap, setConfirmMap] = useState<Record<string, boolean>>({});

  const loadData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    setGateMessage(null);
    try {
      const status = await mcpApi.getMCPSettings();
      setSettings(status);
      setSdkAvailable(status.sdk_available);
      if (!status.enabled) {
        setServers([]);
        setAudits([]);
        return;
      }
      const [payload, auditRows] = await Promise.all([
        mcpApi.listMCPServers(),
        mcpApi.listMCPAudits(),
      ]);
      setServers(payload.servers);
      setAudits(auditRows);
    } catch (requestError) {
      const message = getApiErrorMessage(
        requestError,
        '加载 MCP 配置失败。',
      );
      if (message.includes('MCP_CLIENT')) {
        setGateMessage(message);
        setServers([]);
        setAudits([]);
      } else {
        setError(message);
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  async function handleToggleEnabled(enabled: boolean) {
    setBusyKey('settings:toggle');
    setError(null);
    setNotice(null);
    try {
      const status = await mcpApi.updateMCPSettings({ enabled });
      setSettings(status);
      setNotice(
        enabled
          ? 'MCP 工具接入已开启，可以开始配置外部 Server。'
          : 'MCP 工具接入已关闭，已有配置与授权会保留。',
      );
      await loadData();
    } catch (requestError) {
      setError(
        getApiErrorMessage(requestError, '更新 MCP 开关失败。'),
      );
    } finally {
      setBusyKey(null);
    }
  }

  const totals = useMemo(
    () =>
      servers.reduce(
        (accumulator, server) => {
          const summary = summarizeTools(server.tools);
          accumulator.tools += summary.total;
          accumulator.pending += summary.pendingAuthorization;
          if (server.last_error) {
            accumulator.errors += 1;
          }
          return accumulator;
        },
        { tools: 0, pending: 0, errors: 0 },
      ),
    [servers],
  );

  async function handleCreate() {
    if (!form.name.trim()) {
      setError('请填写 MCP Server 名称。');
      return;
    }
    setBusyKey('create');
    setError(null);
    setNotice(null);
    try {
      await mcpApi.createMCPServer({
        name: form.name.trim(),
        transport: form.transport,
        url: form.transport === 'http' ? form.url.trim() : null,
        command: form.transport === 'stdio' ? form.command.trim() : null,
        auth_token: form.authToken.trim() || null,
        default_risk: form.defaultRisk,
      });
      setForm(EMPTY_FORM);
      setNotice('MCP Server 已保存。');
      await loadData();
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '保存 MCP Server 失败。'));
    } finally {
      setBusyKey(null);
    }
  }

  async function handleTest(server: MCPServerRecord) {
    setBusyKey(`${server.id}:test`);
    setError(null);
    setNotice(null);
    try {
      const result = await mcpApi.testMCPServer(server.id);
      if (result.success) {
        setNotice(
          `${server.name} 连接成功，共 ${result.tool_count ?? 0} 个工具。`,
        );
      } else {
        setError(
          `${server.name} 连接失败：${result.error ?? '未知错误'}`,
        );
      }
      await loadData();
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '连接测试失败。'));
    } finally {
      setBusyKey(null);
    }
  }

  async function handleAuthorize(
    server: MCPServerRecord,
    tool: MCPToolRecord,
  ) {
    const key = `${server.id}:${tool.name}`;
    const confirmed = confirmMap[key] ?? true;
    setBusyKey(`${key}:authorize`);
    setError(null);
    setNotice(null);
    try {
      await mcpApi.authorizeMCPTool(server.id, tool.name, {
        authorized: true,
        confirmed: toolNeedsConfirmation(tool) ? confirmed : false,
        risk_level: tool.risk_level,
      });
      setNotice(`${tool.name} 已授权。`);
      await loadData();
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '工具授权失败。'));
    } finally {
      setBusyKey(null);
    }
  }

  async function handleToggle(server: MCPServerRecord) {
    setBusyKey(`${server.id}:toggle`);
    setError(null);
    setNotice(null);
    try {
      await mcpApi.updateMCPServer(server.id, {
        enabled: !server.enabled,
      });
      await loadData();
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '更新 MCP Server 失败。'));
    } finally {
      setBusyKey(null);
    }
  }

  async function handleDelete(server: MCPServerRecord) {
    if (
      !window.confirm(
        `删除 MCP Server「${server.name}」？已产生的调用审计会保留。`,
      )
    ) {
      return;
    }
    setBusyKey(`${server.id}:delete`);
    setError(null);
    setNotice(null);
    try {
      await mcpApi.deleteMCPServer(server.id);
      await loadData();
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '删除 MCP Server 失败。'));
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-950 dark:text-white">
            连接器与 MCP
          </h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            管理外部 MCP 工具、风险授权与调用审计。
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          leftIcon={
            <RefreshCw
              className={isLoading ? 'h-4 w-4 animate-spin' : 'h-4 w-4'}
            />
          }
          onClick={() => void loadData()}
          disabled={isLoading}
        >
          刷新
        </Button>
      </header>

      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-4 pt-5">
          <div className="flex min-w-0 items-start gap-3">
            <span
              className={cn(
                'mt-0.5 rounded-md p-2',
                settings?.enabled
                  ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-950/50 dark:text-emerald-300'
                  : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-300',
              )}
            >
              <Power className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-medium text-slate-900 dark:text-white">
                  MCP 工具接入
                </p>
                {settings ? (
                  <Badge
                    variant={settings.enabled ? 'success' : 'secondary'}
                  >
                    {mcpSettingsStatusLabel(settings.enabled)}
                  </Badge>
                ) : null}
              </div>
              <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                {settings?.enabled
                  ? '外部 MCP Server 与工具调用已可用。'
                  : '开启后即可在下方配置外部 MCP Server。'}
              </p>
              {settings ? (
                <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                  来源：{mcpSettingSourceLabel(settings.source)}
                </p>
              ) : null}
            </div>
          </div>
          <div className="flex items-center gap-3">
            {busyKey === 'settings:toggle' ? (
              <LoaderCircle className="h-4 w-4 animate-spin text-slate-400" />
            ) : null}
            <Switch
              checked={Boolean(settings?.enabled)}
              onCheckedChange={(next) => void handleToggleEnabled(next)}
              disabled={
                !settings || isLoading || busyKey === 'settings:toggle'
              }
              label="MCP 工具接入开关"
            />
          </div>
        </CardContent>
      </Card>

      {gateMessage ? (
        <Card>
          <CardContent className="flex items-start gap-3 pt-5">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
            <div className="text-sm text-slate-600 dark:text-slate-300">
              <p className="font-medium text-slate-900 dark:text-white">
                MCP 客户端未启用
              </p>
              <p className="mt-1">
                {gateMessage} 请刷新重试；若仍失败，请确认上方开关已开启。
              </p>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {error ? (
        <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      {notice ? (
        <div className="flex items-start gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{notice}</span>
        </div>
      ) : null}

      {settings?.enabled ? (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card>
            <CardHeader className="flex-row items-center justify-between gap-3 pb-0">
              <CardTitle className="flex items-center gap-2">
                <Plug className="h-4 w-4 text-brand-600" />
                MCP Server
              </CardTitle>
              <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                <span>{servers.length} 个服务</span>
                <span>·</span>
                <span>{totals.tools} 个工具</span>
                {totals.pending > 0 ? (
                  <>
                    <span>·</span>
                    <span className="text-amber-600">
                      {totals.pending} 个待授权
                    </span>
                  </>
                ) : null}
                {totals.errors > 0 ? (
                  <>
                    <span>·</span>
                    <span className="text-red-600">
                      {totals.errors} 个连接异常
                    </span>
                  </>
                ) : null}
              </div>
            </CardHeader>
            <CardContent className="space-y-4 pt-4">
              {isLoading ? (
                <div className="flex items-center gap-2 py-6 text-sm text-slate-500">
                  <LoaderCircle className="h-4 w-4 animate-spin" />
                  正在加载配置
                </div>
              ) : servers.length === 0 ? (
                <p className="py-4 text-sm text-slate-500 dark:text-slate-400">
                  尚未配置 MCP Server。
                </p>
              ) : (
                servers.map((server) => (
                  <ServerBlock
                    key={server.id}
                    server={server}
                    busyKey={busyKey}
                    confirmMap={confirmMap}
                    onConfirmChange={(key, value) =>
                      setConfirmMap((current) => ({
                        ...current,
                        [key]: value,
                      }))
                    }
                    onTest={handleTest}
                    onAuthorize={handleAuthorize}
                    onToggle={handleToggle}
                    onDelete={handleDelete}
                  />
                ))
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Plus className="h-4 w-4 text-brand-600" />
                新增 MCP Server
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-2">
              <Input
                label="名称"
                value={form.name}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    name: event.target.value,
                  }))
                }
                placeholder="例如 local-tools"
              />
              <label className="block text-sm font-medium text-slate-700 dark:text-slate-200">
                传输方式
                <select
                  className={`mt-1.5 ${selectClassName}`}
                  value={form.transport}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      transport: event.target.value as MCPTransport,
                    }))
                  }
                >
                  <option value="http">HTTP (Streamable)</option>
                  <option value="stdio">stdio</option>
                </select>
              </label>
              {form.transport === 'http' ? (
                <Input
                  label="URL"
                  value={form.url}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      url: event.target.value,
                    }))
                  }
                  placeholder="http://127.0.0.1:8001/mcp"
                />
              ) : (
                <Input
                  label="Command"
                  value={form.command}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      command: event.target.value,
                    }))
                  }
                  placeholder="python"
                />
              )}
              <Input
                label="访问令牌"
                type="password"
                value={form.authToken}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    authToken: event.target.value,
                  }))
                }
                hint="加密存储，仅在调用时解密。"
              />
              <label className="block text-sm font-medium text-slate-700 dark:text-slate-200">
                默认风险级别
                <select
                  className={`mt-1.5 ${selectClassName}`}
                  value={form.defaultRisk}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      defaultRisk: event.target.value as MCPRiskLevel,
                    }))
                  }
                >
                  <option value="read_only">只读</option>
                  <option value="mutating">写入</option>
                  <option value="destructive">破坏性</option>
                </select>
              </label>
              <div className="flex items-end sm:col-span-2">
                <Button
                  leftIcon={
                    busyKey === 'create' ? (
                      <LoaderCircle className="h-4 w-4 animate-spin" />
                    ) : (
                      <Plus className="h-4 w-4" />
                    )
                  }
                  onClick={() => void handleCreate()}
                  disabled={busyKey === 'create'}
                >
                  保存服务
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>

        <Card className="h-fit">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-brand-600" />
              调用审计
            </CardTitle>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              MCP SDK {sdkAvailable ? '可用' : '未安装'}；失败与被拒绝的调用同样记录。
            </p>
          </CardHeader>
          <CardContent className="space-y-3">
            {audits.length === 0 ? (
              <p className="text-sm text-slate-500 dark:text-slate-400">
                暂无调用记录。
              </p>
            ) : (
              audits.slice(0, 30).map((audit) => (
                <div
                  key={audit.id}
                  className="rounded-md border border-slate-100 px-3 py-2 text-xs dark:border-slate-800"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-medium text-slate-800 dark:text-slate-100">
                      {formatAuditTarget(audit)}
                    </span>
                    <Badge variant={auditStatusVariant(audit.status)}>
                      {auditStatusLabel(audit.status)}
                    </Badge>
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-slate-500 dark:text-slate-400">
                    <span>{riskLabel(audit.risk_level)}</span>
                    <span>·</span>
                    <span>
                      {audit.decision === 'denied' ? '已拒绝' : '已允许'}
                    </span>
                    <span>·</span>
                    <span>{formatDurationMs(audit.duration_ms)}</span>
                    <span>·</span>
                    <span>{formatDateTime(audit.created_at)}</span>
                  </div>
                  {audit.error ? (
                    <p className="mt-1 line-clamp-2 text-red-600 dark:text-red-300">
                      {audit.error}
                    </p>
                  ) : null}
                </div>
              ))
            )}
          </CardContent>
        </Card>
        </div>
      ) : null}
    </div>
  );
}

interface ServerBlockProps {
  server: MCPServerRecord;
  busyKey: string | null;
  confirmMap: Record<string, boolean>;
  onConfirmChange: (key: string, value: boolean) => void;
  onTest: (server: MCPServerRecord) => Promise<void>;
  onAuthorize: (
    server: MCPServerRecord,
    tool: MCPToolRecord,
  ) => Promise<void>;
  onToggle: (server: MCPServerRecord) => Promise<void>;
  onDelete: (server: MCPServerRecord) => Promise<void>;
}

function ServerBlock({
  server,
  busyKey,
  confirmMap,
  onConfirmChange,
  onTest,
  onAuthorize,
  onToggle,
  onDelete,
}: ServerBlockProps) {
  const summary = summarizeTools(server.tools);
  return (
    <div className="rounded-md border border-slate-200 p-4 dark:border-slate-800">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-semibold text-slate-900 dark:text-white">
              {server.name}
            </span>
            <Badge variant="outline">{server.transport}</Badge>
            <Badge variant={server.enabled ? 'success' : 'secondary'}>
              {server.enabled ? '启用' : '停用'}
            </Badge>
            <Badge variant={riskVariant(server.default_risk)}>
              默认 {riskLabel(server.default_risk)}
            </Badge>
          </div>
          <p className="mt-1 truncate text-xs text-slate-500 dark:text-slate-400">
            {server.transport === 'http'
              ? server.url ?? '未配置 URL'
              : server.command ?? '未配置命令'}
          </p>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            {summary.total} 个工具 · {summary.authorized} 个已授权 ·{' '}
            {summary.pendingAuthorization} 个待授权 · 最近测试{' '}
            {formatDateTime(server.last_tested_at)}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
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
            onClick={() => void onTest(server)}
            disabled={busyKey === `${server.id}:test`}
          >
            测试连接
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void onToggle(server)}
            disabled={busyKey === `${server.id}:toggle`}
          >
            {server.enabled ? '停用' : '启用'}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-label={`删除 ${server.name}`}
            onClick={() => void onDelete(server)}
            disabled={busyKey === `${server.id}:delete`}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {server.last_error ? (
        <p className="mt-2 rounded-md bg-red-50 px-2 py-1 text-xs text-red-700 dark:bg-red-950/40 dark:text-red-200">
          {server.last_error}
        </p>
      ) : null}

      {server.tools.length > 0 ? (
        <div className="mt-3 space-y-2">
          {server.tools.map((tool) => {
            const key = `${server.id}:${tool.name}`;
            const busy = busyKey === `${key}:authorize`;
            const needsConfirmation = toolNeedsConfirmation(tool);
            const alreadyAuthorized =
              tool.authorized &&
              (!needsConfirmation || tool.confirmed);
            return (
              <div
                key={key}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-slate-50 px-3 py-2 dark:bg-slate-900/60"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-slate-800 dark:text-slate-100">
                      {tool.name}
                    </span>
                    <Badge variant={riskVariant(tool.risk_level)}>
                      {riskLabel(tool.risk_level)}
                    </Badge>
                    <Badge
                      variant={
                        alreadyAuthorized || tool.risk_level === 'read_only'
                          ? 'success'
                          : 'warning'
                      }
                    >
                      {toolAuthorizationLabel(tool)}
                    </Badge>
                    {tool.available === false ? (
                      <Badge variant="secondary">未在服务清单</Badge>
                    ) : null}
                  </div>
                  {tool.description ? (
                    <p className="mt-1 line-clamp-1 text-xs text-slate-500 dark:text-slate-400">
                      {tool.description}
                    </p>
                  ) : null}
                </div>
                <div className="flex items-center gap-2">
                  {needsConfirmation && !tool.confirmed ? (
                    <label className="flex items-center gap-1 text-xs text-slate-500 dark:text-slate-400">
                      <input
                        type="checkbox"
                        className="h-3.5 w-3.5 rounded border-slate-300"
                        checked={confirmMap[key] ?? true}
                        onChange={(event) =>
                          onConfirmChange(key, event.target.checked)
                        }
                      />
                      同时确认
                    </label>
                  ) : null}
                  {tool.risk_level === 'read_only' ? null : (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => void onAuthorize(server, tool)}
                      disabled={busy}
                      leftIcon={
                        busy ? (
                          <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <ShieldCheck className="h-3.5 w-3.5" />
                        )
                      }
                    >
                      {alreadyAuthorized ? '重新授权' : '授权'}
                    </Button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
          还没有工具清单，先测试连接完成发现。
        </p>
      )}
    </div>
  );
}

export default SettingsPage;
