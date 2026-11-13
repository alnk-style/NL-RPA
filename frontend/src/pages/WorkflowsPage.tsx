import {
  useDeferredValue,
  useMemo,
  useState,
} from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  LoaderCircle,
  Plus,
  Search,
  Trash2,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import * as workflowApi from '../api/workflows';
import { listExecutions, runWorkflow } from '../api/executions';
import { queryKeys } from '../api/queryKeys';
import { WorkflowTable } from '../components/workflows/WorkflowTable';
import { Button } from '../components/ui/Button';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '../components/ui/Card';
import { Input } from '../components/ui/Input';
import { getApiErrorMessage } from '../lib/errors';
import { shouldWarnHostRestart } from '../lib/hosts';
import type { WorkflowRecord } from '../types/workflow';

export function WorkflowsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<WorkflowRecord | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newGoal, setNewGoal] = useState('');
  const [duplicateSource, setDuplicateSource] =
    useState<WorkflowRecord | null>(null);
  const [duplicateName, setDuplicateName] = useState('');
  const [restartWarning, setRestartWarning] =
    useState<WorkflowRecord | null>(null);
  const deferredSearch = useDeferredValue(search);
  const workflowsQuery = useQuery({
    queryKey: queryKeys.workflows,
    queryFn: workflowApi.listWorkflows,
  });
  const executionsQuery = useQuery({
    queryKey: queryKeys.executions,
    queryFn: listExecutions,
  });

  const deleteMutation = useMutation({
    mutationFn: workflowApi.deleteWorkflow,
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: queryKeys.workflows,
      });
      setDeleteTarget(null);
      setNotice('工作流已删除');
    },
    onError: (requestError) => {
      setError(getApiErrorMessage(requestError, '删除工作流失败'));
    },
  });
  const createMutation = useMutation({
    mutationFn: workflowApi.createWorkflow,
    onSuccess: async (workflow) => {
      await queryClient.invalidateQueries({
        queryKey: queryKeys.workflows,
      });
      setShowCreate(false);
      setNewName('');
      setNewGoal('');
      navigate(`/workflows/${workflow.id}/studio`);
    },
    onError: (requestError) => {
      setError(getApiErrorMessage(requestError, '创建工作流失败'));
    },
  });

  const filteredWorkflows = useMemo(() => {
    const query = deferredSearch.trim().toLowerCase();
    const rows = workflowsQuery.data ?? [];
    if (!query) {
      return rows;
    }
    return rows.filter((workflow) =>
      [
        workflow.name,
        workflow.description,
        workflow.natural_language_input,
      ].some((value) => value?.toLowerCase().includes(query)),
    );
  }, [deferredSearch, workflowsQuery.data]);

  function openDuplicate(workflow: WorkflowRecord) {
    setDuplicateSource(workflow);
    setDuplicateName(`${workflow.name} 副本`);
    setError(null);
  }

  async function confirmDuplicate() {
    if (!duplicateSource || !duplicateName.trim()) {
      return;
    }
    const key = `${duplicateSource.id}:duplicate`;
    setBusyAction(key);
    setError(null);
    try {
      const duplicate = await workflowApi.duplicateWorkflow(
        duplicateSource.id,
        duplicateName.trim(),
      );
      setDuplicateSource(null);
      await queryClient.invalidateQueries({
        queryKey: queryKeys.workflows,
      });
      navigate(`/workflows/${duplicate.id}/studio`);
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '复制工作流失败'));
    } finally {
      setBusyAction(null);
    }
  }

  async function handleTogglePublished(workflow: WorkflowRecord) {
    const key = `${workflow.id}:publish`;
    setBusyAction(key);
    setError(null);
    setNotice(null);
    try {
      if (workflow.status === 'published') {
        await workflowApi.unpublishWorkflow(workflow.id);
      } else {
        await workflowApi.publishWorkflow(workflow.id);
      }
      await queryClient.invalidateQueries({
        queryKey: queryKeys.workflows,
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.workflowVersions(workflow.id),
      });
    } catch (requestError) {
      setError(
        getApiErrorMessage(
          requestError,
          workflow.status === 'published' ? '停用工作流失败' : '发布工作流失败',
        ),
      );
    } finally {
      setBusyAction(null);
    }
  }

  async function handleRun(
    workflow: WorkflowRecord,
    confirmedRestart = false,
  ) {
    if (
      !confirmedRestart
      && shouldWarnHostRestart(
        executionsQuery.data ?? [],
        workflow.id,
      )
    ) {
      setRestartWarning(workflow);
      return;
    }
    const key = `${workflow.id}:run`;
    setBusyAction(key);
    setError(null);
    setNotice(null);
    try {
      const result = await runWorkflow(workflow.id);
      await queryClient.invalidateQueries({
        queryKey: queryKeys.executions,
      });
      navigate(`/executions/${result.id}`);
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, '启动工作流失败'));
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">工作流</h1>
          <p className="mt-1 text-sm text-slate-500">
            按版本管理已发布的自动化流程
          </p>
        </div>
        <Button
          leftIcon={<Plus className="h-4 w-4" />}
          onClick={() => {
            setError(null);
            setShowCreate(true);
          }}
        >
          新建工作流
        </Button>
      </header>

      <div className="relative max-w-xl">
        <Search className="pointer-events-none absolute left-3 top-1/2 z-10 h-4 w-4 -translate-y-1/2 text-slate-400" />
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="搜索名称或需求描述"
          className="pl-9"
          aria-label="搜索工作流"
        />
      </div>

      {error ? (
        <Notice tone="error" text={error} />
      ) : null}
      {notice ? <Notice tone="success" text={notice} /> : null}

      {workflowsQuery.isLoading ? (
        <div className="flex h-52 items-center justify-center rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <LoaderCircle className="h-5 w-5 animate-spin text-slate-400" />
        </div>
      ) : filteredWorkflows.length ? (
        <WorkflowTable
          workflows={filteredWorkflows}
          busyAction={busyAction}
          onDuplicate={openDuplicate}
          onDelete={setDeleteTarget}
          onRun={(workflow) => void handleRun(workflow)}
          onTogglePublished={(workflow) =>
            void handleTogglePublished(workflow)
          }
        />
      ) : (
        <div className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-16 text-center dark:border-slate-700 dark:bg-slate-900">
          <p className="text-base font-semibold">
            {search ? '没有匹配的工作流' : '还没有工作流'}
          </p>
          <p className="mt-1 text-sm text-slate-500">
            {search ? '尝试更换关键词' : '从自然语言描述开始创建草稿'}
          </p>
        </div>
      )}

      {duplicateSource ? (
        <Dialog title="复制工作流">
          <Input
            value={duplicateName}
            autoFocus
            onChange={(event) => setDuplicateName(event.target.value)}
            aria-label="副本名称"
          />
          <DialogActions>
            <Button
              variant="secondary"
              onClick={() => setDuplicateSource(null)}
            >
              取消
            </Button>
            <Button
              onClick={() => void confirmDuplicate()}
              disabled={
                !duplicateName.trim()
                || busyAction === `${duplicateSource.id}:duplicate`
              }
            >
              复制
            </Button>
          </DialogActions>
        </Dialog>
      ) : null}

      {showCreate ? (
        <Dialog title="新建工作流">
          <div className="space-y-3">
            <Input
              value={newName}
              autoFocus
              onChange={(event) => setNewName(event.target.value)}
              placeholder="工作流名称"
              aria-label="工作流名称"
            />
            <textarea
              className="min-h-24 w-full rounded-md border border-slate-200 bg-white p-3 text-sm outline-none focus:border-brand-500 dark:border-slate-700 dark:bg-slate-900"
              value={newGoal}
              onChange={(event) => setNewGoal(event.target.value)}
              placeholder="可选：描述要完成的目标，进入 Studio 后生成结构化草稿"
            />
            <p className="text-xs text-slate-500">
              留空目标即可从空白结构化工作流开始。
            </p>
          </div>
          <DialogActions>
            <Button
              variant="secondary"
              onClick={() => setShowCreate(false)}
            >
              取消
            </Button>
            <Button
              disabled={!newName.trim() || createMutation.isPending}
              onClick={() => createMutation.mutate({
                name: newName.trim(),
                natural_language_input: newGoal.trim() || newName.trim(),
              })}
            >
              创建并打开 Studio
            </Button>
          </DialogActions>
        </Dialog>
      ) : null}

      {deleteTarget ? (
        <Dialog title="删除工作流">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            确定删除“{deleteTarget.name}”吗？历史版本也会一并删除。
          </p>
          <DialogActions>
            <Button variant="secondary" onClick={() => setDeleteTarget(null)}>
              取消
            </Button>
            <Button
              variant="danger"
              leftIcon={<Trash2 className="h-4 w-4" />}
              disabled={deleteMutation.isPending}
              onClick={() => deleteMutation.mutate(deleteTarget.id)}
            >
              删除
            </Button>
          </DialogActions>
        </Dialog>
      ) : null}

      {restartWarning ? (
        <Dialog title="确认在新的执行主机上重新开始">
          <p className="text-sm leading-6 text-slate-600 dark:text-slate-300">
            最近一次执行包含 host-bound 桌面状态，但原执行主机已不可用。
            直接重新开始时，系统无法确认窗口、登录态和已完成副作用是否仍然有效，
            可能产生重复下载、发送或写入。
          </p>
          <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-300">
            如需保留原检查点，请先恢复原 Agent。只有明确接受重复副作用风险时，
            才在新主机重新开始。
          </p>
          <DialogActions>
            <Button
              variant="secondary"
              onClick={() => setRestartWarning(null)}
            >
              取消
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                const workflow = restartWarning;
                setRestartWarning(null);
                void handleRun(workflow, true);
              }}
            >
              接受风险并重新开始
            </Button>
          </DialogActions>
        </Dialog>
      ) : null}
    </section>
  );
}

function Notice({
  tone,
  text,
}: {
  tone: 'success' | 'error';
  text: string;
}) {
  return (
    <div
      className={
        tone === 'success'
          ? 'rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700'
          : 'rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700'
      }
    >
      {text}
    </div>
  );
}

function Dialog({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4">
      <Card
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="w-full max-w-md"
      >
        <CardHeader>
          <CardTitle>{title}</CardTitle>
          {children}
        </CardHeader>
        <CardContent />
      </Card>
    </div>
  );
}

function DialogActions({ children }: { children: React.ReactNode }) {
  return <div className="mt-4 flex justify-end gap-2">{children}</div>;
}

export default WorkflowsPage;
