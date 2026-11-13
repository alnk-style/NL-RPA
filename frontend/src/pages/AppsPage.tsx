import {
  AlertTriangle,
  AppWindow,
  CheckCircle2,
  FolderSearch,
  LoaderCircle,
  PackagePlus,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  X,
  XCircle,
} from 'lucide-react';
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { useNavigate } from 'react-router-dom';

import * as localAppApi from '../api/localApps';
import * as mcpApi from '../api/mcp';
import { AppCard } from '../components/apps/AppCard';
import { AppDetailsPanel } from '../components/apps/AppDetailsPanel';
import { AppStatusSummary } from '../components/apps/AppStatusSummary';
import { LocalAppDetailsPanel } from '../components/apps/LocalAppDetailsPanel';
import { LocalLogsDialog } from '../components/apps/LocalLogsDialog';
import { LocalRuntimeSummary } from '../components/apps/LocalRuntimeSummary';
import { InstalledAppMcpDialog } from '../components/apps/InstalledAppMcpDialog';
import { Button } from '../components/ui/Button';
import { Input } from '../components/ui/Input';
import {
  APP_STATUS_OPTIONS,
  filterApps,
  type AppStatusFilter,
} from '../lib/apps';
import { getApiErrorMessage } from '../lib/errors';
import type {
  DiscoverableInstalledApp,
} from '../lib/installed-app-mcp';
import {
  linkedServerForLocalApp,
  localAppMatches,
  unlinkedServers,
} from '../lib/local-apps';
import type {
  LocalAppBinding,
  LocalAppInfo,
  LocalAppScanError,
} from '../types/localApps';
import type {
  MCPServerRecord,
  MCPSettingsRecord,
  MCPToolCallAudit,
  MCPToolRecord,
  MCPTransport,
  MCPRiskLevel,
} from '../types/mcp';

interface CreateForm {
  name: string;
  description: string;
  transport: MCPTransport;
  url: string;
  command: string;
  authToken: string;
  defaultRisk: MCPRiskLevel;
}

const EMPTY_FORM: CreateForm = {
  name: '',
  description: '',
  transport: 'http',
  url: '',
  command: '',
  authToken: '',
  defaultRisk: 'mutating',
};

const selectClass =
  'mt-1.5 h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-950 shadow-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-white';

