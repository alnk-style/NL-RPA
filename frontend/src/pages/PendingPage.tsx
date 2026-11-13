import { useEffect, useMemo, useState } from 'react';
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  Clock3,
  RefreshCw,
  Search,
} from 'lucide-react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import {
  cancelHumanTask,
  listHumanTasks,
  openBrowserProfileLogin,
  resumeHumanTask,
} from '../api/humanTasks';
import { queryKeys } from '../api/queryKeys';
import { HumanTaskList } from '../components/pending/HumanTaskList';
import { HumanTaskPanel } from '../components/pending/HumanTaskPanel';
import { Button } from '../components/ui/Button';
import { getApiErrorMessage } from '../lib/errors';
import {
  humanTaskVerificationMessage,
} from '../lib/human-tasks';
import type { HumanTaskRecord } from '../types/humanTasks';

const STATUS_OPTIONS = [
  { value: 'pending', label: '待处理' },
  { value: 'resolved', label: '已解决' },
  { value: 'cancelled', label: '已取消' },
  { value: 'expired', label: '已过期' },
  { value: '', label: '全部状态' },
];

export function PendingPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedStatus = searchParams.get('status');
  const [status, setStatus] = useState(
    requestedStatus === null ? 'pending' : requestedStatus,
  );
  const requestedTaskId = Number(searchParams.get('task'));
  const [selectedId, setSelectedId] = useState<number | null>(
    Number.isFinite(requestedTaskId) && requestedTaskId > 0
      ? requestedTaskId
      : null,
  );
  const [verifyingId, setVerifyingId] = useState<number | null>(null);
  const [openingLoginId, setOpeningLoginId] = useState<number | null>(
    null,
  );
  const [actionError, setActionError] = useState<string | null>(null);

  const tasksQuery = useQuery({
    queryKey: [...queryKeys.humanTasks, status],
    queryFn: () => listHumanTasks(status ? { status } : {}),
    refetchInterval: 10_000,
  });
  const tasks = tasksQuery.data?.items ?? [];
  const selectedTask = useMemo(
    () => tasks.find((task) => task.id === selectedId) ?? tasks[0] ?? null,
    [selectedId, tasks],
  );

  useEffect(() => {
    if (!selectedTask) {
      setSelectedId(null);
      return;
    }
    if (selectedTask.id !== selectedId) {
      setSelectedId(selectedTask.id);
    }
  }, [selectedId, selectedTask]);

  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    if (status) {
      next.set('status', status);
    } else {
      next.delete('status');
    }
    if (selectedId) {
      next.set('task', String(selectedId));
    } else {
      next.delete('task');
    }
    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, selectedId, setSearchParams, status]);

  const resumeMutation = useMutation({
    mutationFn: ({
      task,
      candidateId,
    }: {
      task: HumanTaskRecord;
      candidateId?: string;
    }) => resumeHumanTask(
      task.id,
      task.kind === 'visual_ambiguous'
        ? { candidate_id: candidateId ?? '' }
        : task.kind === 'manual_confirmation'
          ? { manual_confirmed: true }
          : {},
    ),
  });
  const cancelMutation = useMutation({
    mutationFn: (humanTaskId: number) => cancelHumanTask(humanTaskId),
  });
  const openLoginMutation = useMutation({
    mutationFn: ({
      profileId,
      task,
    }: {
      profileId: number;
      task: HumanTaskRecord;
    }) => openBrowserProfileLogin(
      profileId,
      task.id,
      task.current_url,
    ),
  });

  async function refreshAll() {
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: queryKeys.humanTasks,
      }),
      queryClient.invalidateQueries({
        queryKey: queryKeys.executions,
      }),
    ]);
  }

  async function handleResume(
    task: HumanTaskRecord,
    candidateId?: string,
  ) {
    setVerifyingId(task.id);
    setActionError(null);
    try {
      await resumeMutation.mutateAsync({ task, candidateId });
      await refreshAll();
    } catch (requestError) {
      setActionError(
        humanTaskVerificationMessage(
          requestError,
          getApiErrorMessage(
            requestError,
            '服务端重新验证失败，任务仍保持待处理。',
          ),
        ),
      );
      await queryClient.invalidateQueries({
        queryKey: queryKeys.humanTask(task.id),
      });
    } finally {
      setVerifyingId(null);
    }
  }

  async function handleCancel(task: HumanTaskRecord) {
    setActionError(null);
    try {
      await cancelMutation.mutateAsync(task.id);
      await refreshAll();
    } catch (requestError) {
      setActionError(
        getApiErrorMessage(requestError, '取消人工任务失败。'),
      );
    }
  }

  async function handleOpenLogin(task: HumanTaskRecord) {
    if (!task.browser_profile_id) {
      setActionError('该任务尚未关联合适的浏览器 Profile。');
      return;
    }
    setOpeningLoginId(task.id);
    setActionError(null);
    try {
      await openLoginMutation.mutateAsync({
        profileId: task.browser_profile_id,
        task,
      });
    } catch (requestError) {
      setActionError(
        getApiErrorMessage(requestError, '无法打开登录窗口。'),
      );
    } finally {
      setOpeningLoginId(null);
    }
  }

  const queryError = tasksQuery.error
    ? getApiErrorMessage(tasksQuery.error, '加载待处理任务失败。')
    : null;

  return (
    <section className="space-y-5">
      <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <h1 className="text-xl font-semibold">待处理</h1>
          <p className="mt-1 text-sm text-slate-500">
            登录、验证码和人工确认只有服务端重新验证通过后才会继续。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-slate-400" />
            <select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setSelectedId(null);
                setActionError(null);
              }}
              className="h-8 rounded-md border border-slate-200 bg-white pl-8 pr-7 text-xs text-slate-700 outline-none focus:border-brand-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
              aria-label="任务状态筛选"
            >
              {STATUS_OPTIONS.map((option) => (
                <option key={option.label} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={
              <RefreshCw className={tasksQuery.isFetching
                ? 'h-4 w-4 animate-spin'
                : 'h-4 w-4'} />
            }
            onClick={() => void tasksQuery.refetch()}
          >
            刷新
          </Button>
        </div>
      </header>

      {queryError ? (
        <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
          {queryError}
        </p>
      ) : null}

      {tasksQuery.isLoading ? (
        <div className="h-64 animate-pulse rounded-lg bg-slate-100 dark:bg-slate-900" />
      ) : tasks.length === 0 ? (
        <div className="flex min-h-56 flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 bg-white px-6 text-center dark:border-slate-700 dark:bg-slate-900">
          <Clock3 className="h-7 w-7 text-slate-400" />
          <p className="mt-3 text-sm font-medium">
            {status === 'pending'
              ? '暂无待处理任务'
              : '当前筛选条件下没有任务'}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            需要人工登录、验证码或确认时，任务会自动出现在这里。
          </p>
        </div>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(320px,0.88fr)_minmax(0,1.12fr)]">
          <HumanTaskList
            tasks={tasks}
            selectedId={selectedTask?.id ?? null}
            onSelect={(task) => {
              setSelectedId(task.id);
              setActionError(null);
            }}
          />
          {selectedTask ? (
            <HumanTaskPanel
              task={selectedTask}
              verifying={verifyingId === selectedTask.id}
              openingLogin={openingLoginId === selectedTask.id}
              actionError={actionError}
              onOpenLogin={() => void handleOpenLogin(selectedTask)}
              onResume={(candidateId) => {
                void handleResume(selectedTask, candidateId);
              }}
              onCancel={() => void handleCancel(selectedTask)}
              onOpenExecution={() => {
                navigate(`/executions/${selectedTask.execution_id}`);
              }}
            />
          ) : null}
        </div>
      )}
    </section>
  );
}

export default PendingPage;
