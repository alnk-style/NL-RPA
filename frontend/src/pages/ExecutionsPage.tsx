import {
  useState,
} from 'react';
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  CheckCircle2,
  Clock3,
  LogIn,
  LoaderCircle,
  Pause,
  Play,
  RefreshCw,
  XCircle,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import * as executionApi from '../api/executions';
import { queryKeys } from '../api/queryKeys';
import { MetricsPanel } from '../components/metrics/MetricsPanel';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import {
  Card,
  CardContent,
} from '../components/ui/Card';
import {
  formatDuration,
  executionVersionLabel,
  getExecutionDurationSeconds,
  isTerminalExecutionStatus,
} from '../lib/execution';
import {
  hostEnvironmentLabel,
  hostStatusLabel,
} from '../lib/hosts';
import { getApiErrorMessage } from '../lib/errors';
import type { ExecutionRecord } from '../types/workflow';

const ACTIVE_STATUSES = new Set(['pending', 'running']);

function statusVariant(
  status: string,
): 'success' | 'danger' | 'warning' | 'secondary' {
  if (status === 'success' || status === 'completed') {
    return 'success';
  }
  if (
    status === 'failed' ||
    status === 'error' ||
    status === 'cancelled'
  ) {
    return 'danger';
  }
  if (status === 'running') {
    return 'warning';
  }
  if (status === 'awaiting_user') {
    return 'warning';
  }
  return 'secondary';
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    success: '成功',
    completed: '已完成',
    failed: '失败',
    error: '错误',
    running: '执行中',
    pending: '等待中',
    paused: '已暂停',
    awaiting_user: '等待人工处理',
    cancelled: '已取消',
  };
  return labels[status] ?? status;
}

function formatDateTime(value: string | null): string {
  if (!value) {
    return '—';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date);
}

function formatExecutionDuration(execution: ExecutionRecord): string {
  const finishedAt = execution.finished_at ?? (
    isTerminalExecutionStatus(execution.status)
      ? execution.started_at
      : null
  );
  return formatDuration(
    getExecutionDurationSeconds(
      execution.started_at,
      finishedAt,
    ),
  );
}

function ExecutionStatusIcon({ status }: { status: string }) {
  if (status === 'success' || status === 'completed') {
    return <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />;
  }
  if (
    status === 'failed' ||
    status === 'error' ||
    status === 'cancelled'
  ) {
    return <XCircle className="h-4 w-4 shrink-0 text-red-600" />;
  }
  if (status === 'running' || status === 'pending') {
    return (
      <LoaderCircle className="h-4 w-4 shrink-0 animate-spin text-amber-600" />
    );
  }
  if (status === 'awaiting_user') {
    return <Clock3 className="h-4 w-4 shrink-0 text-amber-600" />;
  }
  return <Clock3 className="h-4 w-4 shrink-0 text-slate-400" />;
}

