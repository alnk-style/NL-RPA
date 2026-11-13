import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ArrowUpRight,
  ExternalLink,
  Globe2,
  LoaderCircle,
  LogIn,
  ShieldAlert,
  Square,
} from 'lucide-react';

import { getHumanTaskScreenshot } from '../../api/humanTasks';
import { listHosts } from '../../api/hosts';
import { queryKeys } from '../../api/queryKeys';
import {
  canResumeHumanTask,
  humanTaskKindLabel,
  humanTaskStatusLabel,
  humanTaskStatusVariant,
  isHumanTaskExpired,
} from '../../lib/human-tasks';
import { redactSensitiveText } from '../../lib/display';
import { getApiErrorMessage } from '../../lib/errors';
import { cn } from '../../lib/utils';
import {
  candidateOverlayStyle,
  visualCandidatesFromTask,
  type VisualCandidateView,
} from '../../lib/visual-candidates';
import type { HumanTaskRecord } from '../../types/humanTasks';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { VmHumanTaskDetails } from './VmHumanTaskDetails';

function TaskScreenshot({
  taskId,
  task,
  selectedCandidateId,
  onSelectCandidate,
}: {
  taskId: number;
  task: HumanTaskRecord;
  selectedCandidateId: string | null;
  onSelectCandidate: (candidateId: string) => void;
}) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const candidates = visualCandidatesFromTask(task);
  const payload = task.payload ?? {};
  const imageWidth = Number(payload.image_width);
  const imageHeight = Number(payload.image_height);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setImageUrl(null);
    setError(null);
    setLoading(true);
    void getHumanTaskScreenshot(taskId)
      .then((blob) => {
        if (!active) {
          return;
        }
        objectUrl = URL.createObjectURL(blob);
        setImageUrl(objectUrl);
      })
      .catch((requestError: unknown) => {
        if (active) {
          const status = (
            requestError as {
              response?: { status?: number };
            }
          )?.response?.status;
          setError(
            status === 404
              ? '该任务没有可用截图。'
              : getApiErrorMessage(
                  requestError,
                  '暂无可查看的任务截图。',
                ),
          );
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [taskId]);

  if (loading) {
    return (
      <div className="flex h-52 items-center justify-center rounded-md border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-950/40">
        <LoaderCircle className="h-5 w-5 animate-spin text-slate-400" />
      </div>
    );
  }
  if (!imageUrl) {
    return (
      <div className="flex h-32 items-center justify-center rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 text-center text-xs text-slate-500 dark:border-slate-700 dark:bg-slate-950/40">
        {error ?? '该任务没有可用截图。'}
      </div>
    );
  }
  return (
    <div className="overflow-hidden rounded-md border border-slate-200 bg-slate-950 dark:border-slate-800">
      <div className="relative mx-auto max-h-72 max-w-full">
        <a
          href={imageUrl}
          target="_blank"
          rel="noreferrer"
          title="打开任务截图"
        >
          <img
            src={imageUrl}
            alt="人工处理任务截图"
            className="max-h-72 w-full object-contain"
          />
        </a>
        {Number.isFinite(imageWidth) && Number.isFinite(imageHeight) ? (
          <div className="pointer-events-none absolute inset-0">
            {candidates.map((candidate, index) => {
              const selected =
                selectedCandidateId === candidate.candidate_id;
              return (
                <button
                  key={candidate.candidate_id}
                  type="button"
                  className={cn(
                    'pointer-events-auto absolute border-2 text-[10px] font-semibold text-white',
                    selected
                      ? 'border-emerald-400 bg-emerald-500/20'
                      : 'border-amber-400 bg-amber-500/10',
                  )}
                  style={candidateOverlayStyle(
                    candidate.bbox,
                    imageWidth,
                    imageHeight,
                  )}
                  onClick={() => onSelectCandidate(candidate.candidate_id)}
                  title={`候选 ${index + 1}: ${candidate.target}`}
                >
                  <span className="absolute -left-px -top-5 rounded-sm bg-slate-950 px-1.5 py-0.5">
                    {index + 1}
                  </span>
                </button>
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function VisualCandidateList({
  candidates,
  selectedCandidateId,
  onSelectCandidate,
}: {
  candidates: VisualCandidateView[];
  selectedCandidateId: string | null;
  onSelectCandidate: (candidateId: string) => void;
}) {
  if (!candidates.length) {
    return (
      <div className="rounded-md border border-dashed border-slate-300 bg-slate-50 px-3 py-5 text-center text-xs text-slate-500 dark:border-slate-700 dark:bg-slate-950/40">
        视觉提供方没有返回可用候选。请取消任务或重新运行结构化定位。
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {candidates.map((candidate, index) => {
        const selected =
          selectedCandidateId === candidate.candidate_id;
        return (
          <button
            key={candidate.candidate_id}
            type="button"
            className={cn(
              'flex w-full items-start gap-3 rounded-md border px-3 py-2 text-left transition-colors',
              selected
                ? 'border-emerald-300 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/30'
                : 'border-slate-200 bg-white hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800',
            )}
            onClick={() => onSelectCandidate(candidate.candidate_id)}
          >
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-900 text-xs font-semibold text-white dark:bg-slate-100 dark:text-slate-950">
              {index + 1}
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">
                  {candidate.target}
                </span>
                <Badge variant={selected ? 'success' : 'secondary'}>
                  {Math.round(candidate.confidence * 100)}%
                </Badge>
              </span>
              {candidate.evidence ? (
                <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">
                  {candidate.evidence}
                </span>
              ) : null}
              {candidate.expected_after ? (
                <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">
                  预期：{candidate.expected_after}
                </span>
              ) : null}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export function HumanTaskPanel({
  task,
  verifying,
  openingLogin,
  actionError,
  onOpenLogin,
  onResume,
  onCancel,
  onOpenExecution,
}: {
  task: HumanTaskRecord;
  verifying: boolean;
  openingLogin: boolean;
  actionError: string | null;
  onOpenLogin: () => void;
  onResume: (candidateId?: string) => void;
  onCancel: () => void;
  onOpenExecution: () => void;
}) {
  const expired = isHumanTaskExpired(task);
  const visualCandidates = visualCandidatesFromTask(task);
  const visualTask = task.kind === 'visual_ambiguous';
  const [selectedCandidateId, setSelectedCandidateId] = useState<
    string | null
  >(null);
  const hostIdValue = task.host_id ?? task.payload?.host_id;
  const hostId = Number.isInteger(Number(hostIdValue))
    && Number(hostIdValue) > 0
    ? Number(hostIdValue)
    : null;
  const hostsQuery = useQuery({
    queryKey: queryKeys.hosts,
    queryFn: listHosts,
    enabled: hostId !== null,
    refetchInterval: 5000,
  });
  const host = hostId === null
    ? null
    : (hostsQuery.data ?? []).find((item) => item.host_id === hostId)
      ?? null;
  useEffect(() => {
    setSelectedCandidateId(null);
  }, [task.id]);
  const executionAllowed = task.payload?.execution_allowed !== false;
  const resumeAllowed = canResumeHumanTask(task, { verifying })
    && executionAllowed
    && (
      hostId === null
      || (!hostsQuery.isLoading && host?.status === 'online')
    )
    && (!visualTask || Boolean(selectedCandidateId));
  const profileId = task.browser_profile_id;
  const windowTitle = String(task.payload?.window_title ?? '').trim();
  const processName = String(task.payload?.process_name ?? '').trim();

  return (
    <section className="space-y-4 rounded-lg border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold">
              {humanTaskKindLabel(task.kind)}
            </h2>
            <Badge variant={humanTaskStatusVariant(task.status)}>
              {humanTaskStatusLabel(task.status)}
            </Badge>
          </div>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            {task.workflow_name || `工作流 #${task.execution_id}`}
            {` · 执行 #${task.execution_id}`}
            {task.workflow_version ? ` · v${task.workflow_version}` : ''}
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<ArrowUpRight className="h-4 w-4" />}
          onClick={onOpenExecution}
          title="打开执行详情"
        >
          执行详情
        </Button>
      </header>

      <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100">
        {redactSensitiveText(task.instruction)}
      </div>

      {task.current_url ? (
        <div className="flex items-start gap-2 text-xs text-slate-600 dark:text-slate-300">
          <Globe2 className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
          <span className="min-w-0 break-all">
            {redactSensitiveText(task.current_url)}
          </span>
        </div>
      ) : null}
      {windowTitle || processName ? (
        <div className="rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:bg-slate-950/40 dark:text-slate-300">
          <p>目标窗口：{windowTitle || '未记录标题'}</p>
          {processName ? <p className="mt-1">进程：{processName}</p> : null}
        </div>
      ) : null}
      <VmHumanTaskDetails
        host={host}
        loading={hostId !== null && hostsQuery.isLoading}
      />

      <TaskScreenshot
        taskId={task.id}
        task={task}
        selectedCandidateId={selectedCandidateId}
        onSelectCandidate={setSelectedCandidateId}
      />
      {visualTask ? (
        <div className="space-y-3">
          <VisualCandidateList
            candidates={visualCandidates}
            selectedCandidateId={selectedCandidateId}
            onSelectCandidate={setSelectedCandidateId}
          />
          {task.payload?.risk_level === 'high_risk'
          || task.payload?.risk_level === 'destructive' ? (
            <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
              这是高风险动作。选择候选代表明确人工确认，服务端仍会重新校验任务和候选。
            </p>
          ) : null}
          {task.payload?.execution_allowed === false ? (
            <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
              该视觉任务已执行过但验证失败，不能重复点击。可取消任务后检查页面或重新运行。
            </p>
          ) : null}
        </div>
      ) : null}

      {expired ? (
        <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
          任务已过期，执行已终止，不能再次恢复。
        </p>
      ) : null}
      {task.last_verification_error ? (
        <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
          {redactSensitiveText(task.last_verification_error)}
        </p>
      ) : null}
      {actionError ? (
        <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
          {redactSensitiveText(actionError)}
        </p>
      ) : null}

      {task.status === 'pending' && !expired ? (
        <div className="space-y-3 border-t border-slate-100 pt-4 dark:border-slate-800">
          <div className="flex flex-wrap gap-2">
            {profileId ? (
              <Button
                variant="secondary"
                size="sm"
                leftIcon={
                  openingLogin
                    ? <LoaderCircle className="h-4 w-4 animate-spin" />
                    : <ExternalLink className="h-4 w-4" />
                }
                disabled={openingLogin || verifying}
                onClick={onOpenLogin}
                title="打开可见的自动化浏览器登录窗口"
              >
                打开登录窗口
              </Button>
            ) : (
              <span className="flex items-center gap-1.5 text-xs text-slate-500">
                <ShieldAlert className="h-4 w-4" />
                未关联浏览器 Profile
              </span>
            )}
            <Button
              size="sm"
              leftIcon={
                verifying
                  ? <LoaderCircle className="h-4 w-4 animate-spin" />
                  : <LogIn className="h-4 w-4" />
              }
              disabled={!resumeAllowed}
              onClick={() => onResume(selectedCandidateId ?? undefined)}
              title="服务端重新验证通过后从检查点继续"
            >
              {verifying
                ? '正在重新验证'
                : visualTask
                  ? '确认候选并继续'
                  : '验证并继续'}
            </Button>
            <Button
              variant="danger"
              size="sm"
              leftIcon={<Square className="h-4 w-4" />}
              disabled={verifying}
              onClick={onCancel}
              title="取消人工任务并终止执行"
            >
              取消执行
            </Button>
          </div>
          {verifying ? (
            <p className="flex items-center gap-2 text-xs text-amber-700 dark:text-amber-300">
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
              正在重新验证页面或窗口状态，尚未恢复执行。
            </p>
          ) : null}
          <p className="text-xs leading-5 text-slate-500 dark:text-slate-400">
            继续操作不会直接标记成功；只有服务端验证和原检查点都可用时才会恢复。
          </p>
        </div>
      ) : null}
    </section>
  );
}

export default HumanTaskPanel;
