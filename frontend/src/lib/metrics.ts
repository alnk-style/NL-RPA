import type {
  ExecutionStepSummary,
  FailureCategoryStat,
  MetricCard,
  MetricTone,
  MetricValue,
  MetricsOverview,
} from '../types/metrics';

export const FAILURE_CATEGORY_LABELS: Record<string, string> = {
  locator_not_found: '定位未找到',
  locator_not_unique: '定位不唯一',
  element_not_visible: '元素不可见',
  element_disabled: '元素不可用',
  timeout: '超时',
  auth_required: '需要登录',
  permission_denied: '权限不足',
  app_not_found: '程序未找到',
  window_not_found: '窗口未找到',
  business_error: '业务错误',
  unknown: '未知',
};

const LOCATOR_CATEGORIES = new Set([
  'locator_not_found',
  'locator_not_unique',
  'element_not_visible',
  'element_disabled',
]);

export function failureCategoryLabel(category: string): string {
  return FAILURE_CATEGORY_LABELS[category] ?? category;
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value)
    ? value
    : null;
}

export function formatPercent(
  value: number | null | undefined,
  digits = 1,
): string {
  const numeric = asNumber(value);
  if (numeric === null) {
    return '—';
  }
  return `${(numeric * 100).toFixed(digits)}%`;
}

export function formatSeconds(
  value: number | null | undefined,
): string {
  const numeric = asNumber(value);
  if (numeric === null) {
    return '—';
  }
  if (numeric < 1) {
    return `${Math.round(numeric * 1000)} ms`;
  }
  if (numeric < 60) {
    return `${numeric.toFixed(1)} s`;
  }
  const minutes = Math.floor(numeric / 60);
  const seconds = Math.round(numeric % 60);
  return `${minutes}m${seconds.toString().padStart(2, '0')}s`;
}

export function formatMetricValue(
  metric: MetricValue | undefined,
): string {
  if (!metric) {
    return '—';
  }
  const value = asNumber(metric.value);
  switch (metric.unit) {
    case 'ratio':
      return formatPercent(value);
    case 'seconds':
      return formatSeconds(value);
    case 'per_100_executions':
      return value === null ? '—' : value.toFixed(1);
    case 'executions':
      return value === null ? '—' : String(Math.round(value));
    default:
      return value === null ? '—' : String(value);
  }
}

function ratioDetail(metric: MetricValue): string {
  if (
    typeof metric.numerator === 'number' &&
    typeof metric.denominator === 'number'
  ) {
    return `${metric.numerator}/${metric.denominator}`;
  }
  return '';
}

function toneFor(
  key: string,
  value: number | null,
): MetricTone {
  if (value === null) {
    return 'neutral';
  }
  if (key === 'first_generation_success_rate') {
    return value >= 0.8 ? 'ok' : value >= 0.6 ? 'warning' : 'danger';
  }
  if (key === 'auto_repair_success_rate') {
    return value >= 0.6 ? 'ok' : value > 0 ? 'warning' : 'neutral';
  }
  if (key === 'locator_failure_rate') {
    return value <= 0.1 ? 'ok' : value <= 0.2 ? 'warning' : 'danger';
  }
  if (key === 'manual_interventions_per_100_executions') {
    return value <= 10 ? 'ok' : value <= 20 ? 'warning' : 'danger';
  }
  if (key === 'execution_duration_delta_ratio') {
    return value <= 1.2 ? 'ok' : value <= 1.5 ? 'warning' : 'danger';
  }
  return 'neutral';
}