function ExecutionCard({
  execution,
  isBusy,
  onOpen,
  onPause,
  onResume,
  onResumeAfterLogin,
  onOpenPending,
}: {
  execution: ExecutionRecord;
  isBusy: boolean;
  onOpen: () => void;
  onPause: () => void;
  onResume: () => void;
  onResumeAfterLogin: () => void;
  onOpenPending: () => void;
}) {
  const canPause = ACTIVE_STATUSES.has(execution.status);
  const canResume = execution.status === 'paused';
  const canResumeAfterLogin = (
    execution.status === 'awaiting_user'
    && !execution.human_task_id
  );
  const canOpenPending = (
    execution.status === 'awaiting_user'
    && Boolean(execution.human_task_id)
  );

  return (
    <Card className="overflow-hidden">
      <CardContent className="space-y-4 p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <ExecutionStatusIcon status={execution.status} />
              <h2 className="truncate text-base font-semibold">
                {execution.workflow_name || `流程 #${execution.workflow_id}`}
              </h2>
            </div>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              执行 #{execution.id} · {executionVersionLabel(execution)}
            </p>
          </div>
          <Badge variant={statusVariant(execution.status)}>
            {statusLabel(execution.status)}
          </Badge>
        </div>

        <div className="grid gap-3 text-sm sm:grid-cols-2">
          <div className="rounded-md bg-slate-50 p-3 dark:bg-slate-900">
            <p className="text-xs text-slate-500 dark:text-slate-400">
              开始时间
            </p>
            <p className="mt-1 font-medium">
              {formatDateTime(execution.started_at)}
            </p>
          </div>
          <div className="rounded-md bg-slate-50 p-3 dark:bg-slate-900">
            <p className="text-xs text-slate-500 dark:text-slate-400">
              执行耗时
            </p>
            <p className="mt-1 font-medium">
              {formatExecutionDuration(execution)}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-4 text-xs text-slate-500 dark:text-slate-400">
          <span className="flex items-center gap-1.5">
            <Clock3 className="h-3.5 w-3.5" />
            {execution.step_logs?.length ?? 0} 个步骤
          </span>
          <span>{execution.screenshots?.length ?? 0} 张截图</span>
          {execution.host_id ? (
            <span>
              {execution.host_name || `Agent #${execution.host_id}`}
              {` · ${hostEnvironmentLabel(execution.host_environment)}`}
              {` · ${hostStatusLabel(execution.host_status)}`}
              {execution.host_bound ? ' · host-bound' : ''}
            </span>
          ) : (
            <span>服务器执行或历史记录未记录主机</span>
          )}
        </div>

        {execution.error_message &&
        execution.status !== 'paused' ? (
          <p className="line-clamp-3 rounded-md border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
            {execution.error_message}
          </p>
        ) : null}

        <div className="flex flex-wrap justify-end gap-2">
          {canPause ? (
            <Button
              variant="secondary"
              size="sm"
              leftIcon={
                isBusy
                  ? <LoaderCircle className="h-4 w-4 animate-spin" />
                  : <Pause className="h-4 w-4" />
              }
              disabled={isBusy}
              onClick={onPause}
            >
              暂停
            </Button>
          ) : null}
          {canResume ? (
            <Button
              size="sm"
              leftIcon={
                isBusy
                  ? <LoaderCircle className="h-4 w-4 animate-spin" />
                  : <Play className="h-4 w-4" />
              }
              disabled={isBusy}
              onClick={onResume}
            >
              重新运行
            </Button>
          ) : null}
          {canResumeAfterLogin ? (
            <Button
              size="sm"
              leftIcon={
                isBusy
                  ? <LoaderCircle className="h-4 w-4 animate-spin" />
                  : <LogIn className="h-4 w-4" />
              }
              disabled={isBusy}
              onClick={onResumeAfterLogin}
            >
              旧版登录后继续
            </Button>
          ) : null}
          {canOpenPending ? (
            <Button size="sm" onClick={onOpenPending}>
              处理待办
            </Button>
          ) : null}
          <Button variant="secondary" size="sm" onClick={onOpen}>
            查看详情
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function ExecutionsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [busyExecutionId, setBusyExecutionId] = useState<number | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const executionQuery = useQuery({
    queryKey: queryKeys.executions,
    queryFn: executionApi.listExecutions,
    refetchInterval: (query) => (
      query.state.data?.some((execution) => (
        execution.status === 'running'
        || execution.status === 'pending'
        || execution.status === 'awaiting_user'
      ))
        ? 3000
        : false
    ),
  });
  const executions = executionQuery.data ?? [];
  const isLoading = executionQuery.isLoading;
  const queryError = executionQuery.error
    ? getApiErrorMessage(executionQuery.error, '加载执行记录失败。')
    : null;
  const workflowOptions = Array.from(
    new Map(
      executions.map((execution) => [
        execution.workflow_id,
        execution.workflow_name || `流程 #${execution.workflow_id}`,
      ]),
    ),
  ).map(([id, name]) => ({ id, name }));

  const controlMutation = useMutation({
    mutationFn: async ({
      executionId,
      action,
    }: {
      executionId: number;
      action: 'pause' | 'resume' | 'resume-after-login';
    }) => (
      action === 'pause'
        ? executionApi.pauseExecution(executionId)
        : action === 'resume'
          ? executionApi.resumeExecution(executionId)
          : executionApi.resumeExecutionAfterLogin(executionId)
    ),
  });

  async function updateExecutionState(
    executionId: number,
    action: 'pause' | 'resume' | 'resume-after-login',
  ) {
    setBusyExecutionId(executionId);
    setError(null);
    try {
      await controlMutation.mutateAsync({ executionId, action });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.executions,
      });
    } catch (requestError) {
      setError(
        getApiErrorMessage(
          requestError,
          action === 'pause'
            ? '暂停执行失败。'
            : action === 'resume'
              ? '重新运行失败。'
              : '登录后继续失败。',
        ),
      );
    } finally {
      setBusyExecutionId(null);
    }
  }

  return (
    <section className="space-y-6">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            执行监控
          </h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            查看试运行和定时任务的执行结果、截图与错误信息。
          </p>
        </div>
        <Button
          variant="secondary"
          leftIcon={
            isLoading
              ? <LoaderCircle className="h-4 w-4 animate-spin" />
              : <RefreshCw className="h-4 w-4" />
          }
          disabled={isLoading}
          onClick={() => {
            void executionQuery.refetch();
          }}
        >
          刷新
        </Button>
      </div>

      {error || queryError ? (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
          {error ?? queryError}
        </div>
      ) : null}

      <MetricsPanel workflowOptions={workflowOptions} />

      {isLoading ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {Array.from({ length: 4 }, (_, index) => (
            <Card
              key={index}
              className="h-[250px] animate-pulse bg-slate-100 dark:bg-slate-900"
            />
          ))}
        </div>
      ) : executions.length > 0 ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {executions.map((execution) => (
            <ExecutionCard
              key={execution.id}
              execution={execution}
              isBusy={busyExecutionId === execution.id}
              onOpen={() => navigate(`/executions/${execution.id}`)}
              onPause={() => {
                void updateExecutionState(execution.id, 'pause');
              }}
              onResume={() => {
                void updateExecutionState(execution.id, 'resume');
              }}
              onResumeAfterLogin={() => {
                void updateExecutionState(
                  execution.id,
                  'resume-after-login',
                );
              }}
              onOpenPending={() => {
                navigate(
                  `/pending?task=${execution.human_task_id}`,
                );
              }}
            />
          ))}
        </div>
      ) : (
        <Card className="flex min-h-[280px] flex-col items-center justify-center border-dashed px-6 py-12 text-center">
          <Clock3 className="h-10 w-10 text-slate-300 dark:text-slate-600" />
          <h2 className="mt-4 text-base font-semibold">暂无执行记录</h2>
          <p className="mt-1 max-w-sm text-sm leading-6 text-slate-500 dark:text-slate-400">
            在流程编辑器中点击“试运行”，或等待已发布流程的定时任务触发。
          </p>
        </Card>
      )}
    </section>
  );
}

export default ExecutionsPage;
