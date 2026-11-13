export type HumanTaskStatus =
  | 'pending'
  | 'resolved'
  | 'cancelled'
  | 'expired'
  | string;

const KIND_LABELS: Record<string, string> = {
  login_required: '登录',
  mfa_required: '多因素验证',
  captcha_required: '验证码 / 扫码',
  manual_confirmation: '人工确认',
  visual_ambiguous: '视觉确认',
};

const STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  resolved: '已解决',
  cancelled: '已取消',
  expired: '已过期',
};

const STATUS_VARIANTS: Record<
  string,
  'default' | 'secondary' | 'success' | 'warning' | 'danger' | 'outline'
> = {
  pending: 'warning',
  resolved: 'success',
  cancelled: 'secondary',
  expired: 'danger',
};

export function humanTaskKindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind ?? '人工处理';
}

export function humanTaskStatusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status ?? '未知';
}

export function humanTaskStatusVariant(
  status: string,
): 'default' | 'secondary' | 'success' | 'warning' | 'danger' | 'outline' {
  return STATUS_VARIANTS[status] ?? 'outline';
}

export function canResumeHumanTask(
  task: {
    status?: string;
    expires_at?: string | null;
  },
  options: {
    verifying?: boolean;
    now?: number;
  } = {},
): boolean {
  if (task.status !== 'pending' || options.verifying) {
    return false;
  }
  if (!task.expires_at) {
    return true;
  }
  const expiresAt = Date.parse(task.expires_at);
  if (Number.isNaN(expiresAt)) {
    return true;
  }
  return expiresAt > (options.now ?? Date.now());
}

export function formatHumanTaskWaitingTime(
  seconds: number | null | undefined,
): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) {
    return '—';
  }
  const value = Math.max(0, Math.floor(seconds));
  if (value === 0) {
    return '刚刚';
  }
  if (value < 60) {
    return '不到 1 分钟';
  }
  const minutes = Math.floor(value / 60);
  if (minutes < 60) {
    return `${minutes} 分钟`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  if (hours < 24) {
    return remainingMinutes
      ? `${hours} 小时 ${remainingMinutes} 分钟`
      : `${hours} 小时`;
  }
  const days = Math.floor(hours / 24);
  return `${days} 天`;
}

export function humanTaskVerificationMessage(
  error: unknown,
  fallback = '重新验证失败，请保持任务待处理并检查当前页面。',
): string {
  if (error && typeof error === 'object') {
    const response = (error as {
      response?: { data?: { detail?: unknown } };
    }).response;
    const detail = response?.data?.detail;
    if (typeof detail === 'string' && detail) {
      return detail;
    }
    if (detail && typeof detail === 'object') {
      const reason = (detail as { reason?: unknown }).reason;
      if (typeof reason === 'string' && reason) {
        return reason;
      }
    }
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

export function isHumanTaskExpired(
  task: {
    status?: string;
    expires_at?: string | null;
  },
  now = Date.now(),
): boolean {
  if (task.status === 'expired') {
    return true;
  }
  if (!task.expires_at) {
    return false;
  }
  const expiresAt = Date.parse(task.expires_at);
  return !Number.isNaN(expiresAt) && expiresAt <= now;
}

