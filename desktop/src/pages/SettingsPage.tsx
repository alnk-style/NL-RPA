import { invoke } from '@tauri-apps/api/core';
import { open as openDialog } from '@tauri-apps/plugin-dialog';
import {
  AppWindow,
  FolderOpen,
  LoaderCircle,
  Monitor,
  Plus,
  RefreshCw,
  Server,
  Settings,
  ShieldCheck,
  Trash2,
} from 'lucide-react';
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from 'react';

import api from '../../../frontend/src/api/axios';
import { Badge } from '../../../frontend/src/components/ui/Badge';
import { Button } from '../../../frontend/src/components/ui/Button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '../../../frontend/src/components/ui/Card';
import { Input } from '../../../frontend/src/components/ui/Input';
import { getApiErrorMessage } from '../../../frontend/src/lib/errors';
import { cn } from '../../../frontend/src/lib/utils';
import {
  InstalledAppIcon,
  InstalledAppPicker,
} from '../components/InstalledAppPicker';
import {
  getBackendUrl,
  getRemoteBackendUrl,
  getRunMode,
  isTauriMode,
  setRemoteBackendUrl,
  setRunMode,
  type RunMode,
} from '../lib/backend';
import {
  executableLabel,
  pathKey,
  type InstalledApp,
  type InstalledAppsResponse,
} from '../lib/installed-apps';

interface AgentRecord {
  id: number;
  name: string;
  platform: string;
  status: string;
}

interface AgentListResponse {
  agents: AgentRecord[];
}

interface AgentConfig {
  allowed_apps: string[];
  allowed_doc_dirs: string[];
}

interface DesktopSettings {
  run_mode: RunMode;
  remote_backend_url: string;
  deepseek_api_key_set: boolean;
  agent_pairing_code_set: boolean;
}

interface AllowlistEditorProps {
  title: string;
  description: string;
  icon: typeof AppWindow;
  items: string[];
  value: string;
  placeholder: string;
  busy: boolean;
  onValueChange: (value: string) => void;
  onAdd: () => void;
  onBrowse: () => void;
  onRemove: (value: string) => void;
}

function AllowlistEditor({
  title,
  description,
  icon: Icon,
  items,
  value,
  placeholder,
  busy,
  onValueChange,
  onAdd,
  onBrowse,
  onRemove,
}: AllowlistEditorProps) {
  return (
    <section className="space-y-3">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-md bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300">
          <Icon className="h-4 w-4" />
        </div>
        <div>
          <h3 className="text-sm font-semibold">{title}</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {description}
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          value={value}
          onChange={(event) => onValueChange(event.target.value)}
          placeholder={placeholder}
          disabled={busy}
        />
        <div className="flex gap-2">
          <Button
            variant="secondary"
            leftIcon={<FolderOpen className="h-4 w-4" />}
            onClick={onBrowse}
            disabled={busy || !isTauriMode()}
          >
            浏览
          </Button>
          <Button
            leftIcon={<Plus className="h-4 w-4" />}
            onClick={onAdd}
            disabled={busy || !value.trim()}
          >
            添加
          </Button>
        </div>
      </div>

      <div className="divide-y divide-slate-100 rounded-md border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
        {items.length > 0 ? (
          items.map((item) => (
            <div
              key={item}
              className="flex items-center gap-3 px-3 py-2.5"
            >
              <span className="min-w-0 flex-1 break-all font-mono text-xs text-slate-700 dark:text-slate-200">
                {item}
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="text-red-600 hover:bg-red-50 hover:text-red-700 dark:text-red-400 dark:hover:bg-red-950/30"
                onClick={() => onRemove(item)}
                disabled={busy}
                aria-label={`删除 ${item}`}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))
        ) : (
          <p className="px-3 py-4 text-center text-xs text-slate-400">
            暂无白名单
          </p>
        )}
      </div>
    </section>
  );
}

