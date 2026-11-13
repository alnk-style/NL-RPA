import {
  Check,
  LoaderCircle,
  RefreshCw,
  Search,
  X,
} from 'lucide-react';
import {
  useDeferredValue,
  useEffect,
  useMemo,
  useState,
} from 'react';

import api from '../../api/axios';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Input } from '../../components/ui/Input';
import {
  SCOPED_DESKTOP_TOOLS,
  executableLabel,
  filterDiscoverableApps,
  installedAppIconUrl,
  type DiscoverableInstalledApp,
} from '../../lib/installed-app-mcp';
import { AuthenticatedAppIcon } from './AuthenticatedAppIcon';

interface AgentRecord {
  id: number;
  name: string;
  platform: string;
  status: string;
}

interface InstalledAppMcpDialogProps {
  open: boolean;
  apps: DiscoverableInstalledApp[];
  loading: boolean;
  error: string | null;
  busyKey: string | null;
  onRefresh: () => void;
  onCreate: (
    app: DiscoverableInstalledApp,
    agentId: number,
  ) => void;
  onClose: () => void;
}

export function InstalledAppMcpDialog({
  open,
  apps,
  loading,
  error,
  busyKey,
  onRefresh,
  onCreate,
  onClose,
}: InstalledAppMcpDialogProps) {
  const [query, setQuery] = useState('');
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [agentId, setAgentId] = useState<number | null>(null);
  const [agentsLoading, setAgentsLoading] = useState(false);
  const deferredQuery = useDeferredValue(query);

  useEffect(() => {
    if (!open) {
      setQuery('');
      setSelectedKey(null);
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busyKey) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [busyKey, onClose, open]);

  useEffect(() => {
    if (!open) {
      return;
    }
    let cancelled = false;
    setAgentsLoading(true);
    void api
      .get<{ agents: AgentRecord[] }>('/api/agents')
      .then((response) => {
        if (cancelled) {
          return;
        }
        const nextAgents = response.data.agents ?? [];
        setAgents(nextAgents);
        setAgentId(
          nextAgents.find((agent) => agent.status === 'online')?.id ??
            nextAgents[0]?.id ??
            null,
        );
      })
      .catch(() => {
        if (!cancelled) {
          setAgents([]);
          setAgentId(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setAgentsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const filteredApps = useMemo(
    () => filterDiscoverableApps(apps, deferredQuery),
    [apps, deferredQuery],
  );
  const selectedApp =
    apps.find((app) => app.key === selectedKey) ?? null;
  const createDisabled =
    !selectedApp ||
    Boolean(selectedApp.generated_app_id) ||
    agentId === null ||
    agentsLoading ||
    Boolean(busyKey);
  const backendUrl =
    api.defaults.baseURL ??
    (typeof window !== 'undefined' ? window.location.origin : '');

  if (!open) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-3 backdrop-blur-sm sm:p-5"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target && !busyKey) {
          onClose();
        }
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="installed-mcp-title"
        className="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900"
      >
        <header className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <div>
            <h2
              id="installed-mcp-title"
              className="text-base font-semibold text-slate-950 dark:text-white"
            >
              从已安装软件创建 MCP
            </h2>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              已发现 {apps.length} 个应用
            </p>
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={onRefresh}
              disabled={loading || Boolean(busyKey)}
              aria-label="重新扫描本机软件"
              title="重新扫描"
            >
              <RefreshCw
                className={loading ? 'h-4 w-4 animate-spin' : 'h-4 w-4'}
              />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={onClose}
              disabled={Boolean(busyKey)}
              aria-label="关闭本机软件选择器"
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
        </header>

        <div className="grid gap-3 border-b border-slate-100 px-5 py-3 sm:grid-cols-[1fr_260px] dark:border-slate-800">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索名称或路径"
              className="pl-9"
              autoFocus
            />
          </div>
          <select
            aria-label="选择桌面 Agent"
            value={agentId ?? ''}
            onChange={(event) =>
              setAgentId(
                event.target.value ? Number(event.target.value) : null,
              )
            }
            disabled={agentsLoading || agents.length === 0}
            className="h-10 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-950 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-white"
          >
            {agents.length === 0 ? (
              <option value="">未连接 Agent</option>
            ) : null}
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name} · {agent.status}
              </option>
            ))}
          </select>
        </div>

        {error ? (
          <p className="border-b border-red-100 bg-red-50 px-5 py-2 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
            {error}
          </p>
        ) : null}

        <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="min-h-0 overflow-y-auto border-b border-slate-100 lg:border-b-0 lg:border-r dark:border-slate-800">
            {loading && apps.length === 0 ? (
              <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-slate-500">
                <LoaderCircle className="h-4 w-4 animate-spin" />
                正在扫描本机软件
              </div>
            ) : filteredApps.length > 0 ? (
              <div className="divide-y divide-slate-100 dark:divide-slate-800">
                {filteredApps.map((app) => {
                  const selected = app.key === selectedKey;
                  return (
                    <button
                      key={app.key}
                      type="button"
                      onClick={() => setSelectedKey(app.key)}
                      className={
                        selected
                          ? 'flex w-full items-center gap-3 bg-brand-50 px-5 py-3 text-left dark:bg-brand-950/30'
                          : 'flex w-full items-center gap-3 px-5 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-800/60'
                      }
                    >
                      <AuthenticatedAppIcon
                        iconUrl={installedAppIconUrl(
                          app.icon_url,
                          backendUrl,
                        )}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                          {app.name}
                        </span>
                        <span className="block truncate text-xs text-slate-500 dark:text-slate-400">
                          {executableLabel(app.executable_path)}
                        </span>
                      </span>
                      {app.generated_app_id ? (
                        <Badge variant="success">
                          <Check className="mr-1 h-3 w-3" />
                          已创建
                        </Badge>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="flex min-h-64 items-center justify-center px-6 text-center text-sm text-slate-500">
                没有匹配的应用
              </div>
            )}
          </div>

          <div className="min-h-0 overflow-y-auto p-5">
            {selectedApp ? (
              <div className="space-y-4">
                <div className="flex items-start gap-3">
                  <AuthenticatedAppIcon
                    iconUrl={installedAppIconUrl(
                      selectedApp.icon_url,
                      backendUrl,
                    )}
                    className="h-11 w-11"
                  />
                  <div className="min-w-0">
                    <h3 className="break-words text-sm font-semibold text-slate-950 dark:text-white">
                      {selectedApp.name}
                    </h3>
                    <p className="mt-1 break-all font-mono text-[11px] text-slate-500 dark:text-slate-400">
                      {selectedApp.executable_path}
                    </p>
                  </div>
                </div>

                <div>
                  <p className="text-xs font-medium text-slate-700 dark:text-slate-200">
                    将创建的 MCP 工具
                  </p>
                  <ul className="mt-2 space-y-1.5">
                    {SCOPED_DESKTOP_TOOLS.map((tool) => (
                      <li
                        key={tool.name}
                        className="flex items-center justify-between gap-2 text-xs"
                      >
                        <span className="font-mono text-slate-600 dark:text-slate-300">
                          {tool.name}
                        </span>
                        <Badge
                          variant={
                            tool.risk === 'read_only'
                              ? 'outline'
                              : 'warning'
                          }
                        >
                          {tool.risk === 'read_only' ? '只读' : '需授权'}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                </div>

                <p className="text-xs leading-5 text-slate-500 dark:text-slate-400">
                  使用当前 Agent 的桌面连接器，在本机进程内运行，不创建独立进程或端口。
                </p>

                <Button
                  className="w-full"
                  onClick={() => {
                    if (selectedApp && agentId !== null) {
                      onCreate(selectedApp, agentId);
                    }
                  }}
                  disabled={createDisabled}
                  leftIcon={
                    busyKey === `discover:${selectedApp.key}` ? (
                      <LoaderCircle className="h-4 w-4 animate-spin" />
                    ) : selectedApp.generated_app_id ? (
                      <Check className="h-4 w-4" />
                    ) : undefined
                  }
                >
                  {selectedApp.generated_app_id
                    ? '已创建 MCP'
                    : '创建 MCP'}
                </Button>
              </div>
            ) : (
              <div className="flex min-h-56 items-center justify-center text-center text-sm text-slate-500">
                选择左侧应用查看将创建的工具
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
