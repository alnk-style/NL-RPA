import {
  useEffect,
  useState,
} from 'react';
import {
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Download,
  FileText,
  Image,
  LogIn,
  LoaderCircle,
  MinusCircle,
  Pause,
  Play,
  Radio,
  Server,
  ShieldAlert,
  Square,
  TriangleAlert,
  XCircle,
} from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';

import api from '../api/axios';
import {
  cancelExecution,
  getExecutionArtifact,
  getExecutionDetail,
  getExecutionScreenshot,
  pauseExecution,
  resumeExecution,
  resumeExecutionAfterLogin,
} from '../api/executions';
import {
  cancelHumanTask,
  getHumanTask,
  resumeHumanTask,
} from '../api/humanTasks';
import { queryKeys } from '../api/queryKeys';
import { ExecutionMetricsPanel } from '../components/metrics/ExecutionMetricsPanel';
import { ArtifactPanel } from '../components/executions/ArtifactPanel';
import { DeliveryPanel } from '../components/executions/DeliveryPanel';
import { RunTimeline } from '../components/executions/RunTimeline';
import { VariablePanel } from '../components/executions/VariablePanel';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '../components/ui/Card';
import {
  applyExecutionStatusUpdate,
  buildExecutionWebSocketUrl,
  executionVersionLabel,
  formatDuration,
  getExecutionDurationSeconds,
  isTerminalExecutionStatus,
  mergeExecutionStepLogs,
  normalizeExecutionStepLog,
} from '../lib/execution';
import { getApiErrorMessage } from '../lib/errors';
import {
  hostEnvironmentLabel,
  hostStatusLabel,
} from '../lib/hosts';
import {
  humanTaskKindLabel,
  humanTaskStatusLabel,
  humanTaskVerificationMessage,
} from '../lib/human-tasks';
import { redactSensitiveText } from '../lib/display';
import {
  buildVisualCandidateVerification,
  visualCandidatesFromTask,
} from '../lib/visual-candidates';
import {
  riskLabel,
  riskVariant,
} from '../lib/mcp-settings';
import { formatMCPResultPath } from '../lib/mcp-workflow';
import { cn } from '../lib/utils';
import { getActionPresentation } from '../lib/workflow-editor';
import type {
  ExecutionArtifact,
  ExecutionRecord,
  ExecutionStepLog,
  ExecutionStepStatus,
} from '../types/workflow';

type LiveState =
  | 'waiting'
  | 'connecting'
  | 'live'
  | 'reconnecting'
  | 'completed'
  | 'offline';

const TERMINAL_STATUSES = new Set([
  'success',
  'failed',
  'error',
  'completed',
  'cancelled',
]);

const STATUS_LABELS: Record<string, string> = {
  pending: '等待执行',
  running: '执行中',
  warning: '截图失败',
  success: '执行成功',
  completed: '执行完成',
  failed: '执行失败',
  error: '执行错误',
  cancelled: '已取消',
  paused: '已暂停',
  awaiting_user: '等待人工处理',
};

function statusVariant(
  status: string,
): 'success' | 'danger' | 'warning' | 'secondary' {
  if (status === 'success' || status === 'completed') {
    return 'success';
  }
  if (status === 'failed' || status === 'error' || status === 'cancelled') {
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

function stepStatusVariant(
  status: ExecutionStepStatus,
): 'success' | 'danger' | 'warning' | 'secondary' {
  if (status === 'success') {
    return 'success';
  }
  if (status === 'failed') {
    return 'danger';
  }
  if (status === 'warning') {
    return 'warning';
  }
  if (status === 'running') {
    return 'warning';
  }
  return 'secondary';
}

function formatDateTime(value: string | null): string {
  if (!value) {
    return '—';
  }

  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      }).format(date);
}

function StepStatusIcon({ status }: { status: ExecutionStepStatus }) {
  if (status === 'success') {
    return <CheckCircle2 className="h-4 w-4 text-emerald-600" />;
  }
  if (status === 'failed' || status === 'cancelled') {
    return <XCircle className="h-4 w-4 text-red-600" />;
  }
  if (status === 'warning' || status === 'unknown') {
    return <TriangleAlert className="h-4 w-4 text-amber-600" />;
  }
  if (status === 'awaiting_user') {
    return <Clock3 className="h-4 w-4 text-amber-600" />;
  }
  if (status === 'running') {
    return (
      <LoaderCircle className="h-4 w-4 animate-spin text-amber-600" />
    );
  }
  if (status === 'skipped') {
    return <MinusCircle className="h-4 w-4 text-slate-400" />;
  }
  return <Clock3 className="h-4 w-4 text-slate-400" />;
}