interface ApplicationAllowlistEditorProps {
  items: string[];
  apps: InstalledApp[];
  value: string;
  busy: boolean;
  scanning: boolean;
  scanError: string | null;
  onValueChange: (value: string) => void;
  onAdd: () => void;
  onBrowse: () => void;
  onRemove: (value: string) => void;
  onOpenPicker: () => void;
  onRefreshApps: () => void;
}

function ApplicationAllowlistEditor({
  items,
  apps,
  value,
  busy,
  scanning,
  scanError,
  onValueChange,
  onAdd,
  onBrowse,
  onRemove,
  onOpenPicker,
  onRefreshApps,
}: ApplicationAllowlistEditorProps) {
  const appsByPath = useMemo(
    () =>
      new Map(
        apps.map((app) => [pathKey(app.executable_path), app]),
      ),
    [apps],
  );

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-md bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            <AppWindow className="h-4 w-4" />
          </div>
          <div>
            <h3 className="text-sm font-semibold">允许的程序</h3>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              从已安装应用中选择，或手动指定 .exe。
            </p>
          </div>
        </div>
        <Button
          variant="secondary"
          leftIcon={<AppWindow className="h-4 w-4" />}
          onClick={onOpenPicker}
          disabled={busy}
        >
          选择已安装应用
        </Button>
      </div>

      <div className="flex min-h-5 items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
        {scanning ? (
          <>
            <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
            正在扫描已安装应用
          </>
        ) : (
          <span>已发现 {apps.length} 个应用</span>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="h-7 px-2"
          onClick={onRefreshApps}
          disabled={busy || scanning}
          aria-label="重新扫描已安装应用"
          title="重新扫描"
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </Button>
      </div>

      {scanError ? (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          {scanError}
        </p>
      ) : null}

      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          value={value}
          onChange={(event) => onValueChange(event.target.value)}
          placeholder="C:\\Program Files\\App\\app.exe"
          disabled={busy}
        />
        <div className="flex gap-2">
          <Button
            variant="secondary"
            leftIcon={<FolderOpen className="h-4 w-4" />}
            onClick={onBrowse}
            disabled={busy || !isTauriMode()}
          >
            浏览
          </Button>
          <Button
            leftIcon={<Plus className="h-4 w-4" />}
            onClick={onAdd}
            disabled={busy || !value.trim()}
          >
            添加
          </Button>
        </div>
      </div>

      <div className="divide-y divide-slate-100 rounded-md border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
        {items.length > 0 ? (
          items.map((item) => {
            const app = appsByPath.get(pathKey(item));
            return (
              <div
                key={item}
                className="flex items-center gap-3 px-3 py-2.5"
              >
                <InstalledAppIcon app={app} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                    {app?.name ?? executableLabel(item)}
                  </p>
                  <p
                    className="truncate font-mono text-[11px] text-slate-500 dark:text-slate-400"
                    title={item}
                  >
                    {item}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-red-600 hover:bg-red-50 hover:text-red-700 dark:text-red-400 dark:hover:bg-red-950/30"
                  onClick={() => onRemove(item)}
                  disabled={busy}
                  aria-label={`删除 ${item}`}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            );
          })
        ) : (
          <p className="px-3 py-4 text-center text-xs text-slate-400">
            暂无白名单
          </p>
        )}
      </div>
    </section>
  );
}

export function SettingsPage() {
  const [runMode, setRunModeState] = useState<RunMode>(getRunMode);
  const [remoteUrl, setRemoteUrl] = useState(getRemoteBackendUrl);
  const [deepseekApiKey, setDeepseekApiKey] = useState('');
  const [pairingCode, setPairingCode] = useState('');
  const [deepseekApiKeySet, setDeepseekApiKeySet] = useState(false);
  const [pairingCodeSet, setPairingCodeSet] = useState(false);
  const [autostartEnabled, setAutostartEnabled] = useState(false);
  const [autostartBusy, setAutostartBusy] = useState(false);
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<number | null>(
    null,
  );
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [installedApps, setInstalledApps] = useState<InstalledApp[]>([]);
  const [installedAppsLoading, setInstalledAppsLoading] =
    useState(false);
  const [installedAppsError, setInstalledAppsError] = useState<
    string | null
  >(null);
  const [installedPickerOpen, setInstalledPickerOpen] = useState(false);
  const [appsInput, setAppsInput] = useState('');
  const [dirsInput, setDirsInput] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    void api
      .get<AgentListResponse>('/api/agents')
      .then((response) => {
        if (cancelled) {
          return;
        }

        const nextAgents = response.data.agents ?? [];
        setAgents(nextAgents);
        setSelectedAgentId((current) => {
          if (
            current !== null &&
            nextAgents.some((agent) => agent.id === current)
          ) {
            return current;
          }

          return (
            nextAgents.find((agent) => agent.status === 'online')?.id ??
            nextAgents[0]?.id ??
            null
          );
        });
      })
      .catch((requestError) => {
        if (!cancelled) {
          setError(
            getApiErrorMessage(
              requestError,
              '加载 Agent 列表失败。',
            ),
          );
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!isTauriMode()) {
      setIsLoading(false);
      return;
    }

    void invoke<DesktopSettings>('get_desktop_settings')
      .then((settings) => {
        setRunModeState(settings.run_mode);
        setRemoteUrl(settings.remote_backend_url);
        setDeepseekApiKeySet(settings.deepseek_api_key_set);
        setPairingCodeSet(settings.agent_pairing_code_set);
      })
      .catch((invokeError) => {
        setError(
          invokeError instanceof Error
            ? invokeError.message
            : '加载桌面运行配置失败。',
        );
      });

    void invoke<boolean>('get_autostart_enabled')
      .then(setAutostartEnabled)
      .catch(() => setAutostartEnabled(false));
  }, []);

  const loadConfig = useCallback(async (agentId: number) => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await api.get<AgentConfig>(
        `/api/agents/${agentId}/config`,
      );
      setConfig({
        allowed_apps: response.data.allowed_apps ?? [],
        allowed_doc_dirs: response.data.allowed_doc_dirs ?? [],
      });
    } catch (requestError) {
      setConfig(null);
      setError(
        getApiErrorMessage(requestError, '加载 Agent 配置失败。'),
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadInstalledApps = useCallback(
    async (agentId: number, refresh = false) => {
      setInstalledAppsLoading(true);
      setInstalledAppsError(null);
      try {
        const response = await api.get<InstalledAppsResponse>(
          `/api/agents/${agentId}/config/installed-apps`,
          {
            params: refresh ? { refresh: true } : undefined,
          },
        );
        setInstalledApps(response.data.apps ?? []);
      } catch (requestError) {
        setInstalledApps([]);
        setInstalledAppsError(
          getApiErrorMessage(
            requestError,
            '扫描已安装应用失败。',
          ),
        );
      } finally {
        setInstalledAppsLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (selectedAgentId === null) {
      setConfig(null);
      setInstalledApps([]);
      setInstalledAppsError(null);
      setInstalledPickerOpen(false);
      setIsLoading(false);
      return;
    }

    setInstalledPickerOpen(false);
    void loadConfig(selectedAgentId);
    void loadInstalledApps(selectedAgentId);
  }, [loadConfig, loadInstalledApps, selectedAgentId]);

  async function saveConfig(nextConfig: AgentConfig) {
    if (selectedAgentId === null) {
      return;
    }

    setIsSaving(true);
    setError(null);
    setNotice(null);

    try {
      const response = await api.put<AgentConfig>(
        `/api/agents/${selectedAgentId}/config`,
        nextConfig,
      );
      setConfig({
        allowed_apps: response.data.allowed_apps ?? [],
        allowed_doc_dirs: response.data.allowed_doc_dirs ?? [],
      });
      setNotice('Agent 白名单已更新。');
    } catch (requestError) {
      setError(
        getApiErrorMessage(requestError, '保存 Agent 配置失败。'),
      );
    } finally {
      setIsSaving(false);
    }
  }

  async function addInstalledApp(app: InstalledApp) {
    if (!config) {
      return;
    }
    const executable = app.executable_path.trim();
    const exists = config.allowed_apps.some(
      (item) => pathKey(item) === pathKey(executable),
    );
    if (exists) {
      return;
    }
    await saveConfig({
      ...config,
      allowed_apps: [...config.allowed_apps, executable],
    });
  }

  async function handleSaveRunMode() {
    if (runMode === 'remote' && !remoteUrl.trim()) {
      setError('请输入远程服务器地址。');
      setNotice(null);
      return;
    }

    try {
      if (isTauriMode()) {
        const saved = await invoke<DesktopSettings>(
          'save_desktop_settings',
          {
            input: {
              run_mode: runMode,
              remote_backend_url: remoteUrl,
              deepseek_api_key: deepseekApiKey || null,
              agent_pairing_code: pairingCode || null,
            },
          },
        );
        setDeepseekApiKeySet(saved.deepseek_api_key_set);
        setPairingCodeSet(saved.agent_pairing_code_set);
        setDeepseekApiKey('');
        setPairingCode('');
      }

      setRunMode(runMode);
      setRemoteBackendUrl(remoteUrl);
      api.defaults.baseURL = getBackendUrl();
      setError(null);
      setNotice('运行模式与连接配置已保存。');
    } catch (invokeError) {
      setError(
        invokeError instanceof Error
          ? invokeError.message
          : '保存桌面运行配置失败。',
      );
      setNotice(null);
    }
  }

  async function handleAutostartToggle() {
    if (!isTauriMode()) {
      return;
    }

    const nextEnabled = !autostartEnabled;
    setAutostartBusy(true);
    setError(null);

    try {
      await invoke('set_autostart_enabled', {
        enabled: nextEnabled,
      });
      setAutostartEnabled(nextEnabled);
    } catch (invokeError) {
      setError(
        invokeError instanceof Error
          ? invokeError.message
          : '更新开机自启动失败。',
      );
    } finally {
      setAutostartBusy(false);
    }
  }

  async function handleOpenLogs() {
    if (!isTauriMode()) {
      return;
    }

    try {
      await invoke('open_logs_directory');
    } catch (invokeError) {
      setError(
        invokeError instanceof Error
          ? invokeError.message
          : '打开日志目录失败。',
      );
    }
  }

  async function browseExecutable() {
    try {
      const selected = await openDialog({
        multiple: false,
        directory: false,
        filters: [
          {
            name: 'Windows 可执行文件',
            extensions: ['exe'],
          },
        ],
      });

      if (typeof selected === 'string') {
        setAppsInput(selected);
      }
    } catch (dialogError) {
      setError(
        dialogError instanceof Error
          ? dialogError.message
          : '选择程序文件失败。',
      );
    }
  }

  async function browseDirectory() {
    try {
      const selected = await openDialog({
        multiple: false,
        directory: true,
      });

      if (typeof selected === 'string') {
        setDirsInput(selected);
      }
    } catch (dialogError) {
      setError(
        dialogError instanceof Error
          ? dialogError.message
          : '选择目录失败。',
      );
    }
  }

  const runModeLabel = runMode === 'local' ? '本地模式' : '远程模式';
  const selectedAgent = agents.find(
    (agent) => agent.id === selectedAgentId,
  );

  return (
    <section className="mx-auto max-w-5xl space-y-6">
      <div>
        <div className="flex items-center gap-3">
          <Settings className="h-5 w-5 text-slate-500" />
          <h1 className="text-2xl font-semibold tracking-tight">设置</h1>
        </div>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          管理桌面客户端连接、启动行为和 Agent 权限。
        </p>
      </div>

      {error ? (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
          {error}
        </div>
      ) : notice ? (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:border-emerald-900/60 dark:bg-emerald-950/30 dark:text-emerald-300">
          {notice}
        </div>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>运行模式</CardTitle>
          <CardDescription>
            当前使用{runModeLabel}
            {selectedAgent ? `，Agent：${selectedAgent.name}` : ''}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="inline-flex rounded-md border border-slate-200 bg-slate-100 p-1 dark:border-slate-700 dark:bg-slate-800">
            {(
              [
                ['local', Monitor, '本地模式'],
                ['remote', Server, '远程模式'],
              ] as const
            ).map(([value, Icon, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setRunModeState(value)}
                className={cn(
                  'inline-flex h-9 items-center gap-2 rounded px-4 text-sm font-medium transition-colors',
                  runMode === value
                    ? 'bg-white text-slate-950 shadow-sm dark:bg-slate-950 dark:text-white'
                    : 'text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white',
                )}
              >
                <Icon className="h-4 w-4" />
                {label}
              </button>
            ))}
          </div>

          {runMode === 'remote' ? (
            <Input
              label="远程服务器地址"
              value={remoteUrl}
              onChange={(event) => setRemoteUrl(event.target.value)}
              placeholder="https://rpa.example.com"
            />
          ) : null}

          <Input
            label="DeepSeek API Key"
            type="password"
            value={deepseekApiKey}
            onChange={(event) => setDeepseekApiKey(event.target.value)}
            placeholder={
              deepseekApiKeySet
                ? '已配置，留空表示不修改'
                : 'sk-...'
            }
            hint="仅写入本机桌面应用数据目录，不会发送到前端页面。"
          />

          {runMode === 'remote' ? (
            <Input
              label="Agent 配对码"
              value={pairingCode}
              onChange={(event) => setPairingCode(event.target.value)}
              placeholder={
                pairingCodeSet
                  ? '已配置，留空表示清除'
                  : '从远程管理页生成'
              }
              hint="远程模式首次连接时使用；本地模式会自动配对。"
            />
          ) : null}

          <Button
            leftIcon={<ShieldCheck className="h-4 w-4" />}
            onClick={() => void handleSaveRunMode()}
          >
            保存运行模式
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>启动行为</CardTitle>
          <CardDescription>控制客户端在用户登录后自动运行。</CardDescription>
        </CardHeader>
        <CardContent>
          <label className="flex items-center justify-between gap-4 rounded-md border border-slate-200 px-4 py-3 dark:border-slate-800">
            <span>
              <span className="block text-sm font-medium">开机自启动</span>
              <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400">
                登录 Windows 后自动启动托盘客户端。
              </span>
            </span>
            <input
              type="checkbox"
              className="peer sr-only"
              checked={autostartEnabled}
              onChange={() => void handleAutostartToggle()}
              disabled={!isTauriMode() || autostartBusy}
            />
            <span
              className={cn(
                'relative h-6 w-11 shrink-0 rounded-full transition-colors',
                'bg-slate-300 peer-checked:bg-brand-600',
                'peer-disabled:cursor-not-allowed peer-disabled:opacity-50',
              )}
            >
              <span
                className={cn(
                  'absolute left-1 top-1 h-4 w-4 rounded-full bg-white shadow-sm transition-transform',
                  autostartEnabled && 'translate-x-5',
                )}
              />
            </span>
          </label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-start justify-between gap-4">
          <div>
            <CardTitle>Agent 白名单</CardTitle>
            <CardDescription>
              限制桌面 Agent 可启动的程序和可访问的目录。
            </CardDescription>
          </div>
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<RefreshCw className="h-4 w-4" />}
            onClick={() =>
              selectedAgentId !== null &&
              void loadConfig(selectedAgentId)
            }
            disabled={selectedAgentId === null || isLoading}
          >
            刷新
          </Button>
        </CardHeader>
        <CardContent className="space-y-5">
          {agents.length > 0 ? (
            <label className="block max-w-sm">
              <span className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">
                Agent
              </span>
              <select
                value={selectedAgentId ?? ''}
                onChange={(event) =>
                  setSelectedAgentId(
                    event.target.value
                      ? Number(event.target.value)
                      : null,
                  )
                }
                className="h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-950 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-white"
              >
                {agents.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name} · {agent.status}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <div className="flex items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
              <span>尚未找到已配对的 Agent。</span>
              <Badge variant="warning">未连接</Badge>
            </div>
          )}

          {isLoading ? (
            <div className="flex min-h-40 items-center justify-center text-sm text-slate-500">
              正在加载配置...
            </div>
          ) : config ? (
            <div className="space-y-6">
              <ApplicationAllowlistEditor
                items={config.allowed_apps}
                apps={installedApps}
                value={appsInput}
                busy={isSaving}
                scanning={installedAppsLoading}
                scanError={installedAppsError}
                onValueChange={setAppsInput}
                onBrowse={() => void browseExecutable()}
                onAdd={() => {
                  const value = appsInput.trim();
                  if (
                    !value ||
                    config.allowed_apps.some(
                      (item) => pathKey(item) === pathKey(value),
                    )
                  ) {
                    return;
                  }
                  void saveConfig({
                    ...config,
                    allowed_apps: [...config.allowed_apps, value],
                  });
                  setAppsInput('');
                }}
                onRemove={(value) =>
                  void saveConfig({
                    ...config,
                    allowed_apps: config.allowed_apps.filter(
                      (item) => item !== value,
                    ),
                  })
                }
                onOpenPicker={() => setInstalledPickerOpen(true)}
                onRefreshApps={() => {
                  if (selectedAgentId !== null) {
                    void loadInstalledApps(selectedAgentId, true);
                  }
                }}
              />

              <AllowlistEditor
                title="允许的目录"
                description="限制文档、截图和导出文件的访问范围。"
                icon={FolderOpen}
                items={config.allowed_doc_dirs}
                value={dirsInput}
                placeholder="C:\\Users\\Administrator\\Documents"
                busy={isSaving}
                onValueChange={setDirsInput}
                onBrowse={() => void browseDirectory()}
                onAdd={() => {
                  const value = dirsInput.trim();
                  if (
                    !value ||
                    config.allowed_doc_dirs.includes(value)
                  ) {
                    return;
                  }
                  void saveConfig({
                    ...config,
                    allowed_doc_dirs: [
                      ...config.allowed_doc_dirs,
                      value,
                    ],
                  });
                  setDirsInput('');
                }}
                onRemove={(value) =>
                  void saveConfig({
                    ...config,
                    allowed_doc_dirs: config.allowed_doc_dirs.filter(
                      (item) => item !== value,
                    ),
                  })
                }
              />
            </div>
          ) : (
            <p className="text-sm text-slate-500 dark:text-slate-400">
              选择 Agent 后可编辑白名单。
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>日志</CardTitle>
          <CardDescription>打开桌面客户端本地日志目录。</CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            variant="secondary"
            leftIcon={<FolderOpen className="h-4 w-4" />}
            onClick={() => void handleOpenLogs()}
            disabled={!isTauriMode()}
          >
            打开日志目录
          </Button>
        </CardContent>
      </Card>

      <InstalledAppPicker
        open={
          installedPickerOpen &&
          selectedAgentId !== null &&
          config !== null
        }
        apps={installedApps}
        loading={installedAppsLoading}
        error={installedAppsError}
        allowedApps={config?.allowed_apps ?? []}
        busy={isSaving}
        onRefresh={() => {
          if (selectedAgentId !== null) {
            void loadInstalledApps(selectedAgentId, true);
          }
        }}
        onAdd={(app) => void addInstalledApp(app)}
        onClose={() => setInstalledPickerOpen(false)}
      />
    </section>
  );
}

export default SettingsPage;
