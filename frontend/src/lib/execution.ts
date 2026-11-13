import type {
  ExecutionRecord,
  ExecutionStepLog,
  ExecutionStepStatus,
} from '../types/workflow';

type UnknownRecord = Record<string, unknown>;

const TERMINAL_EXECUTION_STATUSES = new Set([
  'success',
  'failed',
  'error',
  'completed',
  'cancelled',
]);

export function isTerminalExecutionStatus(status: string): boolean {
  return TERMINAL_EXECUTION_STATUSES.has(status);
}

function asRecord(value: unknown): UnknownRecord | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  return value as UnknownRecord;
}

function asString(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null;
  }
  const text = value.trim();
  return text || null;
}

function asNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function getStepStatus(value: unknown): ExecutionStepStatus {
  const status = asString(value)?.toLowerCase();
  if (
    status === 'success' ||
    status === 'warning' ||
    status === 'failed' ||
    status === 'running' ||
    status === 'pending' ||
    status === 'skipped' ||
    status === 'awaiting_user' ||
    status === 'cancelled'
  ) {
    return status;
  }
  if (status === 'completed') {
    return 'success';
  }
  if (status === 'error') {
    return 'failed';
  }
  return 'unknown';
}

export function normalizeExecutionStepLog(
  value: unknown,
  fallbackIndex: number,
  screenshotAvailable = false,
): ExecutionStepLog {
  const record = asRecord(value) ?? {};
  const startedAt = asNumber(record.started_at);
  const finishedAt = asNumber(record.finished_at);
  const durationMs = asNumber(record.duration_ms);
  const explicitDuration =
    asNumber(record.duration_seconds) ??
    asNumber(record.duration) ??
    (durationMs === null ? null : durationMs / 1000);
  const measuredDuration =
    startedAt !== null &&
    finishedAt !== null &&
    finishedAt >= startedAt
      ? finishedAt - startedAt
      : null;
  const screenshotPath = asString(record.screenshot_path);

    return {
      index: asNumber(record.step_index) ?? fallbackIndex,
      action:
        asString(record.action) ??
        asString(record.action_type) ??
        'unknown',
      target:
        asString(record.target) ??
        asString(record.value) ??
        asString(record.message) ??
        '',
    selector: asString(record.selector),
    status: getStepStatus(record.status),
    durationSeconds: measuredDuration ?? explicitDuration,
    screenshotPath,
    hasScreenshot:
      screenshotAvailable ||
      Boolean(screenshotPath) ||
      Boolean(record.has_screenshot),
    error: asString(record.error),
    screenshotError: asString(record.screenshot_error),
    startedAt,
    finishedAt,
    executor: asString(record.executor) ?? undefined,
    server: asString(record.server) ?? undefined,
    tool: asString(record.tool) ?? undefined,
    risk_level: asString(record.risk_level) ?? undefined,
    decision: asString(record.decision) ?? undefined,
    duration_ms: durationMs ?? undefined,
    attempts: asNumber(record.attempts) ?? undefined,
    audit_id: asNumber(record.audit_id) ?? undefined,
    artifact_count: asNumber(record.artifact_count) ?? undefined,
    result_path: asString(record.result_path) ?? undefined,
    arguments_summary: asRecord(record.arguments_summary),
    result_summary:
      record.result_summary ??
      asString(record.result_summary) ??
      undefined,
  };
}

export function mergeExecutionStepLogs(
  current: ExecutionStepLog[],
  incoming: ExecutionStepLog,
): ExecutionStepLog[] {
  const position = current.findIndex(
    (step) => step.index === incoming.index,
  );

  if (position === -1) {
    return [...current, incoming].sort((left, right) => (
      left.index - right.index
    ));
  }

  const next = [...current];
  next[position] = incoming;
  return next;
}

export function formatDuration(
  seconds: number | null | undefined,
): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return '—';
  }

  const safeSeconds = Math.max(0, seconds);
  if (safeSeconds < 1) {
    return `${Math.round(safeSeconds * 1000)} ms`;
  }
  if (safeSeconds < 60) {
    return `${safeSeconds.toFixed(1)} s`;
  }

  const minutes = Math.floor(safeSeconds / 60);
  const remainingSeconds = Math.round(safeSeconds % 60);
  return `${minutes} min ${remainingSeconds} s`;
}

export function executionVersionLabel(execution: {
  workflow_version?: number | null;
  workflow_version_id?: number | null;
}): string {
  if (
    typeof execution.workflow_version === 'number'
    && Number.isFinite(execution.workflow_version)
  ) {
    return `v${execution.workflow_version}`;
  }
  if (
    typeof execution.workflow_version_id === 'number'
    && Number.isFinite(execution.workflow_version_id)
  ) {
    return `版本 ID ${execution.workflow_version_id}`;
  }
  return '未记录版本';
}

export function deliveryStatusVariant(
  status: string,
): 'success' | 'danger' | 'warning' | 'secondary' {
  if (status === 'success') {
    return 'success';
  }
  if (status === 'failed' || status === 'cancelled') {
    return 'danger';
  }
  if (status === 'warning' || status === 'unknown') {
    return 'warning';
  }
  return 'secondary';
}

export function getExecutionDurationSeconds(
  startedAt: string,
  finishedAt: string | null,
  nowMs = Date.now(),
): number | null {
  const start = Date.parse(startedAt);
  if (Number.isNaN(start)) {
    return null;
  }

  const end = finishedAt ? Date.parse(finishedAt) : nowMs;
  if (Number.isNaN(end) || end < start) {
    return null;
  }

  return (end - start) / 1000;
}

export function applyExecutionStatusUpdate(
  current: ExecutionRecord,
  payload: UnknownRecord,
  nowMs = Date.now(),
): ExecutionRecord {
  const status = asString(payload.status) ?? current.status;
  const terminal = isTerminalExecutionStatus(status);
  const startedAt = asString(payload.started_at) ?? current.started_at;
  const payloadFinishedAt = asString(payload.finished_at);
  const finishedAt = payloadFinishedAt ?? (
    terminal
      ? current.finished_at ?? new Date(nowMs).toISOString()
      : current.finished_at
  );
  const errorMessage = typeof payload.error_message === 'string'
    ? payload.error_message
    : current.error_message;

  return {
    ...current,
    status,
    started_at: startedAt,
    finished_at: finishedAt,
    error_message: errorMessage,
  };
}

export function buildExecutionWebSocketUrl(
  executionId: number,
  baseUrl: string,
): string {
  const url = new URL(baseUrl);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.pathname = `/api/executions/${executionId}/live`;
  try {
    const token = localStorage.getItem('token');
    if (token) {
      url.searchParams.set('token', token);
    }
  } catch {
    // Storage can be unavailable in hardened webviews.
  }
  url.hash = '';
  return url.toString().replace(/\/$/, '');
}

export function buildScreenshotRequestPath(
  executionId: number,
  stepIndex: number,
): string {
  return `/api/executions/${executionId}/screenshot/${stepIndex}`;
}
