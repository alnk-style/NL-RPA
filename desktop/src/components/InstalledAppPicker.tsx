import {
  AppWindow,
  Check,
  LoaderCircle,
  RefreshCw,
  Search,
  X,
} from 'lucide-react';
import {
  useDeferredValue,
  useEffect,
  useRef,
  useState,
} from 'react';

import api from '../../../frontend/src/api/axios';
import { Button } from '../../../frontend/src/components/ui/Button';
import { Input } from '../../../frontend/src/components/ui/Input';
import { cn } from '../../../frontend/src/lib/utils';
import { getBackendUrl } from '../lib/backend';
import {
  executableLabel,
  filterInstalledApps,
  installedAppIconUrl,
  pathKey,
  type InstalledApp,
} from '../lib/installed-apps';

const iconObjectUrls = new Map<string, string>();
const iconRequests = new Map<string, Promise<string>>();

async function loadIconBlobUrl(iconUrl: string): Promise<string> {
  const cached = iconObjectUrls.get(iconUrl);
  if (cached) {
    return cached;
  }
  const pending = iconRequests.get(iconUrl);
  if (pending) {
    return pending;
  }
  const request = api
    .get<Blob>(iconUrl, { responseType: 'blob' })
    .then((response) => {
      const objectUrl = URL.createObjectURL(response.data);
      iconObjectUrls.set(iconUrl, objectUrl);
      return objectUrl;
    })
    .finally(() => {
      iconRequests.delete(iconUrl);
    });
  iconRequests.set(iconUrl, request);
  return request;
}

export function InstalledAppIcon({
  app,
  className,
}: {
  app: InstalledApp | undefined;
  className?: string;
}) {
  const containerRef = useRef<HTMLSpanElement | null>(null);
  const [visible, setVisible] = useState(false);
  const [src, setSrc] = useState('');

  useEffect(() => {
    const element = containerRef.current;
    if (!app || !element) {
      return;
    }
    if (!('IntersectionObserver' in window)) {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: '160px' },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [app?.key]);

  useEffect(() => {
    if (!app || !visible) {
      return;
    }
    let cancelled = false;
    const fullUrl = installedAppIconUrl(
      app.icon_url,
      getBackendUrl(),
    );
    void loadIconBlobUrl(fullUrl)
      .then((objectUrl) => {
        if (!cancelled) {
          setSrc(objectUrl);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSrc('');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [app?.icon_url, visible]);

  return (
    <span
      ref={containerRef}
      className={cn(
        'flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-md border border-slate-200 bg-slate-50 text-slate-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-500',
        className,
      )}
    >
      {src ? (
        <img
          src={src}
          alt=""
          className="h-full w-full object-contain p-1"
        />
      ) : (
        <AppWindow className="h-4 w-4" />
      )}
    </span>
  );
}

interface InstalledAppPickerProps {
  open: boolean;
  apps: InstalledApp[];
  loading: boolean;
  error: string | null;
  allowedApps: string[];
  busy: boolean;
  onRefresh: () => void;
  onAdd: (app: InstalledApp) => void;
  onClose: () => void;
}

export function InstalledAppPicker({
  open,
  apps,
  loading,
  error,
  allowedApps,
  busy,
  onRefresh,
  onAdd,
  onClose,
}: InstalledAppPickerProps) {
  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query);

  useEffect(() => {
    if (!open) {
      setQuery('');
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, open]);

  if (!open) {
    return null;
  }

  const allowed = new Set(allowedApps.map(pathKey));
  const filteredApps = filterInstalledApps(apps, deferredQuery);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4 backdrop-blur-sm"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target) {
          onClose();
        }
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="installed-app-picker-title"
        className="flex max-h-[86vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900"
      >
        <header className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <div>
            <h2
              id="installed-app-picker-title"
              className="text-base font-semibold text-slate-950 dark:text-white"
            >
              选择已安装应用
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
              disabled={loading || busy}
              aria-label="重新扫描已安装应用"
              title="重新扫描"
            >
              <RefreshCw
                className={cn(
                  'h-4 w-4',
                  loading && 'animate-spin',
                )}
              />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={onClose}
              aria-label="关闭应用选择器"
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
        </header>

        <div className="border-b border-slate-100 px-5 py-3 dark:border-slate-800">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索应用名称或路径"
              className="pl-9"
              autoFocus
            />
          </div>
          {error ? (
            <p className="mt-2 text-xs text-red-600 dark:text-red-400">
              {error}
            </p>
          ) : null}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {loading && apps.length === 0 ? (
            <div className="flex min-h-56 items-center justify-center gap-2 text-sm text-slate-500">
              <LoaderCircle className="h-4 w-4 animate-spin" />
              正在扫描已安装应用
            </div>
          ) : filteredApps.length > 0 ? (
            <div className="divide-y divide-slate-100 dark:divide-slate-800">
              {filteredApps.map((app) => {
                const added = allowed.has(pathKey(app.executable_path));
                return (
                  <div
                    key={app.key}
                    className="flex items-center gap-3 px-5 py-3"
                  >
                    <InstalledAppIcon app={app} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                        {app.name}
                      </p>
                      <p
                        className="truncate text-xs text-slate-500 dark:text-slate-400"
                        title={app.executable_path}
                      >
                        {executableLabel(app.executable_path)}
                      </p>
                    </div>
                    <Button
                      variant={added ? 'ghost' : 'secondary'}
                      size="sm"
                      leftIcon={
                        added ? (
                          <Check className="h-4 w-4" />
                        ) : undefined
                      }
                      onClick={() => onAdd(app)}
                      disabled={busy || added}
                    >
                      {added ? '已添加' : '添加'}
                    </Button>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="flex min-h-56 items-center justify-center px-6 text-center text-sm text-slate-500">
              没有匹配的应用
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
