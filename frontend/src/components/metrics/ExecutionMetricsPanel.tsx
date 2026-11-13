import { useMemo } from 'react';
import { Activity } from 'lucide-react';

import { summarizeExecutionSteps } from '../../lib/metrics';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/Card';

export function ExecutionMetricsPanel({
  stepLogs,
  durationLabel,
}: {
  stepLogs: unknown[] | null | undefined;
  durationLabel: string;
}) {
  const summary = useMemo(
    () => summarizeExecutionSteps(stepLogs),
    [stepLogs],
  );

  const items = [
    { label: '步骤', value: String(summary.stepCount) },
    { label: '失败步骤', value: String(summary.failedSteps) },
    { label: '定位失败', value: String(summary.locatorFailures) },
    {
      label: '自动修复',
      value: summary.repairsAttempted
        ? `${summary.repairsSucceeded}/${summary.repairsAttempted}`
        : '—',
    },
    { label: '执行耗时', value: durationLabel },
  ];

  return (
    <Card>
      <CardHeader className="flex flex-row items-center gap-2 pb-2">
        <Activity className="h-4 w-4 text-slate-400" />
        <CardTitle className="text-sm text-slate-500 dark:text-slate-400">
          本次执行指标
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          {items.map((item) => (
            <div key={item.label}>
              <p className="text-[11px] text-slate-500 dark:text-slate-400">
                {item.label}
              </p>
              <p className="mt-0.5 text-sm font-semibold tabular-nums">
                {item.value}
              </p>
            </div>
          ))}
        </div>
        {summary.failureCategories.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {summary.failureCategories.map((row) => (
              <span
                key={row.category}
                className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] text-amber-700 dark:bg-amber-950/40 dark:text-amber-300"
              >
                {row.label} × {row.count}
              </span>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export default ExecutionMetricsPanel;
