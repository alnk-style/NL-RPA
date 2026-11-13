import {
  AlertTriangle,
  CheckCircle2,
  CircleOff,
  Clock3,
  ShieldAlert,
} from 'lucide-react';

import {
  appStatusCounts,
} from '../../lib/apps';
import type { MCPServerRecord } from '../../types/mcp';

const ITEMS = [
  { key: 'connected', label: '已连接', icon: CheckCircle2, tone: 'text-emerald-600' },
  { key: 'pending_authorization', label: '待授权', icon: ShieldAlert, tone: 'text-amber-600' },
  { key: 'error', label: '异常', icon: AlertTriangle, tone: 'text-red-600' },
  { key: 'untested', label: '尚未测试', icon: Clock3, tone: 'text-slate-500' },
  { key: 'disabled', label: '已停用', icon: CircleOff, tone: 'text-slate-400' },
] as const;

export function AppStatusSummary({
  servers,
}: {
  servers: MCPServerRecord[];
}) {
  const counts = appStatusCounts(servers);

  return (
    <section className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-slate-200 bg-slate-200 sm:grid-cols-5">
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

export default AppStatusSummary;