export function buildMetricCards(
  overview: MetricsOverview | null,
): MetricCard[] {
  if (!overview) {
    return [];
  }
  const metrics = overview.metrics ?? {};
  const definitions: Array<{
    key: string;
    label: string;
    detail: (metric: MetricValue) => string;
  }> = [
    {
      key: 'first_generation_success_rate',
      label: '首次生成成功率',
      detail: ratioDetail,
    },
    {
      key: 'locator_failure_rate',
      label: '定位失败率',
      detail: (metric) => {
        const failures = metric.locator_failures ?? 0;
        const steps = metric.step_count ?? 0;
        return steps ? `${failures}/${steps} 步` : '—';
      },
    },
    {
      key: 'auto_repair_success_rate',
      label: '自动修复成功率',
      detail: (metric) => {
        const success = metric.repairs_succeeded ?? 0;
        const attempted = metric.repairs_attempted ?? 0;
        return attempted ? `${success}/${attempted} 次` : '暂无修复';
      },
    },
    {
      key: 'manual_interventions_per_100_executions',
      label: '人工介入/百次',
      detail: (metric) => (
        `${metric.interventions ?? 0} 次介入`
      ),
    },
    {
      key: 'workflow_creation_duration_seconds',
      label: '流程创建耗时',
      detail: (metric) => (
        metric.samples ? `${metric.samples} 个流程` : '—'
      ),
    },
    {
      key: 'full_rerun_count',
      label: '整条重跑次数',
      detail: (metric) => (
        `${metric.execution_count ?? 0} 次执行`
      ),
    },
    {
      key: 'execution_duration_delta_ratio',
      label: '执行耗时增量',
      detail: (metric) => {
        if (
          typeof metric.delta_percent === 'number'
        ) {
          const sign = metric.delta_percent > 0 ? '+' : '';
          return `${sign}${metric.delta_percent.toFixed(1)}%`;
        }
        return '暂无基线';
      },
    },
  ];

  return definitions.map((definition) => {
    const metric = metrics[definition.key];
    const value = asNumber(metric?.value) ?? null;
    return {
      key: definition.key,
      label: definition.label,
      value: formatMetricValue(metric),
      detail: metric ? definition.detail(metric) : '—',
      tone: toneFor(definition.key, value),
    };
  });
}

export function buildFailureCategoryRows(
  breakdown: Record<string, FailureCategoryStat> | undefined,
): Array<{
  category: string;
  label: string;
  count: number;
  sharePercent: number;
}> {
  if (!breakdown) {
    return [];
  }
  return Object.entries(breakdown)
    .map(([category, stat]) => ({
      category,
      label: failureCategoryLabel(category),
      count: stat?.count ?? 0,
      sharePercent: stat?.share_percent ?? 0,
    }))
    .filter((row) => row.count > 0)
    .sort((a, b) => b.count - a.count);
}

export function summarizeExecutionSteps(
  stepLogs: unknown[] | null | undefined,
): ExecutionStepSummary {
  const summary: ExecutionStepSummary = {
    stepCount: 0,
    failedSteps: 0,
    locatorFailures: 0,
    repairsAttempted: 0,
    repairsSucceeded: 0,
    failureCategories: [],
  };
  const counts = new Map<string, number>();
  for (const raw of stepLogs ?? []) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      continue;
    }
    const entry = raw as Record<string, unknown>;
    if (entry.type === 'checkpoint') {
      continue;
    }
    summary.stepCount += 1;
    const status = String(entry.status ?? '').toLowerCase();
    const failed = ['failed', 'error', 'failure'].includes(status);
    const category = typeof entry.failure_category === 'string'
      ? entry.failure_category
      : '';
    if (failed) {
      summary.failedSteps += 1;
      const key = category || 'unknown';
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    if (category && LOCATOR_CATEGORIES.has(category)) {
      summary.locatorFailures += 1;
    }
    const repairAttempted = (
      status === 'repaired' ||
      Boolean(entry.repaired_step) ||
      (typeof entry.attempts === 'number' && entry.attempts > 1)
    );
    if (repairAttempted) {
      summary.repairsAttempted += 1;
      if (['success', 'repaired', 'completed'].includes(status)) {
        summary.repairsSucceeded += 1;
      }
    }
  }
  summary.failureCategories = Array.from(counts.entries())
    .map(([category, count]) => ({
      category,
      label: failureCategoryLabel(category),
      count,
    }))
    .sort((a, b) => b.count - a.count);
  return summary;
}