export function AppsPage() {
  const navigate = useNavigate();
  const [settings, setSettings] = useState<MCPSettingsRecord | null>(null);
  const [servers, setServers] = useState<MCPServerRecord[]>([]);
  const [localApps, setLocalApps] = useState<LocalAppInfo[]>([]);
  const [bindings, setBindings] = useState<
    Record<string, LocalAppBinding>
  >({});
  const [scanErrors, setScanErrors] = useState<LocalAppScanError[]>([]);
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<AppStatusFilter>('all');
  const [selectedServerId, setSelectedServerId] = useState<number | null>(
    null,
  );
  const [selectedLocalAppId, setSelectedLocalAppId] = useState<
    string | null
  >(null);
  const [audits, setAudits] = useState<MCPToolCallAudit[]>([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState<CreateForm>(EMPTY_FORM);
  const [scanLoading, setScanLoading] = useState(false);
  const [installedDialogOpen, setInstalledDialogOpen] = useState(false);
  const [discoverableApps, setDiscoverableApps] = useState<
    DiscoverableInstalledApp[]
  >([]);
  const [discoverableLoading, setDiscoverableLoading] = useState(false);
  const [discoverableError, setDiscoverableError] = useState<
    string | null
  >(null);
  const [logsAppId, setLogsAppId] = useState<string | null>(null);
  const [logs, setLogs] = useState('');
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsLines, setLogsLines] = useState(200);

  const loadServers = useCallback(async () => {
    const payload = await mcpApi.listMCPServers();
    setServers(payload.servers);
    return payload.servers;
  }, []);

  const loadLocalApps = useCallback(async () => {
    const payload = await localAppApi.listLocalApps();
    setLocalApps(payload.apps);
    setScanErrors(payload.errors);
    const results = await Promise.allSettled(
      payload.apps.map(async (app) => ({
        appId: app.app_id,
        binding: await localAppApi.getLocalAppBinding(app.app_id),
      })),
    );
    const nextBindings: Record<string, LocalAppBinding> = {};
    results.forEach((result) => {
      if (result.status === 'fulfilled') {
        nextBindings[result.value.appId] = result.value.binding;
      }
    });
    setBindings(nextBindings);
    return payload.apps;
  }, []);

  const loadDiscoverableApps = useCallback(async (refresh = false) => {
    setDiscoverableLoading(true);
    setDiscoverableError(null);
    try {
      const payload = await localAppApi.listDiscoverableApps(refresh);
      setDiscoverableApps(payload.apps);
    } catch (requestError) {
      setDiscoverableApps([]);
      setDiscoverableError(
        getApiErrorMessage(
          requestError,
          '扫描本机已安装软件失败。',
        ),
      );
    } finally {
      setDiscoverableLoading(false);
    }
  }, []);

  const loadPage = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    const failures: string[] = [];
    try {
      const status = await mcpApi.getMCPSettings();
      setSettings(status);
      if (status.enabled) {
        try {
          await loadServers();
        } catch (requestError) {
          failures.push(
            getApiErrorMessage(requestError, '加载 MCP 应用失败。'),
          );
        }
      } else {
        setServers([]);
      }
      try {
        await loadLocalApps();
      } catch (requestError) {
        failures.push(
          getApiErrorMessage(requestError, '加载本机应用失败。'),
        );
      }
      if (failures.length > 0) {
        setError(failures.join(' '));
      }
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '加载应用失败。'));
    } finally {
      setIsLoading(false);
    }
  }, [loadLocalApps, loadServers]);

  useEffect(() => {
    void loadPage();
  }, [loadPage]);

  const selectedServer = useMemo(
    () => servers.find((server) => server.id === selectedServerId) ?? null,
    [selectedServerId, servers],
  );
  const selectedLocalApp = useMemo(
    () =>
      localApps.find((app) => app.app_id === selectedLocalAppId) ?? null,
    [localApps, selectedLocalAppId],
  );
  const selectedLocalServer = useMemo(
    () =>
      selectedLocalApp
        ? linkedServerForLocalApp(selectedLocalApp, servers)
        : null,
    [selectedLocalApp, servers],
  );
  const selectedBinding = selectedLocalApp
    ? bindings[selectedLocalApp.app_id] ?? null
    : null;
  const filteredLocalApps = useMemo(
    () => localApps.filter((app) => localAppMatches(app, query)),
    [localApps, query],
  );
  const filteredServers = useMemo(
    () =>
      filterApps(
        unlinkedServers(localApps, servers),
        query,
        statusFilter,
      ),
    [localApps, query, servers, statusFilter],
  );
  const activeServerId = selectedLocalServer?.id ?? selectedServerId;
  const logsApp = useMemo(
    () => localApps.find((app) => app.app_id === logsAppId) ?? null,
    [localApps, logsAppId],
  );

  const loadAudits = useCallback(async (serverId: number) => {
    setAuditLoading(true);
    try {
      setAudits(await mcpApi.listMCPAuditsByServer(serverId));
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '加载调用记录失败。'));
    } finally {
      setAuditLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeServerId === null) {
      setAudits([]);
      return;
    }
    void loadAudits(activeServerId);
  }, [activeServerId, loadAudits]);

  async function refreshAfter(action: () => Promise<void>) {
    try {
      await action();
    } finally {
      const refreshes: Array<Promise<unknown>> = [loadLocalApps()];
      if (settings?.enabled) {
        refreshes.push(loadServers());
      }
      await Promise.allSettled(refreshes);
    }
  }

  async function handleScanLocalApps() {
    setScanLoading(true);
    setError(null);
    setNotice(null);
    try {
      const result = await localAppApi.scanLocalApps();
      setLocalApps(result.apps);
      setScanErrors(result.errors);
      if (settings?.enabled) {
        await loadServers();
      }
      const changes = [
        result.added.length > 0 ? `新增 ${result.added.length} 个` : '',
        result.removed.length > 0 ? `移除 ${result.removed.length} 个` : '',
        result.errors.length > 0 ? `${result.errors.length} 个校验失败` : '',
      ].filter(Boolean);
      setNotice(
        changes.length > 0
          ? `扫描完成：${changes.join('，')}。`
          : `扫描完成：共发现 ${result.apps.length} 个本机应用。`,
      );
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '扫描本机应用失败。'));
    } finally {
      setScanLoading(false);
    }
  }

  async function handleCreateInstalledAppMcp(
    app: DiscoverableInstalledApp,
    agentId: number,
  ) {
    const key = `discover:${app.key}`;
    setBusyKey(key);
    setError(null);
    setNotice(null);
    try {
      const result = await localAppApi.createInstalledAppMCP(
        app.key,
        agentId,
      );
      setNotice(`${result.app.name} 已创建并启动 MCP。`);
      setInstalledDialogOpen(false);
      setDiscoverableApps((current) =>
        current.map((item) =>
          item.key === app.key
            ? {
                ...item,
                generated_app_id: result.generated.app_id,
              }
            : item,
        ),
      );
      await refreshAfter(async () => undefined);
      setSelectedServerId(result.server_id);
      setSelectedLocalAppId(result.app.app_id);
    } catch (requestError) {
      setDiscoverableError(
        getApiErrorMessage(
          requestError,
          '创建本机软件 MCP 失败。',
        ),
      );
    } finally {
      setBusyKey(null);
    }
  }

  async function handleLocalOperation(
    app: LocalAppInfo,
    operation: 'start' | 'stop' | 'restart',
  ) {
    const key = `local:${app.app_id}:${operation}`;
    setBusyKey(key);
    setError(null);
    setNotice(null);
    try {
      if (operation === 'start') {
        await localAppApi.startLocalApp(app.app_id);
        setNotice(`${app.name} 已启动。`);
      } else if (operation === 'stop') {
        await localAppApi.stopLocalApp(app.app_id);
        setNotice(`${app.name} 已停止。`);
      } else {
        await localAppApi.restartLocalApp(app.app_id);
        setNotice(`${app.name} 已重启。`);
      }
      await refreshAfter(async () => undefined);
    } catch (requestError) {
      setError(
        getApiErrorMessage(
          requestError,
          `${app.name} ${operation === 'start' ? '启动' : operation === 'stop' ? '停止' : '重启'}失败。`,
        ),
      );
      await refreshAfter(async () => undefined);
    } finally {
      setBusyKey(null);
    }
  }

  async function openLogs(app: LocalAppInfo, lines = logsLines) {
    setLogsAppId(app.app_id);
    setLogsLines(lines);
    setLogsLoading(true);
    setLogs('');
    try {
      const payload = await localAppApi.getLocalAppLogs(app.app_id, lines);
      setLogs(payload.logs);
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '读取运行日志失败。'));
    } finally {
      setLogsLoading(false);
    }
  }

  async function refreshLogs() {
    const current = localApps.find((app) => app.app_id === logsAppId);
    if (!current) {
      return;
    }
    await openLogs(current, logsLines);
  }

  async function handleBindingAutoStart(
    app: LocalAppInfo,
    autoStart: boolean,
  ) {
    const key = `local:${app.app_id}:binding`;
    setBusyKey(key);
    setError(null);
    setNotice(null);
    try {
      const updated = await localAppApi.updateLocalAppBinding(
        app.app_id,
        autoStart,
      );
      setBindings((current) => ({
        ...current,
        [app.app_id]: updated,
      }));
      setNotice(
        autoStart
          ? `${app.name} 已开启自动启动。`
          : `${app.name} 已关闭自动启动。`,
      );
      await loadLocalApps();
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '更新自动启动失败。'));
    } finally {
      setBusyKey(null);
    }
  }

  async function handleRemoveBinding(app: LocalAppInfo) {
    const key = `local:${app.app_id}:unbind`;
    setBusyKey(key);
    setError(null);
    setNotice(null);
    try {
      const updated = await localAppApi.removeLocalAppBinding(app.app_id);
      setBindings((current) => ({
        ...current,
        [app.app_id]: updated,
      }));
      setNotice(`${app.name} 已解除绑定并停止。`);
      await refreshAfter(async () => undefined);
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '解除绑定失败。'));
    } finally {
      setBusyKey(null);
    }
  }

  async function handleDeleteGeneratedApp(app: LocalAppInfo) {
    const key = `local:${app.app_id}:delete-generated`;
    setBusyKey(key);
    setError(null);
    setNotice(null);
    try {
      await localAppApi.deleteGeneratedLocalApp(app.app_id);
      setSelectedLocalAppId(null);
      setNotice(`${app.name} 的生成 MCP 已删除。`);
      await refreshAfter(async () => undefined);
      await loadDiscoverableApps(true);
    } catch (requestError) {
      setError(
        getApiErrorMessage(
          requestError,
          '删除生成的 MCP 失败。',
        ),
      );
    } finally {
      setBusyKey(null);
    }
  }

  async function handleTest(server: MCPServerRecord) {
    const key = `${server.id}:test`;
    setBusyKey(key);
    setError(null);
    setNotice(null);
    try {
      const result = await mcpApi.testMCPServer(server.id);
      if (result.success) {
        setNotice(
          `${server.name} 测试成功，发现 ${result.tool_count ?? 0} 个工具。`,
        );
      } else {
        setError(
          `${server.name} 测试失败：${result.error ?? '未知错误'}`,
        );
      }
      await loadServers();
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '测试连接失败。'));
    } finally {
      setBusyKey(null);
    }
  }

  async function handleToggle(server: MCPServerRecord) {
    const key = `${server.id}:toggle`;
    setBusyKey(key);
    setError(null);
    setNotice(null);
    try {
      await mcpApi.updateMCPServer(server.id, {
        enabled: !server.enabled,
      });
      setNotice(server.enabled ? '应用已停用。' : '应用已启用。');
      await loadServers();
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '更新应用状态失败。'));
    } finally {
      setBusyKey(null);
    }
  }

  async function handleAuthorize(
    server: MCPServerRecord,
    tool: MCPToolRecord,
    authorized: boolean,
    confirmed: boolean,
  ) {
    const key = `${server.id}:${tool.name}:authorize`;
    setBusyKey(key);
    setError(null);
    setNotice(null);
    try {
      await refreshAfter(async () => {
        await mcpApi.authorizeMCPTool(server.id, tool.name, {
          authorized,
          confirmed,
          risk_level: tool.risk_level,
        });
        setNotice(
          authorized
            ? `${tool.name} 已授权。`
            : `${tool.name} 已取消授权。`,
        );
      });
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '更新工具授权失败。'));
    } finally {
      setBusyKey(null);
    }
  }

  async function handleCreateAndTest() {
    if (!form.name.trim()) {
      setError('请填写应用名称。');
      return;
    }
    if (form.transport === 'http' && !form.url.trim()) {
      setError('HTTP 接入需要填写地址。');
      return;
    }
    if (form.transport === 'stdio' && !form.command.trim()) {
      setError('stdio 接入需要填写命令。');
      return;
    }
    setBusyKey('create');
    setError(null);
    setNotice(null);
    let created: MCPServerRecord;
    try {
      created = await mcpApi.createMCPServer({
        name: form.name.trim(),
        description: form.description.trim() || null,
        transport: form.transport,
        url: form.transport === 'http' ? form.url.trim() : null,
        command: form.transport === 'stdio' ? form.command.trim() : null,
        auth_token: form.authToken.trim() || null,
        default_risk: form.defaultRisk,
      });
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '添加应用失败。'));
      setBusyKey(null);
      return;
    }

    try {
      const test = await mcpApi.testMCPServer(created.id);
      setForm(EMPTY_FORM);
      setCreateOpen(false);
      setSelectedServerId(created.id);
      setNotice(
        test.success
          ? `${created.name} 已添加并发现 ${test.tool_count ?? 0} 个工具。`
          : `${created.name} 已添加，但连接测试失败：${test.error ?? '未知错误'}`,
      );
      await loadServers();
    } catch (requestError) {
      setForm(EMPTY_FORM);
      setCreateOpen(false);
      setSelectedServerId(created.id);
      setError(
        getApiErrorMessage(
          requestError,
          `${created.name} 已添加，但连接测试失败。`,
        ),
      );
      try {
        await loadServers();
      } catch {
        // Keep the partial-success message visible.
      }
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <section className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-950 dark:text-white">
            应用
          </h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            查看已接入应用、连接状态和可用工具。
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button
            variant="secondary"
            size="sm"
            leftIcon={<PackagePlus className="h-4 w-4" />}
            disabled={!settings?.enabled || isLoading}
            onClick={() => {
              setInstalledDialogOpen(true);
              void loadDiscoverableApps();
            }}
          >
            从本机软件创建
          </Button>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={
              scanLoading ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <FolderSearch className="h-4 w-4" />
              )
            }
            disabled={scanLoading || isLoading}
            onClick={() => void handleScanLocalApps()}
          >
            扫描本地应用
          </Button>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={
              isLoading ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )
            }
            disabled={isLoading}
            onClick={() => void loadPage()}
          >
            刷新
          </Button>
          <Button
            size="sm"
            leftIcon={<Plus className="h-4 w-4" />}
            disabled={!settings?.enabled || isLoading}
            onClick={() => setCreateOpen(true)}
          >
            添加应用
          </Button>
        </div>
      </header>

      {settings && !settings.enabled ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>应用工具接入尚未开启。开启后才能添加和测试应用。</span>
          </div>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={<Settings2 className="h-3.5 w-3.5" />}
            onClick={() => navigate('/settings')}
          >
            前往连接器
          </Button>
        </div>
      ) : null}

      {error ? (
        <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="break-words">{error}</span>
        </div>
      ) : null}

      {notice ? (
        <div className="flex items-start gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="break-words">{notice}</span>
        </div>
      ) : null}

      <AppStatusSummary servers={servers} />
      <LocalRuntimeSummary apps={localApps} />

      {scanErrors.length > 0 ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="min-w-0">
              <p className="font-medium">部分应用包未通过 Manifest 校验</p>
              <ul className="mt-1 space-y-1 text-xs leading-5">
                {scanErrors.map((item) => (
                  <li key={`${item.app_id}:${item.error}`} className="break-words">
                    <span className="font-mono">{item.app_id}</span>：{item.error}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      ) : null}

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <label className="relative block min-w-0 flex-1 sm:max-w-sm">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="h-10 w-full rounded-md border border-slate-200 bg-white pl-9 pr-3 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900"
            placeholder="搜索应用或工具"
            aria-label="搜索应用"
          />
        </label>
        <div className="flex gap-1 overflow-x-auto pb-1 sm:pb-0">
          {APP_STATUS_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              className={
                statusFilter === option.value
                  ? 'shrink-0 rounded-md bg-slate-900 px-3 py-2 text-xs font-medium text-white'
                  : 'shrink-0 rounded-md px-3 py-2 text-xs font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-800 dark:hover:text-white'
              }
              onClick={() => setStatusFilter(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((index) => (
            <div
              key={index}
              className="h-56 animate-pulse rounded-lg bg-slate-100 dark:bg-slate-800"
            />
          ))}
        </div>
      ) : filteredLocalApps.length > 0 || filteredServers.length > 0 ? (
        <div className="space-y-6">
          {filteredLocalApps.length > 0 ? (
            <section className="space-y-3">
              <div className="flex items-end justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
                    本机应用
                  </h2>
                  <p className="mt-0.5 text-xs text-slate-500">
                    经 Manifest 校验的内置应用包
                  </p>
                </div>
              </div>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {filteredLocalApps.map((app) => {
                  const linkedServer = linkedServerForLocalApp(app, servers);
                  return (
                    <AppCard
                      key={app.app_id}
                      server={linkedServer}
                      localApp={app}
                      binding={bindings[app.app_id] ?? null}
                      busyKey={busyKey}
                      onOpen={() => {
                        setSelectedServerId(null);
                        setSelectedLocalAppId(app.app_id);
                      }}
                      onStart={() =>
                        void handleLocalOperation(app, 'start')
                      }
                      onStop={() =>
                        void handleLocalOperation(app, 'stop')
                      }
                      onRestart={() =>
                        void handleLocalOperation(app, 'restart')
                      }
                      onLogs={() => void openLogs(app)}
                      onTest={
                        linkedServer
                          ? () => void handleTest(linkedServer)
                          : undefined
                      }
                    />
                  );
                })}
              </div>
            </section>
          ) : null}

          {filteredServers.length > 0 ? (
            <section className="space-y-3">
              <div>
                <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
                  其他 MCP 应用
                </h2>
                <p className="mt-0.5 text-xs text-slate-500">
                  手动接入或未包含在本机应用包中的应用
                </p>
              </div>
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {filteredServers.map((server) => (
                  <AppCard
                    key={server.id}
                    server={server}
                    busyKey={busyKey}
                    onOpen={() => {
                      setSelectedLocalAppId(null);
                      setSelectedServerId(server.id);
                    }}
                    onTest={() => void handleTest(server)}
                    onToggle={() => void handleToggle(server)}
                  />
                ))}
              </div>
            </section>
          ) : null}
        </div>
      ) : (
        <div className="flex min-h-64 flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 bg-white px-5 text-center dark:border-slate-700 dark:bg-slate-900">
          <AppWindow className="h-8 w-8 text-slate-400" />
          <p className="mt-3 text-sm font-medium text-slate-800 dark:text-slate-100">
            {localApps.length === 0 && servers.length === 0
              ? '还没有发现应用'
              : '没有匹配的应用'}
          </p>
          <p className="mt-1 max-w-md text-xs leading-5 text-slate-500">
            {localApps.length === 0 && servers.length === 0
              ? '扫描本机应用，或添加一个 MCP 应用连接。'
              : '调整搜索关键词或状态筛选后重试。'}
          </p>
        </div>
      )}

      {selectedLocalApp ? (
        <LocalAppDetailsPanel
          key={selectedLocalApp.app_id}
          app={selectedLocalApp}
          server={selectedLocalServer}
          binding={selectedBinding}
          bindingLoading={
            busyKey === `local:${selectedLocalApp.app_id}:binding` ||
            busyKey === `local:${selectedLocalApp.app_id}:unbind`
          }
          audits={audits}
          auditLoading={auditLoading}
          busyKey={busyKey}
          onClose={() => setSelectedLocalAppId(null)}
          onStart={() =>
            void handleLocalOperation(selectedLocalApp, 'start')
          }
          onStop={() =>
            void handleLocalOperation(selectedLocalApp, 'stop')
          }
          onRestart={() =>
            void handleLocalOperation(selectedLocalApp, 'restart')
          }
          onLogs={() => void openLogs(selectedLocalApp)}
          onAutoStartChange={(autoStart) =>
            void handleBindingAutoStart(
              selectedLocalApp,
              autoStart,
            )
          }
          onUnbind={() => void handleRemoveBinding(selectedLocalApp)}
          onDeleteGenerated={() =>
            void handleDeleteGeneratedApp(selectedLocalApp)
          }
          onTest={() => {
            if (selectedLocalServer) {
              void handleTest(selectedLocalServer);
            }
          }}
          onToggle={() => {
            if (selectedLocalServer) {
              void handleToggle(selectedLocalServer);
            }
          }}
          onRefreshAudits={() => {
            if (selectedLocalServer) {
              void loadAudits(selectedLocalServer.id);
            }
          }}
          onAuthorize={(tool, authorized, confirmed) => {
            if (selectedLocalServer) {
              void handleAuthorize(
                selectedLocalServer,
                tool,
                authorized,
                confirmed,
              );
            }
          }}
        />
      ) : selectedServer ? (
        <AppDetailsPanel
          key={selectedServer.id}
          server={selectedServer}
          audits={audits}
          auditLoading={auditLoading}
          busyKey={busyKey}
          onClose={() => setSelectedServerId(null)}
          onTest={(server) => void handleTest(server)}
          onToggle={(server) => void handleToggle(server)}
          onRefreshAudits={(server) => void loadAudits(server.id)}
          onAuthorize={(...args) => void handleAuthorize(...args)}
        />
      ) : null}

      {logsApp ? (
        <LocalLogsDialog
          app={logsApp}
          logs={logs}
          loading={logsLoading}
          lines={logsLines}
          embedded={logsApp.host_mode === 'in_process'}
          onLinesChange={(lines) => {
            setLogsLines(lines);
            void openLogs(logsApp, lines);
          }}
          onRefresh={() => void refreshLogs()}
          onClose={() => setLogsAppId(null)}
        />
      ) : null}

      {createOpen ? (
        <CreateAppDialog
          form={form}
          busy={busyKey === 'create'}
          onChange={setForm}
          onClose={() => {
            if (busyKey !== 'create') {
              setCreateOpen(false);
            }
          }}
          onSubmit={() => void handleCreateAndTest()}
        />
      ) : null}

      <InstalledAppMcpDialog
        open={installedDialogOpen}
        apps={discoverableApps}
        loading={discoverableLoading}
        error={discoverableError}
        busyKey={busyKey}
        onRefresh={() => void loadDiscoverableApps(true)}
        onCreate={(app, agentId) =>
          void handleCreateInstalledAppMcp(app, agentId)
        }
        onClose={() => setInstalledDialogOpen(false)}
      />
    </section>
  );
}

