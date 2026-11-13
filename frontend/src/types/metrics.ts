export interface MetricValue {
  value: number | null;
  unit: string;
  numerator?: number;
  denominator?: number;
  samples?: number;
  execution_count?: number;
  interventions?: number;
  locator_failures?: number;
  step_count?: number;
  repairs_attempted?: number;
  repairs_succeeded?: number;
  current_avg_seconds?: number | null;
  baseline_avg_seconds?: number | null;
  delta_percent?: number | null;
  current_samples?: number;
  baseline_samples?: number;
}

export interface FailureCategoryStat {
  count: number;
  rate: number;
  share_percent: number;
}

export interface MetricsDimensions {
  workflow_id: number | null;
  executor: string | null;
  user_id: number | null;
}

export interface MetricsRange {
  preset: string;
  start: string | null;
  end: string;
}

export interface MetricsOverview {
  enabled: boolean;
  generated_at: string;
  range: MetricsRange;
  dimensions: MetricsDimensions;
  execution_count: number;
  metrics: Record<string, MetricValue>;
  failure_categories: Record<string, FailureCategoryStat>;
}

export interface MetricsQueryParams {
  range?: string;
  workflowId?: number | null;
  executor?: string | null;
  start?: string;
  end?: string;
}

export type MetricTone = 'ok' | 'warning' | 'danger' | 'neutral';

export interface MetricCard {
  key: string;
  label: string;
  value: string;
  detail: string;
  tone: MetricTone;
}

export interface ExecutionStepSummary {
  stepCount: number;
  failedSteps: number;
  locatorFailures: number;
  repairsAttempted: number;
  repairsSucceeded: number;
  failureCategories: Array<{
    category: string;
    label: string;
    count: number;
  }>;
}
