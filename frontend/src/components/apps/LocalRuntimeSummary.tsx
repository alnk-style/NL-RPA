import {
  AlertTriangle,
  Ban,
  Boxes,
  CircleOff,
  PlayCircle,
} from 'lucide-react';

import { localAppStatusCounts } from '../../lib/local-apps';
import type { LocalAppInfo } from '../../types/localApps';

const ITEMS = [
  { key: 'all', label: '本机应用', icon: Boxes, tone: 'text-slate-500' },
  { key: 'running', label: '运行中', icon: PlayCircle, tone: 'text-emerald-600' },
  { key: 'stopped', label: '未运行', icon: CircleOff, tone: 'text-slate-400' },
  { key: 'error', label: '启动失败', icon: AlertTriangle, tone: 'text-red-600' },
  { key: 'unsupported', label: '不支持', icon: Ban, tone: 'text-amber-600' },
] as const;

export function LocalRuntimeSummary({
  apps,
}: {
  apps: LocalAppInfo[];
}) {
  const counts = localAppStatusCounts(apps);

  return (
    <section className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-slate-200 bg-slate-200 sm:grid-cols-5 dark:border-slate-800 dark:bg-slate-800">
      {ITEMS.map(({ key, label, icon: Icon, tone }) => (
        <div
          key={key}
          className="flex min-w-0 items-center gap-2 bg-white px-3 py-3 dark:bg-slate-900"
        >
          <Icon className={`h-4 w-4 shrink-0 ${tone}`} />
          <div className="min-w-0">
            <p className="text-lg font-semibold leading-5 text-slate-900 dark:text-white">
              {counts[key]}
            </p>
            <p className="truncate text-[11px] text-slate-500 dark:text-slate-400">
              {label}
            </p>
          </div>
        </div>
      ))}
    </section>
  );
}

export default LocalRuntimeSummary;