function CreateAppDialog({
  form,
  busy,
  onChange,
  onClose,
  onSubmit,
}: {
  form: CreateForm;
  busy: boolean;
  onChange: (form: CreateForm) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-3 sm:p-5">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-app-title"
        className="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-slate-900"
      >
        <header className="flex items-start justify-between border-b border-slate-200 p-4 dark:border-slate-800">
          <div>
            <h2
              id="add-app-title"
              className="text-base font-semibold text-slate-950 dark:text-white"
            >
              添加应用
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              配置连接后会立即测试并发现工具。
            </p>
          </div>
          <button
            type="button"
            className="flex h-8 w-8 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-800"
            onClick={onClose}
            disabled={busy}
            aria-label="关闭添加应用"
          >
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="grid gap-4 p-4 sm:grid-cols-2">
          <Input
            label="应用名称"
            value={form.name}
            onChange={(event) =>
              onChange({ ...form, name: event.target.value })
            }
            placeholder="例如 演示应用"
          />
          <Input
            label="应用描述"
            value={form.description}
            onChange={(event) =>
              onChange({ ...form, description: event.target.value })
            }
            placeholder="可选"
          />
          <label className="text-sm font-medium text-slate-700 dark:text-slate-200">
            接入方式
            <select
              className={selectClass}
              value={form.transport}
              onChange={(event) =>
                onChange({
                  ...form,
                  transport: event.target.value as MCPTransport,
                })
              }
            >
              <option value="http">HTTP 服务</option>
              <option value="stdio">本地命令</option>
            </select>
          </label>
          {form.transport === 'http' ? (
            <Input
              label="服务地址"
              value={form.url}
              onChange={(event) =>
                onChange({ ...form, url: event.target.value })
              }
              placeholder="http://127.0.0.1:8767/mcp"
            />
          ) : (
            <Input
              label="启动命令"
              value={form.command}
              onChange={(event) =>
                onChange({ ...form, command: event.target.value })
              }
              placeholder="python"
            />
          )}
          <Input
            label="访问令牌"
            type="password"
            value={form.authToken}
            onChange={(event) =>
              onChange({ ...form, authToken: event.target.value })
            }
            hint="可选，保存后不会回显。"
          />
          <label className="text-sm font-medium text-slate-700 dark:text-slate-200">
            默认风险级别
            <select
              className={selectClass}
              value={form.defaultRisk}
              onChange={(event) =>
                onChange({
                  ...form,
                  defaultRisk: event.target.value as MCPRiskLevel,
                })
              }
            >
              <option value="read_only">只读</option>
              <option value="mutating">写入</option>
              <option value="destructive">高风险</option>
            </select>
          </label>
        </div>
        <footer className="flex flex-wrap justify-end gap-2 border-t border-slate-200 p-4 dark:border-slate-800">
          <Button
            variant="secondary"
            onClick={onClose}
            disabled={busy}
          >
            取消
          </Button>
          <Button
            onClick={onSubmit}
            disabled={busy}
            leftIcon={
              busy ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <Plus className="h-4 w-4" />
              )
            }
          >
            保存并测试
          </Button>
        </footer>
      </div>
    </div>
  );
}

export default AppsPage;
