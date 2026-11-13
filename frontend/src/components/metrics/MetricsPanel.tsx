import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import * as metricsApi from '../../api/metrics';
import {
  buildFailureCategoryRows,
  buildMetricCards,
} from '../../lib/metrics';
import type { MetricsOverview } from '../../types/metrics';
import { Button } from '../ui/Button';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/Card';

const RANGES = [
  { value: '24h', label: '24 小时' },
  { value: '7d', label: '7 天' },
  { value: '30d', label: '30 天' },
];

const EXECUTORS = [
  { value: '', label: '全部执行器' },
  { value: 'web', label: '网页' },
  { value: 'windows', label: '桌面' },
  { value: 'mcp', label: 'MCP' },
];

const TONE_CLASSES: Record<string, string> = {
  ok: 'text-emerald-600 dark:text-emerald-400',
  warning: 'text-amber-600 dark:text-amber-400',
  danger: 'text-red-600 dark:text-red-400',
  neutral: 'text-slate-600 dark:text-slate-300',
};

export function MetricsPanel({
  workflowOptions = [],
}: {
  workflowOptions?: Array<{ id: number; name: string }>;
}) {
  const [range, setRange] = useState('7d');
  const [executor, setExecutor] = useState('');
  const [workflowId, setWorkflowId] = useState('');
  const [overview, setOverview] = useState<MetricsOverview | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [disabled, setDisabled] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadMetrics = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await metricsApi.getMetricsOverview({
        range,
        executor: executor || null,
        workflowId: workflowId ? Number(workflowId) : null,
      });
      setOverview(response);
      setDisabled(false);
    } catch (requestError) {
      if (metricsApi.isMetricsDisabledError(requestError)) {
        setDisabled(true);
        setOverview(null);
      } else {
        setError('指标加载失败。');
      }
    } finally {
      setIsLoading(false);
    }
  }, [executor, range, workflowId]);

  useEffect(() => {
    void loadMetrics();
  }, [loadMetrics]);

  if (disabled) {
    return null;
  }

  const cards = buildMetricCards(overview);
  const failureRows = buildFailureCategoryRows(
    overview?.failure_categories,
  );

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 pb-3 sm:flex-row sm:items-center sm:justify-between">
        <CardTitle className="text-sm text-slate-500 dark:text-slate-400">
          执行指标
        </CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex overflow-hidden rounded-md border border-slate-200 dark:border-slate-700">
            {RANGES.map((item) => (
              <button
                key={item.value}
                type="button"
                className={
                  range === item.value
                    ? 'bg-slate-900 px-2.5 py-1 text-xs font-medium text-white dark:bg-slate-100 dark:text-slate-900'
                    : 'bg-white px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800'
                }
                onClick={() => setRange(item.value)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <select
            aria-label="执行器"
            className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
            value={executor}
            onChange={(event) => setExecutor(event.target.value)}
          >
            {EXECUTORS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          {workflowOptions.length > 0 ? (
            <select
              aria-label="流程"
              className="max-w-[180px] rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
              value={workflowId}
              onChange={(event) => setWorkflowId(event.target.value)}
            >
              <option value="">全部流程</option>
              {workflowOptions.map((workflow) => (
                <option key={workflow.id} value={workflow.id}>
                  {workflow.name || `流程 #${workflow.id}`}
                </option>
              ))}
            </select>
          ) : null}
          <Button
            variant="ghost"
            size="sm"
            leftIcon={
              <RefreshCw
                className={
                  isLoading ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'
                }
              />
            }
            disabled={isLoading}
            onClick={() => {
              void loadMetrics();
            }}
          >
            刷新
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {error ? (
          <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
        ) : null}
        {isLoading && !overview ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {Array.from({ length: 7 }, (_, index) => (
              <div
                key={index}
                className="h-[74px] animate-pulse rounded-md bg-slate-100 dark:bg-slate-800"
              />
            ))}
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {cards.map((card) => (
              <div
                key={card.key}
                className="rounded-md border border-slate-100 bg-slate-50 px-3 py-2.5 dark:border-slate-800 dark:bg-slate-950/40"
              >
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {card.label}
                </p>
                <p
                  className={`mt-1 text-lg font-semibold tabular-nums ${
                    TONE_CLASSES[card.tone] ?? TONE_CLASSES.neutral
                  }`}
                >
                  {card.value}
                </p>
                <p className="text-[11px] text-slate-400 dark:text-slate-500">
                  {card.detail}
                </p>
              </div>
            ))}
          </div>
        )}
        <div>
          <p className="mb-2 text-xs font-medium text-slate-500 dark:text-slate-400">
            失败分类
          </p>
          {failureRows.length === 0 ? (
            <p className="text-xs text-slate-400 dark:text-slate-500">
              当前时间窗没有失败步骤。
            </p>
          ) : (
            <div className="space-y-1.5">
              {failureRows.map((row) => (
                <div
                  key={row.category}
                  className="flex items-center gap-3 text-xs"
                >
                  <span className="w-24 shrink-0 text-slate-600 dark:text-slate-300">
                    {row.label}
                  </span>
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                    <div
                      className="h-full rounded-full bg-amber-500"
                      style={{
                        width: `${Math.min(row.sharePercent, 100)}%`,
                      }}
                    />
                  </div>
                  <span className="w-16 shrink-0 text-right tabular-nums text-slate-500 dark:text-slate-400">
                    {row.count} 次 · {row.sharePercent.toFixed(0)}%
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default MetricsPanel;