function LiveIndicator({
  state,
  error,
}: {
  state: LiveState;
  error: string | null;
}) {
  const labels: Record<LiveState, string> = {
    waiting: '等待连接',
    connecting: '正在连接',
    live: '实时更新',
    reconnecting: '正在重连',
    completed: '执行已结束',
    offline: '实时连接不可用',
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <div
        className={cn(
          'flex items-center gap-1.5 text-xs font-medium',
          state === 'live'
            ? 'text-emerald-600'
            : state === 'offline'
              ? 'text-red-600'
              : 'text-slate-500',
        )}
      >
        {state === 'completed' ? (
          <CheckCircle2 className="h-3.5 w-3.5" />
        ) : state === 'offline' ? (
          <TriangleAlert className="h-3.5 w-3.5" />
        ) : (
          <Radio
            className={cn(
              'h-3.5 w-3.5',
              state === 'live' && 'animate-pulse',
            )}
          />
        )}
        <span>{labels[state]}</span>
      </div>
      {error ? (
        <p className="max-w-[260px] text-right text-[11px] text-red-600">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function ExecutionScreenshot({
  executionId,
  stepIndex,
  hasScreenshot,
  reloadKey,
}: {
  executionId: number;
  stepIndex: number;
  hasScreenshot: boolean;
  reloadKey: string;
}) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;

    if (!hasScreenshot) {
      setImageUrl(null);
      setError('该步骤暂无截图。');
      return () => {
        active = false;
      };
    }

    setIsLoading(true);
    setError(null);

    void getExecutionScreenshot(executionId, stepIndex)
      .then((blob) => {
        if (!active) {
          return;
        }
        objectUrl = URL.createObjectURL(blob);
        setImageUrl(objectUrl);
      })
      .catch((requestError: unknown) => {
        if (active) {
          setError(
            getApiErrorMessage(requestError, '加载步骤截图失败。'),
          );
        }
      })
      .finally(() => {
        if (active) {
          setIsLoading(false);
        }
      });

    return () => {
      active = false;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [executionId, hasScreenshot, reloadKey, stepIndex]);

  if (isLoading) {
    return (
      <div className="flex h-44 items-center justify-center bg-slate-100">
        <LoaderCircle className="h-5 w-5 animate-spin text-slate-500" />
      </div>
    );
  }

  if (error || !imageUrl) {
    return (
      <div className="flex h-28 items-center justify-center gap-2 bg-slate-50 text-xs text-slate-500">
        <Image className="h-4 w-4" />
        {error ?? '暂无截图'}
      </div>
    );
  }

  return (
    <a
      href={imageUrl}
      target="_blank"
      rel="noreferrer"
      className="block bg-slate-100"
      title="在新窗口查看原图"
    >
      <img
        src={imageUrl}
        alt={`步骤 ${stepIndex + 1} 截图`}
        className="max-h-[420px] w-full object-contain"
      />
    </a>
  );
}

function formatFileSize(size: unknown): string | null {
  if (typeof size !== 'number' || !Number.isFinite(size) || size < 0) {
    return null;
  }
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function ExecutionArtifactItem({
  executionId,
  artifact,
  index,
}: {
  executionId: number;
  artifact: ExecutionArtifact;
  index: number;
}) {
  const [isDownloading, setIsDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const name = artifact.name?.trim() || `文件 ${index + 1}`;
  const size = formatFileSize(artifact.size);
  const source = artifact.source;

  async function handleDownload() {
    setIsDownloading(true);
    setDownloadError(null);
    try {
      const blob = await getExecutionArtifact(executionId, index);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = name;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setDownloadError(getApiErrorMessage(error, '下载文件失败。'));
    } finally {
      setIsDownloading(false);
    }
  }

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2 text-sm font-medium text-slate-800 dark:text-slate-100">
          <FileText className="h-4 w-4 shrink-0 text-slate-400" />
          <span className="break-all">{name}</span>
          {size ? (
            <span className="text-xs font-normal text-slate-400">
              {size}
            </span>
          ) : null}
        </p>
        <p className="mt-1 break-all text-xs text-slate-500 dark:text-slate-400">
          {artifact.delivered && artifact.delivered_path
            ? `已保存到 ${artifact.delivered_path}`
            : artifact.error ??
              '文件保存在执行记录中，可直接下载。'}
        </p>
        {source?.server || source?.tool ? (
          <p className="mt-1 break-all text-[11px] text-slate-400">
            来源：{source?.server || '应用'}
            {source?.tool ? ` · ${source.tool}` : ''}
            {artifact.media_type ? ` · ${artifact.media_type}` : ''}
          </p>
        ) : null}
        <p className="mt-1 text-[11px] text-slate-400">
          {artifact.delivered ? '已投递' : '保留在执行记录'}
        </p>
        {downloadError ? (
          <p className="mt-1 text-xs text-red-600">{downloadError}</p>
        ) : null}
      </div>
      <Button
        variant="secondary"
        size="sm"
        leftIcon={
          isDownloading ? (
            <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )
        }
        disabled={isDownloading}
        onClick={() => {
          void handleDownload();
        }}
      >
        下载
      </Button>
    </li>
  );
}

function mcpFailureHint(error: string | null): string | null {
  if (!error) {
    return null;
  }
  const value = error.toLowerCase();
  if (value.includes('result_path') || value.includes('结果路径')) {
    return '结果路径不存在或无法从工具结果中提取。';
  }
  if (value.includes('not authorized') || value.includes('permission')) {
    return '工具未授权，请先在应用详情中完成授权。';
  }
  if (value.includes('invalid argument') || value.includes('参数')) {
    return '工具参数不合法，请检查必填项和参数类型。';
  }
  if (value.includes('timeout') || value.includes('超时')) {
    return '应用执行超时，请检查应用状态或增加超时时间。';
  }
  if (
    value.includes('not connected') ||
    value.includes('unavailable') ||
    value.includes('未连接')
  ) {
    return '应用当前未连接，请先在“应用”页面测试连接。';
  }
  return null;
}

function summaryText(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }
  const text = typeof value === 'string'
    ? value
    : JSON.stringify(value);
  return text.length > 600 ? `${text.slice(0, 600)}...` : text;
}

function MCPExecutionDetails({ step }: { step: ExecutionStepLog }) {
  const hint = mcpFailureHint(step.error);
  const argumentsText = summaryText(step.arguments_summary);
  const resultText = summaryText(step.result_summary);

  return (
    <div className="space-y-3 border-t border-slate-100 p-4 dark:border-slate-800">
      <div className="grid gap-px overflow-hidden rounded-md border border-slate-200 bg-slate-200 text-xs sm:grid-cols-3 dark:border-slate-800 dark:bg-slate-800">
        {[
          ['风险级别', riskLabel(step.risk_level ?? '')],
          [
            '授权决策',
            step.decision === 'denied'
              ? '已拒绝'
              : step.decision === 'allowed'
                ? '已允许'
                : '—',
          ],
          [
            '耗时',
            typeof step.duration_ms === 'number'
              ? `${Math.round(step.duration_ms)} ms`
              : '—',
          ],
          ['尝试次数', String(step.attempts ?? '—')],
          ['审计 ID', step.audit_id ? `#${step.audit_id}` : '—'],
          ['产物数量', String(step.artifact_count ?? 0)],
        ].map(([label, value]) => (
          <div
            key={label}
            className="bg-white px-3 py-2.5 dark:bg-slate-900"
          >
            <p className="text-[11px] text-slate-400">{label}</p>
            <p className="mt-1 break-all text-slate-700 dark:text-slate-200">
              {value}
            </p>
          </div>
        ))}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
          <p className="text-[11px] font-medium text-slate-500">结果路径</p>
          <p className="mt-1 break-all font-mono text-xs text-slate-700 dark:text-slate-200">
            {formatMCPResultPath(step.result_path)}
          </p>
        </div>
        <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
          <p className="text-[11px] font-medium text-slate-500">授权来源</p>
          <p className="mt-1 break-all text-xs text-slate-700 dark:text-slate-200">
            {step.decision === 'denied'
              ? '权限门拒绝'
              : '已通过平台权限门'}
          </p>
        </div>
      </div>

      {argumentsText ? (
        <div>
          <p className="text-[11px] font-medium text-slate-500">参数摘要</p>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-slate-50 p-3 font-mono text-[11px] leading-5 text-slate-700 dark:bg-slate-950/50 dark:text-slate-300">
            {argumentsText}
          </pre>
        </div>
      ) : null}

      {resultText ? (
        <div>
          <p className="text-[11px] font-medium text-slate-500">结果摘要</p>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-slate-50 p-3 font-mono text-[11px] leading-5 text-slate-700 dark:bg-slate-950/50 dark:text-slate-300">
            {resultText}
          </pre>
        </div>
      ) : null}

      {hint ? (
        <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
          {hint}
        </p>
      ) : step.error ? (
        <p className="break-all rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
          {redactSensitiveText(step.error)}
        </p>
      ) : null}
    </div>
  );
}

export function ExecutionDetail() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { id } = useParams();
  const executionId = Number(id);
  const [steps, setSteps] = useState<ExecutionStepLog[]>([]);
  const [expandedSteps, setExpandedSteps] = useState<Set<number>>(
    () => new Set(),
  );
  const [liveState, setLiveState] = useState<LiveState>('waiting');
  const [liveError, setLiveError] = useState<string | null>(null);
  const [isControlBusy, setIsControlBusy] = useState(false);
  const [controlError, setControlError] = useState<string | null>(
    null,
  );
  const [selectedVisualCandidateId, setSelectedVisualCandidateId] =
    useState<string | null>(null);
  const [now, setNow] = useState(Date.now());

  const executionQuery = useQuery({
    queryKey: ['executions', executionId],
    queryFn: () => getExecutionDetail(executionId),
    enabled: Number.isFinite(executionId),
    refetchInterval: (query) => {
      const data = query.state.data as ExecutionRecord | undefined;
      return data && !isTerminalExecutionStatus(data.status)
        ? 2500
        : false;
    },
  });
  const execution = executionQuery.data ?? null;
  const humanTaskId = execution?.human_task_id ?? null;
  const humanTaskQuery = useQuery({
    queryKey: queryKeys.humanTask(humanTaskId ?? 0),
    queryFn: () => getHumanTask(humanTaskId as number),
    enabled: humanTaskId !== null,
  });
  const humanTask = humanTaskQuery.data ?? null;
  const isLoading = executionQuery.isLoading;
  const error = executionQuery.error
    ? getApiErrorMessage(executionQuery.error, '加载执行详情失败。')
    : !Number.isFinite(executionId)
      ? '执行记录编号无效。'
      : null;

  async function refreshExecution() {
    await queryClient.invalidateQueries({
      queryKey: ['executions', executionId],
    });
    if (humanTaskId !== null) {
      await queryClient.invalidateQueries({
        queryKey: queryKeys.humanTask(humanTaskId),
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.humanTasks,
      });
    }
  }

  async function handleExecutionControl(
    action: 'pause' | 'resume' | 'resume-after-login',
  ) {
    setIsControlBusy(true);
    setControlError(null);
    try {
      if (action === 'pause') {
        await pauseExecution(executionId);
      } else if (action === 'resume') {
        await resumeExecution(executionId);
      } else {
        await resumeExecutionAfterLogin(executionId);
      }
      await refreshExecution();
    } catch (requestError) {
      setControlError(
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
      setIsControlBusy(false);
    }
  }

  async function handleHumanTaskAction(
    action: 'resume' | 'cancel',
  ) {
    if (!humanTask) {
      return;
    }
    setIsControlBusy(true);
    setControlError(null);
    try {
      if (action === 'resume') {
        await resumeHumanTask(
          humanTask.id,
          humanTask.kind === 'visual_ambiguous'
            ? buildVisualCandidateVerification(
                selectedVisualCandidateId ?? '',
              )
            : humanTask.kind === 'manual_confirmation'
              ? { manual_confirmed: true }
              : {},
        );
      } else {
        await cancelHumanTask(
          humanTask.id,
          '用户从执行详情取消了人工任务。',
        );
      }
      await refreshExecution();
    } catch (requestError) {
      setControlError(
        action === 'resume'
          ? humanTaskVerificationMessage(
              requestError,
              '服务端重新验证失败，执行仍保持等待处理。',
            )
          : getApiErrorMessage(requestError, '取消人工任务失败。'),
      );
    } finally {
      setIsControlBusy(false);
    }
  }

  async function handleCancelExecution() {
    setIsControlBusy(true);
    setControlError(null);
    try {
      await cancelExecution(
        executionId,
        '用户从执行详情取消了执行。',
      );
      await refreshExecution();
    } catch (requestError) {
      setControlError(
        getApiErrorMessage(requestError, '取消执行失败。'),
      );
    } finally {
      setIsControlBusy(false);
    }
  }

  useEffect(() => {
    if (!execution) {
      setSteps([]);
      return;
    }
    setSteps(
      (execution.step_logs ?? []).map((stepLog, index) => (
        normalizeExecutionStepLog(
          stepLog,
          index,
          Boolean(execution.screenshots?.[index]),
        )
      )),
    );
  }, [execution, executionId]);

  useEffect(() => {
    setSelectedVisualCandidateId(null);
  }, [humanTask?.id]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!executionQuery.isSuccess) {
      return;
    }

    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;
    let reconnectAttempts = 0;
    let terminalMessageReceived = false;

    const baseUrl =
      api.defaults.baseURL ?? window.location.origin;
    const socketUrl = buildExecutionWebSocketUrl(
      executionId,
      baseUrl,
    );

    function connect() {
      if (disposed) {
        return;
      }

      setLiveState(reconnectAttempts > 0 ? 'reconnecting' : 'connecting');
      setLiveError(null);

      try {
        socket = new WebSocket(socketUrl);
      } catch {
        setLiveState('offline');
        setLiveError('无法创建实时连接。');
        return;
      }

      socket.onopen = () => {
        if (disposed) {
          return;
        }
        reconnectAttempts = 0;
        setLiveState('live');
      };

      socket.onmessage = (event) => {
        let payload: Record<string, unknown>;
        try {
          payload = JSON.parse(String(event.data)) as Record<
            string,
            unknown
          >;
        } catch {
          return;
        }

        const messageType = String(payload.type ?? '');
        if (
          messageType.startsWith('execution.')
          || messageType.startsWith('human_task.')
        ) {
          const eventPayload = (
            payload.payload
            && typeof payload.payload === 'object'
            ? payload.payload as Record<string, unknown>
            : {}
          );
          if (messageType === 'execution.resume_failed') {
            setControlError(
              String(
                eventPayload.reason
                  ?? '服务端重新验证失败，执行未恢复。',
              ),
            );
          }
          if (messageType === 'execution.resumed') {
            setControlError(null);
          }
          void queryClient.invalidateQueries({
            queryKey: ['executions', executionId],
          });
          if (humanTaskId !== null) {
            void queryClient.invalidateQueries({
              queryKey: queryKeys.humanTask(humanTaskId),
            });
          }
          void queryClient.invalidateQueries({
            queryKey: queryKeys.humanTasks,
          });
          return;
        }
        if (messageType === 'status') {
          const nextStatus = String(payload.status ?? '');
          if (nextStatus) {
            if (TERMINAL_STATUSES.has(nextStatus)) {
              terminalMessageReceived = true;
              void refreshExecution();
            } else {
              void refreshExecution();
            }
          }
          return;
        }

        if (messageType === 'step') {
          const rawStep = payload.step_log;
          const rawIndex = (
            rawStep &&
            typeof rawStep === 'object' &&
            'step_index' in rawStep
          )
            ? Number(
                (rawStep as Record<string, unknown>).step_index,
              )
            : 0;
          const incoming = normalizeExecutionStepLog(
            rawStep,
            Number.isFinite(rawIndex) ? rawIndex : 0,
            payload.screenshot_path !== null &&
              payload.screenshot_path !== undefined,
          );
          setSteps((current) => (
            mergeExecutionStepLogs(current, incoming)
          ));
          void refreshExecution();
          return;
        }

        if (messageType === 'completed') {
          terminalMessageReceived = true;
          setLiveState('completed');
          void refreshExecution();
          return;
        }

        if (messageType === 'error') {
          terminalMessageReceived = true;
          setLiveState('offline');
          setLiveError(
            String(payload.detail ?? '实时通道返回错误。'),
          );
        }
      };

      socket.onerror = () => {
        if (!disposed && !terminalMessageReceived) {
          setLiveError('实时连接发生错误。');
        }
      };

      socket.onclose = () => {
        socket = null;
        if (disposed || terminalMessageReceived) {
          return;
        }
        if (reconnectAttempts >= 5) {
          setLiveState('offline');
          setLiveError('实时连接已断开，稍后刷新页面重试。');
          return;
        }

        reconnectAttempts += 1;
        setLiveState('reconnecting');
        reconnectTimer = window.setTimeout(
          connect,
          Math.min(1000 * 2 ** (reconnectAttempts - 1), 8000),
        );
      };
    }

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      socket?.close();
    };
  }, [
    executionId,
    executionQuery.isSuccess,
    humanTaskId,
    queryClient,
  ]);

  if (isLoading) {
    return (
      <div className="h-[420px] animate-pulse rounded-lg bg-slate-100" />
    );
  }

  if (!execution || error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-5 text-sm text-red-700">
        <p>{error ?? '未找到执行记录。'}</p>
        <Button
          variant="ghost"
          size="sm"
          className="mt-3"
          leftIcon={<ArrowLeft className="h-4 w-4" />}
          onClick={() => navigate('/executions')}
        >
          返回执行监控
        </Button>
      </div>
    );
  }

  const terminalExecution = isTerminalExecutionStatus(execution.status);
  const durationSeconds = getExecutionDurationSeconds(
    execution.started_at,
    execution.finished_at ?? (
      terminalExecution ? execution.started_at : null
    ),
    terminalExecution ? undefined : now,
  );
  const stepCount = steps.length || execution.step_logs?.length || 0;
  const artifacts = execution.artifacts ?? [];
  const currentVisualCandidates = humanTask
    ? visualCandidatesFromTask(humanTask)
    : [];
  const screenshotReloadKey = [
    execution.status,
    execution.finished_at ?? '',
    execution.screenshots?.length ?? 0,
  ].join(':');

  return (
    <section className="space-y-5">
      <div>
        <Button
          variant="ghost"
          size="sm"
          className="-ml-3 mb-2"
          leftIcon={<ArrowLeft className="h-4 w-4" />}
          onClick={() => navigate('/executions')}
        >
          返回执行监控
        </Button>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">
              执行详情 #{execution.id}
            </h1>
            <Badge variant={statusVariant(execution.status)}>
              {STATUS_LABELS[execution.status] ?? execution.status}
            </Badge>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-3">
            <span className="font-mono text-xs text-slate-500">
              {executionVersionLabel(execution)}
            </span>
            {execution.status === 'running' ||
            execution.status === 'pending' ? (
              <Button
                variant="secondary"
                size="sm"
                leftIcon={
                  isControlBusy
                    ? <LoaderCircle className="h-4 w-4 animate-spin" />
                    : <Pause className="h-4 w-4" />
                }
                disabled={isControlBusy}
                onClick={() => {
                  void handleExecutionControl('pause');
                }}
              >
                暂停
              </Button>
            ) : null}
            {execution.status === 'paused' ? (
              <Button
                size="sm"
                leftIcon={
                  isControlBusy
                    ? <LoaderCircle className="h-4 w-4 animate-spin" />
                    : <Play className="h-4 w-4" />
                }
                disabled={isControlBusy}
                onClick={() => {
                  void handleExecutionControl('resume');
                }}
              >
                重新运行
              </Button>
            ) : null}
            {execution.status === 'awaiting_user' && humanTask ? (
              <Button
                size="sm"
                leftIcon={<ArrowRight className="h-4 w-4" />}
                disabled={isControlBusy}
                onClick={() => {
                  navigate(`/pending?task=${humanTask.id}`);
                }}
              >
                前往待处理
              </Button>
            ) : execution.status === 'awaiting_user' ? (
              <Button
                size="sm"
                leftIcon={
                  isControlBusy
                    ? <LoaderCircle className="h-4 w-4 animate-spin" />
                    : <LogIn className="h-4 w-4" />
                }
                disabled={isControlBusy}
                onClick={() => {
                  void handleExecutionControl('resume-after-login');
                }}
              >
                旧版登录后继续
              </Button>
            ) : null}
            {execution.status === 'awaiting_user' ? (
              <Button
                variant="danger"
                size="sm"
                leftIcon={
                  isControlBusy
                    ? <LoaderCircle className="h-4 w-4 animate-spin" />
                    : <Square className="h-4 w-4" />
                }
                disabled={isControlBusy}
                onClick={() => {
                  if (humanTask) {
                    void handleHumanTaskAction('cancel');
                  } else {
                    void handleCancelExecution();
                  }
                }}
                title="取消并终止执行"
              >
                取消执行
              </Button>
            ) : null}
            <LiveIndicator state={liveState} error={liveError} />
          </div>
        </div>
      </div>

      {controlError ? (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
          {controlError}
        </div>
      ) : null}

      <div className="rounded-md border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
          <Server className="h-4 w-4 text-slate-400" />
          <span className="font-medium text-slate-800 dark:text-slate-100">
            执行主机
          </span>
          {execution.host_id ? (
            <>
              <Badge variant="outline">
                {hostEnvironmentLabel(execution.host_environment)}
              </Badge>
              <span className="break-words">
                {execution.host_name || `Agent #${execution.host_id}`}
              </span>
              <Badge
                variant={
                  execution.host_status === 'online'
                    ? 'success'
                    : execution.host_status === 'busy'
                      ? 'warning'
                      : 'secondary'
                }
              >
                {hostStatusLabel(execution.host_status)}
              </Badge>
              <span>
                {execution.host_bound
                  ? 'host-bound 固定'
                  : '未标记固定'}
              </span>
              {execution.host_affinity_configured ? (
                <span>会话亲和已记录</span>
              ) : null}
            </>
          ) : (
            <span>
              服务器侧执行或历史记录未记录主机；普通 Web/Data 步骤不受主机策略影响。
            </span>
          )}
        </div>
        {execution.host_reason ? (
          <p className="mt-2 break-words text-xs text-slate-500">
            路由原因：{execution.host_reason}
          </p>
        ) : null}
        {execution.host_capabilities?.length ? (
          <p className="mt-1 break-words text-xs text-slate-500">
            能力：{execution.host_capabilities.join('、')}
          </p>
        ) : null}
      </div>

      {execution.status === 'awaiting_user' && humanTask ? (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 dark:border-amber-900/60 dark:bg-amber-950/30">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <ShieldAlert className="h-4 w-4 text-amber-700" />
                <p className="text-sm font-semibold text-amber-900 dark:text-amber-100">
                  当前人工任务：{humanTaskKindLabel(humanTask.kind)}
                </p>
                <Badge variant="warning">
                  {humanTaskStatusLabel(humanTask.status)}
                </Badge>
              </div>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-amber-900 dark:text-amber-100">
                {redactSensitiveText(humanTask.instruction)}
              </p>
              {humanTask.current_url ? (
                <p className="mt-2 break-all font-mono text-xs text-amber-800 dark:text-amber-200">
                  {redactSensitiveText(humanTask.current_url)}
                </p>
              ) : null}
              {humanTask.last_verification_error ? (
                <p className="mt-2 text-xs text-red-700 dark:text-red-300">
                  {redactSensitiveText(
                    humanTask.last_verification_error,
                  )}
                </p>
              ) : null}
              {humanTask.kind === 'visual_ambiguous' ? (
                <div className="mt-3 space-y-2">
                  {currentVisualCandidates.length ? (
                    currentVisualCandidates.map((candidate, index) => (
                      <button
                        key={candidate.candidate_id}
                        type="button"
                        className={
                          selectedVisualCandidateId
                            === candidate.candidate_id
                            ? 'flex w-full items-start gap-2 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-left text-xs dark:border-emerald-800 dark:bg-emerald-950/30'
                            : 'flex w-full items-start gap-2 rounded-md border border-amber-200 bg-white/60 px-3 py-2 text-left text-xs hover:bg-white dark:border-amber-900/60 dark:bg-slate-950/20 dark:hover:bg-slate-900/40'
                        }
                        onClick={() => {
                          setSelectedVisualCandidateId(
                            candidate.candidate_id,
                          );
                        }}
                      >
                        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-900 text-[10px] font-semibold text-white dark:bg-slate-100 dark:text-slate-950">
                          {index + 1}
                        </span>
                        <span className="min-w-0">
                          <span className="block font-medium">
                            {candidate.target}
                            {` · ${Math.round(candidate.confidence * 100)}%`}
                          </span>
                          {candidate.evidence ? (
                            <span className="mt-0.5 block text-slate-500 dark:text-slate-400">
                              {candidate.evidence}
                            </span>
                          ) : null}
                        </span>
                      </button>
                    ))
                  ) : (
                    <p className="rounded-md border border-dashed border-amber-300 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/60 dark:text-amber-200">
                      视觉任务缺少候选数据，可前往待处理中心查看兼容详情。
                    </p>
                  )}
                </div>
              ) : null}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="secondary"
                size="sm"
                leftIcon={<ArrowRight className="h-4 w-4" />}
                onClick={() => {
                  navigate(`/pending?task=${humanTask.id}`);
                }}
              >
                打开待处理中心
              </Button>
              <Button
                size="sm"
                leftIcon={
                  isControlBusy
                    ? <LoaderCircle className="h-4 w-4 animate-spin" />
                    : <LogIn className="h-4 w-4" />
                }
                disabled={
                  isControlBusy
                  || humanTask.status !== 'pending'
                  || humanTask.payload?.execution_allowed === false
                  || (
                    humanTask.kind === 'visual_ambiguous'
                    && !selectedVisualCandidateId
                  )
                }
                onClick={() => {
                  void handleHumanTaskAction('resume');
                }}
              >
                {isControlBusy
                  ? '正在重新验证'
                  : humanTask.kind === 'visual_ambiguous'
                    ? '确认候选并继续'
                    : '重新验证并继续'}
              </Button>
              <Button
                variant="danger"
                size="sm"
                leftIcon={<Square className="h-4 w-4" />}
                disabled={isControlBusy}
                onClick={() => {
                  void handleHumanTaskAction('cancel');
                }}
              >
                取消
              </Button>
            </div>
          </div>
        </div>
      ) : execution.status === 'awaiting_user' ? (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 dark:border-amber-900/60 dark:bg-amber-950/30">
          <p className="text-sm font-semibold text-amber-900 dark:text-amber-100">
            旧版等待记录
          </p>
          <p className="mt-1 text-sm leading-6 text-amber-800 dark:text-amber-200">
            该执行来自旧版 awaiting_user 流程，尚无 HumanTask。
            可继续使用兼容入口，或取消执行后重新发起。
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              size="sm"
              leftIcon={<LogIn className="h-4 w-4" />}
              disabled={isControlBusy}
              onClick={() => {
                void handleExecutionControl('resume-after-login');
              }}
            >
              旧版登录后继续
            </Button>
            <Button
              variant="danger"
              size="sm"
              leftIcon={<Square className="h-4 w-4" />}
              disabled={isControlBusy}
              onClick={() => {
                void handleCancelExecution();
              }}
            >
              取消执行
            </Button>
          </div>
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-500">
              执行状态
            </CardTitle>
          </CardHeader>
          <CardContent className="flex items-center gap-2 text-sm">
            <StepStatusIcon
              status={
                execution.status === 'running'
                  ? 'running'
                  : execution.status === 'paused'
                    ? 'pending'
                  : execution.status === 'awaiting_user'
                    ? 'warning'
                  : execution.status === 'success' ||
                      execution.status === 'completed'
                    ? 'success'
                    : execution.status === 'pending'
                      ? 'pending'
                      : 'failed'
              }
            />
            {STATUS_LABELS[execution.status] ?? execution.status}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-500">
              开始时间
            </CardTitle>
          </CardHeader>
          <CardContent className="flex items-center gap-2 text-sm">
            <Clock3 className="h-4 w-4 text-slate-400" />
            {formatDateTime(execution.started_at)}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-500">
              执行耗时
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm font-semibold">
            {formatDuration(durationSeconds)}
          </CardContent>
        </Card>
      </div>

      <ExecutionMetricsPanel
        stepLogs={execution.step_logs}
        durationLabel={formatDuration(durationSeconds)}
      />
      <VariablePanel variables={execution.variables ?? {}} />
      <DeliveryPanel deliveries={execution.deliveries ?? []} />

      {execution.error_message &&
      execution.status !== 'paused' ? (
        <Card className="border-red-200">
          <CardHeader>
            <CardTitle className="text-red-700">错误信息</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="whitespace-pre-wrap text-sm leading-6 text-red-700">
              {execution.error_message}
            </p>
          </CardContent>
        </Card>
      ) : null}

      <ArtifactPanel
        executionId={execution.id}
        artifacts={artifacts}
      />

      <Card>
        <CardHeader className="border-b border-slate-100">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle>执行时间线</CardTitle>
              <p className="mt-1 text-xs text-slate-500">
                {stepCount} 个步骤
              </p>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {steps.length === 0 ? (
            <div className="flex min-h-40 items-center justify-center text-sm text-slate-500">
              暂无步骤日志
            </div>
          ) : (
            <RunTimeline steps={steps}>
              {steps.map((step, index) => {
                const expanded = expandedSteps.has(step.index);
                const presentation = getActionPresentation(step.action);
                const isMCP =
                  step.executor === 'mcp' || step.action === 'call_tool';
                const target = isMCP
                  ? `${step.server || '未指定应用'} · ${step.tool || step.target || '未指定工具'}`
                  : step.target || step.selector || '未指定目标';

                return (
                  <li
                    key={`${step.index}-${index}`}
                    className="relative pb-3 pl-10 last:pb-0"
                  >
                    {index < steps.length - 1 ? (
                      <span className="absolute bottom-0 left-[15px] top-8 border-l border-slate-200" />
                    ) : null}
                    <span className="absolute left-0 top-1 flex h-8 w-8 items-center justify-center rounded-full border border-slate-200 bg-white">
                      <StepStatusIcon status={step.status} />
                    </span>

                    <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
                      <button
                        type="button"
                        className="flex w-full items-start justify-between gap-4 px-4 py-3 text-left hover:bg-slate-50"
                        onClick={() => {
                          setExpandedSteps((current) => {
                            const next = new Set(current);
                            if (next.has(step.index)) {
                              next.delete(step.index);
                            } else {
                              next.add(step.index);
                            }
                            return next;
                          });
                        }}
                        aria-expanded={expanded}
                      >
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-xs font-semibold text-slate-500">
                              步骤 {index + 1}
                            </span>
                            <Badge
                              variant={stepStatusVariant(step.status)}
                              className="h-5 px-2"
                            >
                              {STATUS_LABELS[step.status] ?? step.status}
                            </Badge>
                            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-600">
                              {isMCP ? 'MCP' : step.action}
                            </span>
                            {isMCP && step.risk_level ? (
                              <Badge variant={riskVariant(step.risk_level)}>
                                {riskLabel(step.risk_level)}
                              </Badge>
                            ) : null}
                          </div>
                          <h3 className="mt-2 text-sm font-semibold text-slate-900">
                            {isMCP
                              ? `MCP · ${step.server || '应用'}`
                              : presentation.label}
                          </h3>
                          <p className="mt-1 break-all text-xs leading-5 text-slate-500">
                            {isMCP ? step.tool || target : target}
                          </p>
                          {step.error ? (
                            <p className="mt-2 break-all text-xs leading-5 text-red-600">
          {redactSensitiveText(step.error)}
                            </p>
                          ) : null}
                          {step.screenshotError ? (
                            <p className="mt-2 break-all text-xs leading-5 text-amber-600">
                              {step.screenshotError}
                            </p>
                          ) : null}
                        </div>

                        <div className="flex shrink-0 items-center gap-2 pt-0.5 text-xs text-slate-500">
                          <span>{formatDuration(step.durationSeconds)}</span>
                          <ChevronDown
                            className={cn(
                              'h-4 w-4 transition-transform',
                              expanded && 'rotate-180',
                            )}
                          />
                        </div>
                      </button>

                      {expanded ? (
                        isMCP ? (
                          <>
                            <MCPExecutionDetails step={step} />
                            {step.hasScreenshot ? (
                              <div className="border-t border-slate-100 dark:border-slate-800">
                                <ExecutionScreenshot
                                  executionId={execution.id}
                                  stepIndex={step.index}
                                  hasScreenshot={step.hasScreenshot}
                                  reloadKey={screenshotReloadKey}
                                />
                              </div>
                            ) : null}
                          </>
                        ) : (
                          <div className="border-t border-slate-100">
                            <ExecutionScreenshot
                              executionId={execution.id}
                              stepIndex={step.index}
                              hasScreenshot={step.hasScreenshot}
                              reloadKey={screenshotReloadKey}
                            />
                          </div>
                        )
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </RunTimeline>
          )}
        </CardContent>
      </Card>
    </section>
  );
}

export default ExecutionDetail;
